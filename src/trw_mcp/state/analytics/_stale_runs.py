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

import json
import os
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import structlog

from trw_mcp.exceptions import StateError
from trw_mcp.state._helpers import read_jsonl_resilient
from trw_mcp.state.analytics import report as _report
from trw_mcp.state.persistence import FileStateReader, FileStateWriter

logger = structlog.get_logger(__name__)

# Per-process throttle for auto_close_stale_runs. Sweeping every active
# run.yaml on every session_start cost ~3-5s on a project with ~200 runs;
# stale-run cleanup is idempotent and not time-critical, so once per hour
# is plenty.
_AUTO_CLOSE_MIN_INTERVAL_SECONDS = 3600.0
_auto_close_state_lock = threading.Lock()
_auto_close_last_ts: float = 0.0
_auto_close_persisted_loaded: bool = False

# PRD-FIX-082: persist throttle to disk so per-process restarts (esp.
# user-project stdio installs) inherit the hour-window throttle. Without
# persistence, every fresh MCP process pays the scan cost on its first call.
_AUTO_CLOSE_THROTTLE_FILE_VERSION = 1


def _auto_close_state_path(trw_dir: Path) -> Path:
    """Return the path to the persisted throttle state file."""

    return trw_dir / "runtime" / "auto_close_last_ts.json"


def _load_persisted_throttle(trw_dir: Path) -> float:
    """Load the persisted throttle timestamp as monotonic-equivalent seconds.

    Returns 0.0 when the file is missing, malformed, or older than the
    throttle window (the latter is a fail-safe so a stale file doesn't
    permanently block sweeps).
    """

    path = _auto_close_state_path(trw_dir)
    try:
        if not path.exists():
            return 0.0
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
        version = data.get("version")
        last_iso = data.get("last_ts")
        if version != _AUTO_CLOSE_THROTTLE_FILE_VERSION or not isinstance(last_iso, str):
            logger.debug(
                "auto_close_throttle_state_unreadable",
                reason="version_or_field_mismatch",
                path=str(path),
            )
            return 0.0
        last_dt = datetime.fromisoformat(last_iso.replace("Z", "+00:00"))
        if last_dt.tzinfo is None:
            last_dt = last_dt.replace(tzinfo=timezone.utc)
        wall_age = (datetime.now(timezone.utc) - last_dt).total_seconds()
        if wall_age >= _AUTO_CLOSE_MIN_INTERVAL_SECONDS:
            # Persisted timestamp is older than the throttle window. Treat as
            # "no prior call" so the next call runs the sweep.
            return 0.0
        # Translate wall-clock age into a synthetic monotonic anchor: pretend
        # the prior call happened (now_monotonic - wall_age) seconds ago.
        return time.monotonic() - wall_age
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        logger.debug(
            "auto_close_throttle_state_unreadable",
            path=str(path),
            exc_info=True,
        )
        return 0.0


