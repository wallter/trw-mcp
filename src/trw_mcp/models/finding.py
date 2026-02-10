"""Finding models — FindingEntry, FindingRef, FindingsIndex, FindingsRegistry.

These models represent structured research findings discovered during
framework execution. Part of the findings pipeline (PRD-CORE-010).
"""

from __future__ import annotations

from datetime import date
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class FindingSeverity(str, Enum):
    """Finding severity classification."""

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


class FindingStatus(str, Enum):
    """Finding lifecycle status."""

    OPEN = "open"
    ACKNOWLEDGED = "acknowledged"
    IN_PROGRESS = "in-progress"
    RESOLVED = "resolved"
    WONT_FIX = "wont-fix"


class FindingEntry(BaseModel):
    """Full finding entry — stored as per-run YAML.

    Each finding represents a discrete discovery from a research shard,
    with structured metadata for severity, component, and lifecycle tracking.
    """

    model_config = ConfigDict(use_enum_values=True, populate_by_name=True)

    id: str
    summary: str
    detail: str
    severity: FindingSeverity = FindingSeverity.MEDIUM
    status: FindingStatus = FindingStatus.OPEN
    component: str = ""
    tags: list[str] = Field(default_factory=list)
    source_shard: str = ""
    source_wave: int = 1
    run_id: str = ""
    target_prd: str | None = None
    prd_candidate: bool = False
    dedup_of: str | None = None
    created: date = Field(default_factory=date.today)
    updated: date = Field(default_factory=date.today)


class FindingRef(BaseModel):
    """Lightweight finding reference — stored in global registry.

    Contains only the fields needed for cross-run queries and index display.
    """

    model_config = ConfigDict(use_enum_values=True)

    id: str
    summary: str
    severity: FindingSeverity = FindingSeverity.MEDIUM
    status: FindingStatus = FindingStatus.OPEN
    run_id: str = ""
    target_prd: str | None = None


class FindingsIndex(BaseModel):
    """Per-run findings index — stored at {run_dir}/findings/index.yaml."""

    model_config = ConfigDict(use_enum_values=True)

    entries: list[FindingEntry] = Field(default_factory=list)
    total_count: int = 0
    last_updated: date = Field(default_factory=date.today)


class FindingsRegistry(BaseModel):
    """Global findings registry — stored at .trw/findings/registry.yaml."""

    model_config = ConfigDict(use_enum_values=True)

    entries: list[FindingRef] = Field(default_factory=list)
    total_count: int = 0
    runs_indexed: list[str] = Field(default_factory=list)
