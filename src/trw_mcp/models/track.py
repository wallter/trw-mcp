"""Track management models — Track, TrackRegistry, FileConflict.

These models represent concurrent sprint track state for parallel
workstream coordination. Part of PRD-CORE-003.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class TrackStatus(str, Enum):
    """Sprint track lifecycle status."""

    ACTIVE = "active"
    COMPLETE = "complete"
    PAUSED = "paused"


class ConflictSeverity(str, Enum):
    """File conflict severity classification."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class Track(BaseModel):
    """Single sprint track — a parallel workstream with PRD scope and file set."""

    model_config = ConfigDict(use_enum_values=True, populate_by_name=True)

    name: str
    sprint: str
    prd_scope: list[str] = Field(default_factory=list)
    files: list[str] = Field(default_factory=list)
    run_path: str | None = None
    status: TrackStatus = TrackStatus.ACTIVE
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
    )


class TrackRegistry(BaseModel):
    """Sprint track registry — all tracks for a sprint.

    Persisted to ``.trw/tracks/{sprint_id}.yaml``.
    """

    model_config = ConfigDict(use_enum_values=True)

    sprint_id: str
    tracks: list[Track] = Field(default_factory=list)


class FileConflict(BaseModel):
    """File-level conflict between concurrent tracks."""

    model_config = ConfigDict(use_enum_values=True)

    file_path: str
    tracks: list[str] = Field(default_factory=list)
    severity: ConflictSeverity = ConflictSeverity.MEDIUM
    reason: str = ""


class MergeRecommendation(BaseModel):
    """Merge ordering recommendation for a track."""

    model_config = ConfigDict(use_enum_values=True)

    track_name: str
    order: int
    conflict_count: int = 0
    rationale: str = ""
