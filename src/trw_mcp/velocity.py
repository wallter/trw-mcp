"""Velocity computation — pure functions for metrics, trends, and debt scanning.

PRD-CORE-015: Analogous to scoring.py for learning utility. All functions
are pure (no side effects) except compute_debt_indicators which scans files.

Statistical methods use only Python stdlib (math, statistics) — no numpy.
"""

from __future__ import annotations

import math
import re
from datetime import datetime, timezone
from pathlib import Path

from trw_mcp.models.velocity import (
    DebtIndicators,
    LearningSnapshot,
    OverheadMetrics,
    TrendResult,
    VelocityMetrics,
)

# Numerical stability epsilon for floating-point comparisons
_FLOAT_EPSILON: float = 1e-15

# Framework operation event types (meta-operations, not task output)
_FRAMEWORK_OPS: frozenset[str] = frozenset({
    "run_init",
    "trw_status",
    "checkpoint",
    "reflection_complete",
    "trw_reflect_complete",
    "claude_md_sync",
    "claude_md_synced",
    "trw_velocity",
    "framework_review",
})


def compute_run_velocity(
    events: list[dict[str, object]],
    wave_manifest: dict[str, object] | None = None,
) -> VelocityMetrics:
    """Compute velocity metrics for a single run from its events.

    Args:
        events: List of event dicts from events.jsonl.
        wave_manifest: Optional wave manifest data for completion rate.

    Returns:
        VelocityMetrics with computed fields.
    """
    if not events:
        return VelocityMetrics()

    # Parse timestamps
    timestamps: list[datetime] = []
    for ev in events:
        ts_str = str(ev.get("ts", ""))
        if ts_str:
            try:
                ts = datetime.fromisoformat(ts_str)
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                timestamps.append(ts)
            except ValueError:
                continue

    if not timestamps:
        return VelocityMetrics()

    # Total duration
    first_ts = min(timestamps)
    last_ts = max(timestamps)
    total_minutes = (last_ts - first_ts).total_seconds() / 60.0

    # Phase durations from phase_enter/phase_check events
    phase_durations = _compute_phase_durations(events)

    # Shard throughput
    shard_completes = sum(
        1 for ev in events
        if str(ev.get("event", "")) in ("shard_complete", "shard_completed")
    )
    total_hours = max(total_minutes / 60.0, 1.0 / 60.0)  # min 1 minute
    shard_throughput = round(shard_completes / total_hours, 4)

    # Completion rate from wave manifest
    completion_rate = 1.0
    if wave_manifest:
        waves = wave_manifest.get("waves", [])
        if isinstance(waves, list):
            total_shards = 0
            completed_shards = 0
            for w in waves:
                if isinstance(w, dict):
                    shard_list = w.get("shards", [])
                    if isinstance(shard_list, list):
                        total_shards += len(shard_list)
                    if str(w.get("status", "")) in ("complete", "partial"):
                        completed_shards += len(shard_list) if isinstance(shard_list, list) else 0
            if total_shards > 0:
                completion_rate = round(completed_shards / total_shards, 4)

    # Waves completed
    waves_completed = sum(
        1 for ev in events
        if str(ev.get("event", "")) == "wave_validated"
        and ev.get("valid") is True
    )

    # Learning reuse count (recall events)
    learning_reuse = sum(
        1 for ev in events
        if str(ev.get("event", "")) in ("recall_query", "trw_recall", "recall")
    )

    return VelocityMetrics(
        total_duration_minutes=round(total_minutes, 4),
        phase_durations=phase_durations,
        shard_throughput=shard_throughput,
        completion_rate=completion_rate,
        waves_completed=waves_completed,
        learning_reuse_count=learning_reuse,
    )


