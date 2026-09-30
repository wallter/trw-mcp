"""The run's append-only ``meta/events.jsonl`` as a second witness to what was declared (EVIDENCE-DELETION-POLICY).

A scope or evidence that was ever declared cannot be undeclared by deleting files. The primary artifacts
(``meta/run.yaml``, review receipts, ``meta/integration-review.yaml``) are rewritable and deletable; every
declaration a blocking gate relies on is ALSO appended to ``events.jsonl`` when it is made, and the gate reads
both. Deleting the primary leaves the witness; the witness itself unreadable is UNKNOWN and the caller fails
closed. Runs recorded before a witness existed carry no witness events and are judged as before.

The log is read through the evidence-bound reader (no symlink anywhere below the run, regular file, inode
bound). A torn FINAL line -- a writer killed mid-append -- is ignored; any other line that is not a JSON object is
corruption, because silently skipping it could drop a declaration.
"""

from __future__ import annotations

import json
from pathlib import Path

from trw_mcp.state._evidence_bound_read import EvidenceUnreadable, read_evidence_text

EVENTS = "meta/events.jsonl"
INTEGRATION_REVIEW_RECORDED = "integration_review_recorded"


def run_events(run_path: Path) -> list[dict[str, object]] | None:
    """Every event in the run's log; ``None`` when the log is absent. Raises :class:`EvidenceUnreadable`."""
    text = read_evidence_text(run_path, EVENTS)
    if text is None:
        return None
    lines = text.split("\n")
    torn_tail = lines.pop() if not text.endswith("\n") else ""
    events: list[dict[str, object]] = []
    for number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except ValueError as exc:
            raise EvidenceUnreadable(f"{EVENTS} line {number} is not JSON") from exc
        if not isinstance(event, dict):
            raise EvidenceUnreadable(f"{EVENTS} line {number} is not an object")
        events.append(event)
    if torn_tail.strip():
        try:
            tail = json.loads(torn_tail)
        except ValueError:  # trw-fail-silent-allow: a torn final append carries no complete declaration
            tail = None
        if isinstance(tail, dict):
            events.append(tail)
    return events


def _field(event: dict[str, object], name: str) -> object:
    # log_event merges fields at the top level; older writers nested them under "data".
    if name in event:
        return event[name]
    nested = event.get("data")
    return nested.get(name) if isinstance(nested, dict) else None


def recorded_scope(run_path: Path) -> list[str]:
    """Every PRD the run ever declared: ``run_init.prd_scope`` plus each ``audit_cycle_complete.prd_id``.

    Raises :class:`EvidenceUnreadable` when the log exists but cannot be trusted.
    """
    entries: list[str] = []
    for event in run_events(run_path) or []:
        kind = event.get("event")
        if kind == "run_init":
            scope = _field(event, "prd_scope")
            if isinstance(scope, list):
                entries.extend(str(entry) for entry in scope)
        elif kind == "audit_cycle_complete":
            prd_id = _field(event, "prd_id")
            if isinstance(prd_id, str) and prd_id:
                entries.append(prd_id)
    return entries


def last_integration_review(run_path: Path) -> dict[str, object] | None:
    """The most recent ``integration_review_recorded`` event, or ``None`` when none was ever recorded."""
    found: dict[str, object] | None = None
    for event in run_events(run_path) or []:
        if event.get("event") == INTEGRATION_REVIEW_RECORDED:
            found = event
    return found


INTEGRATION_REVIEW = "meta/integration-review.yaml"


def read_integration_review(run_path: Path) -> tuple[dict[str, object] | None, str]:
    """``(mapping, text)`` from ONE bound read; ``(None, "")`` when absent or empty. Raises :class:`EvidenceUnreadable`.

    The gate judges and the witness hashes the same bytes (codex r1: two reads could authenticate one file
    and judge another).
    """
    from trw_mcp.state._persistence_helpers import _safe_yaml

    text = read_evidence_text(run_path, INTEGRATION_REVIEW)
    if text is None or not text.strip():
        return None, ""
    try:
        data = _safe_yaml().load(text)
    except Exception as exc:  # justified: every parser failure is unreadable, raised
        raise EvidenceUnreadable(f"{INTEGRATION_REVIEW} is not valid YAML") from exc
    if not isinstance(data, dict):
        raise EvidenceUnreadable(f"{INTEGRATION_REVIEW} root must be a mapping, got {type(data).__name__}")
    return data, text


def integration_review_witness_block(run_path: Path, text: str) -> str | None:
    """The block when the witnessed integration review is missing, emptied or changed; ``None`` otherwise.

    *text* is what :func:`read_integration_review` read (``""`` when absent or empty). Never recorded and
    absent stays "no integration review was run" (INT-REVIEW-ABSENCE-POLICY), and a run from before the
    witness existed is judged as before.
    """
    import hashlib

    present = bool(text)

    try:
        recorded = last_integration_review(run_path)
    except EvidenceUnreadable:
        if present:
            return None  # the artifact itself is read and judged; only its provenance is unverified
        return (
            "Delivery blocked: meta/integration-review.yaml is absent and the run's event log cannot be read, "
            "so whether an integration review was recorded is UNKNOWN. Repair meta/events.jsonl or re-run the "
            "integration review with trw_review(); this gate has no allow_unverified escape."
        )
    if recorded is None:
        return None
    verdict = str(_field(recorded, "verdict") or "unknown")
    if not present:
        return (
            f"Delivery blocked: an integration review (verdict {verdict!r}) was recorded for this run, but "
            "meta/integration-review.yaml is now missing or empty. A recorded review cannot be withdrawn by "
            "deleting it; re-run the integration review with trw_review(). This gate has no allow_unverified escape."
        )
    if hashlib.sha256(text.encode("utf-8")).hexdigest() != str(_field(recorded, "sha256") or ""):
        return (
            f"Delivery blocked: meta/integration-review.yaml no longer matches the integration review recorded "
            f"for this run (verdict {verdict!r}); it was edited after trw_review wrote it. Re-run the integration "
            "review with trw_review(); this gate has no allow_unverified escape."
        )
    return None


__all__ = [
    "EVENTS",
    "INTEGRATION_REVIEW_RECORDED",
    "integration_review_witness_block",
    "last_integration_review",
    "read_integration_review",
    "recorded_scope",
    "run_events",
]
