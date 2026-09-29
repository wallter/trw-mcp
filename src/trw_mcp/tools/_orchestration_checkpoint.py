"""Checkpoint execution helper for orchestration tools."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import cast

import structlog

from trw_mcp.exceptions import StateError
from trw_mcp.models.typed_dicts import CheckpointEventDataDict, CheckpointRecordDict
from trw_mcp.state._decision_queue import record_decision
from trw_mcp.state._factory_experiment import check as check_factory_gate
from trw_mcp.state._factory_experiment import is_factory_message
from trw_mcp.state._factory_receipt_gate import factory_payload_refusal, unresolved_receipts
from trw_mcp.state._helpers import read_jsonl_resilient
from trw_mcp.state._no_active_run import is_no_active_run, no_active_run_remedy
from trw_mcp.state._paths import TRWCallContext, resolve_run_path, resolve_trw_dir
from trw_mcp.state.persistence import FileEventLogger, FileStateReader, FileStateWriter
from trw_mcp.tools._orchestration_time import checkpoint_time_fields, utc_now

logger = structlog.get_logger(__name__)
_events = FileEventLogger(FileStateWriter())

#: Machine-readable reason for the empty-message refusal. A checkpoint IS its
#: message — ``checkpoints.jsonl`` carries nothing else a later session can
#: resume from — so a blank one preserves no material state (VISION Principle 5)
#: while ``recorded: True`` would report that it did (CONSTITUTION HB-1).
EMPTY_MESSAGE_REASON = "empty_message"

#: Remedy for :data:`EMPTY_MESSAGE_REASON`, phrased as the one executable fix.
EMPTY_MESSAGE_REMEDY = "Remedy: pass message=<what you completed and what is next> — it is the resume point."

#: PRD-CORE-329-FR01: refusal reason when ``blocked_decision`` is missing
#: ``question`` or ``why_unreachable``. Checked ahead of run resolution and
#: the ordinary checkpoint write, same as :data:`EMPTY_MESSAGE_REASON` — no
#: partial record (neither the checkpoint nor the decision) is ever written.
BLOCKED_DECISION_INVALID_REASON = "blocked_decision_invalid"
BLOCKED_DECISION_INVALID_REMEDY = "Remedy: blocked_decision needs non-empty question and why_unreachable."


def _invalid_blocked_decision(blocked_decision: dict[str, object] | None) -> bool:
    """True when *blocked_decision* is present but fails FR01's shape check."""
    if blocked_decision is None:
        return False
    question = blocked_decision.get("question")
    why_unreachable = blocked_decision.get("why_unreachable")
    return not (isinstance(question, str) and question.strip()) or not (
        isinstance(why_unreachable, str) and why_unreachable.strip()
    )


def _not_recorded(
    context: TRWCallContext | None,
    *,
    reason: str,
    remedy: str,
) -> dict[str, object]:
    """PRD-CORE-233 FR02/NFR04 — truthful "nothing was persisted" payload.

    Zero filesystem writes happen on this path: no run directory is created and
    no ``checkpoints.jsonl`` is touched, so PRD-CORE-141's anti-hijack invariant
    is preserved. The response carries an EXPLICIT ``recorded`` flag rather than
    letting the caller infer state from a missing key, and never reuses the
    ``checkpoint_created`` status token — under the value hierarchy a quiet
    result must not be mistakable for a successful one.

    The failure goes quiet in the response, so it must stay loud in the logs:
    ``checkpoint_not_recorded`` is the monitoring signal for orchestrators that
    stop honoring the FR01 caller-supplied-run contract (``no_active_run``) or
    that call the tool with nothing to preserve (``empty_message``).

    ``reason`` is the stable machine key consumers switch on; the human-readable
    ``remedy`` may be reworded freely without breaking them.
    """
    logger.warning(
        "checkpoint_not_recorded",
        reason=reason,
        pin_key=context.session_id if context is not None else None,
    )
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "recorded": False,
        "status": "not_recorded",
        "reason": reason,
        "remedy": remedy,
    }


