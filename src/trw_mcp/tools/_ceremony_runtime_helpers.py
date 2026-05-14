"""Ceremony runtime helpers — extracted from ceremony.py.

Belongs to the ``ceremony.py`` facade. Re-exported there for back-compat
(several tests patch these directly via
``trw_mcp.tools.ceremony.{helper}``).

Eleven helpers covering run-status read, run hints, reflection,
instruction sync, learning reflection messaging, and pin-isolation
age computation.

Extracted as DIST-243 batch 53 to push parent ``ceremony.py`` away from
the 936-LOC top-of-list violator position.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING, cast

import structlog

from trw_mcp.exceptions import StateError
from trw_mcp.models.config import get_config
from trw_mcp.models.typed_dicts import (
    ClaudeMdSyncResultDict,
    ReflectResultDict,
    RunStatusDict,
)
from trw_mcp.state.analytics import find_success_patterns, update_analytics
from trw_mcp.state.claude_md import execute_claude_md_sync
from trw_mcp.state.persistence import FileEventLogger, FileStateReader, FileStateWriter

logger = structlog.get_logger(__name__)

if TYPE_CHECKING:
    from trw_mcp.models.config import TRWConfig


def _get_run_status(run_dir: Path) -> RunStatusDict:
    """Extract status summary from a run directory."""
    reader = FileStateReader()
    result: RunStatusDict = {"active_run": str(run_dir)}
    try:
        run_yaml = run_dir / "meta" / "run.yaml"
        if run_yaml.exists():
            data = reader.read_yaml(run_yaml)
            result["phase"] = str(data.get("phase", "unknown"))
            result["status"] = str(data.get("status", "unknown"))
            result["task_name"] = str(data.get("task", ""))
            if "owner_session_id" in data:
                sid = data["owner_session_id"]
                result["owner_session_id"] = str(sid) if sid is not None else None
            wave_status = data.get("wave_status")
            if wave_status and isinstance(wave_status, dict):
                result["wave_status"] = wave_status
    except (StateError, OSError, ValueError):
        result["status"] = "error_reading"
    return result


def _candidate_run_hints(limit: int = 3) -> list[dict[str, object]]:
    """Return recent pinned run candidates without adopting any of them."""
    try:
        from trw_mcp.state._pin_store import load_pin_store

        pins = load_pin_store()
    except Exception:  # justified: guidance only, never block ceremony tools
        logger.debug("candidate_run_hints_failed", exc_info=True)
        return []
    candidates: list[dict[str, object]] = []
    for pin_key, entry in pins.items():
        run_path = entry.get("run_path")
        if not isinstance(run_path, str) or not run_path:
            continue
        if not Path(run_path).exists():
            continue
        candidates.append(
            {
                "run_path": run_path,
                "pin_key": pin_key,
                "pid": entry.get("pid"),
                "last_heartbeat_ts": entry.get("last_heartbeat_ts"),
                "adopt_command": f'trw_adopt_run(run_path="{run_path}")',
            }
        )
    candidates.sort(key=lambda item: str(item.get("last_heartbeat_ts") or ""), reverse=True)
    return candidates[:limit]


def _no_active_run_hint(candidate_runs: list[dict[str, object]]) -> str:
    """Build actionable no-pin guidance while preserving PRD-CORE-141 isolation."""
    hint = (
        "No active run for this session. Call trw_init() to create a new run, "
        "or call trw_adopt_run(run_path=...) to resume an existing run."
    )
    if candidate_runs:
        hint += " Candidate run paths are advisory only; TRW will not auto-adopt another session's run."
    return hint


def _mark_run_complete(run_dir: Path) -> None:
    """Mark a run as complete by updating status in run.yaml."""
    reader = FileStateReader()
    writer = FileStateWriter()
    run_yaml = run_dir / "meta" / "run.yaml"
    if not run_yaml.exists():
        return
    try:
        data = reader.read_yaml(run_yaml)
        data["status"] = "complete"
        writer.write_yaml(run_yaml, data)
    except Exception:  # justified: fail-open, marking complete is best-effort
        logger.warning("mark_run_complete_failed", exc_info=True, run_dir=str(run_dir))


def _persist_surface_snapshot_pointer(run_dir: Path, snapshot_id: str) -> None:
    """Persist the run's surface snapshot pointer into ``run.yaml`` (FR-2)."""
    run_yaml = run_dir / "meta" / "run.yaml"
    if not run_yaml.exists():
        return
    reader = FileStateReader()
    writer = FileStateWriter()
    try:
        data = reader.read_yaml(run_yaml)
        data["surface_snapshot_id"] = snapshot_id
        data["run_surface_snapshot_path"] = "meta/run_surface_snapshot.yaml"
        writer.write_yaml(run_yaml, data)
    except Exception:  # justified: fail-open, pointer persistence must not block session start
        logger.warning(
            "surface_snapshot_pointer_persist_failed",
            run_dir=str(run_dir),
            snapshot_id=snapshot_id,
            exc_info=True,
        )


