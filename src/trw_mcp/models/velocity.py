"""Velocity instrumentation models — per-run metrics, cross-run history, trend analysis.

PRD-CORE-015: Pydantic v2 models for velocity computation, persistence,
and statistical trend fitting. All models use use_enum_values=True for
YAML round-trip compatibility.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class VelocityMetrics(BaseModel):
    """Per-run velocity metrics computed from events.jsonl."""

    model_config = ConfigDict(use_enum_values=True)

    total_duration_minutes: float = 0.0
    phase_durations: dict[str, float] = Field(default_factory=dict)
    shard_throughput: float = 0.0
    completion_rate: float = 0.0
    waves_completed: int = 0
    learning_reuse_count: int = 0


class LearningSnapshot(BaseModel):
    """Learning layer state at time of velocity computation."""

    model_config = ConfigDict(use_enum_values=True)

    active_count: int = 0
    mature_count: int = 0
    effectiveness_ratio: float = 0.0
    mean_q_value: float = 0.0


class DebtIndicators(BaseModel):
    """Lightweight technical debt proxy metrics."""

    model_config = ConfigDict(use_enum_values=True)

    todo_count: int = 0
    test_skip_count: int = 0
    lint_violation_estimate: int = 0
    mypy_ignore_count: int = 0


class OverheadMetrics(BaseModel):
    """Framework overhead tracking."""

    model_config = ConfigDict(use_enum_values=True)

    framework_overhead_ratio: float = 0.0
    framework_op_count: int = 0
    total_event_count: int = 0


class VelocitySnapshot(BaseModel):
    """Complete velocity snapshot for a single run."""

    model_config = ConfigDict(use_enum_values=True)

    run_id: str
    task: str
    timestamp: str  # ISO 8601 UTC string for YAML compatibility
    framework_version: str = "v18.0_TRW"
    metrics: VelocityMetrics = Field(default_factory=VelocityMetrics)
    learning_snapshot: LearningSnapshot = Field(default_factory=LearningSnapshot)
    debt_indicators: DebtIndicators = Field(default_factory=DebtIndicators)
    overhead: OverheadMetrics = Field(default_factory=OverheadMetrics)


class VelocityHistory(BaseModel):
    """Cross-run velocity history stored in velocity.yaml."""

    model_config = ConfigDict(use_enum_values=True)

    version: str = "1.0"
    history: list[VelocitySnapshot] = Field(default_factory=list)


class TrendResult(BaseModel):
    """Result of statistical trend analysis (not persisted)."""

    model_config = ConfigDict(use_enum_values=True)

    direction: str  # "improving" | "stable" | "declining" | "insufficient_data"
    linear_slope: float | None = None
    linear_intercept: float | None = None
    linear_r_squared: float | None = None
    acceleration_direction: str | None = None  # "accelerating" | "decelerating" | "stable"
    acceleration_p_value: float | None = None
    confounders: list[str] = Field(default_factory=list)
    data_points: int = 0


class VelocityAlert(BaseModel):
    """Velocity alert emitted by trw_phase_check."""

    model_config = ConfigDict(use_enum_values=True)

    alert_type: str = "negative_acceleration"
    trend_slope: float = 0.0
    trend_r_squared: float = 0.0
    message: str = ""
    severity: str = "warning"


class VelocitySummary(BaseModel):
    """Compact velocity summary for trw_status output."""

    model_config = ConfigDict(use_enum_values=True)

    last_run_throughput: float = 0.0
    trend_direction: str = "insufficient_data"
    trend_confidence: float | None = None
    runs_in_history: int = 0