def execute_checkpoint(
    run_path: str | None,
    message: str,
    shard_id: str | None,
    *,
    context: TRWCallContext | None = None,
    blocked_decision: dict[str, object] | None = None,
    slice_done: str = "",
) -> dict[str, object]:
    """Persist checkpoint state and return the base response payload.

    Args:
        context: Optional :class:`TRWCallContext` (PRD-CORE-141 FR03).  When
            provided, ``resolve_run_path`` is ctx-aware — no-pin sessions never
            hijack another session's run.
        blocked_decision: PRD-CORE-329 FR01 — ``{question, options,
            why_unreachable}``. Validated before any write; on success, one
            record is appended to the project-level decision queue after the
            ordinary checkpoint write succeeds.

    Returns:
        A payload whose ``recorded`` flag is ``True`` when the checkpoint was
        appended, or the :func:`_not_recorded` payload when the caller has no
        resolvable run (PRD-CORE-233 FR02), supplied no message, or supplied
        an invalid ``blocked_decision``.
    """
    # Precondition, checked before ANY resolution or write: the message is the
    # entire payload of a checkpoint. ``trw_checkpoint()`` with no arguments used
    # to append ``{"message": ""}`` and answer ``recorded: True`` — a handoff
    # artifact that preserves nothing, reported as if it preserved something.
    # Ordered ahead of run resolution deliberately: it needs no I/O, so the
    # zero-write guarantee holds even for a caller that also has no run.
    if not message.strip():
        return _not_recorded(context, reason=EMPTY_MESSAGE_REASON, remedy=EMPTY_MESSAGE_REMEDY)
    # PRD-CORE-340-FR11/FR12: a recognized factory transition is refused before any resolution or
    # write unless the experiment is enabled and in time. Ordinary messages never reach the gate.
    if is_factory_message(message):
        gate = check_factory_gate()
        if not gate.enabled:
            return _not_recorded(context, reason=gate.reason or "factory_disabled", remedy=f"Remedy: {gate.message}")
        if slice_done.strip():
            # PRD-CORE-340-FR04: factory START/READY/USED supersede slice_done; refused before any write.
            refused = _not_recorded(
                context,
                reason="factory_slice_done_conflict",
                remedy="Factory START/READY/USED supersede slice_done; record the slice in the factory attempt instead.",
            )
            refused["error_type"] = "factory_slice_done_conflict"
            return refused
        # FACTORY-START-VALIDATE: a malformed payload (no attempt, unknown kind or key, the reader's own schema
        # violations) never enters the append-only journal. Zero I/O, so it sits with the other refusals.
        if (problem := factory_payload_refusal(message)) is not None:
            invalid = _not_recorded(
                context,
                reason="factory_payload_invalid",
                remedy=f"Factory payload refused: {problem}. Fix the key and record it again; nothing was written.",
            )
            invalid["error_type"] = "factory_payload_invalid"
            return invalid
    # Same zero-I/O precondition ordering for FR01: an invalid blocked_decision
    # refuses the WHOLE call, including the ordinary checkpoint write — a
    # caller cannot get a half-recorded state (checkpoint written, decision
    # silently dropped) by supplying a malformed dict.
    if _invalid_blocked_decision(blocked_decision):
        return _not_recorded(
            context,
            reason=BLOCKED_DECISION_INVALID_REASON,
            remedy=BLOCKED_DECISION_INVALID_REMEDY,
        )

    reader = FileStateReader()
    writer = FileStateWriter()
    try:
        resolved_path = resolve_run_path(run_path, context=context)
    except StateError as exc:
        # FR02 boundary: exactly one softened branch — no explicit run_path, a
        # ctx-aware caller, and no pin. Everything else (a run_path that does
        # not exist or escapes the project root, an unreadable run) is a caller
        # mistake rather than a missing precondition and still raises.
        if run_path is not None or context is None or not is_no_active_run(exc):
            raise
        return _not_recorded(context, reason="no_active_run", remedy=no_active_run_remedy())
    meta_path = resolved_path / "meta"

    # FACTORY-READY-RECEIPT-RESOLVE: a READY/USED naming a receipt that does not exist is refused
    # before the write (the journal is append-only, so a placeholder id could never be repaired).
    if is_factory_message(message) and (unresolved := unresolved_receipts(resolved_path, message)):
        refused = _not_recorded(
            context,
            reason="factory_receipt_unresolved",
            remedy=(
                f"Unresolved receipt(s): {', '.join(unresolved)}. Record the receipt first (trw_build_check returns "
                "build_receipt_id; a verifier's receipt id comes from `receipt verify`), then checkpoint with that id."
            ),
        )
        refused["error_type"] = "factory_receipt_unresolved"
        return refused

    state_data = reader.read_yaml(meta_path / "run.yaml")
    machine_now = utc_now()
    ts = machine_now.isoformat()

    checkpoint: CheckpointRecordDict = {
        "ts": ts,
        "message": message,
        "state": state_data,
    }
    if shard_id:
        checkpoint["shard_id"] = shard_id

    writer.append_jsonl(
        meta_path / "checkpoints.jsonl",
        cast("dict[str, object]", checkpoint),
    )

    event_data: CheckpointEventDataDict = {"message": message}
    if shard_id:
        event_data["shard_id"] = shard_id
    if slice_done.strip():
        # PRD-CORE-338-FR03: the first event per id is the slice's completion time.
        event_data["slice_done"] = slice_done.strip()
    _events.log_event(
        meta_path / "events.jsonl",
        "checkpoint",
        cast("dict[str, object]", event_data),
    )

    logger.info(
        "checkpoint_ok",
        run_id=str(state_data.get("run_id", "")),
        message=message[:80],
    )
    # Truncated echo: the caller already has its own message; the full text is
    # persisted in checkpoints.jsonl. Echoing it back verbatim doubled the
    # token cost of every long checkpoint call.
    result: dict[str, object] = {
        "timestamp": ts,
        # Symmetric with the not-recorded payload: the caller reads ONE field to
        # know whether the checkpoint exists, never an absence (NFR04).
        "recorded": True,
        "status": "checkpoint_created",
        "message": message if len(message) <= 120 else message[:120] + "…",
    }
    # PRD-CORE-338-FR06: adds keys only for a mis-stamped message or a tracked run (NFR02).
    result.update(
        checkpoint_time_fields(
            state_data,
            message,
            machine_now,
            lambda: read_jsonl_resilient(meta_path / "events.jsonl"),
        )
    )
    if blocked_decision is not None:
        raw_options = blocked_decision.get("options")
        options = list(raw_options) if isinstance(raw_options, list) else []
        decision_id = record_decision(
            resolve_trw_dir(),
            run_path=str(resolved_path),
            question=str(blocked_decision.get("question", "")),
            options=[str(opt) for opt in options],
            why_unreachable=str(blocked_decision.get("why_unreachable", "")),
        )
        result["blocked_decision_id"] = decision_id
    return result