def _do_reflect(trw_dir: Path, run_dir: Path | None) -> ReflectResultDict:
    """Execute reflection logic — extract learnings from events.

    Simplified version of the full trw_reflect tool, focused on
    mechanical extraction for delivery ceremony.
    """
    from trw_mcp.state.analytics import (
        extract_learnings_mechanical,
        find_repeated_operations,
        is_error_event,
    )

    config = get_config()
    reader = FileStateReader()
    writer = FileStateWriter()
    writer.ensure_dir(trw_dir / config.learnings_dir / config.entries_dir)
    writer.ensure_dir(trw_dir / config.reflections_dir)

    events: list[dict[str, object]] = []
    if run_dir:
        events_path = run_dir / "meta" / "events.jsonl"
        if reader.exists(events_path):
            events = reader.read_jsonl(events_path)

    error_events = [e for e in events if is_error_event(e)]
    repeated_ops = find_repeated_operations(events)
    success_patterns = find_success_patterns(events)
    new_learnings = extract_learnings_mechanical(
        error_events,
        repeated_ops,
        trw_dir,
        max_errors=5,
        max_repeated=3,
    )

    # Success patterns are analytics data only — do NOT create learning entries
    # (PRD-FIX-021: suppress telemetry noise from "Success: X (Nx)" entries).

    if run_dir and (run_dir / "meta").exists():
        FileEventLogger(writer).log_event(
            run_dir / "meta" / "events.jsonl",
            "reflection_complete",
            {
                "reflection_id": "delivery",
                "scope": "delivery",
                "learnings_produced": len(new_learnings),
            },
        )

    update_analytics(trw_dir, len(new_learnings))
    return {
        "status": "success",
        "events_analyzed": len(events),
        "learnings_produced": len(new_learnings),
        "success_patterns": len(success_patterns),
    }


def _do_instruction_sync(trw_dir: Path) -> ClaudeMdSyncResultDict:
    """Sync platform instruction files (CLAUDE.md, AGENTS.md, etc.).

    Resolves get_config + execute_claude_md_sync through the parent
    ``ceremony`` module so test monkeypatches at
    ``trw_mcp.tools.ceremony.{get_config, execute_claude_md_sync}``
    take effect at call time.
    """
    del trw_dir  # canonical execute_claude_md_sync resolves trw_dir from config
    from trw_mcp.clients.llm import LLMClient
    from trw_mcp.tools import ceremony as _ceremony

    get_config_fn = cast("Callable[[], TRWConfig]", getattr(_ceremony, "get_config", get_config))
    config = get_config_fn()
    reader = FileStateReader()
    llm = LLMClient()

    platforms = config.target_platforms
    if len(platforms) == 1:
        client = platforms[0]
    elif len(platforms) > 1:
        client = "all"
    else:
        client = "auto"

    sync_fn = cast(
        "Callable[..., ClaudeMdSyncResultDict]",
        getattr(_ceremony, "execute_claude_md_sync", execute_claude_md_sync),
    )
    raw = sync_fn(
        scope="root",
        target_dir=None,
        config=config,
        reader=reader,
        llm=llm,
        client=client,
    )
    raw["status"] = "success"
    return raw


def _learning_reflection_message(learnings_count: int) -> str:
    """Return a self-reflection message based on session learning count.

    PRD-CORE-125 FR05: Informational reminder (never blocks delivery).
    """
    if learnings_count > 0:
        return f"{learnings_count} discovery/discoveries persisted for future sessions."
    return (
        "Note: No discoveries were recorded this session. "
        "Consider what you learned — even a one-line root cause "
        "helps the next agent avoid re-discovery."
    )


def _parse_iso_utc(ts: str) -> datetime | None:
    """Parse an ISO 8601 UTC timestamp tolerating the ``Z`` suffix; None on failure."""
    if not ts:
        return None
    try:
        normalized = ts[:-1] + "+00:00" if ts.endswith("Z") else ts
        parsed = datetime.fromisoformat(normalized)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except ValueError:
        return None


def _timedelta_hours(hours: float) -> timedelta:
    """Return a ``timedelta`` spanning *hours* (isolated for patchability)."""
    return timedelta(hours=hours)


def _compute_run_age_hours(run_dir: Path | None) -> float:
    """Return the run's age in hours from run.yaml ``created_at``; falls back to mtime."""
    if run_dir is None:
        return 0.0
    run_yaml = run_dir / "meta" / "run.yaml"
    if not run_yaml.exists():
        return 0.0
    try:
        reader = FileStateReader()
        data = reader.read_yaml(run_yaml)
        for key in ("created_at", "created_ts", "started_at"):
            val = data.get(key)
            parsed: datetime | None = None
            if isinstance(val, datetime):
                parsed = val if val.tzinfo else val.replace(tzinfo=timezone.utc)
            elif isinstance(val, str) and val:
                parsed = _parse_iso_utc(val)
            if parsed is not None:
                return max(0.0, (datetime.now(timezone.utc) - parsed).total_seconds() / 3600.0)
    except Exception:  # justified: fail-open — run-age probe must not raise
        logger.debug("run_age_read_failed", run_path=str(run_dir), exc_info=True)
    try:
        mtime = run_yaml.stat().st_mtime
        mtime_dt = datetime.fromtimestamp(mtime, tz=timezone.utc)
        return max(0.0, (datetime.now(timezone.utc) - mtime_dt).total_seconds() / 3600.0)
    except OSError:
        return 0.0
