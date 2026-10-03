"""Send one learnings batch and isolate the entries the backend rejects (INC-147).

A 422 used to fail a batch as a unit, and the cycle kept the whole batch dirty
and re-sent it forever: one learning the backend would not take held back every
learning of the project. Here a 422 is read for the entries it names
(``loc: ["body", "entries", <index>, ...]``). Those are returned as rejected,
with the server's reason, and the rest are re-sent. A 422 that names no
location is split in halves until the entries it refuses are isolated; one that
names only request-level fields (``loc: ["body", "client_id"]``) is a failure of
the request, so nothing is quarantined for it.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import structlog

from trw_mcp.telemetry.anonymizer import redact_secrets

logger = structlog.get_logger(__name__)

_MAX_REASON_CHARS = 300
_MAX_LOGGED_BODY_CHARS = 2_000

#: Posts one ``entries`` payload; returns the response (raises on transport failure).
PostEntries = Callable[[list[dict[str, object]]], Awaitable[Any]]


@dataclass
class BatchOutcome:
    """What one top-level batch came to: counts, rejected ids with reasons, and the last error seen."""

    pushed: int = 0
    skipped: int = 0
    failed: int = 0
    rejected: dict[str, str] = field(default_factory=dict)
    last_error: str | None = None


def _details(body: object) -> list[dict[str, Any]] | str:
    """The ``detail`` of an error body: FastAPI's list of ``{loc, msg}``, or a plain message."""
    detail = body.get("detail", body) if isinstance(body, dict) else body
    if isinstance(detail, list):
        return [
            {"loc": list(item.get("loc") or []), "msg": str(item.get("msg", ""))}
            for item in detail
            if isinstance(item, dict)
        ]
    return str(detail)


def _body(resp: Any) -> object:
    try:
        return resp.json()
    except ValueError:  # trw-fail-silent-allow: a non-JSON error body is reported as its text
        return str(getattr(resp, "text", ""))


def _loc_text(loc: list[Any]) -> str:
    return ".".join(str(part) for part in loc) or "request"


def _reason(status: int, details: list[dict[str, Any]] | str) -> str:
    """One line naming the HTTP status and the server's own words, never the echoed input."""
    if isinstance(details, list):
        text = "; ".join(f"{_loc_text(d['loc'][1:])}: {d['msg']}" for d in details) or "no detail"
    else:
        text = details or "no detail"
    return redact_secrets(f"HTTP {status}: {text}")[:_MAX_REASON_CHARS]


def _entry_reasons(details: list[dict[str, Any]] | str, count: int) -> tuple[dict[int, str], bool]:
    """``(reason by entry index, whether any location names the request rather than an entry)``."""
    named: dict[int, str] = {}
    request_level = False
    for item in details if isinstance(details, list) else []:
        loc = item["loc"]
        index = loc[2] if len(loc) > 2 and loc[:2] == ["body", "entries"] else None
        if isinstance(index, int) and 0 <= index < count:
            text = f"{_loc_text(loc[3:]) if loc[3:] else 'entry'}: {item['msg']}"
            named[index] = redact_secrets(f"{named[index]}; {text}" if index in named else text)[:_MAX_REASON_CHARS]
        elif loc:
            request_level = True
    return named, request_level


async def send_learning_batch(
    post: PostEntries,
    items: list[tuple[str, dict[str, object]]],
    *,
    client_id: str,
    refused: Callable[[list[str]], bool] | None = None,
) -> BatchOutcome:
    """Send *items* (``(entry id, payload)``) and isolate what the server rejects. Never raises.

    *refused* is asked with the entry ids immediately before every POST, the 422 recovery re-sends included
    (PRD-CORE-333, EGRESS-RECHECK-INNER-RETRIES): a True answer counts those items failed and sends nothing.
    """
    out = BatchOutcome()
    inferred: dict[str, str] = {}
    await _send(post, items, out, inferred, client_id, refused)
    if inferred and len(inferred) == len(items) and len(items) > 1:
        # Every entry refused without a single location: that is the request, not the entries.
        out.failed += len(inferred)
        out.last_error = next(iter(inferred.values()))
    else:
        out.rejected.update(inferred)
    return out


async def _send(
    post: PostEntries,
    items: list[tuple[str, dict[str, object]]],
    out: BatchOutcome,
    inferred: dict[str, str],
    client_id: str,
    refused: Callable[[list[str]], bool] | None = None,
) -> None:
    if not items:
        return
    if refused is not None and refused([entry_id for entry_id, _ in items]):
        # Failed, never acknowledged: the cycle marks nothing synced on a failed push, and the next page leaves a
        # quarantined row out.
        out.failed += len(items)
        out.last_error = "a learning was quarantined during the push; nothing more was sent"
        logger.info("sync_push_stopped_by_quarantine", client_id=client_id, count=len(items))
        return
    try:
        resp = await post([payload for _, payload in items])
        status = int(resp.status_code)
        # Parsed here so an unreadable success body fails the batch like a transport error.
        result = resp.json() if status < 400 else {}
        counts = [int(result.get(k, 0)) for k in ("errors", "inserted", "updated", "skipped")]
    # trw-fail-silent-allow: counted as failed with last_error and a warning; the batch stays dirty for retry
    except Exception as exc:  # justified: boundary, a transport failure fails this batch and keeps it dirty
        out.failed += len(items)
        out.last_error = f"{type(exc).__name__}: {redact_secrets(str(exc))[:200]}"
        logger.warning(
            "sync_push_error",
            event_type="sync_push_error",
            client_id=client_id,
            count=len(items),
            error=out.last_error,
            exc_info=True,
        )
        return
    if status < 400:
        errors, inserted, updated, skipped = counts
        if errors > 0:
            # Backend reports only aggregate errors, so keep the whole batch dirty for safe retry.
            out.failed += len(items)
            out.last_error = f"HTTP {status}: backend reported {errors} error(s)"
        else:
            out.pushed += inserted + updated
            out.skipped += skipped
        return
    details = _details(_body(resp))
    reason = _reason(status, details)
    log_body = redact_secrets(str(details))[:_MAX_LOGGED_BODY_CHARS]
    if status != 422:
        out.failed += len(items)
        out.last_error = reason
        logger.warning(
            "sync_push_error", client_id=client_id, status_code=status, count=len(items), response_body=log_body
        )
        return
    logger.warning(
        "sync_push_rejected", client_id=client_id, status_code=status, count=len(items), response_body=log_body
    )
    named, request_level = _entry_reasons(details, len(items))
    if named:
        for index, why in named.items():
            out.rejected[items[index][0]] = f"HTTP 422: {why}"
        await _send(post, [item for i, item in enumerate(items) if i not in named], out, inferred, client_id, refused)
    elif request_level:
        out.failed += len(items)
        out.last_error = reason
    elif len(items) == 1:
        inferred[items[0][0]] = reason
    else:
        half = len(items) // 2
        await _send(post, items[:half], out, inferred, client_id, refused)
        await _send(post, items[half:], out, inferred, client_id, refused)
