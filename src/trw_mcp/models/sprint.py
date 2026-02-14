"""Sprint document models — SprintTrack, FileOverlapEntry, SprintDoc.

Pydantic v2 models for parsed sprint planning documents. Used by
the sprint parser and sprint orchestration tools.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

# Shared model configuration for all sprint models
_SPRINT_MODEL_CONFIG = ConfigDict(strict=True)


class SprintTrack(BaseModel):
    """Single track within a sprint — a parallel workstream.

    Attributes:
        name: Track letter (A, B, C, etc.).
        title: Human-readable track title.
        prd_scope: PRD IDs governing this track.
        files: Files modified by this track.
        validation_criteria: Validation checklist items.
        dod_items: Definition of Done items for this track.
    """

    model_config = _SPRINT_MODEL_CONFIG

    name: str
    title: str = ""
    prd_scope: list[str] = Field(default_factory=list)
    files: list[str] = Field(default_factory=list)
    validation_criteria: list[str] = Field(default_factory=list)
    dod_items: list[str] = Field(default_factory=list)


class FileOverlapEntry(BaseModel):
    """Row from the File Overlap Matrix table in a sprint doc.

    Attributes:
        file_path: Path to the file.
        track_owners: Map of track letter to ownership indicator (WRITE, READ, etc.).
        has_conflict: True if multiple tracks modify this file.
    """

    model_config = _SPRINT_MODEL_CONFIG

    file_path: str
    track_owners: dict[str, str] = Field(default_factory=dict)
    has_conflict: bool = False


class SprintDoc(BaseModel):
    """Parsed sprint planning document.

    Attributes:
        sprint_number: Sprint number extracted from title.
        title: Sprint title.
        goal: Sprint goal statement.
        tracks: List of track definitions.
        file_overlap_matrix: File conflict analysis.
        merge_order: Merge order guidance.
        dod_items: Top-level Definition of Done items.
        source_path: Path to source markdown file.
    """

    model_config = _SPRINT_MODEL_CONFIG

    sprint_number: int
    title: str = ""
    goal: str = ""
    tracks: list[SprintTrack] = Field(default_factory=list)
    file_overlap_matrix: list[FileOverlapEntry] = Field(default_factory=list)
    merge_order: str = ""
    dod_items: list[str] = Field(default_factory=list)
    source_path: str = ""
