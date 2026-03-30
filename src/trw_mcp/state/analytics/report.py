"""Cross-run analytics — ceremony scoring, run scanning, aggregate metrics.

PRD-CORE-031-FR03/FR05: Pure functions that scan all run directories,
compute ceremony compliance scores, and assemble aggregate analytics.

Stale-run lifecycle helpers are extracted to ``_stale_runs.py`` for
module-size compliance; all public names are re-exported here so
existing import paths are preserved.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import structlog

from trw_mcp.exceptions import StateError
from trw_mcp.models.config import get_config as get_config
from trw_mcp.models.config._client_profile import CeremonyWeights
from trw_mcp.models.typed_dicts import (
    AggregateMetrics,
    AnalyticsReport,
    CeremonyScoreResult,
    CeremonyTrendItem,
    RunAnalysisResult,
    TierMetrics,
)
from trw_mcp.state._paths import resolve_project_root as resolve_project_root
from trw_mcp.state._paths import resolve_trw_dir
from trw_mcp.state.persistence import FileStateReader, FileStateWriter

logger = structlog.get_logger(__name__)


def __getattr__(name: str) -> object:
    """Backward-compat shim for removed module-level singletons (FIX-044)
    and lazy re-exports from _stale_runs sub-module.
    """
    # Stale-run re-exports (avoid circular import with _stale_runs)
    _STALE_REEXPORTS = {
        "auto_close_stale_runs",
        "count_stale_runs",
        "_get_last_activity_timestamp",
        "_write_archive_summary",
        "_is_run_stale",
    }
    if name in _STALE_REEXPORTS:
        from trw_mcp.state.analytics import _stale_runs

        value = getattr(_stale_runs, name)
        # Cache on module dict for subsequent fast access
        globals()[name] = value
        return value

    # Legacy backward-compat shim
    from trw_mcp.state._helpers import _compat_getattr

    return _compat_getattr(name)


# --- Ceremony Scoring (FR05) ---

_CEREMONY_WEIGHTS: dict[str, int] = CeremonyWeights().as_dict()


def _classify_event(
    event_type: str,
    tool_name: str,
    is_tool_invocation: bool,
) -> tuple[bool, bool, bool, bool, bool, bool, bool | None]:
    """Classify a single event and extract ceremony flags.

    Returns tuple of: (has_session_start, has_deliver, has_checkpoint, has_learn,
                       has_build_check, has_review, build_passed).
    """
    has_session_start = event_type == "session_start" or (is_tool_invocation and tool_name == "trw_session_start")
    has_deliver = event_type in ("reflection_complete", "claude_md_synced", "trw_deliver_complete") or (
        is_tool_invocation and tool_name in ("trw_deliver", "trw_reflect")
    )
    has_checkpoint = event_type == "checkpoint" or (is_tool_invocation and tool_name == "trw_checkpoint")
    has_learn = "learn" in event_type or (is_tool_invocation and tool_name == "trw_learn")
    has_build_check = event_type == "build_check_complete" or (is_tool_invocation and tool_name == "trw_build_check")
    has_review = event_type in ("review_complete", "spec_reconciliation") or (
        is_tool_invocation and tool_name == "trw_review"
    )
    build_passed: bool | None = None
    return has_session_start, has_deliver, has_checkpoint, has_learn, has_build_check, has_review, build_passed


def _accumulate_event_counts(
    events: list[dict[str, object]],
) -> tuple[bool, bool, int, int, bool, bool, bool | None]:
    """Scan events and accumulate ceremony counts.

    Returns: (has_session_start, has_deliver, checkpoint_count, learn_count,
              has_build_check, has_review, build_passed).
    """
    has_session_start = False
    has_deliver = False
    checkpoint_count = 0
    learn_count = 0
    has_build_check = False
    has_review = False
    build_passed: bool | None = None

    for evt in events:
        event_type = str(evt.get("event", ""))
        tool_name = str(evt.get("tool_name", ""))
        is_tool_invocation = event_type == "tool_invocation"

        ss, dl, cp, ln, bc, rv, _bp = _classify_event(event_type, tool_name, is_tool_invocation)

        if ss:
            has_session_start = True
        if dl:
            has_deliver = True
        if cp:
            checkpoint_count += 1
        if ln:
            learn_count += 1
        if bc:
            has_build_check = True
            if "tests_passed" in evt:
                build_passed = str(evt["tests_passed"]).lower() == "true"
        if rv:
            has_review = True

    return has_session_start, has_deliver, checkpoint_count, learn_count, has_build_check, has_review, build_passed


def compute_ceremony_score(
    events: list[dict[str, object]],
    trw_dir: Path | None = None,
    weights: CeremonyWeights | None = None,
) -> CeremonyScoreResult:
    """Compute ceremony compliance score (0-100) from events.

    Weight semantics are binary: the weight is the full value awarded when
    the corresponding ceremony step is present (boolean gate). A weight of 0
    effectively disables scoring for that step.

    Scoring model (additive, per-component weight when present):
    - session_start event present: weights["session_start"] points
    - reflection_complete or claude_md_synced present: weights["deliver"] points
    - checkpoint event count >= 1: weights["checkpoint"] points
    - Any event with "learn" in type: weights["learn"] points
    - build_check_complete present: weights["build_check"] points
    - review event present: weights["review"] points

    Args:
        events: List of event dicts from events.jsonl (run-level).
        trw_dir: Optional .trw directory path. When provided, also reads
            ``{trw_dir}/context/session-events.jsonl`` and merges those events
            with the run-level events before scoring. This is required because
            ``trw_session_start`` fires before ``trw_init`` creates the run
            directory, so the session_start event is written to the fallback
            session-events.jsonl path (FIX-051-FR01/FR05).
        weights: Optional CeremonyWeights override. When None, uses the
            module-level ``_CEREMONY_WEIGHTS`` defaults (backward compatible).

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

    has_session_start, has_deliver, checkpoint_count, learn_count, has_build_check, has_review, build_passed = (
        _accumulate_event_counts(events)
    )

    w = weights.as_dict() if weights is not None else _CEREMONY_WEIGHTS

    score = 0
    if has_session_start:
        score += w["session_start"]
    if has_deliver:
        score += w["deliver"]
    if checkpoint_count >= 1:
        score += w["checkpoint"]
    if learn_count >= 1:
        score += w["learn"]
    if has_build_check:
        score += w["build_check"]
    if has_review:
        score += w["review"]

    result: CeremonyScoreResult = {
        "score": score,
        "session_start": has_session_start,
        "deliver": has_deliver,
        "checkpoint_count": checkpoint_count,
        "learn_count": learn_count,
        "build_check": has_build_check,
        "build_passed": build_passed,
        "review": has_review,
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
    runs_root = project_root / config.runs_root
    parse_errors: list[str] = []
    runs: list[RunAnalysisResult] = []

    if not runs_root.exists():
        return _empty_report(parse_errors)

    # Scan all run directories
    for task_dir in sorted(runs_root.iterdir()):
        if not task_dir.is_dir():
            continue
        for run_dir in sorted(task_dir.iterdir()):
            try:
                run_data = _analyze_single_run(run_dir, trw_dir=resolve_trw_dir())
                if run_data is not None:
                    runs.append(run_data)
            except Exception as exc:  # per-item error handling: skip bad run dirs without aborting report  # noqa: PERF203
                parse_errors.append(f"{run_dir.name}: {exc}")

    # Apply since filter (validate ISO date format)
    if since:
        try:
            datetime.fromisoformat(since.replace("Z", "+00:00"))
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
        profile_weights = get_config().client_profile.ceremony_weights
        ceremony = compute_ceremony_score(events, trw_dir=trw_dir, weights=profile_weights)
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
            dt = datetime.strptime(ts_part, "%Y%m%dT%H%M%S%z").replace(tzinfo=timezone.utc)
            return dt.isoformat()
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
            ceremony_trend.append(
                CeremonyTrendItem(
                    run_id=str(run.get("run_id", "")),
                    score=score,
                    started_at=str(run.get("started_at", "")),
                )
            )

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
    build_pass_rate = sum(1 for b in build_results if b) / len(build_results) if build_results else 0.0
    avg_learnings = total_learnings / len(runs) if runs else 0.0

    # FR07: Build ceremony_by_tier breakdown
    ceremony_by_tier: dict[str, TierMetrics] = {}
    for tier_name, tier_score_list in tier_scores.items():
        count = len(tier_score_list)
        avg = round(sum(tier_score_list) / count, 1) if count else 0.0
        pass_rate = (
            round(
                sum(1 for s in tier_score_list if s >= 70) / count,
                2,
            )
            if count
            else 0.0
        )
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
