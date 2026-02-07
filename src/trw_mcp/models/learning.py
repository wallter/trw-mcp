"""Learning models — LearningEntry, Reflection, Pattern, Script.

These models represent the self-learning layer stored in .trw/ directories.
They accumulate knowledge over time, enabling Claude Code to become
progressively more effective in a specific repository.
"""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class LearningStatus(str, Enum):
    """Status of a learning entry in its lifecycle.

    - active: Currently relevant and actionable.
    - resolved: The issue was fixed; kept for history but not promoted.
    - obsolete: No longer applicable; superseded or outdated.
    """

    ACTIVE = "active"
    RESOLVED = "resolved"
    OBSOLETE = "obsolete"


class LearningEntry(BaseModel):
    """Individual learning entry stored in .trw/learnings/entries/.

    Captured during reflection or manually via trw_learn.
    Impact scores drive CLAUDE.md promotion and pruning decisions.
    """

    model_config = ConfigDict(strict=True)

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
    """

    model_config = ConfigDict(strict=True)

    sessions_tracked: int = 0
    total_learnings: int = 0
    total_patterns: int = 0
    scripts_created: int = 0
    scripts_refined: int = 0
    avg_learnings_per_session: float = 0.0
    high_impact_learnings: int = 0
    low_impact_pruned: int = 0
    claude_md_syncs: int = 0
    top_tools_used: list[str] = Field(default_factory=list)
    common_error_patterns: list[str] = Field(default_factory=list)
