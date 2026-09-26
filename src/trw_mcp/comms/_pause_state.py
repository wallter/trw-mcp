"""What a formation pause means to comms: member state, the send gate, and ack_pause.

Belongs to the ``trw_mcp.comms`` facade. Design: PAUSE-RESUME-DESIGN.md rev 2
(lane C READY; enforced per lead decision 957a6567, refined by lane C SF4).

- While a pause is active a bound, non-orchestrator member reports ``paused``,
  and ``paused_acked`` once it has acked. The orchestrator is never paused.
- A paused member's send is refused ``formation_paused`` unless it is
  ``kind=status`` or ``kind=reply`` addressed to the orchestrator member. The gate
  runs BEFORE the mailbox transaction, so the refusal is never counted: an older
  server verifying a mailbox would reject an unknown refusal bucket as corrupt.
- ``trw_inbox`` fetch and ACK stay allowed, so a paused member can drain the lead's notes.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog

from trw_mcp.formation import (
    FormationError,
    PauseError,
    PauseRecord,
    ack_pause,
    orchestrator_run_of,
    read_manifest,
    read_pause,
)

if TYPE_CHECKING:
    from trw_mcp.comms._identity import CallerBinding, CallerSnapshot

_logger = structlog.get_logger(__name__)
PAUSED_STATES = frozenset({"paused", "paused_acked"})
#: The pause reason is orchestrator-authored; a member sees at most this much of it.
REASON_MAX_CHARS = 200


def active_pause(binding: CallerBinding) -> PauseRecord | None:
    """The pause *binding* is subject to; ``None`` for the orchestrator. A malformed file raises."""
    record = read_pause(binding.manifest_path)
    if record is None or binding.run_path.resolve() == orchestrator_run_of(read_manifest(binding.manifest_path)):
        return None
    return record


def member_state(binding: CallerBinding) -> tuple[str, dict[str, str]] | None:
    """``(paused|paused_acked, pause block)`` when paused, else ``None``. Fail-open: state is advisory."""
    try:
        record = active_pause(binding)
    except FormationError:
        # trw-fail-silent-allow: the state is advisory; logged, and the send gate fails closed on the same read
        _logger.info("comms_pause_record_unreadable")
        return None
    if record is None:
        return None
    state = "paused_acked" if binding.member_id in record.acks else "paused"
    return state, {"pause_id": record.pause_id, "reason": record.reason[:REASON_MAX_CHARS]}


def send_refusal(snapshot: CallerSnapshot, recipient_member_id: str | None, kind: str) -> str | None:
    """The closed refusal for a send under a pause, or ``None`` when the send may proceed."""
    try:
        record = active_pause(snapshot.binding)
        if record is None:
            return None
        orchestrator_run = orchestrator_run_of(read_manifest(snapshot.binding.manifest_path))
    except FormationError:
        return "formation_unavailable"  # fail closed: an unreadable pause must not be read as "not paused"
    to_orchestrator = any(
        r.member_id == recipient_member_id and r.run_path is not None and Path(r.run_path).resolve() == orchestrator_run
        for r in snapshot.recipients
    )
    return None if kind in ("status", "reply") and to_orchestrator else "formation_paused"


def ack(binding: CallerBinding, pause_id: str | None) -> dict[str, Any]:
    """``trw_inbox(action='ack_pause')`` for an eligible bound member: its own ack only."""
    if not pause_id:
        return {"status": "refused", "reason": "pause_id_mismatch"}
    try:
        recorded = ack_pause(binding.manifest_path, binding.member_id, binding.run_path, pause_id)
    except PauseError as exc:
        return {"status": "refused", "reason": exc.reason}
    except FormationError:
        return {"status": "refused", "reason": "formation_unavailable"}
    return {"status": "ok", "pause_id": pause_id, "acked": True, "already": not recorded}


__all__ = ["PAUSED_STATES", "REASON_MAX_CHARS", "ack", "active_pause", "member_state", "send_refusal"]
