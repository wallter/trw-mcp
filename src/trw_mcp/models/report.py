"""Post-run analytics report models — PRD-CORE-030.

Structured report for post-run and mid-run analytics.
All fields are validated via Pydantic v2 for safe serialization.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class PhaseEntry(BaseModel):
    """Single phase in the run timeline."""

    model_config = ConfigDict(use_enum_values=True)

    phase: str
    entered_at: str
    exited_at: str | None = None
    duration_seconds: float | None = None


class EventSummary(BaseModel):
    """Aggregated event counts."""

    total_count: int = 0
    by_type: dict[str, int] = Field(default_factory=dict)


class LearningSummary(BaseModel):
    """Learning yield metrics for the run."""

    total_produced: int = 0
    avg_impact: float = 0.0
    high_impact_count: int = 0
    tags_used: list[str] = Field(default_factory=list)


class BuildSummary(BaseModel):
    """Build verification snapshot (from cached build-status.yaml)."""

    tests_passed: bool = False
    mypy_clean: bool = False
    coverage_pct: float = 0.0
    test_count: int = 0
    duration_secs: float = 0.0


class DurationInfo(BaseModel):
    """Run duration computed from first/last event timestamps."""

    start_ts: str | None = None
    end_ts: str | None = None
    elapsed_seconds: float | None = None


class RunReport(BaseModel):
    """Post-run analytics report — PRD-CORE-030-FR01.

    Assembled from run.yaml, events.jsonl, checkpoints.jsonl,
    build-status.yaml, and .trw/learnings/.
    """

    model_config = ConfigDict(use_enum_values=True)

    run_id: str
    task: str
    status: str
    phase: str
    framework: str = ""
    run_type: str = "implementation"
    generated_at: str
    prd_scope: list[str] = Field(default_factory=list)

    duration: DurationInfo = Field(default_factory=DurationInfo)
    phase_timeline: list[PhaseEntry] = Field(default_factory=list)
    event_summary: EventSummary = Field(default_factory=EventSummary)
    checkpoint_count: int = 0
    learning_summary: LearningSummary = Field(default_factory=LearningSummary)
    build: BuildSummary | None = None
    reversion_rate: float = 0.0
