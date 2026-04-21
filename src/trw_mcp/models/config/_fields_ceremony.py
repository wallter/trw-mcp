"""Ceremony, compliance, documentation generation, and enforcement fields."""

from __future__ import annotations

from typing import Literal

from pydantic import Field


class _CeremonyFields:
    """Ceremony domain mixin — mixed into _TRWConfigFields via MI."""

    # -- Documentation generation --

    claude_md_max_lines: int = 500
    sub_claude_md_max_lines: int = 50
    max_auto_lines: int = 300
    agents_md_enabled: bool = True
    agent_teams_enabled: bool = True
    target_platforms: list[str] = Field(
        default_factory=lambda: ["claude-code"],
        description="Platforms to sync instruction files for.",
    )
    ceremony_mode: Literal["full", "light"] = "full"
    response_format: Literal["yaml", "json"] = "yaml"
    agents_md_learning_injection: bool = True
    agents_md_learning_max: int = 5
    agents_md_learning_min_impact: float = 0.7

    # -- Framework version & AARE-F --

    framework_version: str = "v24.6_TRW"
    aaref_version: str = "v2.0.0"

    # -- PRD quality gates & semantic validation --

    ambiguity_rate_max: float = 0.05
    completeness_min: float = 0.85
    traceability_coverage_min: float = 0.90
    consistency_validation_min: float = 0.95
    validation_density_weight: float = 20.0
    validation_structure_weight: float = 20.0
    validation_implementation_readiness_weight: float = 25.0
    validation_traceability_weight: float = 35.0
    validation_smell_weight: float = 0.0
    validation_readability_weight: float = 0.0
    validation_ears_weight: float = 0.0
    density_weight_problem_statement: float = Field(default=2.0, ge=0.0, le=10.0)
    density_weight_functional_requirements: float = Field(default=2.0, ge=0.0, le=10.0)
    density_weight_traceability_matrix: float = Field(default=1.5, ge=0.0, le=10.0)
    density_weight_default: float = Field(default=1.0, ge=0.0, le=10.0)
    validation_skeleton_threshold: float = 30.0
    validation_draft_threshold: float = 60.0
    validation_review_threshold: float = 85.0
    validation_fk_optimal_min: float = 8.0
    validation_fk_optimal_max: float = 12.0

    # -- Risk-based validation & phase gates --

    risk_scaling_enabled: bool = True
    phase_gate_enforcement: Literal["strict", "lenient", "off"] = "lenient"
    prd_min_content_density: float = 0.30
    prd_required_status_for_implement: str = "approved"
    prds_relative_path: str = "docs/requirements-aare-f/prds"
    index_auto_sync_on_status_change: bool = True
    strict_input_criteria: bool = False

    # -- PRD grooming --

    grooming_max_iterations: int = 5
    grooming_target_completeness: float = 0.85
    grooming_research_scope: Literal["full", "codebase", "minimal"] = "full"
    grooming_placeholder_density_threshold: float = 0.10
    grooming_partial_density_threshold: float = 0.20

    # -- Research findings --

    finding_dedup_threshold: float = 0.6
    findings_dir: str = "findings"
    findings_entries_dir: str = "entries"
    findings_registry_file: str = "registry.yaml"

    # -- Reflection & patterns --

    reflect_sequence_lookback: int = 3
    reflect_max_positive_learnings: int = 5
    reflect_max_success_patterns: int = 5
    reflect_q_value_threshold: float = 0.6

    # -- Phase reversion --

    reversion_rate_elevated: float = 0.15
    reversion_rate_concerning: float = 0.30

    # -- Technical debt --

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

    # -- Compliance --

    compliance_strictness: Literal["strict", "lenient", "off"] = "lenient"
    compliance_long_session_event_threshold: int = 5
    compliance_pass_threshold: float = 0.8
    compliance_warning_threshold: float = 0.5
    compliance_dir: str = "compliance"
    compliance_history_file: str = "history.jsonl"
    compliance_changelog_filename: str = "CHANGELOG.md"
    commit_fr_trailer_enabled: bool = True
    sprint_integration_branch_pattern: str = "sprint-{N}-integration"
    compliance_review_retention_days: int = 365
    provenance_enabled: bool = True
    confidence_threshold: float = Field(default=0.8, ge=0.0, le=1.0)

    # -- ATDD, hooks, enforcement, validation, compaction, progressive --

    atdd_enabled: bool = True
    test_skeleton_dir: str = ""
    completion_hooks_blocking: bool = False
    self_review_blocking: bool = False
    enforcement_variant: str = "baseline"
    incremental_validation_enabled: bool = True
    security_check_enabled: bool = True
    compact_instructions_template: str = ""
    pause_after_compaction: bool = False
    progressive_disclosure: bool = False

    # -- Ceremony alert & feedback --

    ceremony_alert_threshold: int = 40
    ceremony_alert_consecutive: int = 3
    ceremony_feedback_min_samples: int = 10
    ceremony_feedback_score_threshold: float = 80.0
    ceremony_feedback_quality_threshold: float = 0.9
    ceremony_feedback_escalation_threshold: float = 60.0
    ceremony_feedback_escalation_window: int = 5

    # -- Semantic checks & assertions --

    semantic_checks_enabled: bool = True
    assertion_failure_penalty: float = Field(default=0.15, ge=0.0, le=1.0)
    assertion_stale_threshold_days: int = Field(default=30, ge=1)
    observation_masking: bool = True
    compact_after_turns: int = 10
    minimal_after_turns: int = 30

    # -- Migration gate & DRY check --

    migration_gate_enabled: bool = True
    dry_check_enabled: bool = True
    dry_check_min_block_size: int = 5
    agent_learning_injection: bool = True
    agent_learning_max: int = 5
    agent_learning_min_impact: float = 0.5

    # -- PRD-QUAL-056 audit quality fields --

    max_audit_cycles: int = Field(default=3, ge=1, le=10, description="Maximum audit cycles before escalation")
    audit_pattern_promotion_threshold: int = Field(
        default=3, ge=1, le=20, description="Minimum distinct PRDs for audit pattern promotion"
    )

    # -- Nudge control (S4, PRD-CORE-125) --

    nudge_enabled: bool | None = None
    nudge_urgency_mode: Literal["adaptive", "always_low", "always_high", "off"] = "adaptive"
    nudge_budget_chars: int = Field(default=600, ge=100, le=2000)
    nudge_dedup_enabled: bool = True
    # PRD-CORE-145 FR01: messenger variant — selects which content-generator
    # strategy produces the nudge text. "standard" preserves pre-PRD pool-based
    # dispatch. "minimal" routes through compute_nudge_minimal (compressed).
    # Default None resolves to "standard" so behavior is byte-identical to
    # pre-PRD until operators explicitly opt in.
    nudge_messenger: Literal["standard", "minimal"] | None = None

    # -- Nudge pool tuning (PRD-CORE-129) --
    nudge_pool_weight_workflow: int = 40
    nudge_pool_weight_learnings: int = 30
    nudge_pool_weight_ceremony: int = 20
    nudge_pool_weight_context: int = 10
    nudge_pool_cooldown_after: int = Field(default=3, ge=1, le=20)
    nudge_pool_cooldown_calls: int = Field(default=10, ge=1, le=100)
    # PRD-CORE-144 FR03: wall-clock cap on pool cooldown so the primary
    # ("learnings") pool cannot remain indefinitely cooled out of rotation.
    nudge_pool_cooldown_wall_clock_max_hours: int = Field(
        default=24,
        ge=1,
        le=720,
        description="Maximum wall-clock hours a nudge pool may remain in cooldown before forced re-engagement.",
    )

    # -- Hook control (S9, PRD-CORE-125) --

    hooks_enabled: bool | None = None

    # -- Framework reference (S12, PRD-CORE-125) --

    framework_md_enabled: bool | None = None

    # -- Skills & Agents (S10, S11, PRD-CORE-125) --

    skills_enabled: bool | None = None
    agents_enabled: bool | None = None
