"""Learning models — LearningEntry, Reflection, Pattern, Script.

These models represent the self-learning layer stored in .trw/ directories.
They accumulate knowledge over time, enabling Claude Code to become
progressively more effective in a specific repository.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


def _utc_today() -> date:
    """Today's UTC date: the one calendar every learning writer stamps.

    ``date.today`` is the LOCAL date, so a learning created at 19:00 MT (01:00 UTC)
    was named for one day and, once updated, stamped with the next (E2E-INC-010).
    """
    return datetime.now(tz=timezone.utc).date()


# PRD-CORE-001, PRD-CORE-004: Learning entry models with utility scoring


class LearningStatus(str, Enum):
    """Status of a learning entry in its lifecycle.

    - active: Currently relevant and actionable.
    - resolved: The issue was fixed; kept for history but not promoted.
    - obsolete: No longer applicable; superseded or outdated.
    """

    ACTIVE = "active"
    RESOLVED = "resolved"
    OBSOLETE = "obsolete"


# PRD-CORE-110: Type classifications for learnings
class LearningType(str, Enum):
    """Type classification for learning entries (PRD-CORE-110)."""

    INCIDENT = "incident"
    PATTERN = "pattern"
    CONVENTION = "convention"
    HYPOTHESIS = "hypothesis"
    WORKAROUND = "workaround"
    DECISION = "decision"  # PRD-CORE-334 FR01, as trw_memory's MemoryType


class LearningConfidence(str, Enum):
    """Validation confidence level for learning entries (PRD-CORE-110)."""

    HYPOTHESIS = "hypothesis"
    UNVERIFIED = "unverified"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    VERIFIED = "verified"


class LearningProtectionTier(str, Enum):
    """Protection level for learning entries (PRD-CORE-110)."""

    CRITICAL = "critical"
    HIGH = "high"
    NORMAL = "normal"
    LOW = "low"
    PROTECTED = "protected"
    PERMANENT = "permanent"


class LearningEntry(BaseModel):
    """Individual learning entry stored in .trw/learnings/entries/.

    Captured during reflection or manually via trw_learn.
    Impact scores drive CLAUDE.md promotion and pruning decisions.
    """

    model_config = ConfigDict(strict=True, use_enum_values=True)

    id: str
    summary: str
    detail: str
    tags: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)
    impact: float = Field(ge=0.0, le=1.0, default=0.5)
    status: LearningStatus = LearningStatus.ACTIVE
    recurrence: int = Field(ge=0, default=1)
    created: date = Field(default_factory=_utc_today)
    updated: date = Field(default_factory=_utc_today)
    resolved_at: date | None = None
    promoted_to_claude_md: bool = False
    last_accessed_at: date | None = None
    access_count: int = Field(ge=0, default=0)
    outcome_history: list[str] = Field(default_factory=list)
    shard_id: str | None = None

    # PRD-CORE-110: Typed learning fields
    type: LearningType = Field(
        default=LearningType.PATTERN,
        description="Learning type classification.",
    )
    nudge_line: str = Field(
        default="",
        description="Compact nudge text for ceremony display (max 80 chars).",
    )
    expires: str = Field(
        default="",
        description="Expiration date/condition (ISO 8601 or free text).",
    )
    confidence: LearningConfidence = Field(
        default=LearningConfidence.UNVERIFIED,
        description="Validation confidence level.",
    )
    # PRD-CORE-312-FR01: plain str (not a re-declared enum) -- the YAML sidecar
    # mirrors the SQLite row for rollback safety, so it need not own a second
    # validated vocabulary; the SQLite write already coerces/rejects it.
    evidence_level: str = Field(
        default="unknown",
        description="Author-claimed evidence level (observed/verified/inferred/unknown).",
    )
    task_type: str = Field(
        default="",
        description="Task type identifier (e.g., 'bug-fix', 'feature').",
    )
    domain: list[str] = Field(
        default_factory=list,
        description="Domain tags (e.g., ['testing', 'security']).",
    )
    phase_origin: str = Field(
        default="",
        description="Framework phase when this learning was created.",
    )
    phase_affinity: list[str] = Field(
        default_factory=list,
        description="Phases where this learning is most relevant.",
    )
    team_origin: str = Field(
        default="",
        description="Team identifier that created this learning.",
    )
    protection_tier: LearningProtectionTier = Field(
        default=LearningProtectionTier.NORMAL,
        description="Protection level against pruning/archival.",
    )

    @field_validator("type", mode="before")
    @classmethod
    def _coerce_type(cls, v: object) -> LearningType:
        """Coerce string/enum values to LearningType, rejecting invalid values."""
        if isinstance(v, LearningType):
            return v
        if isinstance(v, str):
            if not v:  # Empty string -> default (backward compat)
                return LearningType.PATTERN
            try:
                return LearningType(v)
            except ValueError as err:
                raise ValueError(f"type must be one of {', '.join(t.value for t in LearningType)}") from err
        raise ValueError(f"type must be a string or LearningType, got {type(v).__name__}")

    @field_validator("confidence", mode="before")
    @classmethod
    def _coerce_confidence(cls, v: object) -> LearningConfidence:
        """Coerce string/enum values to LearningConfidence, rejecting invalid values."""
        if isinstance(v, LearningConfidence):
            return v
        if isinstance(v, str):
            if not v:  # Empty string -> default (backward compat)
                return LearningConfidence.UNVERIFIED
            try:
                return LearningConfidence(v)
            except ValueError as err:
                raise ValueError(f"confidence must be one of {', '.join(c.value for c in LearningConfidence)}") from err
        raise ValueError(f"confidence must be a string or LearningConfidence, got {type(v).__name__}")

    @field_validator("protection_tier", mode="before")
    @classmethod
    def _coerce_protection_tier(cls, v: object) -> LearningProtectionTier:
        """Coerce string/enum values to LearningProtectionTier, rejecting invalid values."""
        if isinstance(v, LearningProtectionTier):
            return v
        if isinstance(v, str):
            if not v:  # Empty string -> default (backward compat)
                return LearningProtectionTier.NORMAL
            try:
                return LearningProtectionTier(v)
            except ValueError as err:
                raise ValueError(
                    f"protection_tier must be one of {', '.join(p.value for p in LearningProtectionTier)}"
                ) from err
        raise ValueError(f"protection_tier must be a string or LearningProtectionTier, got {type(v).__name__}")

    @field_validator("nudge_line", mode="before")
    @classmethod
    def _truncate_nudge_line(cls, v: str) -> str:
        """Truncate nudge_line to max 80 chars, preferring word boundaries."""
        if not isinstance(v, str) or len(v) <= 80:
            return v if isinstance(v, str) else ""
        for i in range(60, 80):
            if v[i] == " ":
                return v[:i] + "\u2026"
        return v[:80]

    # PRD-CORE-026: Source attribution for human vs agent learnings
    source_type: Literal["human", "agent", "tool", "consolidated"] = Field(
        default="agent",
        description="Learning provenance: 'human', 'agent', 'tool', or 'consolidated'.",
    )
    source_identity: str = Field(
        default="",
        description="Name of the source (e.g., 'Tyler', 'claude-opus-4-7').",
    )

    # PRD-CORE-099: Client & model provenance auto-detection
    client_profile: str = Field(
        default="",
        description="IDE/client that created this entry (e.g., 'claude-code', 'opencode').",
    )
    model_id: str = Field(
        default="",
        description="AI model that created this entry (e.g., 'claude-opus-4-7').",
    )
    assertions: list[dict[str, object]] = Field(
        default_factory=list,
        description="Executable assertion metadata stored for rollback-safe YAML backup.",
    )

    # PRD-CORE-042: Dedup merge tracking
    merged_from: list[str] = Field(
        default_factory=list,
        description="IDs of learnings that were merged into this entry.",
    )

    # PRD-CORE-044: Consolidation tracking
    consolidated_from: list[str] = Field(
        default_factory=list,
        description="IDs of learnings consolidated into this entry (source entries).",
    )
    consolidated_into: str | None = None

    # PRD-CORE-111: Code-grounded anchors
    anchors: list[dict[str, object]] = Field(default_factory=list, description="Code symbol anchors")
    anchor_validity: float | None = Field(ge=0.0, le=1.0, default=None, description="Anchor validity score")

    # C5 FIX: Eval chain run attribution — identifies which run authored this
    # learning. Used by the eval/scoring consumer to distinguish self-authored
    # entries from tar-pipe-injected entries in chain evaluation runs.
    # Backward-compat: None for pre-stamping entries.
    source_run_id: str | None = Field(
        default=None,
        description="Run ID of the eval run that authored this entry (C5 fix).",
    )


class Pattern(BaseModel):
    """Discovered codebase pattern in .trw/patterns/.

    Patterns are recurring conventions or behaviors discovered
    through repeated observation. Confidence increases with evidence.
    """

    model_config = ConfigDict(strict=True)

    name: str
    domain: str
    description: str
    confidence: float = Field(ge=0.0, le=1.0, default=0.5)
    evidence: list[str] = Field(default_factory=list)
    first_seen: date = Field(default_factory=_utc_today)
    last_seen: date = Field(default_factory=_utc_today)
    occurrences: int = Field(ge=1, default=1)