def _compute_phase_durations(
    events: list[dict[str, object]],
) -> dict[str, float]:
    """Compute per-phase durations from phase_enter/phase_check events.

    Args:
        events: List of event dicts.

    Returns:
        Dict mapping phase name to duration in minutes.
    """
    phase_enters: dict[str, datetime] = {}
    phase_durations: dict[str, float] = {}

    for ev in events:
        event_type = str(ev.get("event", ""))
        ts_str = str(ev.get("ts", ""))
        if not ts_str:
            continue
        try:
            ts = datetime.fromisoformat(ts_str)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
        except ValueError:
            continue

        if event_type == "phase_enter":
            phase_name = str(ev.get("phase", ""))
            if phase_name:
                phase_enters[phase_name] = ts

        elif event_type == "phase_check":
            phase_name = str(ev.get("phase", ""))
            if phase_name and phase_name in phase_enters:
                duration = (ts - phase_enters[phase_name]).total_seconds() / 60.0
                phase_durations[phase_name] = round(
                    phase_durations.get(phase_name, 0.0) + duration, 4,
                )

    return phase_durations


def compute_learning_effectiveness(
    entries_dir: Path,
    cold_start_threshold: int = 3,
    effective_q_threshold: float = 0.5,
) -> LearningSnapshot:
    """Compute learning effectiveness ratio from learning entries.

    Args:
        entries_dir: Path to .trw/learnings/entries/ directory.
        cold_start_threshold: Minimum q_observations to be "mature".
        effective_q_threshold: Q-value above which a learning is "effective".

    Returns:
        LearningSnapshot with effectiveness ratio.
    """
    from trw_mcp.exceptions import StateError
    from trw_mcp.state.persistence import FileStateReader

    reader = FileStateReader()
    active_count = 0
    mature_count = 0
    effective_count = 0
    q_sum = 0.0

    if not entries_dir.exists():
        return LearningSnapshot()

    for entry_file in entries_dir.iterdir():
        if entry_file.suffix != ".yaml":
            continue
        try:
            data = reader.read_yaml(entry_file)
        except (StateError, ValueError, TypeError, OSError):
            continue

        status = str(data.get("status", "active"))
        if status != "active":
            continue

        active_count += 1
        q_value = float(str(data.get("q_value", data.get("impact", 0.5))))
        q_obs = int(str(data.get("q_observations", 0)))
        q_sum += q_value

        if q_obs >= cold_start_threshold:
            mature_count += 1
            if q_value > effective_q_threshold:
                effective_count += 1

    effectiveness_ratio = 0.0
    if mature_count > 0:
        effectiveness_ratio = round(effective_count / mature_count, 4)

    mean_q = round(q_sum / max(active_count, 1), 4)

    return LearningSnapshot(
        active_count=active_count,
        mature_count=mature_count,
        effectiveness_ratio=effectiveness_ratio,
        mean_q_value=mean_q,
    )


def compute_debt_indicators(
    src_dir: Path,
    tests_dir: Path | None = None,
) -> DebtIndicators:
    """Scan source and test files for lightweight debt proxy metrics.

    Args:
        src_dir: Path to source directory (e.g. trw-mcp/src/).
        tests_dir: Optional path to tests directory.

    Returns:
        DebtIndicators with counts.
    """
    todo_count = 0
    noqa_count = 0
    type_ignore_count = 0
    skip_count = 0

    # Scan source files
    if src_dir.exists():
        for py_file in src_dir.rglob("*.py"):
            try:
                content = py_file.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            todo_count += len(re.findall(r"\b(?:TODO|FIXME)\b", content))
            noqa_count += len(re.findall(r"#\s*noqa", content))
            type_ignore_count += len(re.findall(r"#\s*type:\s*ignore", content))

    # Scan test files for skips
    if tests_dir and tests_dir.exists():
        for py_file in tests_dir.rglob("*.py"):
            try:
                content = py_file.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            skip_count += len(re.findall(
                r"@pytest\.mark\.skip|pytest\.skip\(",
                content,
            ))

    return DebtIndicators(
        todo_count=todo_count,
        test_skip_count=skip_count,
        lint_violation_estimate=noqa_count,
        mypy_ignore_count=type_ignore_count,
    )


