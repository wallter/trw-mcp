"""Quality dashboard aggregation — PRD-QUAL-031.

Computes ceremony trends, coverage trends, review verdict summaries,
degradation alerts, and sprint-over-sprint comparisons from session
event data. All functions handle missing fields gracefully.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import structlog

from trw_mcp.exceptions import StateError
from trw_mcp.models.config import get_config
from trw_mcp.models.typed_dicts import (
    CeremonyTrendResult,
    CoverageTrendResult,
    DegradationAlertResult,
    ReviewTrendResult,
)
from trw_mcp.state._helpers import safe_str
from trw_mcp.state.persistence import FileStateReader

logger = structlog.get_logger()


def _linear_slope(values: list[float]) -> float | None:
    """Compute slope via simple linear regression. Returns None if <3 points."""
    n = len(values)
    if n < 3:
        return None
    xs = list(range(n))
    sum_x = sum(xs)
    sum_y = sum(values)
    sum_xy = sum(x * y for x, y in zip(xs, values))
    sum_x2 = sum(x * x for x in xs)
    denom = n * sum_x2 - sum_x * sum_x
    if denom == 0:
        return 0.0
    return round((n * sum_xy - sum_x * sum_y) / denom, 4)


def compute_ceremony_trend(sessions: list[dict[str, object]]) -> CeremonyTrendResult:
    """Compute avg/min/max/slope/session_count/pass_rate for ceremony scores.

    Args:
        sessions: List of session dicts, each may have "ceremony_score".

    Returns:
        Dict with trend metrics. Missing ceremony_score sessions are skipped.
    """
    scores: list[float] = []
    for s in sessions:
        val = s.get("ceremony_score")
        if val is not None:
            try:
                scores.append(float(str(val)))
            except (ValueError, TypeError):
                continue

    if not scores:
        return {
            "avg": None,
            "min": None,
            "max": None,
            "slope": None,
            "session_count": 0,
            "pass_rate": None,
        }

    try:
        _threshold = float(get_config().ceremony_alert_threshold)
    except (ValueError, TypeError, AttributeError):
        _threshold = 60.0
    pass_count = sum(1 for sc in scores if sc >= _threshold)
    return {
        "avg": round(sum(scores) / len(scores), 2),
        "min": round(min(scores), 2),
        "max": round(max(scores), 2),
        "slope": _linear_slope(scores),
        "session_count": len(scores),
        "pass_rate": round(pass_count / len(scores), 4),
    }


def compute_coverage_trend(sessions: list[dict[str, object]]) -> CoverageTrendResult:
    """Compute avg/min/max/below_threshold_count for coverage.

    Args:
        sessions: List of session dicts, each may have "coverage_pct".

    Returns:
        Dict with coverage trend metrics.
    """
    values: list[float] = []
    for s in sessions:
        val = s.get("coverage_pct")
        if val is not None:
            try:
                values.append(float(str(val)))
            except (ValueError, TypeError):
                continue

    if not values:
        return {
            "avg": None,
            "min": None,
            "max": None,
            "below_threshold_count": 0,
            "session_count": 0,
        }

    try:
        _cov_min = get_config().build_check_coverage_min
    except (ValueError, TypeError, AttributeError):
        _cov_min = 85.0
    below = sum(1 for v in values if v < _cov_min)
    return {
        "avg": round(sum(values) / len(values), 2),
        "min": round(min(values), 2),
        "max": round(max(values), 2),
        "below_threshold_count": below,
        "session_count": len(values),
    }


def compute_review_trend(sessions: list[dict[str, object]]) -> ReviewTrendResult:
    """Count block/warn/pass verdicts from sessions.

    Args:
        sessions: List of session dicts, each may have "review_verdict".

    Returns:
        Dict with verdict counts.
    """
    counts: dict[str, int] = {"block": 0, "warn": 0, "pass": 0}
    for s in sessions:
        verdict = safe_str(s, "review_verdict").lower()
        if verdict in counts:
            counts[verdict] += 1
    return {
        "block": counts["block"],
        "warn": counts["warn"],
        "pass": counts["pass"],
        "total": sum(counts.values()),
    }


def detect_degradation(
    sessions: list[dict[str, object]],
    threshold: int = 40,
    consecutive: int = 3,
) -> list[DegradationAlertResult]:
    """Alert when ceremony_score < threshold for N consecutive sessions.

    Args:
        sessions: List of session dicts in chronological order.
        threshold: Score below which a session counts as degraded.
        consecutive: Number of consecutive degraded sessions to trigger alert.

    Returns:
        List of alert dicts with type, consecutive_sessions, threshold,
        first_occurrence, severity, and legacy start_index/end_index/scores.
    """
    if consecutive <= 0:
        return []

    alerts: list[DegradationAlertResult] = []
    streak: list[float] = []
    streak_start = 0

    def _make_alert(start: int, end: int, scores: list[float]) -> DegradationAlertResult:
        # Try to extract ISO timestamp from first session in streak
        first_session = sessions[start]
        ts_str = str(first_session.get("timestamp") or first_session.get("ts") or "")
        first_occurrence = ts_str if ts_str else datetime.now(timezone.utc).isoformat()
        length = len(scores)
        return {
            "type": "ceremony_degradation",
            "consecutive_sessions": length,
            "threshold": threshold,
            "first_occurrence": first_occurrence,
            "severity": "critical" if length >= consecutive * 2 else "warning",
            # Legacy fields for backward compat
            "start_index": start,
            "end_index": end,
            "scores": scores,
            "length": length,
        }

    for i, s in enumerate(sessions):
        val = s.get("ceremony_score")
        if val is None:
            # Reset streak on missing data
            if len(streak) >= consecutive:
                alerts.append(_make_alert(streak_start, i - 1, list(streak)))
            streak = []
            continue

        try:
            score = float(str(val))
        except (ValueError, TypeError):
            streak = []
            continue

        if score < threshold:
            if not streak:
                streak_start = i
            streak.append(score)
        else:
            if len(streak) >= consecutive:
                alerts.append(_make_alert(streak_start, i - 1, list(streak)))
            streak = []

    # Check final streak
    if len(streak) >= consecutive:
        alerts.append(_make_alert(streak_start, len(sessions) - 1, list(streak)))

    return alerts


def _sprint_id(session: dict[str, object]) -> str:
    """Extract sprint ID from a session dict."""
    task_name = safe_str(session, "task_name")
    if task_name:
        # Try to extract sprint-NN pattern
        lower = task_name.lower()
        for prefix in ("sprint-", "sprint_", "sprint "):
            idx = lower.find(prefix)
            if idx >= 0:
                rest = task_name[idx:].split()[0].split("/")[0]
                return rest
        return task_name
    return "untagged"


def compare_sprints(
    sessions: list[dict[str, object]],
    sprint_a: str,
    sprint_b: str,
) -> dict[str, object] | None:
    """Compare two sprints, return deltas.

    Args:
        sessions: List of session dicts.
        sprint_a: First sprint ID to compare.
        sprint_b: Second sprint ID to compare.

    Returns:
        Dict with deltas, or None if either sprint has no data.
    """
    a_sessions: list[dict[str, object]] = []
    b_sessions: list[dict[str, object]] = []

    for s in sessions:
        sid = _sprint_id(s)
        if sid == sprint_a:
            a_sessions.append(s)
        elif sid == sprint_b:
            b_sessions.append(s)

    if not a_sessions or not b_sessions:
        return None

    a_trend = compute_ceremony_trend(a_sessions)
    b_trend = compute_ceremony_trend(b_sessions)

    a_cov = compute_coverage_trend(a_sessions)
    b_cov = compute_coverage_trend(b_sessions)

    def _delta(va: object, vb: object) -> float | None:
        if va is None or vb is None:
            return None
        try:
            return round(float(str(vb)) - float(str(va)), 4)
        except (ValueError, TypeError):
            return None

    return {
        "sprint_a": sprint_a,
        "sprint_b": sprint_b,
        "ceremony_avg_delta": _delta(a_trend.get("avg"), b_trend.get("avg")),
        "ceremony_slope_a": a_trend.get("slope"),
        "ceremony_slope_b": b_trend.get("slope"),
        "coverage_avg_delta": _delta(a_cov.get("avg"), b_cov.get("avg")),
        "session_count_a": a_trend.get("session_count", 0),
        "session_count_b": b_trend.get("session_count", 0),
    }


def aggregate_dashboard(
    trw_dir: Path,
    window_days: int = 90,
    compare_sprint: str = "",
) -> dict[str, object]:
    """Main entry point — reads session events and aggregates all trends.

    Reads from .trw/context/analytics.yaml for aggregate counters and
    .trw/context/session-events.jsonl for per-session data.

    Args:
        trw_dir: Path to .trw directory.
        window_days: Number of days to include in the window.
        compare_sprint: Optional sprint ID to compare against previous.

    Returns:
        Dict with all dashboard data.
    """
    context_dir = trw_dir / "context"

    reader = FileStateReader()

    # Read aggregate counters from analytics.yaml
    analytics: dict[str, object] = {}
    analytics_path = context_dir / "analytics.yaml"
    if analytics_path.exists():
        try:
            analytics = reader.read_yaml(analytics_path)
        except (OSError, StateError):
            logger.debug("analytics_yaml_load_failed", path=str(analytics_path), exc_info=True)

    # Read session events from session-events.jsonl
    events_path = context_dir / "session-events.jsonl"
    raw_events: list[dict[str, object]] = []
    if events_path.exists():
        try:
            raw_events = reader.read_jsonl(events_path)
        except (OSError, StateError):
            logger.debug("session_events_load_failed", path=str(events_path), exc_info=True)

    # Filter to window and extract session-like events
    cutoff = datetime.now(timezone.utc).timestamp() - (window_days * 86400)
    sessions: list[dict[str, object]] = []
    legacy_skipped = 0

    for evt in raw_events:
        # Try to parse timestamp
        ts_str = safe_str(evt, "timestamp") or safe_str(evt, "ts")
        if ts_str:
            try:
                ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00")).timestamp()
                if ts < cutoff:
                    continue
            except (ValueError, TypeError):
                legacy_skipped += 1
                continue

        # Build a session-like dict from the event
        session: dict[str, object] = {}
        data = evt.get("data")
        source = data if isinstance(data, dict) else evt

        for field in (
            "ceremony_score", "coverage_pct", "review_verdict",
            "task_name", "phase", "tests_passed", "mypy_clean",
        ):
            if field in source:
                session[field] = source[field]
            elif field in evt:
                session[field] = evt[field]

        if session:
            sessions.append(session)

    ceremony = compute_ceremony_trend(sessions)
    coverage = compute_coverage_trend(sessions)
    review = compute_review_trend(sessions)

    try:
        cfg = get_config()
        alert_threshold = cfg.ceremony_alert_threshold
        alert_consecutive = cfg.ceremony_alert_consecutive
    except (ValueError, TypeError, AttributeError):
        alert_threshold = 40
        alert_consecutive = 3

    alerts = detect_degradation(sessions, alert_threshold, alert_consecutive)

    # Sprint comparison
    sprint_comparison: dict[str, object] | None = None
    if compare_sprint:
        # Find the "previous" sprint by looking at unique sprints
        sprint_ids: list[str] = []
        seen: set[str] = set()
        for s in sessions:
            sid = _sprint_id(s)
            if sid not in seen:
                seen.add(sid)
                sprint_ids.append(sid)
        if compare_sprint in sprint_ids:
            idx = sprint_ids.index(compare_sprint)
            if idx > 0:
                sprint_comparison = compare_sprints(
                    sessions, sprint_ids[idx - 1], compare_sprint,
                )

    return {
        "ceremony_trend": ceremony,
        "coverage_trend": coverage,
        "review_trend": review,
        "alerts": alerts,
        "sprint_comparison": sprint_comparison,
        "metadata": {
            "sessions_analyzed": len(sessions),
            "window_days": window_days,
            "analytics_counters": analytics,
        },
        "legacy_runs_skipped": legacy_skipped,
    }
