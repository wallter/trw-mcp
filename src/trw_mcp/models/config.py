"""Framework configuration — single source of truth for all TRW defaults.

All configuration values are centralized here. Both application code
and test suites import from this module — no parallel constants.
"""

from __future__ import annotations

from typing import ClassVar, Literal

from pydantic import BaseModel, ConfigDict
from pydantic_settings import BaseSettings, SettingsConfigDict


class TRWConfig(BaseSettings):
    """Single source of truth for all TRW MCP server configuration.

    Values come from (in priority order):
    1. Environment variables (prefixed TRW_)
    2. .trw/config.yaml overrides (loaded at runtime)
    3. Defaults defined here (from FRAMEWORK.md §DEFAULTS)

    Unknown environment variables and config.yaml keys are silently ignored.
    """

    model_config = SettingsConfigDict(
        env_prefix="TRW_",
        case_sensitive=False,
        extra="ignore",
    )

    # Orchestration: wave/shard execution limits
    parallelism_max: int = 10
    timebox_hours: int = 8
    max_research_waves: int = 3

    # Orchestration: ORC-level consensus thresholds
    # Not consumed by MCP tools — tracked at prompt level only
    min_shards_target: int = 3
    min_shards_floor: int = 2
    consensus_quorum: float = 0.67
    max_child_depth: int = 2
    checkpoint_secs: int = 600

    # Phase time caps (percentage of total timebox, ORC-level tracking)
    # 6-phase model: RESEARCH → PLAN → IMPLEMENT → VALIDATE → REVIEW → DELIVER
    phase_cap_research: float = 0.25
    phase_cap_plan: float = 0.15
    phase_cap_implement: float = 0.35
    phase_cap_validate: float = 0.10
    phase_cap_review: float = 0.10
    phase_cap_deliver: float = 0.05

    # Learning: storage and retrieval
    learning_max_entries: int = 500
    learning_promotion_impact: float = 0.7
    learning_prune_age_days: int = 30
    learning_repeated_op_threshold: int = 3
    recall_receipt_max_entries: int = 1000
    recall_max_results: int = 25
    recall_compact_fields: frozenset[str] = frozenset(
        {"id", "summary", "impact", "tags", "status"}
    )

    # Learning: utility scoring with Ebbinghaus decay (PRD-CORE-004, PRD-CORE-026)
    learning_decay_half_life_days: float = 14.0
    learning_decay_use_exponent: float = 0.6
    learning_utility_prune_threshold: float = 0.10
    learning_utility_delete_threshold: float = 0.05
    q_learning_rate: float = 0.15
    q_recurrence_bonus: float = 0.02
    q_cold_start_threshold: int = 3
    source_human_utility_boost: float = 0.1
    access_count_utility_boost_cap: float = 0.15

    # Learning: outcome correlation tracking
    learning_outcome_correlation_window_minutes: int = 240
    learning_outcome_correlation_scope: str = "session"
    learning_outcome_history_cap: int = 20
    recall_utility_lambda: float = 0.3

    # Documentation generation
    claude_md_max_lines: int = 300
    sub_claude_md_max_lines: int = 50
    agents_md_enabled: bool = True
    agent_teams_enabled: bool = True

    # Scoring subsystem (outcome-based utility, Sprint 8 extraction)
    scoring_default_days_unused: int = 30
    scoring_recency_discount_floor: float = 0.5
    scoring_error_fallback_reward: float = -0.3
    scoring_error_keywords: tuple[str, ...] = (
        "error", "fail", "exception", "crash", "timeout",
    )

    # Directory structure and paths
    task_root: str = "docs"
    trw_dir: str = ".trw"
    learnings_dir: str = "learnings"
    entries_dir: str = "entries"
    receipts_dir: str = "receipts"
    reflections_dir: str = "reflections"
    scripts_dir: str = "scripts"
    patterns_dir: str = "patterns"
    context_dir: str = "context"
    scratch_dir: str = "scratch"
    events_file: str = "events.jsonl"
    checkpoints_file: str = "checkpoints.jsonl"
    frameworks_dir: str = "frameworks"
    templates_dir: str = "templates"

    # Framework version and AARE-F standard
    framework_version: str = "v24.0_TRW"
    aaref_version: str = "v1.1.0"

    # PRD quality gates (AARE-F standard)
    ambiguity_rate_max: float = 0.05
    completeness_min: float = 0.85
    traceability_coverage_min: float = 0.90
    consistency_validation_min: float = 0.95

    # Semantic validation: quality dimension weights (must sum to 100)
    validation_density_weight: float = 25.0
    validation_structure_weight: float = 15.0
    validation_traceability_weight: float = 20.0
    validation_smell_weight: float = 15.0
    validation_readability_weight: float = 10.0
    validation_ears_weight: float = 15.0

    # Semantic validation: PRD status thresholds
    validation_skeleton_threshold: float = 30.0
    validation_draft_threshold: float = 60.0
    validation_review_threshold: float = 85.0

    # Semantic validation: readability (Flesch-Kincaid grade level)
    validation_fk_optimal_min: float = 8.0
    validation_fk_optimal_max: float = 12.0

    # Risk-based validation scaling (PRD-QUAL-013)
    risk_scaling_enabled: bool = True

    # Phase gates and PRD enforcement (PRD-CORE-009)
    phase_gate_enforcement: Literal["strict", "lenient", "off"] = "lenient"
    prd_min_content_density: float = 0.30
    prd_required_status_for_implement: str = "approved"
    prds_relative_path: str = "docs/requirements-aare-f/prds"
    index_auto_sync_on_status_change: bool = True

    # PRD grooming (PRD-CORE-011)
    grooming_max_iterations: int = 5
    grooming_target_completeness: float = 0.85
    grooming_research_scope: Literal["full", "codebase", "minimal"] = "full"
    grooming_placeholder_density_threshold: float = 0.10
    grooming_partial_density_threshold: float = 0.20

    # Research findings pipeline (PRD-CORE-010)
    finding_dedup_threshold: float = 0.6
    findings_dir: str = "findings"
    findings_entries_dir: str = "entries"
    findings_registry_file: str = "registry.yaml"

    # Reflection and pattern extraction (PRD-QUAL-001)
    reflect_sequence_lookback: int = 3
    reflect_max_positive_learnings: int = 5
    reflect_max_success_patterns: int = 5
    reflect_q_value_threshold: float = 0.6

    # Phase reversion metrics (PRD-CORE-013-FR07)
    reversion_rate_elevated: float = 0.15
    reversion_rate_concerning: float = 0.30

    # Technical debt registry and scoring (PRD-CORE-016)
    debt_registry_filename: str = "debt-registry.yaml"
    debt_id_prefix: str = "DEBT"
    debt_initial_decay_score: float = 0.5
    debt_decay_base_score: float = 0.3
    debt_decay_daily_rate: float = 0.01
    debt_decay_assessment_rate: float = 0.05
    debt_auto_promote_threshold: float = 0.9
    debt_actionable_threshold: float = 0.7
    debt_budget_critical_ratio: float = 0.20
    debt_budget_high_ratio: float = 0.15
    debt_default_wave_size: int = 5

    # Compliance auditing (PRD-QUAL-003)
    compliance_strictness: Literal["strict", "lenient", "off"] = "lenient"
    compliance_long_session_event_threshold: int = 5
    compliance_pass_threshold: float = 0.8
    compliance_warning_threshold: float = 0.5
    compliance_dir: str = "compliance"
    compliance_history_file: str = "history.jsonl"
    compliance_changelog_filename: str = "CHANGELOG.md"

    # Wave adaptation and iteration (PRD-CORE-006)
    adaptation_enabled: bool = True
    max_total_waves: int = 8
    max_adaptations_per_run: int = 5
    max_shards_added_per_adaptation: int = 3
    adaptation_auto_approve_threshold: int = 5

    # Velocity tracking and statistical analysis (PRD-CORE-015)
    velocity_alert_min_runs: int = 5
    velocity_alert_r_squared_min: float = 0.4
    framework_overhead_threshold: float = 0.30
    velocity_history_max_entries: int = 200
    velocity_stable_threshold: float = 0.05
    velocity_effective_q_threshold: float = 0.5
    velocity_sign_test_alpha: float = 0.1
    velocity_confounder_jump_ratio: float = 1.5

    # Project source paths for testing and analysis
    source_package_path: str = "trw-mcp/src"
    source_package_name: str = "trw_mcp"
    tests_relative_path: str = "trw-mcp/tests"
    test_map_filename: str = "test-map.yaml"

    # LLM augmentation (anthropic SDK — optional [ai] dependency)
    llm_enabled: bool = True
    llm_default_model: str = "haiku"

    # Adaptive gates for shard evaluation (PRD-QUAL-005)
    gate_default_type: str = "FULL"
    gate_strategy: str = "hybrid"
    gate_early_stop_confidence: float = 0.85
    gate_max_rounds: int = 5
    gate_convergence_epsilon: float = 0.05
    gate_escalation_enabled: bool = True
    gate_max_total_judges: int = 13
    gate_tokens_per_vote: int = 2000
    gate_debate_context_multiplier: float = 1.5
    gate_critic_overhead_multiplier: float = 2.0
    gate_tokens_per_1k_chars: int = 500
    gate_architecture_score_penalty: float = 0.1

    # Code simplifier and sprint workflow (PRD-QUAL-010)
    auto_simplify_enabled: bool = False
    simplifier_wave_size: int = 10
    sprint_code_simplifier_wave_size: int = 10  # legacy alias; prefer simplifier_wave_size
    sprint_commit_pattern: str = "feat(sprint{num}): Track {track}"
    simplifier_verification_timeout_secs: int = 120
    simplifier_backup_dir: str = ".trw/simplifier-backups"

    # Phase input criteria strictness (PRD-CORE-009)
    # When True, phase_check(direction="enter") reports errors instead of warnings
    strict_input_criteria: bool = False

    # Build verification gate (PRD-CORE-023)
    build_check_enabled: bool = True
    build_check_timeout_secs: int = 300
    build_check_coverage_min: float = 85.0
    build_gate_enforcement: Literal["strict", "lenient", "off"] = "lenient"
    build_check_pytest_args: str = ""
    build_check_mypy_args: str = "--strict"

    # Debug and telemetry
    debug: bool = False
    logs_dir: str = "logs"
    telemetry: bool = False          # session-level flag; consumed by trw_session_start
    telemetry_enabled: bool = True   # per-tool toggle (reserved for future use)
    telemetry_file: str = "tool-telemetry.jsonl"
    llm_usage_log_enabled: bool = True
    llm_usage_log_file: str = "llm_usage.jsonl"


