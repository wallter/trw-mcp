"""Cross-run analytics — ceremony scoring, run scanning, aggregate metrics.

PRD-CORE-031-FR03/FR05: Pure functions that scan all run directories,
compute ceremony compliance scores, and assemble aggregate analytics.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import structlog

from trw_mcp.models.config import get_config
from trw_mcp.models.typed_dicts import (
    AggregateMetrics,
    AnalyticsReport,
    CeremonyScoreResult,
    CeremonyTrendItem,
    RunAnalysisResult,
    TierMetrics,
)
from trw_mcp.state._paths import resolve_project_root, resolve_trw_dir
from trw_mcp.exceptions import StateError
from trw_mcp.state.persistence import FileStateReader, FileStateWriter

logger = structlog.get_logger()


def __getattr__(name: str) -> object:
    """Backward-compat shim for removed module-level singletons (FIX-044)."""
    from trw_mcp.state._helpers import _compat_getattr

    return _compat_getattr(name)


# --- Ceremony Scoring (FR05) ---

_CEREMONY_WEIGHTS: dict[str, int] = {
    "session_start": 30,
    "deliver": 30,
    "checkpoint": 20,
    "learn": 10,
    "build_check": 10,
}


def compute_ceremony_score(
    events: list[dict[str, object]],
    trw_dir: Path | None = None,
) -> CeremonyScoreResult:
    """Compute ceremony compliance score (0-100) from events.

    Scoring model (additive):
    - session_start event present: 30 points
    - reflection_complete or claude_md_synced present: 30 points (deliver proxy)
    - checkpoint event count >= 1: 20 points
    - Any event with "learn" in type: 10 points
    - build_check_complete present: 10 points

    Args:
        events: List of event dicts from events.jsonl (run-level).
        trw_dir: Optional .trw directory path. When provided, also reads
            ``{trw_dir}/context/session-events.jsonl`` and merges those events
            with the run-level events before scoring. This is required because
            ``trw_session_start`` fires before ``trw_init`` creates the run
            directory, so the session_start event is written to the fallback
            session-events.jsonl path (FIX-051-FR01/FR05).

    Returns:
        Dict with score, per-component booleans, and counts.
    """
    # FIX-051-FR01/FR05: Merge session-level events from the fallback path.
    # Extracted to shared helper _merge_session_events in _deferred_delivery.py
    if trw_dir is not None:
        from trw_mcp.tools._deferred_delivery import _merge_session_events
        events = _merge_session_events(list(events), trw_dir)
    else:
        events = list(events)
    has_session_start = False
    has_deliver = False
    checkpoint_count = 0
    learn_count = 0
    has_build_check = False
    build_passed: bool | None = None

    for evt in events:
        event_type = str(evt.get("event", ""))
        tool_name = str(evt.get("tool_name", ""))
        is_tool_invocation = event_type == "tool_invocation"

        if event_type == "session_start" or (
            is_tool_invocation and tool_name == "trw_session_start"
        ):
            has_session_start = True
        elif event_type in ("reflection_complete", "claude_md_synced", "trw_deliver_complete") or (
            is_tool_invocation and tool_name in ("trw_deliver", "trw_reflect")
        ):
            has_deliver = True
        elif event_type == "checkpoint" or (
            is_tool_invocation and tool_name == "trw_checkpoint"
        ):
            checkpoint_count += 1
        elif "learn" in event_type or (
            is_tool_invocation and tool_name == "trw_learn"
        ):
            learn_count += 1
        elif event_type == "build_check_complete" or (
            is_tool_invocation and tool_name == "trw_build_check"
        ):
            has_build_check = True
            if "tests_passed" in evt:
                build_passed = str(evt["tests_passed"]).lower() == "true"

    score = 0
    if has_session_start:
        score += _CEREMONY_WEIGHTS["session_start"]
    if has_deliver:
        score += _CEREMONY_WEIGHTS["deliver"]
    if checkpoint_count >= 1:
        score += _CEREMONY_WEIGHTS["checkpoint"]
    if learn_count >= 1:
        score += _CEREMONY_WEIGHTS["learn"]
    if has_build_check:
        score += _CEREMONY_WEIGHTS["build_check"]

    result: CeremonyScoreResult = {
        "score": score,
        "session_start": has_session_start,
        "deliver": has_deliver,
        "checkpoint_count": checkpoint_count,
        "learn_count": learn_count,
        "build_check": has_build_check,
        "build_passed": build_passed,
    }
    return result


# --- Run Scanning (FR03) ---


def scan_all_runs(
    since: str | None = None,
) -> AnalyticsReport:
    """Scan all run directories and compute per-run and aggregate metrics.

    Args:
        since: Optional ISO date string filter (YYYY-MM-DD or full ISO).
            Only runs with started_at >= since are included.

    Returns:
        Structured dict with runs, aggregate, generated_at, runs_scanned,
        and parse_errors.
    """
    config = get_config()
    writer = FileStateWriter()

    project_root = resolve_project_root()
    task_root = project_root / config.task_root
    parse_errors: list[str] = []
    runs: list[RunAnalysisResult] = []

    if not task_root.exists():
        return _empty_report(parse_errors)

    # Scan all run directories
    for task_dir in sorted(task_root.iterdir()):
        runs_dir = task_dir / "runs"
        if not runs_dir.is_dir():
            continue
        for run_dir in sorted(runs_dir.iterdir()):
            try:
                run_data = _analyze_single_run(run_dir, trw_dir=resolve_trw_dir())
                if run_data is not None:
                    runs.append(run_data)
            except Exception as exc:  # justified: scan-resilience, skip bad run dirs without aborting report
                parse_errors.append(f"{run_dir.name}: {exc}")

    # Apply since filter (validate ISO date format)
    if since:
        try:
            datetime.fromisoformat(since)
        except ValueError:
            logger.debug("analytics_since_invalid_format", since=since)
            parse_errors.append(f"since filter '{since}' is not a valid ISO date")
        runs = [r for r in runs if str(r.get("started_at", "")) >= since]

    # Sort by started_at ascending
    runs.sort(key=lambda r: str(r.get("started_at", "")))

    # Compute aggregates
    aggregate = _compute_aggregates(runs)

    generated_at = datetime.now(timezone.utc).isoformat()
    report: AnalyticsReport = {
        "runs": runs,
        "aggregate": aggregate,
        "generated_at": generated_at,
        "runs_scanned": len(runs),
        "parse_errors": parse_errors,
    }

    # Cache to analytics-report.yaml (best-effort)
    try:
        trw_dir = resolve_trw_dir()
        cache_dir = trw_dir / config.context_dir
        writer.ensure_dir(cache_dir)
        cache_path = cache_dir / "analytics-report.yaml"
        writer.write_yaml(cache_path, dict(report))
    except Exception:  # justified: fail-open, cache write is best-effort optimization
        logger.warning("analytics_report_cache_write_failed", exc_info=True)

    return report


def _analyze_single_run(run_dir: Path, trw_dir: Path | None = None) -> RunAnalysisResult | None:
    """Analyze a single run directory and return per-run metrics.

    Returns None if run.yaml doesn't exist or cannot be read.
    """
    reader = FileStateReader()

    run_yaml = run_dir / "meta" / "run.yaml"
    if not run_yaml.exists():
        return None

    try:
        state_data = reader.read_yaml(run_yaml)
    except (OSError, StateError):
        return None

    run_id = str(state_data.get("run_id", run_dir.name))
    started_at = _parse_run_id_timestamp(run_id)

    # Read events
    events_path = run_dir / "meta" / "events.jsonl"
    events: list[dict[str, object]] = []
    if events_path.exists():
        try:
            events = reader.read_jsonl(events_path)
        except Exception:  # justified: scan-resilience, corrupt events file should not block run analysis
            logger.warning("events_read_failed", path=str(events_path), exc_info=True)

    # Compute ceremony score
    score: int | None
    session_start_flag: bool
    deliver_flag: bool
    checkpoint_count: int
    learn_count: int
    build_check_flag: bool
    build_passed: bool | None
    try:
        ceremony = compute_ceremony_score(events, trw_dir=trw_dir)
        score = ceremony["score"]
        session_start_flag = ceremony["session_start"]
        deliver_flag = ceremony["deliver"]
        checkpoint_count = ceremony["checkpoint_count"]
        learn_count = ceremony["learn_count"]
        build_check_flag = ceremony["build_check"]
        build_passed = ceremony["build_passed"]
    except Exception:  # justified: fail-open, ceremony score computation must not block analytics
        score = None
        session_start_flag = False
        deliver_flag = False
        checkpoint_count = 0
        learn_count = 0
        build_check_flag = False
        build_passed = None

    result: RunAnalysisResult = {
        "run_id": run_id,
        "started_at": started_at,
        "task": str(state_data.get("task", "")),
        "status": str(state_data.get("status", "")),
        "phase": str(state_data.get("phase", "")),
        "score": score,
        "session_start": session_start_flag,
        "deliver": deliver_flag,
        "checkpoint_count": checkpoint_count,
        "learn_count": learn_count,
        "build_check": build_check_flag,
        "build_passed": build_passed,
    }
    # PRD-CORE-060-FR07: Include complexity_class for tier-based aggregation
    cc = state_data.get("complexity_class")
    if cc is not None:
        result["complexity_class"] = str(cc)
    return result


def _parse_run_id_timestamp(run_id: str) -> str:
    """Parse ISO timestamp from a run_id like '20260220T120000Z-abcd1234'.

    Returns:
        ISO format string, or the raw run_id if parsing fails.
    """
    try:
        ts_part = run_id.split("-")[0]
        if len(ts_part) >= 16 and "T" in ts_part:
            dt = datetime.strptime(ts_part, "%Y%m%dT%H%M%SZ")
            return dt.replace(tzinfo=timezone.utc).isoformat()
    except (ValueError, IndexError):
        logger.debug("run_id_timestamp_parse_failed", run_id=run_id, exc_info=True)
    return run_id


def _compute_aggregates(runs: list[RunAnalysisResult]) -> AggregateMetrics:
    """Compute aggregate metrics from per-run data."""
    if not runs:
        return AggregateMetrics(
            total_runs=0,
            avg_ceremony_score=0.0,
            build_pass_rate=0.0,
            avg_learnings_per_run=0.0,
            ceremony_trend=[],
            ceremony_by_tier={},
        )

    scores: list[int] = []
    build_results: list[bool] = []
    total_learnings = 0
    ceremony_trend: list[CeremonyTrendItem] = []

    # PRD-CORE-060-FR07: Tier-grouped ceremony scores
    tier_scores: dict[str, list[int]] = {}

    for run in runs:
        score = run.get("score")
        if isinstance(score, int):
            scores.append(score)
            ceremony_trend.append(CeremonyTrendItem(
                run_id=str(run.get("run_id", "")),
                score=score,
                started_at=str(run.get("started_at", "")),
            ))

            # FR07: Group by complexity_class
            tier = str(run.get("complexity_class") or "unclassified")
            tier_scores.setdefault(tier, []).append(score)

        bp = run.get("build_passed")
        if bp is not None:
            build_results.append(bool(bp))

        learn_count = run.get("learn_count")
        if isinstance(learn_count, int):
            total_learnings += learn_count

    avg_score = sum(scores) / len(scores) if scores else 0.0
    build_pass_rate = (
        sum(1 for b in build_results if b) / len(build_results)
        if build_results else 0.0
    )
    avg_learnings = total_learnings / len(runs) if runs else 0.0

    # FR07: Build ceremony_by_tier breakdown
    ceremony_by_tier: dict[str, TierMetrics] = {}
    for tier_name, tier_score_list in tier_scores.items():
        count = len(tier_score_list)
        avg = round(sum(tier_score_list) / count, 1) if count else 0.0
        pass_rate = round(
            sum(1 for s in tier_score_list if s >= 70) / count, 2,
        ) if count else 0.0
        ceremony_by_tier[tier_name] = TierMetrics(
            count=count,
            avg_score=avg,
            pass_rate=pass_rate,
        )

    return AggregateMetrics(
        total_runs=len(runs),
        avg_ceremony_score=round(avg_score, 2),
        build_pass_rate=round(build_pass_rate, 4),
        avg_learnings_per_run=round(avg_learnings, 2),
        ceremony_trend=ceremony_trend,
        ceremony_by_tier=ceremony_by_tier,
    )


# --- Stale Run Auto-Close (PRD-FIX-028) ---


def _get_last_activity_timestamp(run_dir: Path) -> datetime | None:
    """Get the most recent checkpoint timestamp from a run directory.

    Returns None if no checkpoints exist.
    """
    reader = FileStateReader()

    cp_path = run_dir / "meta" / "checkpoints.jsonl"
    if not cp_path.exists():
        return None

    try:
        records = reader.read_jsonl(cp_path)
    except (OSError, StateError):
        return None

    if not records:
        return None

    latest: datetime | None = None
    for record in records:
        ts_str = str(record.get("ts", ""))
        if ts_str:
            try:
                ts = datetime.fromisoformat(ts_str)
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                if latest is None or ts > latest:
                    latest = ts
            except ValueError:
                continue
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
    started_at = _parse_run_id_timestamp(run_id)

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

    Considers checkpoint timestamps: the most recent checkpoint resets the
    staleness clock.
    """
    run_id = str(run_data.get("run_id", run_dir.name))
    started_at = _parse_run_id_timestamp(run_id)
    try:
        run_dt = datetime.fromisoformat(started_at)
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
    cfg = get_config()
    reader = FileStateReader()
    writer = FileStateWriter()

    if ttl_hours is not None:
        threshold_hours = ttl_hours
    elif age_days is not None:
        threshold_hours = age_days * 24
    else:
        threshold_hours = cfg.run_stale_ttl_hours

    project_root = resolve_project_root()
    task_root = project_root / cfg.task_root

    closed: list[str] = []
    errors: list[str] = []
    now = datetime.now(timezone.utc)

    if not task_root.exists():
        return {"runs_closed": closed, "count": 0, "errors": errors}

    for task_dir in sorted(task_root.iterdir()):
        runs_dir = task_dir / "runs"
        if not runs_dir.is_dir():
            continue
        for run_dir in sorted(runs_dir.iterdir()):
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
                data["abandoned_reason"] = (
                    f"Stale timeout — exceeded threshold: {threshold_hours}h"
                )
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
    cfg = get_config()
    reader = FileStateReader()
    threshold_hours = ttl_hours if ttl_hours is not None else cfg.run_stale_ttl_hours
    project_root = resolve_project_root()
    task_root = project_root / cfg.task_root
    now = datetime.now(timezone.utc)

    count = 0
    if not task_root.exists():
        return 0

    for task_dir in sorted(task_root.iterdir()):
        runs_dir = task_dir / "runs"
        if not runs_dir.is_dir():
            continue
        for run_dir in sorted(runs_dir.iterdir()):
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


def _empty_report(parse_errors: list[str]) -> AnalyticsReport:
    """Return an empty analytics report."""
    return AnalyticsReport(
        runs=[],
        aggregate=AggregateMetrics(
            total_runs=0,
            avg_ceremony_score=0.0,
            build_pass_rate=0.0,
            avg_learnings_per_run=0.0,
            ceremony_trend=[],
            ceremony_by_tier={},
        ),
        generated_at=datetime.now(timezone.utc).isoformat(),
        runs_scanned=0,
        parse_errors=parse_errors,
    )