def compute_overhead_ratio(
    events: list[dict[str, object]],
) -> OverheadMetrics:
    """Compute framework overhead ratio from events.

    Args:
        events: List of event dicts from events.jsonl.

    Returns:
        OverheadMetrics with ratio and counts.
    """
    total = len(events)
    if total == 0:
        return OverheadMetrics()

    framework_ops = sum(
        1 for ev in events
        if str(ev.get("event", "")) in _FRAMEWORK_OPS
    )

    ratio = round(framework_ops / total, 4)

    return OverheadMetrics(
        framework_overhead_ratio=ratio,
        framework_op_count=framework_ops,
        total_event_count=total,
    )


# --- Statistical trend analysis (numpy-free) ---


def linear_fit(
    x: list[float],
    y: list[float],
) -> tuple[float, float, float]:
    """Compute linear regression via normal equations.

    Args:
        x: Independent variable values.
        y: Dependent variable values.

    Returns:
        Tuple of (slope, intercept, r_squared).

    Raises:
        ValueError: If fewer than 2 data points or zero variance.
    """
    n = len(x)
    if n < 2 or len(y) != n:
        msg = f"Need >= 2 matching data points, got {n}"
        raise ValueError(msg)

    sum_x = sum(x)
    sum_y = sum(y)
    sum_xy = sum(xi * yi for xi, yi in zip(x, y))
    sum_x2 = sum(xi * xi for xi in x)

    denom = n * sum_x2 - sum_x * sum_x
    if abs(denom) < _FLOAT_EPSILON:
        msg = "Zero variance in x values"
        raise ValueError(msg)

    slope = (n * sum_xy - sum_x * sum_y) / denom
    intercept = (sum_y - slope * sum_x) / n

    # R-squared
    y_mean = sum_y / n
    ss_tot = sum((yi - y_mean) ** 2 for yi in y)
    ss_res = sum((yi - (slope * xi + intercept)) ** 2 for xi, yi in zip(x, y))

    r_squared = 1.0 - ss_res / max(ss_tot, _FLOAT_EPSILON)
    r_squared = max(0.0, min(1.0, r_squared))

    return round(slope, 6), round(intercept, 6), round(r_squared, 4)


def sign_test(
    values: list[float],
    alpha: float = 0.1,
) -> tuple[str, float]:
    """Acceleration sign test on first differences.

    Counts positive vs negative differences. Uses binomial test
    (exact) to determine if the trend is significantly non-random.

    Args:
        values: Time-ordered metric values (>= 5 required for 4 diffs).
        alpha: Significance level for two-tailed test (default 10%).

    Returns:
        Tuple of (direction, p_value) where direction is
        "accelerating", "decelerating", or "stable".

    Raises:
        ValueError: If fewer than 5 values.
    """
    if len(values) < 5:
        msg = f"Need >= 5 values for sign test, got {len(values)}"
        raise ValueError(msg)

    diffs = [values[i + 1] - values[i] for i in range(len(values) - 1)]
    positives = sum(1 for d in diffs if d > 0)
    negatives = sum(1 for d in diffs if d < 0)
    n = positives + negatives  # exclude zeros

    if n == 0:
        return "stable", 1.0

    # Binomial test: probability of observing >= k successes under p=0.5
    k = max(positives, negatives)
    p_value = _binomial_tail(n, k)

    if p_value < alpha:
        if positives > negatives:
            return "accelerating", round(p_value, 4)
        return "decelerating", round(p_value, 4)

    return "stable", round(p_value, 4)


def _binomial_tail(n: int, k: int) -> float:
    """Two-tailed binomial p-value for k or more extreme under p=0.5.

    Args:
        n: Total trials.
        k: Observed successes (the more extreme count).

    Returns:
        Two-tailed p-value.
    """
    # P(X >= k) under Binomial(n, 0.5)
    p_tail = 0.0
    for i in range(k, n + 1):
        p_tail += _binom_pmf(n, i, 0.5)
    return min(2.0 * p_tail, 1.0)  # Two-tailed


