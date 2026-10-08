"""``trw-mcp feedback list|flush`` — the local feedback outbox (FEEDBACK-LOCAL-OUTBOX).

``list`` is read-only and never prints a message body. ``flush`` is the only retry path: a submit sends once and
never resends older records on its own.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from trw_mcp.tools.submit_feedback import SubmissionPayload


def _positive_int(text: str) -> int:
    value = int(text)
    if value < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return value


def add_feedback_subcommands(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register ``feedback list`` and ``feedback flush``."""
    feedback = subparsers.add_parser("feedback", help="The local feedback outbox: list or retry unsent reports")
    verbs = feedback.add_subparsers(dest="feedback_command")
    listing = verbs.add_parser("list", help="Show pending and sent reports, without their bodies (read-only)")
    listing.add_argument("--json", dest="as_json", action="store_true", help="Print one JSON document")
    flush = verbs.add_parser("flush", help="Resend pending reports, oldest first")
    flush.add_argument("--limit", type=_positive_int, default=10, help="Most reports to send in this run")
    flush.add_argument("--json", dest="as_json", action="store_true", help="Print one JSON document")


def run_feedback(args: argparse.Namespace) -> None:
    """Run ``feedback`` against the project enclosing the cwd, so it finds what ``local feedback`` queued."""
    from trw_mcp.server._subcommands_misc import enclosing_project_bound

    with enclosing_project_bound():
        _run_feedback(args)


def _run_feedback(args: argparse.Namespace) -> None:
    """Dispatch ``feedback list|flush``; exit 1 when a flush left anything unsent."""
    command = str(getattr(args, "feedback_command", None) or "")
    as_json = bool(getattr(args, "as_json", False))
    if command == "list":
        document = list_outbox()
        failed = False
    elif command == "flush":
        document = flush_outbox(limit=int(args.limit))
        failed = (
            bool(document["error"])
            or document.get("remaining", 0) > 0
            or not all(r["sent"] for r in document["results"])
        )
    else:
        print("usage: trw-mcp feedback {list|flush}", file=sys.stderr)
        sys.exit(2)
    if as_json:
        print(json.dumps(document, default=str))
    elif command == "list":
        for section in ("pending", "sent"):
            print(f"{section}: {len(document[section])}")
            for entry in document[section]:
                accepted = (
                    f"  accepted by the server as {entry['submission_id']}, not yet settled locally"
                    if section == "pending" and entry.get("submission_id")
                    else ""
                )
                print(
                    f"  {entry.get('id', '')}  [{entry['category']}] {entry['subject']}  "
                    f"{entry.get('last_error', '')}{accepted}"
                )
        for name in document["unreadable"]:
            print(f"unreadable: {name}")
    else:
        if document["error"]:
            print(f"Not flushed: {document['error']}")
        for result in document["results"]:
            outcome = f"sent {result['submission_id']}" if result["sent"] else f"not sent: {result['error']}"
            note = f" ({result['note']})" if result.get("note") else ""
            print(f"{result['id']}: {outcome}{note}")
        if document.get("remaining", 0):
            print(f"Not flushed: {document['remaining']} report(s) remain pending or claimed")
    sys.exit(1 if failed else 0)


def list_outbox() -> dict[str, Any]:
    """``trw-mcp feedback list``: pending and sent records, without their bodies. Read-only."""
    from trw_mcp.state._paths import resolve_trw_dir
    from trw_mcp.tools import _feedback_outbox as _outbox

    trw_dir = resolve_trw_dir()
    document: dict[str, Any] = {
        sub: [_outbox.summary(r) for _p, r in _outbox.records(trw_dir, "outbox" if sub == "pending" else "sent")]
        for sub in ("pending", "sent")
    }
    document["unreadable"] = _outbox.unreadable(trw_dir, "outbox") + _outbox.unreadable(trw_dir, "sent")
    return document


def flush_outbox(*, limit: int) -> dict[str, Any]:
    """``trw-mcp feedback flush``: resend up to *limit* pending records, oldest first. The only retry path."""
    from trw_mcp.state._paths import resolve_trw_dir
    from trw_mcp.tools import _feedback_outbox as _outbox
    from trw_mcp.tools import submit_feedback as _submit

    backend_url, api_key = _submit._backend()
    if not backend_url or not api_key:
        return {"error": _submit._not_configured(backend_url, api_key), "results": []}
    trw_dir = resolve_trw_dir()
    results: list[dict[str, Any]] = []
    # A record whose delivery is already known (its sent/ copy could not be written) is never resent.
    _outbox.release_stale(trw_dir)
    pending = [p for p, r in _outbox.records(trw_dir, "outbox") if p.suffix == ".json" and not r.get("submission_id")]
    for candidate in pending[:limit]:
        claimed = _outbox.claim(candidate)  # a concurrent submit or flush already holds it: skipped
        if claimed is None:
            continue
        path, record = claimed
        payload, error = _stored_payload(record.get("payload"))
        if payload is None:
            _outbox.settle(path, success=False, submission_id="", error=error, status_code=0)
            results.append({"id": _outbox.stem(path), "sent": False, "submission_id": "", "error": error})
            continue
        sent = _submit._send_recorded(backend_url, api_key, payload, path)
        note = "sent without contact_email (never stored locally)" if record.get("contact_email_dropped") else ""
        results.append({"id": _outbox.stem(path), "sent": sent.success, "submission_id": sent.submission_id,
                        "error": sent.error, "note": note})  # fmt: skip
    remaining = len(_outbox.records(trw_dir, "outbox"))
    return {"error": "", "results": results, "remaining": remaining}


def _stored_payload(stored: object) -> tuple[SubmissionPayload | None, str]:
    """Rebuild a stored record through the same redaction and validation as a fresh submit.

    The file on disk is not trusted: a hand-edited record must not reach the wire unredacted or unvalidated.
    """
    from trw_mcp.telemetry.anonymizer import redact_metadata, redact_secrets
    from trw_mcp.tools.submit_feedback import _validate

    if not isinstance(stored, dict):
        return None, "stored record has no payload"
    raw_meta = stored.get("metadata")
    metadata = {str(k): str(v) for k, v in raw_meta.items()} if isinstance(raw_meta, dict) else None
    payload: SubmissionPayload = {
        "category": str(stored.get("category", "")),
        "subject": redact_secrets(str(stored.get("subject", ""))).strip(),
        "message": redact_secrets(str(stored.get("message", ""))),
        "metadata": redact_metadata(metadata) or {},
    }
    error = _validate(category=payload["category"], subject=payload["subject"], message=payload["message"],
                      metadata=payload["metadata"], contact_email=None)  # fmt: skip
    if error:
        return None, f"stored record is invalid: {error}"
    return payload, ""


__all__ = ["add_feedback_subcommands", "flush_outbox", "list_outbox", "run_feedback"]
