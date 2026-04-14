"""Checkpoint execution helper for orchestration tools."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import cast

import structlog

from trw_mcp.models.typed_dicts import CheckpointEventDataDict, CheckpointRecordDict
from trw_mcp.state._paths import TRWCallContext, resolve_run_path
from trw_mcp.state.persistence import FileEventLogger, FileStateReader, FileStateWriter
from trw_mcp.tools._orchestration_lifecycle import _update_wave_status

logger = structlog.get_logger(__name__)
_events = FileEventLogger(FileStateWriter())


def execute_checkpoint(
    run_path: str | None,
    message: str,
    shard_id: str | None,
    wave_id: str | None,
    *,
    context: TRWCallContext | None = None,
) -> dict[str, str]:
    """Persist checkpoint state and return the base response payload.

    Args:
        context: Optional :class:`TRWCallContext` (PRD-CORE-141 FR03).  When
            provided, ``resolve_run_path`` is ctx-aware — no-pin sessions
            raise ``StateError`` instead of hijacking another session's run.
    """
    reader = FileStateReader()
    writer = FileStateWriter()
    resolved_path = resolve_run_path(run_path, context=context)
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
    result: dict[str, str] = {
        "timestamp": ts,
        "status": "checkpoint_created",
        "message": message,
    }
    if wave_id:
        result["wave_id"] = wave_id
    return result
