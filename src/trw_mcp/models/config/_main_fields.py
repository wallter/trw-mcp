"""TRWConfig field declarations — all configuration values.

Split from _main.py to keep the god-class under the 500-line threshold.
TRWConfig in _main.py inherits from _TRWConfigFields and adds
@cached_property facades, helper methods, and client profile resolution.

All field declarations live HERE. Both application code and tests
import TRWConfig from _main.py — this module is internal.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from trw_mcp.models.config._defaults import (
    DEFAULT_BUILD_CHECK_TIMEOUT_SECS,
    DEFAULT_LEARNING_MAX_ENTRIES,
    DEFAULT_MUTATION_TIMEOUT_SECS,
    DEFAULT_PARALLELISM_MAX,
    DEFAULT_RECALL_MAX_RESULTS,
    DEFAULT_RECALL_RECEIPT_MAX_ENTRIES,
    DEFAULT_SCORING_DEFAULT_DAYS_UNUSED,
)


class _TRWConfigFields(BaseSettings):
    """All TRW configuration fields.

    Values come from (in priority order):
    1. Environment variables (prefixed TRW_)
    2. .trw/config.yaml overrides (loaded at runtime)
    3. Defaults defined here (from FRAMEWORK.md DEFAULTS)

    Unknown environment variables and config.yaml keys are silently ignored.
    """

    model_config = SettingsConfigDict(
        env_prefix="TRW_",
        case_sensitive=False,
        extra="ignore",
    )

    # -- 1. Orchestration --

    parallelism_max: int = DEFAULT_PARALLELISM_MAX
    timebox_hours: int = 8
    max_research_waves: int = 3
    min_shards_target: int = 3
    min_shards_floor: int = 2
    consensus_quorum: float = 0.67
    max_child_depth: int = 2
    checkpoint_secs: int = 600

    # -- 2. Phase time caps --

    phase_cap_research: float = 0.25
    phase_cap_plan: float = 0.15
    phase_cap_implement: float = 0.35
    phase_cap_validate: float = 0.10
    phase_cap_review: float = 0.10
    phase_cap_deliver: float = 0.05

    # -- 3. Learning storage & retrieval --

    learning_max_entries: int = DEFAULT_LEARNING_MAX_ENTRIES
    learning_promotion_impact: float = 0.7
    learning_prune_age_days: int = 30
    learning_repeated_op_threshold: int = 3
    recall_receipt_max_entries: int = DEFAULT_RECALL_RECEIPT_MAX_ENTRIES
    recall_max_results: int = DEFAULT_RECALL_MAX_RESULTS
    recall_compact_fields: frozenset[str] = frozenset({"id", "summary", "impact", "tags", "status"})

    # -- 4. Hybrid retrieval (CORE-041) --

    memory_store_path: str = ".trw/memory/vectors.db"
    embeddings_enabled: bool = False
    retrieval_embedding_model: str = "all-MiniLM-L6-v2"
    retrieval_embedding_dim: int = 384
    hybrid_bm25_candidates: int = 50
    hybrid_vector_candidates: int = 50
    hybrid_rrf_k: int = 60
    hybrid_reranking_enabled: bool = False
    retrieval_fallback_enabled: bool = True
    wal_checkpoint_threshold_mb: int = 10

    # -- 5. Semantic dedup (CORE-042) --

    dedup_enabled: bool = True
    dedup_skip_threshold: float = 0.95
    dedup_merge_threshold: float = 0.85

    # -- 6. Memory consolidation (CORE-044) --

    memory_consolidation_enabled: bool = True
    memory_consolidation_interval_days: int = 7
    memory_consolidation_min_cluster: int = Field(default=3, ge=2)
    memory_consolidation_similarity_threshold: float = Field(default=0.75, ge=0.0, le=1.0)
    memory_consolidation_max_per_cycle: int = Field(default=50, ge=1)

    # -- 7. Tiered memory (CORE-043) --

    memory_hot_max_entries: int = 50
    memory_hot_ttl_days: int = 7
    memory_cold_threshold_days: int = 90
    memory_retention_days: int = 365
    memory_score_w1: float = 0.4
    memory_score_w2: float = 0.3
    memory_score_w3: float = 0.3

    # -- 8. Impact score distribution (CORE-034) --

    impact_forced_distribution_enabled: bool = True
    impact_tier_critical_cap: float = 0.05
    impact_tier_high_cap: float = 0.20
    impact_high_threshold_pct: float = 20.0
    impact_decay_half_life_days: int = 90

    # -- 9. Utility scoring & decay --

    learning_decay_half_life_days: float = 14.0
    learning_decay_use_exponent: float = 0.6
    learning_utility_prune_threshold: float = 0.10
    learning_utility_delete_threshold: float = 0.05
    q_learning_rate: float = 0.15
    q_recurrence_bonus: float = 0.02
    q_cold_start_threshold: int = 3
    source_human_utility_boost: float = 0.1
    access_count_utility_boost_cap: float = 0.15

    # -- 10. Outcome correlation --

    learning_outcome_correlation_window_minutes: int = 480
    learning_outcome_correlation_scope: str = "session"
    learning_outcome_history_cap: int = 20
    recall_utility_lambda: float = 0.3

    # -- 11. Documentation generation --

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
    agents_md_learning_injection: bool = True
    agents_md_learning_max: int = 5
    agents_md_learning_min_impact: float = 0.7

    # -- 12. Scoring subsystem --

    scoring_default_days_unused: int = DEFAULT_SCORING_DEFAULT_DAYS_UNUSED
    scoring_recency_discount_floor: float = 0.5
    scoring_error_fallback_reward: float = -0.3
    scoring_error_keywords: tuple[str, ...] = ("error", "fail", "exception", "crash", "timeout")

    # -- 13. Directory structure & paths --

    task_root: str = "docs"
    runs_root: str = ".trw/runs"
    trw_dir: str = ".trw"
    worktree_dir: str = ".trees"
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

    # -- 14. Framework version & AARE-F --

    framework_version: str = "v24.4_TRW"
    aaref_version: str = "v2.0.0"

    # -- 15-16. PRD quality gates & semantic validation --

    ambiguity_rate_max: float = 0.05
    completeness_min: float = 0.85
    traceability_coverage_min: float = 0.90
    consistency_validation_min: float = 0.95
    validation_density_weight: float = 42.0
    validation_structure_weight: float = 25.0
    validation_traceability_weight: float = 33.0
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

    # -- 17-18. Risk-based validation & phase gates --

    risk_scaling_enabled: bool = True
    phase_gate_enforcement: Literal["strict", "lenient", "off"] = "lenient"
    prd_min_content_density: float = 0.30
    prd_required_status_for_implement: str = "approved"
    prds_relative_path: str = "docs/requirements-aare-f/prds"
    index_auto_sync_on_status_change: bool = True
    strict_input_criteria: bool = False

    # -- 19. PRD grooming --

    grooming_max_iterations: int = 5
    grooming_target_completeness: float = 0.85
    grooming_research_scope: Literal["full", "codebase", "minimal"] = "full"
    grooming_placeholder_density_threshold: float = 0.10
    grooming_partial_density_threshold: float = 0.20

    # -- 20. Research findings --

    finding_dedup_threshold: float = 0.6
    findings_dir: str = "findings"
    findings_entries_dir: str = "entries"
    findings_registry_file: str = "registry.yaml"

    # -- 21. Reflection & patterns --

    reflect_sequence_lookback: int = 3
    reflect_max_positive_learnings: int = 5
    reflect_max_success_patterns: int = 5
    reflect_q_value_threshold: float = 0.6

    # -- 22. Phase reversion --

    reversion_rate_elevated: float = 0.15
    reversion_rate_concerning: float = 0.30

    # -- 23. Technical debt --

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

    # -- 24. Compliance --

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

    # -- 25. Wave adaptation --

    adaptation_enabled: bool = True
    max_total_waves: int = 8
    max_adaptations_per_run: int = 5
    max_shards_added_per_adaptation: int = 3
    adaptation_auto_approve_threshold: int = 5

    # -- 26. Velocity tracking --

    velocity_alert_min_runs: int = 5
    velocity_alert_r_squared_min: float = 0.4
    framework_overhead_threshold: float = 0.30
    velocity_history_max_entries: int = 200
    velocity_stable_threshold: float = 0.05
    velocity_effective_q_threshold: float = 0.5
    velocity_sign_test_alpha: float = 0.1
    velocity_confounder_jump_ratio: float = 1.5

    # -- 27. Project source paths --

    source_package_path: str = "trw-mcp/src"
    source_package_name: str = "trw_mcp"
    tests_relative_path: str = "trw-mcp/tests"
    test_map_filename: str = "test-map.yaml"

    # -- 28. LLM augmentation --

    llm_enabled: bool = True
    llm_default_model: str = "haiku"

    # -- 29. Adaptive gates --

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

    # -- 30. Code simplifier --

    auto_simplify_enabled: bool = False
    simplifier_wave_size: int = 10
    sprint_code_simplifier_wave_size: int = 10
    sprint_commit_pattern: str = "feat(sprint{num}): Track {track}"
    simplifier_verification_timeout_secs: int = 120
    simplifier_backup_dir: str = ".trw/simplifier-backups"

    # -- 31. Build verification --

    build_check_enabled: bool = True
    build_check_timeout_secs: int = DEFAULT_BUILD_CHECK_TIMEOUT_SECS
    build_check_coverage_min: float = 85.0
    build_gate_enforcement: Literal["strict", "lenient", "off"] = "lenient"
    build_check_pytest_args: str = ""
    build_check_mypy_args: str = "--strict"
    build_check_pytest_cmd: str | None = None

    # -- 32. Run maintenance --

    run_auto_close_enabled: bool = True
    run_auto_close_age_days: int = 7
    run_stale_ttl_hours: int = 48

    # -- 33-35. Auto-checkpoint, auto-recall, auto-prune --

    auto_checkpoint_enabled: bool = True
    auto_checkpoint_tool_interval: int = 25
    auto_checkpoint_pre_compact: bool = True
    auto_recall_enabled: bool = True
    auto_recall_max_results: int = 5
    learning_auto_prune_on_deliver: bool = True
    learning_auto_prune_cap: int = 150

    # -- 36. Debug & telemetry --

    debug: bool = False
    logs_dir: str = "logs"
    telemetry: bool = False
    telemetry_enabled: bool = True
    telemetry_file: str = "tool-telemetry.jsonl"
    llm_usage_log_enabled: bool = True
    llm_usage_log_file: str = "llm_usage.jsonl"

    # -- 37. Platform & update channel --

    platform_telemetry_enabled: bool = False
    update_channel: str = "latest"
    platform_url: str = ""
    platform_urls: list[str] = Field(default_factory=list)
    platform_api_key: SecretStr = SecretStr("")
    installation_id: str = ""
    auto_upgrade: bool = False

    # -- 38. Knowledge topology (CORE-021) --

    knowledge_sync_threshold: int = 50
    knowledge_jaccard_threshold: float = Field(default=0.3, ge=0.0, le=1.0)
    knowledge_min_cluster_size: int = Field(default=3, ge=1)
    knowledge_output_dir: str = "knowledge"

    # -- 39. Complexity classification (CORE-060) --

    complexity_tier_minimal: int = 1
    complexity_tier_comprehensive: int = 6
    complexity_weight_novel_patterns: int = 3
    complexity_weight_cross_cutting: int = 2
    complexity_weight_architecture_change: int = 3
    complexity_weight_external_integration: int = 2
    complexity_weight_large_refactoring: int = 1
    complexity_weight_files_affected_max: int = 5
    complexity_hard_override_threshold: int = 2

    # -- 40. MCP transport --

    mcp_transport: str = "stdio"
    mcp_host: str = "127.0.0.1"
    mcp_port: int = 8100

    # -- 41-45. Quality gates (mutation, cross-model, multi-agent, dep audit, API fuzz) --

    mutation_enabled: bool = False
    mutation_threshold: float = 0.50
    mutation_threshold_critical: float = 0.70
    mutation_threshold_experimental: float = 0.30
    mutation_critical_paths: tuple[str, ...] = ("tools/", "state/", "models/")
    mutation_experimental_paths: tuple[str, ...] = ("scratch/",)
    mutation_timeout_secs: int = DEFAULT_MUTATION_TIMEOUT_SECS
    cross_model_review_enabled: bool = False
    cross_model_provider: str = "gemini-2.5-pro"
    cross_model_review_timeout_secs: int = 30
    cross_model_review_block_on_critical: bool = True
    review_confidence_threshold: int = 80
    dep_audit_enabled: bool = True
    dep_audit_level: str = "high"
    dep_audit_timeout_secs: int = 30
    dep_audit_block_on_patchable_only: bool = True
    comment_check_enabled: bool = True
    api_fuzz_enabled: bool = False
    api_fuzz_base_url: str = "http://localhost:8000"
    api_fuzz_level: str = "strict"
    api_fuzz_timeout_secs: int = 120

    # -- 46-50. ATDD, hooks, enforcement, validation, compaction, progressive --

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

    # -- 51-54. OTEL, alerting, trust, ceremony feedback --

    otel_enabled: bool = False
    otel_endpoint: str = ""
    ceremony_alert_threshold: int = 40
    ceremony_alert_consecutive: int = 3
    trust_crawl_boundary: int = 50
    trust_walk_boundary: int = 200
    trust_walk_sample_rate: float = 0.3
    trust_security_tags: tuple[str, ...] = ("auth", "secrets", "permissions", "encryption", "oauth", "jwt")
    trust_locked: bool = False
    ceremony_feedback_min_samples: int = 10
    ceremony_feedback_score_threshold: float = 80.0
    ceremony_feedback_quality_threshold: float = 0.9
    ceremony_feedback_escalation_threshold: float = 60.0
    ceremony_feedback_escalation_window: int = 5

    # -- 55-60. Migration gate, DRY check, learning injection, semantic, assertions, observation --

    migration_gate_enabled: bool = True
    dry_check_enabled: bool = True
    dry_check_min_block_size: int = 5
    agent_learning_injection: bool = True
    agent_learning_max: int = 5
    agent_learning_min_impact: float = 0.5
    semantic_checks_enabled: bool = True
    assertion_failure_penalty: float = Field(default=0.15, ge=0.0, le=1.0)
    assertion_stale_threshold_days: int = Field(default=30, ge=1)
    observation_masking: bool = True
    compact_after_turns: int = 10
    minimal_after_turns: int = 30
