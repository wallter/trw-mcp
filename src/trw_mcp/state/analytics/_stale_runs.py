# Parent facade: state/analytics/report.py
"""Stale-run lifecycle helpers — auto-close, staleness detection, archive summaries.

Extracted from ``report.py`` to keep the facade under the 500-line threshold.
All public names are re-exported from ``report.py`` so existing import paths
(``from trw_mcp.state.analytics.report import auto_close_stale_runs``) are
preserved.

PRD-FIX-028: Auto-close active runs older than a configurable TTL.
PRD-QUAL-050-FR02: Heartbeat-aware staleness detection.

Note: ``get_config`` and ``resolve_project_root`` are accessed through the
parent ``report`` module (``_report.get_config()``) rather than direct imports,
so that ``patch("trw_mcp.state.analytics.report.get_config", ...)`` in tests
correctly intercepts calls from this module.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import structlog

from trw_mcp.exceptions import StateError
from trw_mcp.state.analytics import report as _report
from trw_mcp.state.persistence import FileStateReader, FileStateWriter

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Stale Run helpers (referenced by report.py via re-exports)
# ---------------------------------------------------------------------------


def _get_last_activity_timestamp(run_dir: Path) -> datetime | None:
    """Get the most recent activity timestamp from a run directory.

    Checks both checkpoint timestamps and the heartbeat file mtime,
    returning whichever is more recent.  Runs without a heartbeat file
    fall back to checkpoint-only for backward compatibility.

    PRD-QUAL-050-FR02: heartbeat-aware staleness detection.

    Returns None if neither checkpoints nor heartbeat exist.
    """
    reader = FileStateReader()
    latest: datetime | None = None

    # --- Checkpoint timestamps ---
    cp_path = run_dir / "meta" / "checkpoints.jsonl"
    if cp_path.exists():
        try:
            records = reader.read_jsonl(cp_path)
            for record in records:
                ts_str = str(record.get("ts", ""))
                if ts_str:
                    try:
                        ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                        if ts.tzinfo is None:
                            ts = ts.replace(tzinfo=timezone.utc)
                        if latest is None or ts > latest:
                            latest = ts
                    except ValueError:
                        continue
        except (OSError, StateError):
            pass  # justified: fail-open, unreadable checkpoints should not block staleness check

    # --- Heartbeat file mtime (PRD-QUAL-050-FR02) ---
    heartbeat_path = run_dir / "meta" / "heartbeat"
    try:
        if heartbeat_path.exists():
            hb_mtime = heartbeat_path.stat().st_mtime
            hb_dt = datetime.fromtimestamp(hb_mtime, tz=timezone.utc)
            if latest is None or hb_dt > latest:
                latest = hb_dt
    except OSError:
        pass  # justified: fail-open, missing/unreadable heartbeat falls back to checkpoint-only

    return latest


def _write_archive_summary(
    run_dir: Path,
    run_data: dict[str, object],
    closed_at: str,
) -> None:
    """Write a summary.yaml artifact when closing a stale run."""
    reader = FileStateReader()
    writer = FileStateWriter()

    meta = run_dir / "meta"

    # Count events
    events_count = 0
    events_path = meta / "events.jsonl"
    if events_path.exists():
        try:
            events_count = len(reader.read_jsonl(events_path))
        except (OSError, StateError):
            logger.debug("run_events_read_failed", path=str(events_path))

    # Count checkpoints
    checkpoints_count = 0
    cp_path = meta / "checkpoints.jsonl"
    if cp_path.exists():
        try:
            checkpoints_count = len(reader.read_jsonl(cp_path))
        except (OSError, StateError):
            logger.debug("run_checkpoints_read_failed", path=str(cp_path))

    # Determine started_at from run_id
    run_id = str(run_data.get("run_id", run_dir.name))
    started_at = _report._parse_run_id_timestamp(run_id)

    # Get last activity
    last_activity = _get_last_activity_timestamp(run_dir)
    last_activity_str = last_activity.isoformat() if last_activity else started_at

    summary: dict[str, object] = {
        "run_id": run_id,
        "task": str(run_data.get("task", "")),
        "reason": "Stale timeout \u2014 run exceeded TTL with no activity",
        "closed_at": closed_at,
        "started_at": started_at,
        "last_activity": last_activity_str,
        "events_count": events_count,
        "checkpoints_count": checkpoints_count,
    }
    writer.write_yaml(meta / "summary.yaml", summary)


def _is_run_stale(
    run_dir: Path,
    run_data: dict[str, object],
    ttl_hours: int,
    now: datetime,
) -> bool:
    """Check if a run is stale (exceeds hour-level TTL).

    Considers both checkpoint timestamps and the heartbeat file mtime
    (PRD-QUAL-050-FR02): whichever is more recent resets the staleness clock.
    """
    run_id = str(run_data.get("run_id", run_dir.name))
    started_at = _report._parse_run_id_timestamp(run_id)
    try:
        run_dt = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
    except ValueError:
        return False

    # Check last checkpoint
    last_cp = _get_last_activity_timestamp(run_dir)
    effective_dt = last_cp if last_cp is not None else run_dt

    age_hours = (now - effective_dt).total_seconds() / 3600
    return age_hours > ttl_hours


def auto_close_stale_runs(
    age_days: int | None = None,
    ttl_hours: int | None = None,
) -> dict[str, object]:
    """Auto-close active runs older than a configurable threshold.

    Supports both day-level (legacy) and hour-level TTL (PRD-FIX-028).
    When ttl_hours is provided, it takes precedence over age_days.
    Checkpoint timestamps extend the TTL: the most recent checkpoint
    resets the staleness clock.

    Called automatically during trw_session_start when enabled.

    Args:
        age_days: Days of inactivity before closing. Defaults to config value.
        ttl_hours: Hour-level TTL override. Takes precedence when set.

    Returns:
        Dict with runs_closed list, count, and any errors.
    """
    cfg = _report.get_config()
    reader = FileStateReader()
    writer = FileStateWriter()

    if ttl_hours is not None:
        threshold_hours = ttl_hours
    elif age_days is not None:
        threshold_hours = age_days * 24
    else:
        threshold_hours = cfg.run_stale_ttl_hours

    project_root = _report.resolve_project_root()
    runs_root = project_root / cfg.runs_root

    closed: list[str] = []
    errors: list[str] = []
    now = datetime.now(timezone.utc)

    if not runs_root.exists():
        return {"runs_closed": closed, "count": 0, "errors": errors}

    for task_dir in sorted(runs_root.iterdir()):
        if not task_dir.is_dir():
            continue
        for run_dir in sorted(task_dir.iterdir()):
            run_yaml = run_dir / "meta" / "run.yaml"
            if not run_yaml.exists():
                continue
            try:
                data = reader.read_yaml(run_yaml)
                status = str(data.get("status", ""))
                if status != "active":
                    continue

                if not _is_run_stale(run_dir, data, threshold_hours, now):
                    continue

                run_id = str(data.get("run_id", run_dir.name))
                original_phase = str(data.get("phase", ""))
                data["status"] = "abandoned"
                data["abandoned_at"] = now.isoformat()
                data["original_phase"] = original_phase
                data["abandoned_reason"] = f"Stale timeout \u2014 exceeded threshold: {threshold_hours}h"
                writer.write_yaml(run_yaml, data)
                closed.append(run_id)

                # Write archive summary
                _write_archive_summary(run_dir, data, now.isoformat())

                logger.info(
                    "run_auto_closed",
                    run_id=run_id,
                    threshold_hours=threshold_hours,
                    task=str(data.get("task", "")),
                )
            except (OSError, StateError, ValueError) as exc:
                errors.append(f"{run_dir.name}: {exc}")

    return {"runs_closed": closed, "count": len(closed), "errors": errors}


def count_stale_runs(ttl_hours: int | None = None) -> int:
    """Count active runs that exceed the staleness TTL (read-only).

    Does not modify any run files. Used by trw_status for reporting.

    Args:
        ttl_hours: Hour-level TTL override. Defaults to config value.

    Returns:
        Number of stale active runs.
    """
    cfg = _report.get_config()
    reader = FileStateReader()
    threshold_hours = ttl_hours if ttl_hours is not None else cfg.run_stale_ttl_hours
    project_root = _report.resolve_project_root()
    runs_root = project_root / cfg.runs_root
    now = datetime.now(timezone.utc)

    count = 0
    if not runs_root.exists():
        return 0

    for task_dir in sorted(runs_root.iterdir()):
        if not task_dir.is_dir():
            continue
        for run_dir in sorted(task_dir.iterdir()):
            run_yaml = run_dir / "meta" / "run.yaml"
            if not run_yaml.exists():
                continue
            try:
                data = reader.read_yaml(run_yaml)
                status = str(data.get("status", ""))
                if status != "active":
                    continue
                if _is_run_stale(run_dir, data, threshold_hours, now):
                    count += 1
            except (OSError, StateError):
                continue

    return count