def _binom_pmf(n: int, k: int, p: float) -> float:
    """Binomial probability mass function.

    Args:
        n: Number of trials.
        k: Number of successes.
        p: Probability of success.

    Returns:
        P(X = k) for Binomial(n, p).
    """
    coeff = math.comb(n, k)
    return coeff * (p ** k) * ((1 - p) ** (n - k))


def detect_confounders(
    history: list[dict[str, object]],
    jump_ratio: float = 1.5,
) -> list[str]:
    """Detect confounding factors in velocity history.

    Args:
        history: List of velocity snapshot dicts.
        jump_ratio: Multiplier threshold for learning count jump detection.

    Returns:
        List of confounder description strings.
    """
    confounders: list[str] = []

    if len(history) < 2:
        return confounders

    # Task heterogeneity
    tasks = {str(h.get("task", "")) for h in history}
    if len(tasks) > 1:
        confounders.append(
            f"Task heterogeneity: {len(tasks)} different tasks in history"
        )

    # Framework version changes
    versions = [str(h.get("framework_version", "")) for h in history]
    for i in range(1, len(versions)):
        if versions[i] != versions[i - 1] and versions[i] and versions[i - 1]:
            confounders.append(
                f"Framework version changed: {versions[i - 1]} -> {versions[i]}"
            )
            break

    # Learning count jumps
    for i in range(1, len(history)):
        snap = history[i].get("learning_snapshot", {})
        prev_snap = history[i - 1].get("learning_snapshot", {})
        if isinstance(snap, dict) and isinstance(prev_snap, dict):
            curr_count = int(str(snap.get("active_count", 0)))
            prev_count = int(str(prev_snap.get("active_count", 0)))
            if prev_count > 0 and curr_count > prev_count * jump_ratio:
                confounders.append(
                    f"Learning count jumped >50%: {prev_count} -> {curr_count}"
                )
                break

    return confounders


def compute_trend(
    history: list[dict[str, object]],
    stable_threshold: float = 0.05,
    sign_test_alpha: float = 0.1,
    confounder_jump_ratio: float = 1.5,
) -> TrendResult:
    """Compute velocity trend from history.

    Args:
        history: List of velocity snapshot dicts (ordered by time).
        stable_threshold: Fraction of mean throughput below which
            slope is considered "stable".
        sign_test_alpha: Significance level for the acceleration sign test.
        confounder_jump_ratio: Multiplier threshold for learning count jumps.

    Returns:
        TrendResult with direction, fit parameters, and confounders.
    """
    n = len(history)
    if n < 3:
        return TrendResult(
            direction="insufficient_data",
            data_points=n,
        )

    # Extract shard_throughput values
    throughputs: list[float] = []
    for h in history:
        metrics = h.get("metrics", {})
        if isinstance(metrics, dict):
            throughputs.append(float(str(metrics.get("shard_throughput", 0.0))))
        else:
            throughputs.append(0.0)

    xf = [float(i) for i in range(n)]

    try:
        slope, intercept, r_squared = linear_fit(xf, throughputs)
    except ValueError:
        return TrendResult(
            direction="insufficient_data",
            data_points=n,
        )

    # Determine direction
    mean_throughput = sum(throughputs) / max(n, 1)
    threshold = stable_threshold * max(mean_throughput, 0.01)

    if abs(slope) < threshold:
        direction = "stable"
    elif slope > 0:
        direction = "improving"
    else:
        direction = "declining"

    # Acceleration sign test (>= 5 points)
    accel_direction: str | None = None
    accel_p: float | None = None
    if n >= 5:
        try:
            accel_direction, accel_p = sign_test(throughputs, alpha=sign_test_alpha)
        except ValueError:
            pass

    confounders = detect_confounders(history, jump_ratio=confounder_jump_ratio)

    return TrendResult(
        direction=direction,
        linear_slope=slope,
        linear_intercept=intercept,
        linear_r_squared=r_squared,
        acceleration_direction=accel_direction,
        acceleration_p_value=accel_p,
        confounders=confounders,
        data_points=n,
    )
