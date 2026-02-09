"""Framework configuration — single source of truth for all TRW defaults.

All configuration values are centralized here. Both application code
and test suites import from this module — no parallel constants.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict
from pydantic_settings import BaseSettings, SettingsConfigDict


class TRWConfig(BaseSettings):
    """Single source of truth for all TRW MCP server configuration.

    Values come from (in priority order):
    1. Environment variables (prefixed TRW_)
    2. .trw/config.yaml overrides (loaded at runtime)
    3. Defaults defined here (from FRAMEWORK.md §DEFAULTS)
    """

    model_config = SettingsConfigDict(
        env_prefix="TRW_",
        case_sensitive=False,
        extra="ignore",
    )

    # Orchestration defaults (from FRAMEWORK.md §DEFAULTS)
    parallelism_max: int = 10
    min_shards_target: int = 3
    min_shards_floor: int = 2
    consensus_quorum: float = 0.67
    correlation_min: float = 0.7
    checkpoint_secs: int = 600
    timebox_hours: int = 8
    max_child_depth: int = 2
    max_research_waves: int = 3

    # Phase time caps (percentage of total timebox)
    # NOTE: Framework-documented defaults; not enforced by MCP tools.
    # Used by PhaseTimeCaps for ORC-side time tracking only.
    phase_cap_research: float = 0.25
    phase_cap_plan: float = 0.15
    phase_cap_implement: float = 0.40
    phase_cap_validate: float = 0.10
    phase_cap_review: float = 0.05
    phase_cap_deliver: float = 0.05

    # Learning defaults
    learning_max_entries: int = 500
    learning_prune_threshold: float = 0.3
    learning_promotion_impact: float = 0.7
    learning_prune_age_days: int = 30
    learning_repeated_op_threshold: int = 3
    recall_receipt_max_entries: int = 1000
    claude_md_max_lines: int = 200
    sub_claude_md_max_lines: int = 50

    # Utility scoring (PRD-CORE-004)
    learning_decay_half_life_days: float = 14.0
    learning_decay_use_exponent: float = 0.6
    q_learning_rate: float = 0.15
    q_recurrence_bonus: float = 0.02
    q_cold_start_threshold: int = 3
    learning_utility_prune_threshold: float = 0.10
    learning_utility_delete_threshold: float = 0.05
    recall_utility_lambda: float = 0.3
    learning_outcome_correlation_window_minutes: int = 30
    learning_outcome_history_cap: int = 20

    # Paths (relative to project root, resolved at runtime)
    trw_dir: str = ".trw"
    learnings_dir: str = "learnings"
    entries_dir: str = "entries"
    receipts_dir: str = "receipts"
    reflections_dir: str = "reflections"
    scripts_dir: str = "scripts"
    patterns_dir: str = "patterns"
    context_dir: str = "context"

    # Framework deployment
    frameworks_dir: str = "frameworks"
    templates_dir: str = "templates"
    aaref_version: str = "v1.1.0"

    # AARE-F quality gates
    ambiguity_rate_max: float = 0.05
    completeness_min: float = 0.85
    traceability_coverage_min: float = 0.90
    consistency_validation_min: float = 0.95

    # Semantic validation — dimension weights (must sum to 100)
    validation_density_weight: float = 25.0
    validation_structure_weight: float = 15.0
    validation_traceability_weight: float = 20.0
    validation_smell_weight: float = 15.0
    validation_readability_weight: float = 10.0
    validation_ears_weight: float = 15.0

    # Semantic validation — tier thresholds
    validation_skeleton_threshold: float = 30.0
    validation_draft_threshold: float = 60.0
    validation_review_threshold: float = 85.0

    # Semantic validation — readability
    validation_fk_optimal_min: float = 8.0
    validation_fk_optimal_max: float = 12.0

    # Semantic validation — smell detection
    validation_smell_false_positive_max: float = 0.15

    # LLM augmentation (optional, requires claude-agent-sdk)
    llm_enabled: bool = True
    llm_default_model: str = "haiku"
    llm_max_tokens: int = 500

    # Debug mode (enables file logging to .trw/logs/)
    debug: bool = False
    logs_dir: str = "logs"

    # Telemetry (off by default)
    telemetry: bool = False

    # Framework version
    framework_version: str = "v18.0_TRW"


class PhaseTimeCaps(BaseModel):
    """Phase time cap percentages derived from TRWConfig.

    Convenience accessor mapping phase names to their time caps.
    NOTE: Framework-documented defaults only. Not enforced by MCP tools —
    ORC tracks wall-clock time against these caps at the prompt level.
    """

    model_config = ConfigDict(frozen=True)

    research: float = 0.25
    plan: float = 0.15
    implement: float = 0.40
    validate_phase: float = 0.10
    review: float = 0.05
    deliver: float = 0.05

    def get_cap(self, phase: str) -> float:
        """Get time cap for a phase name.

        Args:
            phase: Phase name (research, plan, implement, validate, review, deliver).

        Returns:
            Time cap as a fraction of total timebox.

        Raises:
            ValueError: If phase name is not recognized.
        """
        caps: dict[str, float] = {
            "research": self.research,
            "plan": self.plan,
            "implement": self.implement,
            "validate": self.validate_phase,
            "review": self.review,
            "deliver": self.deliver,
        }
        if phase not in caps:
            msg = f"Unknown phase: {phase!r}. Valid: {list(caps.keys())}"
            raise ValueError(msg)
        return caps[phase]