def _save_persisted_throttle(trw_dir: Path) -> None:
    """Write the current UTC timestamp atomically (temp + rename)."""

    path = _auto_close_state_path(trw_dir)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "last_ts": datetime.now(timezone.utc).isoformat(),
            "version": _AUTO_CLOSE_THROTTLE_FILE_VERSION,
        }
        # Atomic temp + rename so a crash mid-write cannot leave a partial file.
        fd, tmp_path = tempfile.mkstemp(prefix=".auto_close_", dir=str(path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fp:
                json.dump(payload, fp)
            os.replace(tmp_path, path)
        except Exception:
            # Best-effort cleanup if rename failed.
            try:
                Path(tmp_path).unlink()
            except OSError:
                pass
            raise
    except OSError:
        logger.debug(
            "auto_close_throttle_state_write_failed",
            path=str(path),
            exc_info=True,
        )


def _reset_auto_close_throttle(trw_dir: Path | None = None) -> None:
    """Test hook: reset both in-memory and persisted throttle. Never call from production.

    Args:
        trw_dir: When provided, also delete the persisted state file so the
            next call behaves as if no prior sweep occurred. When omitted,
            only the in-memory state is reset (suitable for unit tests that
            never touched the persisted file).
    """

    global _auto_close_last_ts, _auto_close_persisted_loaded
    with _auto_close_state_lock:
        _auto_close_last_ts = 0.0
        _auto_close_persisted_loaded = False
    if trw_dir is not None:
        try:
            _auto_close_state_path(trw_dir).unlink(missing_ok=True)
        except OSError:
            logger.debug(
                "auto_close_throttle_state_reset_failed",
                path=str(_auto_close_state_path(trw_dir)),
                exc_info=True,
            )


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
    latest: datetime | None = None

    # --- Checkpoint timestamps ---
    cp_path = run_dir / "meta" / "checkpoints.jsonl"
    if cp_path.exists():
        try:
            # checkpoints.jsonl is advisory here: it only resets the staleness
            # clock. A torn concurrent append must drop that one line, not erase
            # every checkpoint timestamp — losing the most-recent checkpoint
            # would make a live run look MORE stale and risk a premature
            # auto-close. Resilient read keeps the surviving timestamps.
            records = read_jsonl_resilient(cp_path)
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
    except OSError:  # justified: fail-open, missing/unreadable heartbeat falls back to checkpoint-only
        pass

    return latest


def _write_archive_summary(
    run_dir: Path,
    run_data: dict[str, object],
    closed_at: str,
) -> None:
    """Write a summary.yaml artifact when closing a stale run."""
    writer = FileStateWriter()

    meta = run_dir / "meta"

    # Count events. These counts are advisory archive metadata only, so a torn
    # concurrent append should drop that one line rather than zero out the whole
    # count via a strict-read StateError.
    events_count = len(read_jsonl_resilient(meta / "events.jsonl"))

    # Count checkpoints (advisory archive metadata, same resilient rationale).
    checkpoints_count = len(read_jsonl_resilient(meta / "checkpoints.jsonl"))

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
    *,
    force: bool = False,
) -> dict[str, object]:
    """Auto-close active runs older than a configurable threshold.

    Supports both day-level (legacy) and hour-level TTL (PRD-FIX-028).
    When ttl_hours is provided, it takes precedence over age_days.
    Checkpoint timestamps extend the TTL: the most recent checkpoint
    resets the staleness clock.

    Called automatically during trw_session_start when enabled. Throttled
    per-process to once per hour because sweeping every active run.yaml
    cost ~3-5s on a project with ~200 runs; the sweep is idempotent and
    not time-critical, so the previous "every session_start" cadence
    burned latency for no benefit. Pass ``force=True`` to bypass the
    throttle (tests, manual repair).

    Args:
        age_days: Days of inactivity before closing. Defaults to config value.
        ttl_hours: Hour-level TTL override. Takes precedence when set.
        force: Skip the per-process throttle (default ``False``).

    Returns:
        Dict with runs_closed list, count, and any errors. When throttled,
        returns ``{"runs_closed": [], "count": 0, "errors": [],
        "throttled": True, "next_eligible_in_seconds": <float>}``.
    """
    global _auto_close_last_ts, _auto_close_persisted_loaded
    cfg = _report.get_config()
    project_root = _report.resolve_project_root()
    trw_dir = project_root / str(cfg.trw_dir)

    if not force:
        with _auto_close_state_lock:
            # PRD-FIX-082: on first call after process boot, seed the in-memory
            # throttle from the persisted file so user-project stdio installs
            # don't pay the scan tax on every fresh process.
            if not _auto_close_persisted_loaded:
                persisted = _load_persisted_throttle(trw_dir)
                if persisted > 0.0:
                    _auto_close_last_ts = persisted
                _auto_close_persisted_loaded = True

            now_mono = time.monotonic()
            elapsed = now_mono - _auto_close_last_ts
            if _auto_close_last_ts > 0.0 and elapsed < _AUTO_CLOSE_MIN_INTERVAL_SECONDS:
                logger.debug(
                    "auto_close_stale_runs_throttled",
                    elapsed_seconds=elapsed,
                    interval_seconds=_AUTO_CLOSE_MIN_INTERVAL_SECONDS,
                )
                return {
                    "runs_closed": [],
                    "count": 0,
                    "errors": [],
                    "throttled": True,
                    "next_eligible_in_seconds": _AUTO_CLOSE_MIN_INTERVAL_SECONDS - elapsed,
                }
            _auto_close_last_ts = now_mono

    # Persist the just-recorded timestamp so future processes inherit the
    # window (PRD-FIX-082). Best-effort: failures are logged but never raise.
    _save_persisted_throttle(trw_dir)

    reader = FileStateReader()
    writer = FileStateWriter()

    if ttl_hours is not None:
        threshold_hours = ttl_hours
    elif age_days is not None:
        threshold_hours = age_days * 24
    else:
        threshold_hours = cfg.run_stale_ttl_hours

    runs_root = project_root / cfg.runs_root

    closed: list[str] = []
    errors: list[str] = []
    now = datetime.now(timezone.utc)

    if not runs_root.exists():
        return {"runs_closed": closed, "count": 0, "errors": errors}

    # Lazy import to avoid a cycle with state/_run_gc importing _paths.
    from trw_mcp.state._run_gc import _prefilter_status

    for task_dir in sorted(runs_root.iterdir()):
        if not task_dir.is_dir():
            continue
        for run_dir in sorted(task_dir.iterdir()):
            run_yaml = run_dir / "meta" / "run.yaml"
            if not run_yaml.exists():
                continue

            # Fast path: regex-prefilter status from the file header so
            # terminal runs short-circuit without invoking the YAML parser.
            # A few legacy run.yaml files have grown to multi-MB; full safe
            # parsing on those takes seconds, and they are typically already
            # terminal so the parse is wasted work.
            prefilter_status = _prefilter_status(run_yaml)
            if prefilter_status is not None and prefilter_status != "active":
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

    # Lazy import — see auto_close_stale_runs for rationale.
    from trw_mcp.state._run_gc import _prefilter_status

    for task_dir in sorted(runs_root.iterdir()):
        if not task_dir.is_dir():
            continue
        for run_dir in sorted(task_dir.iterdir()):
            run_yaml = run_dir / "meta" / "run.yaml"
            if not run_yaml.exists():
                continue
            prefilter_status = _prefilter_status(run_yaml)
            if prefilter_status is not None and prefilter_status != "active":
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
