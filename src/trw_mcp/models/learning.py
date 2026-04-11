"""Learning models — LearningEntry, Reflection, Pattern, Script.

These models represent the self-learning layer stored in .trw/ directories.
They accumulate knowledge over time, enabling Claude Code to become
progressively more effective in a specific repository.
"""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

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
    created: date = Field(default_factory=date.today)
    updated: date = Field(default_factory=date.today)
    resolved_at: date | None = None
    promoted_to_claude_md: bool = False
    last_accessed_at: date | None = None
    access_count: int = Field(ge=0, default=0)
    q_value: float = Field(ge=0.0, le=1.0, default=0.5)
    q_observations: int = Field(ge=0, default=0)
    outcome_history: list[str] = Field(default_factory=list)
    shard_id: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _preseed_q_value(cls, data: dict[str, object]) -> dict[str, object]:
        """Pre-seed q_value from impact when creating a new entry.

        When q_value is not explicitly provided and q_observations is 0
        (a brand-new entry), compute an initial q_value that reflects the
        assessed impact rather than using the flat 0.5 default.  This gives
        high-impact learnings an immediate advantage in recall ranking.

        Only applies to dict input (not already-validated model instances).
        """
        if not isinstance(data, dict):
            return data
        # Only pre-seed when q_value was not explicitly provided
        if "q_value" in data:
            return data
        # Only pre-seed for new entries (no observations yet)
        q_obs_raw = data.get("q_observations", 0)
        q_obs: int = 0
        if isinstance(q_obs_raw, (int, float)):
            q_obs = int(q_obs_raw)
        elif isinstance(q_obs_raw, str):
            try:
                q_obs = int(q_obs_raw)
            except ValueError:
                q_obs = 0
        if q_obs > 0:
            return data
        # Compute pre-seeded q_value from impact
        impact = data.get("impact", 0.5)
        if isinstance(impact, (int, float)):
            from trw_mcp.scoring._correlation import compute_initial_q_value

            data["q_value"] = compute_initial_q_value(float(impact))
        return data

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
                raise ValueError(
                    f"type must be one of {', '.join(t.value for t in LearningType)}"
                ) from err
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
                raise ValueError(
                    f"confidence must be one of {', '.join(c.value for c in LearningConfidence)}"
                ) from err
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
        description="Name of the source (e.g., 'Tyler', 'claude-opus-4-6').",
    )

    # PRD-CORE-099: Client & model provenance auto-detection
    client_profile: str = Field(
        default="",
        description="IDE/client that created this entry (e.g., 'claude-code', 'opencode').",
    )
    model_id: str = Field(
        default="",
        description="AI model that created this entry (e.g., 'claude-opus-4-6').",
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

    # PRD-CORE-108: Causal outcome attribution fields
    outcome_correlation: str = Field(
        default="",
        description="Causal outcome attribution (e.g. 'positive', 'strong_positive').",
    )
    sessions_surfaced: int = Field(
        ge=0,
        default=0,
        description="Number of sessions this learning was surfaced in.",
    )
    avg_rework_delta: float | None = Field(
        default=None,
        description="Rolling average rework impact delta.",
    )

    # PRD-CORE-111: Code-grounded anchors
    anchors: list[dict[str, object]] = Field(default_factory=list, description="Code symbol anchors")
    anchor_validity: float = Field(ge=0.0, le=1.0, default=1.0, description="Anchor validity score")

    # PRD-FIX-052-FR02: Impact tier label (assigned during deliver tier sweep)
    impact_tier: Literal["critical", "high", "medium", "low", "?"] = "?"


class LearningIndex(BaseModel):
    """Index of all learning entries in .trw/learnings/index.yaml."""

    model_config = ConfigDict(strict=True)

    entries: list[LearningEntry] = Field(default_factory=list)
    total_count: int = 0
    last_pruned: date | None = None


class Reflection(BaseModel):
    """Post-run/session reflection log in .trw/reflections/.

    Captures what worked, what failed, what was repeated,
    and what was surprising during a work session.
    """

    model_config = ConfigDict(strict=True)

    id: str
    run_id: str | None = None
    scope: str = "session"
    timestamp: datetime
    events_analyzed: int = 0
    what_worked: list[str] = Field(default_factory=list)
    what_failed: list[str] = Field(default_factory=list)
    repeated_patterns: list[str] = Field(default_factory=list)
    surprises: list[str] = Field(default_factory=list)
    new_learnings: list[str] = Field(default_factory=list)
    patterns_updated: list[str] = Field(default_factory=list)
    scripts_refined: list[str] = Field(default_factory=list)


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
    first_seen: date = Field(default_factory=date.today)
    last_seen: date = Field(default_factory=date.today)
    occurrences: int = Field(ge=1, default=1)


class PatternIndex(BaseModel):
    """Index of all patterns in .trw/patterns/index.yaml."""

    model_config = ConfigDict(strict=True)

    patterns: list[Pattern] = Field(default_factory=list)


class Script(BaseModel):
    """Reusable script in .trw/scripts/.

    Scripts are saved, refined, and reused across sessions.
    Usage tracking identifies which scripts are most valuable.
    """

    model_config = ConfigDict(strict=True)

    name: str
    description: str
    filename: str
    language: str = "bash"
    usage_count: int = Field(ge=0, default=0)
    last_refined: date = Field(default_factory=date.today)
    created: date = Field(default_factory=date.today)


class ScriptIndex(BaseModel):
    """Index of all scripts in .trw/scripts/index.yaml."""

    model_config = ConfigDict(strict=True)

    scripts: list[Script] = Field(default_factory=list)


class ContextArchitecture(BaseModel):
    """Discovered architecture facts in .trw/context/architecture.yaml."""

    model_config = ConfigDict(strict=True)

    language: str = ""
    framework: str = ""
    build_system: str = ""
    test_framework: str = ""
    key_directories: dict[str, str] = Field(default_factory=dict)
    entry_points: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class ContextConventions(BaseModel):
    """Discovered coding conventions in .trw/context/conventions.yaml."""

    model_config = ConfigDict(strict=True)

    naming_style: str = ""
    import_style: str = ""
    error_handling: str = ""
    test_patterns: list[str] = Field(default_factory=list)
    commit_style: str = ""
    notes: list[str] = Field(default_factory=list)


class Analytics(BaseModel):
    """Self-analytics in .trw/context/analytics.yaml.

    Auto-updated by trw_reflect to track improvement over time.
    Zero-dependency feedback loop — no network required.

    PRD-QUAL-012-FR02/FR03/FR04: Revived dead fields, added Q-learning
    and reflection tracking.
    """

    model_config = ConfigDict(strict=True)

    sessions_tracked: int = 0
    total_learnings: int = 0
    avg_learnings_per_session: float = 0.0
    high_impact_learnings: int = 0
    claude_md_syncs: int = 0

    # PRD-QUAL-012-FR02: Previously dead — now populated by update_analytics_extended
    reflections_completed: int = 0
    total_outcomes: int = 0
    successful_outcomes: int = 0
    success_rate: float = 0.0

    # PRD-QUAL-012-FR03: Q-learning activation tracking
    q_learning_activations: int = 0
