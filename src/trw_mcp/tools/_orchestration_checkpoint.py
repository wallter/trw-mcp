"""Checkpoint execution helper for orchestration tools."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import cast

import structlog

from trw_mcp.exceptions import StateError
from trw_mcp.models.typed_dicts import CheckpointEventDataDict, CheckpointRecordDict
from trw_mcp.state._no_active_run import is_no_active_run, no_active_run_remedy
from trw_mcp.state._paths import TRWCallContext, resolve_run_path
from trw_mcp.state.persistence import FileEventLogger, FileStateReader, FileStateWriter
from trw_mcp.tools._orchestration_lifecycle import _update_wave_status

logger = structlog.get_logger(__name__)
_events = FileEventLogger(FileStateWriter())

#: Machine-readable reason for the empty-message refusal. A checkpoint IS its
#: message — ``checkpoints.jsonl`` carries nothing else a later session can
#: resume from — so a blank one preserves no material state (VISION Principle 5)
#: while ``recorded: True`` would report that it did (CONSTITUTION HB-1).
EMPTY_MESSAGE_REASON = "empty_message"

#: Remedy for :data:`EMPTY_MESSAGE_REASON`, phrased as the one executable fix.
EMPTY_MESSAGE_REMEDY = "Remedy: pass message=<what you completed and what is next> — it is the resume point."


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
    wave_id: str | None,
    *,
    context: TRWCallContext | None = None,
) -> dict[str, object]:
    """Persist checkpoint state and return the base response payload.

    Args:
        context: Optional :class:`TRWCallContext` (PRD-CORE-141 FR03).  When
            provided, ``resolve_run_path`` is ctx-aware — no-pin sessions never
            hijack another session's run.

    Returns:
        A payload whose ``recorded`` flag is ``True`` when the checkpoint was
        appended, or the :func:`_not_recorded` payload when the caller has no
        resolvable run (PRD-CORE-233 FR02) or supplied no message.
    """
    # Precondition, checked before ANY resolution or write: the message is the
    # entire payload of a checkpoint. ``trw_checkpoint()`` with no arguments used
    # to append ``{"message": ""}`` and answer ``recorded: True`` — a handoff
    # artifact that preserves nothing, reported as if it preserved something.
    # Ordered ahead of run resolution deliberately: it needs no I/O, so the
    # zero-write guarantee holds even for a caller that also has no run.
    if not message.strip():
        return _not_recorded(context, reason=EMPTY_MESSAGE_REASON, remedy=EMPTY_MESSAGE_REMEDY)

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

    state_data = reader.read_yaml(meta_path / "run.yaml")
    ts = datetime.now(timezone.utc).isoformat()

    checkpoint: CheckpointRecordDict = {
        "ts": ts,
        "message": message,
        "state": state_data,
    }
    if shard_id:
        checkpoint["shard_id"] = shard_id
    if wave_id:
        checkpoint["wave_id"] = wave_id

    writer.append_jsonl(
        meta_path / "checkpoints.jsonl",
        cast("dict[str, object]", checkpoint),
    )

    event_data: CheckpointEventDataDict = {"message": message}
    if shard_id:
        event_data["shard_id"] = shard_id
    if wave_id:
        event_data["wave_id"] = wave_id
    _events.log_event(
        meta_path / "events.jsonl",
        "checkpoint",
        cast("dict[str, object]", event_data),
    )

    if wave_id:
        _update_wave_status(reader, writer, meta_path, wave_id, ts, message)

    logger.info(
        "checkpoint_ok",
        run_id=str(state_data.get("run_id", "")),
        message=message[:80],
        wave_id=wave_id,
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
    if wave_id:
        result["wave_id"] = wave_id
    return result
