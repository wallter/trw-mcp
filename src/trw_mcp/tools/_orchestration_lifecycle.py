"""Run lifecycle helpers extracted from orchestration.py (PRD-CORE-089-FR03).

Contains:
  - _compute_reflection_metrics: Count reflections from event stream.
  - _compute_last_activity_ts: Extract last activity timestamp.
  - _parse_timestamp_hours: ISO timestamp to hours-since.
  - _update_wave_status: Update wave status in run.yaml.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import structlog

from trw_mcp.models.typed_dicts import StatusReflectionDict
from trw_mcp.state.persistence import FileStateReader, FileStateWriter

logger = structlog.get_logger(__name__)


def _compute_reflection_metrics(events: list[dict[str, object]]) -> StatusReflectionDict:
    """Count reflection completions from event stream."""
    reflection_count = sum(1 for e in events if e.get("event") == "reflection_complete")
    return StatusReflectionDict(
        count=reflection_count,
    )


def _compute_last_activity_ts(
    reader: FileStateReader,
    meta_path: Path,
    events: list[dict[str, object]],
) -> tuple[str, float | None]:
    """Extract last activity timestamp and hours-since-activity."""
    checkpoints_path = meta_path / "checkpoints.jsonl"
    last_ts = ""
    hours_since = None

    if checkpoints_path.exists():
        checkpoints = reader.read_jsonl(checkpoints_path)
        if checkpoints:
            last_cp = checkpoints[-1]
            last_ts = str(last_cp.get("ts", ""))
            if last_ts:
                hours_since = _parse_timestamp_hours(last_ts)
                if hours_since is not None:
                    return last_ts, hours_since

    # Fall back to run_init event
    run_init_events = [e for e in events if str(e.get("event", "")) == "run_init"]
    if run_init_events:
        init_ts = str(run_init_events[0].get("ts", ""))
        if init_ts:
            last_ts = init_ts
            hours_since = _parse_timestamp_hours(init_ts)

    return last_ts, hours_since


def _parse_timestamp_hours(ts: str) -> float | None:
    """Parse ISO timestamp and return hours since then, or None on error."""
    try:
        last_dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        return round((now - last_dt).total_seconds() / 3600, 1)
    except (ValueError, TypeError):
        logger.debug("timestamp_parse_failed", timestamp=ts)
        return None


def _update_wave_status(
    reader: FileStateReader,
    writer: FileStateWriter,
    meta_path: Path,
    wave_id: str,
    ts: str,
    message: str,
) -> None:
    """Update wave status in run.yaml with checkpoint metadata."""
    try:
        run_yaml = meta_path / "run.yaml"
        if not run_yaml.exists():
            return
        run_data = reader.read_yaml(run_yaml)
        if not isinstance(run_data, dict):
            return
        wave_status = run_data.get("wave_status", {})
        if not isinstance(wave_status, dict):
            wave_status = {}
        wave_status[wave_id] = {
            "last_checkpoint": ts,
            "message": message,
        }
        run_data["wave_status"] = wave_status
        writer.write_yaml(run_yaml, run_data)
    except Exception:  # justified: fail-open, wave status metadata update must not block checkpoint
        logger.debug("wave_status_update_failed", wave_id=wave_id)


def _apply_ceremony_status(
    result: dict[str, object],
    *,
    tool_name: str,
    debug_event: str,
    trw_dir: Path | None = None,
    mark_checkpoint_first: bool = False,
) -> None:
    """Apply orchestration ceremony status wiring without bloating the facade module."""
    try:
        from trw_mcp.state._paths import resolve_trw_dir
        from trw_mcp.state.ceremony_progress import mark_checkpoint
        from trw_mcp.tools._ceremony_status import append_ceremony_status

        resolved_trw_dir = trw_dir or resolve_trw_dir()
        if mark_checkpoint_first:
            mark_checkpoint(resolved_trw_dir)
        append_ceremony_status(result, resolved_trw_dir)
    except Exception:  # justified: fail-open, ceremony status must not break orchestration tools
        logger.debug(debug_event, exc_info=True)
