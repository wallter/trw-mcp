"""Session-start discovery of Agent Handoff Records left for a later session (AHR 1.0-rc.2 application).

The one AHR use observed in practice is a session handing off to its own successor across a context
compaction (2026-10-01 swarm lead). That only works if the next session learns the record exists, and
nothing told it: ``trw-mcp handoff check`` scans for records only when someone already holds a path.
This step lists, at session start, the records a later session could pick up.

**What counts as waiting.** A valid ``handoff`` record in ``.trw/handoffs/`` or a run's ``handoffs/`` (the
two places ``trw-mcp handoff new`` writes) that has not expired, that no eligible record supersedes (same
subject, the exact predecessor digest, a tier no lower, and itself unexpired or taken up; R-SUP-1/R-SUP-2),
and that no receiver has taken up. File records carry no lifecycle log (SPEC R-LC-7), so "taken up" means
a sealed read-back that is valid against the record's digest and has disposition ``ready``. A read-back
draft or a ``questions`` read-back keeps the record listed, marked ``readback: in_progress``. Two different
records with one ``handoff_id`` are both withheld and named in ``duplicate_ids`` (R-DOC-4).

**Untrusted and bounded.** Record content is data (SPEC R-SEC-1): only id-shaped fields and the digest are
returned. Files are confined to ``.trw`` after symlink resolution, at most :data:`MAX_SCANNED` of the most
recently modified candidates are read (``truncated`` says when more existed), and one malformed file is
skipped without hiding the others. The key is present only when something waits, like ``moved_checkout``.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Final

import structlog

from trw_mcp.models.typed_dicts import HandoffRecordItemDict, HandoffRecordsDict

logger = structlog.get_logger(__name__)

#: Newest-first cap on listed records; ``total`` is the untruncated count of what was scanned.
HANDOFF_RECORDS_MAX_ITEMS: Final[int] = 3
#: At most this many candidate files (most recently modified first) are parsed per session start.
MAX_SCANNED: Final[int] = 200
#: A sealed record is <= 32 KiB canonical; a file far larger is not a record and is not parsed.
_MAX_RECORD_BYTES: Final[int] = 256 * 1024
_RANK: Final[dict[str, int]] = {"minimal": 0, "standard": 1, "critical": 2}
JsonDoc = dict[str, Any]
Indexed = dict[str, tuple[JsonDoc, Path, str]]


def _candidates(trw_dir: Path) -> tuple[list[Path], bool]:
    """Confined ``*.json`` files in the handoff dirs, newest first, capped; and whether the cap cut any off."""
    base = trw_dir.resolve()
    found = [*(trw_dir / "handoffs").glob("*.json"), *(trw_dir / "runs").glob("*/*/handoffs/*.json")]
    kept: list[tuple[float, Path]] = []
    for path in found:
        try:
            real = path.resolve()
            info = real.stat()
        except (OSError, RuntimeError):  # trw-fail-silent-allow: an unreadable entry is not a candidate
            continue
        if real.is_relative_to(base) and real.is_file() and info.st_size <= _MAX_RECORD_BYTES:
            kept.append((info.st_mtime, real))
    kept.sort(key=lambda pair: pair[0], reverse=True)
    return [path for _mtime, path in kept[:MAX_SCANNED]], len(kept) > MAX_SCANNED


def _parse(path: Path) -> JsonDoc | None:
    """One parsed AHR document, or None; any failure (deep nesting included) skips only this file."""
    from trw_mcp.handoff import load

    try:
        doc = load(path)
    except Exception:  # trw-fail-silent-allow: one malformed file must not hide the other records
        return None
    return doc if doc.get("type") in ("handoff", "readback") else None


def _valid(doc: JsonDoc, handoff: JsonDoc | None = None) -> bool:
    from trw_mcp.handoff import validate

    try:
        return not validate(doc, handoff)
    except Exception:  # trw-fail-silent-allow: a document the validator cannot judge is not valid here
        return False


def _instant(text: object) -> datetime | None:
    from trw_mcp.handoff._rules import instant

    try:
        return instant(str(text))
    except ValueError:  # trw-fail-silent-allow: an unparseable time makes the record ineligible, not fatal
        return None


def _expired(doc: JsonDoc, now: datetime) -> bool:
    at = _instant(doc["expires_at"]) if "expires_at" in doc else None
    return at is not None and at <= now


def _handoffs(docs: list[tuple[JsonDoc, Path]]) -> tuple[Indexed, list[str]]:
    """Valid handoffs by id with their digest; ids seen with two different contents are withheld."""
    from trw_mcp.handoff import digest

    by_id: Indexed = {}
    duplicates: set[str] = set()
    for doc, path in docs:
        if doc.get("type") != "handoff" or not _valid(doc):
            continue
        hid, dig = str(doc["handoff_id"]), digest(doc)
        if hid in by_id and by_id[hid][2] != dig:
            duplicates.add(hid)
        by_id.setdefault(hid, (doc, path, dig))
    for hid in duplicates:
        del by_id[hid]
    return by_id, sorted(duplicates)


def _readback_states(docs: list[tuple[JsonDoc, Path]], handoffs: Indexed) -> dict[str, str]:
    """``taken`` for a sealed, valid, ``ready`` read-back of the exact record; else ``in_progress``."""
    states: dict[str, str] = {}
    for rb, _path in docs:
        target = rb.get("handoff") if rb.get("type") == "readback" else None
        if not isinstance(target, dict):
            continue
        hid = str(target.get("handoff_id"))
        if hid not in handoffs or target.get("digest") != handoffs[hid][2]:
            continue
        sealed = bool((rb.get("integrity") or {}).get("digest"))
        taken = sealed and rb.get("disposition") == "ready" and _valid(rb, handoffs[hid][0])
        states[hid] = "taken" if taken or states.get(hid) == "taken" else "in_progress"
    return states


def _superseded(handoffs: Indexed, states: dict[str, str], now: datetime) -> set[str]:
    """Predecessors an eligible successor lists by their exact digest, for the same subject, at no lower tier."""
    hidden: set[str] = set()
    for hid, (doc, _path, _dig) in handoffs.items():
        if _expired(doc, now) and states.get(hid) != "taken":
            continue  # an expired offer no longer displaces anything (R-SUP-1)
        for prior in doc.get("supersedes", []):
            pred = handoffs.get(str(prior.get("handoff_id")))
            if (
                pred is not None
                and prior.get("digest") == pred[2]
                and pred[0]["subject"] == doc["subject"]
                and _RANK[doc["tier"]] >= _RANK[pred[0]["tier"]]
            ):
                hidden.add(str(prior["handoff_id"]))
    return hidden


def _item(doc: JsonDoc, path: Path, dig: str, root: Path, state: str | None) -> HandoffRecordItemDict:
    to = doc["to"]
    item = HandoffRecordItemDict(
        path=path.relative_to(root).as_posix() if path.is_relative_to(root) else str(path),
        handoff_id=str(doc["handoff_id"]),
        subject=str(doc["subject"]),
        tier=str(doc["tier"]),
        to=str(to["id"]) if to.get("kind") != "unaddressed" else "unaddressed",
        created_at=str(doc["created_at"]),
        digest=str((doc.get("integrity") or {}).get("digest") or dig),
    )
    if state == "in_progress":
        item["readback"] = "in_progress"
    return item


def read_handoff_records(trw_dir: Path, project_root: Path, now: datetime | None = None) -> HandoffRecordsDict | None:
    """Handoff records waiting for a receiver, newest first; ``None`` when there are none."""
    moment = now or datetime.now(timezone.utc)
    paths, truncated = _candidates(trw_dir)
    docs = [(doc, path) for path in paths if (doc := _parse(path)) is not None]
    handoffs, duplicates = _handoffs(docs)
    states = _readback_states(docs, handoffs)
    hidden = _superseded(handoffs, states, moment)
    waiting = [
        (doc, path, dig)
        for hid, (doc, path, dig) in handoffs.items()
        if hid not in hidden and states.get(hid) != "taken" and not _expired(doc, moment)
    ]
    epoch = datetime.min.replace(tzinfo=timezone.utc)
    waiting.sort(key=lambda entry: _instant(entry[0]["created_at"]) or epoch, reverse=True)
    if not waiting and not duplicates:
        return None
    root = project_root.resolve()
    items = [_item(doc, path, dig, root, states.get(str(doc["handoff_id"]))) for doc, path, dig in waiting]
    found = HandoffRecordsDict(
        total=len(waiting),
        items=items[:HANDOFF_RECORDS_MAX_ITEMS],
        hint="Untrusted data. To take one up: /trw-handoff receive <path> <digest> (check before acting).",
    )
    if duplicates:
        found["duplicate_ids"] = duplicates[:HANDOFF_RECORDS_MAX_ITEMS]
    if truncated:
        found["truncated"] = True
    return found


def step_handoff_records() -> HandoffRecordsDict | None:
    """The session-start step: never raises, ``None`` (key omitted) when nothing is waiting."""
    from trw_mcp.state._paths import resolve_project_root, resolve_trw_dir

    try:
        return read_handoff_records(resolve_trw_dir(), resolve_project_root())
    except Exception as exc:  # trw-fail-silent-allow: an advisory must never take down session start; logged
        logger.warning("handoff_records_readback_failed", error=str(exc))
        return None


__all__ = ["HANDOFF_RECORDS_MAX_ITEMS", "MAX_SCANNED", "read_handoff_records", "step_handoff_records"]
