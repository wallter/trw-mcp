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


class DurationInfo(BaseModel):
    """Run duration computed from first/last event timestamps."""

    start_ts: str | None = None
    end_ts: str | None = None
    elapsed_seconds: float | None = None