# --- Singleton factory ---------------------------------------------------

_singleton: TRWConfig | None = None


def get_config() -> TRWConfig:
    """Return the shared TRWConfig singleton.

    First call creates the instance; subsequent calls return the same object.
    Use ``_reset_config()`` in tests to clear cached state.
    """
    global _singleton  # noqa: PLW0603
    if _singleton is None:
        _singleton = TRWConfig()
    return _singleton


def _reset_config(config: TRWConfig | None = None) -> None:
    """Reset the config singleton (test helper only).

    Args:
        config: Optional replacement config. If *None*, the next
            ``get_config()`` call creates a fresh default instance.
    """
    global _singleton  # noqa: PLW0603
    _singleton = config


class PhaseTimeCaps(BaseModel):
    """Phase time cap percentages — ORC-level time tracking only.

    Convenience accessor for mapping phase names to their target time fractions.
    NOTE: Framework-documented defaults; not enforced by MCP tools.
    ORC tracks wall-clock time against these caps at the prompt level.
    6-phase model: RESEARCH → PLAN → IMPLEMENT → VALIDATE → REVIEW → DELIVER.
    """

    model_config = ConfigDict(frozen=True)

    research: float = 0.25
    plan: float = 0.15
    implement: float = 0.35
    validate_phase: float = 0.10
    review: float = 0.10
    deliver: float = 0.05

    # Maps canonical phase name → field name; only "validate" differs to avoid
    # the Pydantic BaseModel reserved-name conflict.
    _PHASE_FIELDS: ClassVar[dict[str, str]] = {
        "research": "research",
        "plan": "plan",
        "implement": "implement",
        "validate": "validate_phase",  # field renamed to avoid BaseModel collision
        "review": "review",
        "deliver": "deliver",
    }

    def get_cap(self, phase: str) -> float:
        """Return the time cap fraction for the given phase.

        Args:
            phase: Phase name (research, plan, implement, validate, review, deliver).

        Raises:
            ValueError: If phase is not recognized.
        """
        field = self._PHASE_FIELDS.get(phase)
        if field is None:
            msg = f"Unknown phase: {phase!r}. Valid: {list(self._PHASE_FIELDS)}"
            raise ValueError(msg)
        return float(getattr(self, field))
