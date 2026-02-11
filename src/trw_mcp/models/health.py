"""Health diagnostic model — PRD-CORE-027.

Structured report for flywheel health assessment.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class HealthReport(BaseModel):
    """Flywheel health diagnostic report.

    Aggregates metrics across Q-learning, event stream, recall,
    ceremony compliance, and source attribution to produce a
    go/no-go recommendation.
    """

    model_config = ConfigDict(strict=True)

    # Q-learning activation metrics
    q_activations: int = Field(
        ge=0, default=0,
        description="Count of learnings with q_observations > 0.",
    )
    q_avg_observations: float = Field(
        ge=0.0, default=0.0,
        description="Average q_observations across entries with any.",
    )

    # Event stream health
    events_total: int = Field(ge=0, default=0)
    event_type_distribution: dict[str, int] = Field(default_factory=dict)

    # Recall health
    recall_receipts_count: int = Field(ge=0, default=0)

    # Access count distribution
    access_total: int = Field(ge=0, default=0)
    access_mean: float = Field(ge=0.0, default=0.0)
    entries_never_accessed: int = Field(ge=0, default=0)

    # Source attribution
    source_human: int = Field(ge=0, default=0)
    source_agent: int = Field(ge=0, default=0)
    source_unset: int = Field(ge=0, default=0)

    # Learning stats
    total_learnings: int = Field(ge=0, default=0)
    active_learnings: int = Field(ge=0, default=0)
    high_impact_learnings: int = Field(ge=0, default=0)

    # Ceremony indicators
    reflections_found: int = Field(ge=0, default=0)
    claude_md_syncs_found: int = Field(ge=0, default=0)

    # Overall assessment
    recommendation: str = Field(
        default="unknown",
        description="go, caution, or blocked.",
    )
    issues: list[str] = Field(default_factory=list)
