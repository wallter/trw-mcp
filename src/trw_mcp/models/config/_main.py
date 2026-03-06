"""TRWConfig -- single source of truth for all TRW defaults.

All configuration values are centralized here. Both application code
and test suites import from this module -- no parallel constants.

PRD-CORE-071 Phase 1: Domain sub-configs provide type-narrowed access
(e.g. ``config.build`` returns a ``BuildConfig``). Flat field access
(``config.build_check_enabled``) is preserved -- all flat fields remain.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from trw_mcp.models.config._sub_models import (
    BuildConfig,
    CeremonyFeedbackConfig,
    MemoryConfig,
    OrchestrationConfig,
    PathsConfig,
    ScoringConfig,
    TelemetryConfig,
    TrustConfig,
)


class TRWConfig(BaseSettings):
    """Single source of truth for all TRW MCP server configuration.

    Values come from (in priority order):
    1. Environment variables (prefixed TRW_)
    2. .trw/config.yaml overrides (loaded at runtime)
    3. Defaults defined here (from FRAMEWORK.md DEFAULTS)

    Unknown environment variables and config.yaml keys are silently ignored.

    Table of Contents (section -> first field):
    ------------------------------------------------
     1. Orchestration .................. parallelism_max
     2. Phase time caps ............... phase_cap_research
     3. Learning storage & retrieval .. learning_max_entries
     4. Hybrid retrieval (CORE-041) ... memory_store_path
     5. Semantic dedup (CORE-042) ..... dedup_enabled
     6. Memory consolidation (CORE-044) memory_consolidation_enabled
     7. Tiered memory (CORE-043) ...... memory_hot_max_entries
     8. Impact score distribution (CORE-034) impact_forced_distribution_enabled
     9. Utility scoring & decay ....... learning_decay_half_life_days
    10. Outcome correlation ........... learning_outcome_correlation_window_minutes
    11. Documentation generation ...... claude_md_max_lines
    12. Scoring subsystem ............. scoring_default_days_unused
    13. Directory structure & paths ... task_root
    14. Framework version & AARE-F .... framework_version
    15. PRD quality gates ............. ambiguity_rate_max
    16. Semantic validation ........... validation_density_weight
    17. Risk-based validation ......... risk_scaling_enabled
    18. Phase gates & enforcement ..... phase_gate_enforcement
    19. PRD grooming .................. grooming_max_iterations
    20. Research findings ............. finding_dedup_threshold
    21. Reflection & patterns ......... reflect_sequence_lookback
    22. Phase reversion ............... reversion_rate_elevated
    23. Technical debt ................ debt_registry_filename
    24. Compliance .................... compliance_strictness
    25. Wave adaptation ............... adaptation_enabled
    26. Velocity tracking ............. velocity_alert_min_runs
    27. Project source paths .......... source_package_path
    28. LLM augmentation .............. llm_enabled
    29. Adaptive gates ................ gate_default_type
    30. Code simplifier ............... auto_simplify_enabled
    31. Build verification ............ build_check_enabled
    32. Run maintenance ............... run_auto_close_enabled
    33. Auto-checkpoint (CORE-053) .... auto_checkpoint_enabled
    34. Auto-recall (CORE-049) ........ auto_recall_enabled
    35. Learning auto-prune ........... learning_auto_prune_on_deliver
    36. Debug & telemetry ............. debug
    37. Platform & update channel ..... platform_telemetry_enabled
    38. Knowledge topology (CORE-021) . knowledge_sync_threshold
    39. Complexity classification ...... complexity_tier_minimal
    40. MCP transport .................. mcp_transport
    41. Mutation testing (QUAL-025) .... mutation_enabled
    42. Cross-model review (QUAL-026) .. cross_model_review_enabled
    43. Multi-agent review (QUAL-027) .. review_confidence_threshold
    44. Dependency audit (QUAL-028) .... dep_audit_enabled
    45. API fuzz & comment check (029) . comment_check_enabled
    46. ATDD (CORE-064) ............. atdd_enabled
    47. Completion hooks (CORE-065) .. completion_hooks_blocking
    48. PostToolUse validation (030) . incremental_validation_enabled
    49. Compaction (CORE-066) ........ compact_instructions_template
    50. Progressive disclosure (067) .. progressive_disclosure
    """

    model_config = SettingsConfigDict(
        env_prefix="TRW_",
        case_sensitive=False,
        extra="ignore",
    )

    # -- 1. Orchestration --

    # Wave/shard execution limits
    parallelism_max: int = 10
    timebox_hours: int = 8
    max_research_waves: int = 3

    # ORC-level consensus thresholds
    # Not consumed by MCP tools -- tracked at prompt level only
    min_shards_target: int = 3
    min_shards_floor: int = 2
    consensus_quorum: float = 0.67
    max_child_depth: int = 2
    checkpoint_secs: int = 600

    # -- 2. Phase time caps --
    # Percentage of total timebox, ORC-level tracking.
    # 6-phase model: RESEARCH -> PLAN -> IMPLEMENT -> VALIDATE -> REVIEW -> DELIVER

    phase_cap_research: float = 0.25
    phase_cap_plan: float = 0.15
    phase_cap_implement: float = 0.35
    phase_cap_validate: float = 0.10
    phase_cap_review: float = 0.10
    phase_cap_deliver: float = 0.05

    # -- 3. Learning storage & retrieval --

    learning_max_entries: int = 500
    learning_promotion_impact: float = 0.7
    learning_prune_age_days: int = 30
    learning_repeated_op_threshold: int = 3
    recall_receipt_max_entries: int = 1000
    recall_max_results: int = 25
    recall_compact_fields: frozenset[str] = frozenset(
        {"id", "summary", "impact", "tags", "status"}
    )

    # -- 4. Hybrid retrieval (CORE-041) --

    memory_store_path: str = ".trw/memory/vectors.db"
    embeddings_enabled: bool = False  # Opt-in: requires `pip install trw-memory[embeddings]` (~2GB, PyTorch)
    retrieval_embedding_model: str = "all-MiniLM-L6-v2"  # HuggingFace model for local embeddings
    retrieval_embedding_dim: int = 384  # Must match chosen model
    hybrid_bm25_candidates: int = 50
    hybrid_vector_candidates: int = 50
    hybrid_rrf_k: int = 60
    hybrid_reranking_enabled: bool = False  # Future: cross-encoder reranking
    retrieval_fallback_enabled: bool = True  # Fall back to keyword search when hybrid unavailable

    # -- 5. Semantic dedup (CORE-042) --

    dedup_enabled: bool = True
    dedup_skip_threshold: float = 0.95  # cosine >= this -> skip (exact duplicate)
    dedup_merge_threshold: float = 0.85  # cosine >= this -> merge (near duplicate)

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
    memory_score_w1: float = 0.4   # relevance weight
    memory_score_w2: float = 0.3   # recency weight
    memory_score_w3: float = 0.3   # importance weight

    # -- 8. Impact score distribution (CORE-034) --

    impact_forced_distribution_enabled: bool = True
    impact_tier_critical_cap: float = 0.05   # max 5% at 0.9-1.0
    impact_tier_high_cap: float = 0.20       # max 20% at 0.7-0.89
    impact_high_threshold_pct: float = 20.0  # soft-cap: max % of learnings >= 0.8
    impact_decay_half_life_days: int = 90    # half-life for batch impact decay

    # -- 9. Utility scoring & decay --
    # Q-learning + Ebbinghaus decay (PRD-CORE-004, PRD-CORE-026)

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
    max_auto_lines: int = 80
    agents_md_enabled: bool = True
    agent_teams_enabled: bool = True

    # -- 12. Scoring subsystem --
    # Outcome-based utility, Sprint 8 extraction

    scoring_default_days_unused: int = 30
    scoring_recency_discount_floor: float = 0.5
    scoring_error_fallback_reward: float = -0.3
    scoring_error_keywords: tuple[str, ...] = (
        "error", "fail", "exception", "crash", "timeout",
    )

    # -- 13. Directory structure & paths --

    task_root: str = "docs"
    trw_dir: str = ".trw"
    worktree_dir: str = ".trees"  # INFRA-025-FR06
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

    framework_version: str = "v24.0_TRW"
    aaref_version: str = "v1.1.0"

    # -- 15. PRD quality gates --
    # AARE-F standard thresholds

    ambiguity_rate_max: float = 0.05
    completeness_min: float = 0.85
    traceability_coverage_min: float = 0.90
    consistency_validation_min: float = 0.95

    # -- 16. Semantic validation --

    # Quality dimension weights (must sum to 100)
    validation_density_weight: float = 25.0
    validation_structure_weight: float = 15.0
    validation_traceability_weight: float = 20.0
    validation_smell_weight: float = 15.0
    validation_readability_weight: float = 10.0
    validation_ears_weight: float = 15.0

    # PRD status thresholds
    validation_skeleton_threshold: float = 30.0
    validation_draft_threshold: float = 60.0
    validation_review_threshold: float = 85.0

    # Readability (Flesch-Kincaid grade level)
    validation_fk_optimal_min: float = 8.0
    validation_fk_optimal_max: float = 12.0

    # -- 17. Risk-based validation --
    # Scaling per PRD-QUAL-013

    risk_scaling_enabled: bool = True

    # -- 18. Phase gates & enforcement --
    # PRD-CORE-009

    phase_gate_enforcement: Literal["strict", "lenient", "off"] = "lenient"
    prd_min_content_density: float = 0.30
    prd_required_status_for_implement: str = "approved"
    prds_relative_path: str = "docs/requirements-aare-f/prds"
    index_auto_sync_on_status_change: bool = True

    # Phase input criteria strictness (PRD-CORE-009)
    # When True, phase_check(direction="enter") reports errors instead of warnings
    strict_input_criteria: bool = False

    # -- 19. PRD grooming --
    # PRD-CORE-011

    grooming_max_iterations: int = 5
    grooming_target_completeness: float = 0.85
    grooming_research_scope: Literal["full", "codebase", "minimal"] = "full"
    grooming_placeholder_density_threshold: float = 0.10
    grooming_partial_density_threshold: float = 0.20

    # -- 20. Research findings --
    # PRD-CORE-010

    finding_dedup_threshold: float = 0.6
    findings_dir: str = "findings"
    findings_entries_dir: str = "entries"
    findings_registry_file: str = "registry.yaml"

    # -- 21. Reflection & patterns --
    # PRD-QUAL-001

    reflect_sequence_lookback: int = 3
    reflect_max_positive_learnings: int = 5
    reflect_max_success_patterns: int = 5
    reflect_q_value_threshold: float = 0.6

    # -- 22. Phase reversion --
    # PRD-CORE-013-FR07

    reversion_rate_elevated: float = 0.15
    reversion_rate_concerning: float = 0.30

    # -- 23. Technical debt --
    # Registry and scoring (PRD-CORE-016)

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
    # Auditing (PRD-QUAL-003)

    compliance_strictness: Literal["strict", "lenient", "off"] = "lenient"
    compliance_long_session_event_threshold: int = 5
    compliance_pass_threshold: float = 0.8
    compliance_warning_threshold: float = 0.5
    compliance_dir: str = "compliance"
    compliance_history_file: str = "history.jsonl"
    compliance_changelog_filename: str = "CHANGELOG.md"

    # -- Sprint 44: Git Workflow & Enterprise Compliance --
    commit_fr_trailer_enabled: bool = True  # INFRA-026-FR07
    sprint_integration_branch_pattern: str = "sprint-{N}-integration"  # INFRA-026-FR07
    compliance_review_retention_days: int = 365  # INFRA-027-FR05
    provenance_enabled: bool = True  # INFRA-028-FR06
    confidence_threshold: float = Field(default=0.8, ge=0.0, le=1.0)  # INFRA-028-FR06

    # -- 25. Wave adaptation --
    # PRD-CORE-006

    adaptation_enabled: bool = True
    max_total_waves: int = 8
    max_adaptations_per_run: int = 5
    max_shards_added_per_adaptation: int = 3
    adaptation_auto_approve_threshold: int = 5

    # -- 26. Velocity tracking --
    # Statistical analysis (PRD-CORE-015)

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
    # anthropic SDK -- optional [ai] dependency

    llm_enabled: bool = True
    llm_default_model: str = "haiku"

    # -- 29. Adaptive gates --
    # Shard evaluation (PRD-QUAL-005)

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
    # Sprint workflow (PRD-QUAL-010)

    auto_simplify_enabled: bool = False
    simplifier_wave_size: int = 10
    sprint_code_simplifier_wave_size: int = 10  # legacy alias; prefer simplifier_wave_size
    sprint_commit_pattern: str = "feat(sprint{num}): Track {track}"
    simplifier_verification_timeout_secs: int = 120
    simplifier_backup_dir: str = ".trw/simplifier-backups"

    # -- 31. Build verification --
    # PRD-CORE-023

    build_check_enabled: bool = True
    build_check_timeout_secs: int = 300
    build_check_coverage_min: float = 85.0
    build_gate_enforcement: Literal["strict", "lenient", "off"] = "lenient"
    build_check_pytest_args: str = ""
    build_check_mypy_args: str = "--strict"
    build_check_pytest_cmd: str | None = None  # Custom test command (e.g. "make test")

    # -- 32. Run maintenance --
    # Auto-close orphaned runs (active > N days)

    run_auto_close_enabled: bool = True
    run_auto_close_age_days: int = 7
    run_stale_ttl_hours: int = 48  # hour-level TTL for stale run detection (PRD-FIX-028)

    # -- 33. Auto-checkpoint (CORE-053) --
    # Compaction safety

    auto_checkpoint_enabled: bool = True
    auto_checkpoint_tool_interval: int = 25
    auto_checkpoint_pre_compact: bool = True

    # -- 34. Auto-recall (CORE-049) --
    # Phase-contextual auto-recall

    auto_recall_enabled: bool = True
    auto_recall_max_results: int = 5

    # -- 35. Learning auto-prune --
    # Auto-prune learnings on deliver when active count exceeds cap

    learning_auto_prune_on_deliver: bool = True
    learning_auto_prune_cap: int = 150

    # -- 36. Debug & telemetry --

    debug: bool = False
    logs_dir: str = "logs"
    telemetry: bool = False          # session-level flag; consumed by trw_session_start
    telemetry_enabled: bool = True   # per-tool toggle (reserved for future use)
    telemetry_file: str = "tool-telemetry.jsonl"
    llm_usage_log_enabled: bool = True
    llm_usage_log_file: str = "llm_usage.jsonl"

    # -- 37. Platform & update channel --
    # PRD-CORE-031, PRD-INFRA-014, PRD-INFRA-016

    platform_telemetry_enabled: bool = False  # opt-in; sends anonymized usage to trwframework.com
    update_channel: str = "latest"            # update channel: latest | lts
    platform_url: str = ""                    # single backend URL (backward compat, empty = offline)
    platform_urls: list[str] = Field(default_factory=list)  # multi-backend: fan-out writes, first-success reads
    platform_api_key: str = ""                # API key for platform backend authentication
    installation_id: str = ""                 # anonymized installation identifier
    auto_upgrade: bool = False                # auto-install updates on session start (PRD-INFRA-014)

    # -- 38. Knowledge topology (CORE-021) --
    # Tag-based clustering for auto-generated topic documents

    knowledge_sync_threshold: int = 50
    knowledge_jaccard_threshold: float = Field(default=0.3, ge=0.0, le=1.0)
    knowledge_min_cluster_size: int = Field(default=3, ge=1)
    knowledge_output_dir: str = "knowledge"

    # -- 39. Complexity classification (CORE-060) --
    # Tier boundaries and signal weights for classify_complexity()

    complexity_tier_minimal: int = 3        # raw_score <= this -> MINIMAL
    complexity_tier_comprehensive: int = 7   # raw_score >= this+1 -> COMPREHENSIVE
    complexity_weight_novel_patterns: int = 3
    complexity_weight_cross_cutting: int = 2
    complexity_weight_architecture_change: int = 3
    complexity_weight_external_integration: int = 2
    complexity_weight_large_refactoring: int = 1
    complexity_weight_files_affected_max: int = 5
    complexity_hard_override_threshold: int = 2  # min high-risk signals to force COMPREHENSIVE

    # -- 40. MCP transport --
    # Shared HTTP server: multiple Claude Code instances connect to one process

    mcp_transport: str = "stdio"        # stdio | sse | streamable-http
    mcp_host: str = "127.0.0.1"         # bind address for HTTP transport
    mcp_port: int = 8100                # port for HTTP transport

    # -- 41. Mutation testing (QUAL-025) --
    # Optional VALIDATE phase gate -- runs mutmut on changed files

    mutation_enabled: bool = False
    mutation_threshold: float = 0.50                   # standard feature threshold
    mutation_threshold_critical: float = 0.70          # critical path threshold
    mutation_threshold_experimental: float = 0.30      # experimental code threshold
    mutation_critical_paths: tuple[str, ...] = ("tools/", "state/", "models/")
    mutation_experimental_paths: tuple[str, ...] = ("scratch/",)
    mutation_timeout_secs: int = 300

    # -- 42. Cross-model review (QUAL-026) --
    # Route git diff to external model family for independent review

    cross_model_review_enabled: bool = False
    cross_model_provider: str = "gemini-2.5-pro"
    cross_model_review_timeout_secs: int = 30
    cross_model_review_block_on_critical: bool = True

    # -- 43. Multi-agent review (QUAL-027) --
    # Confidence-scored parallel review with threshold filtering

    review_confidence_threshold: int = 80              # 0-100; findings below are in review-all.yaml only

    # -- 44. Dependency audit (QUAL-028) --
    # pip-audit / npm audit gate for agent dependency changes

    dep_audit_enabled: bool = True
    dep_audit_level: str = "high"                      # critical | high | medium | low
    dep_audit_timeout_secs: int = 30
    dep_audit_block_on_patchable_only: bool = True     # only block when fix_versions available

    # -- 45. API fuzz & comment check (QUAL-029) --
    # Schemathesis API fuzzing + placeholder comment detection hook

    comment_check_enabled: bool = True
    api_fuzz_enabled: bool = False
    api_fuzz_base_url: str = "http://localhost:8000"
    api_fuzz_level: str = "strict"                     # strict | lenient
    api_fuzz_timeout_secs: int = 120

    # -- 46. ATDD (CORE-064) --
    # Acceptance Test-Driven Development -- test skeletons before implementation

    atdd_enabled: bool = True
    test_skeleton_dir: str = ""  # empty = auto-detect from pyproject.toml testpaths

    # -- 47. Completion hooks (CORE-065) --
    # Agent completion enforcement -- warn-not-block by default

    completion_hooks_blocking: bool = False
    self_review_blocking: bool = False

    # -- 48. PostToolUse validation (QUAL-030) --
    # Incremental type checking and security pattern detection after Edit/Write

    incremental_validation_enabled: bool = True
    security_check_enabled: bool = True

    # -- 49. Compaction (CORE-066) --
    # Custom compaction instructions and context preservation

    compact_instructions_template: str = ""  # empty = use built-in TRW template
    pause_after_compaction: bool = False

    # -- 50. Progressive disclosure (CORE-067) --
    # Compact capability cards for non-hot-set tools in tools/list

    progressive_disclosure: bool = False

    # -- 51. OTEL & cost attribution (INFRA-029) --
    # Optional OpenTelemetry integration -- lazy import, fail-open

    otel_enabled: bool = False
    otel_endpoint: str = ""

    # -- 52. Ceremony alerting (QUAL-031) --
    # Quality dashboard degradation detection thresholds

    ceremony_alert_threshold: int = 40
    ceremony_alert_consecutive: int = 3

    # -- 53. Progressive Trust Model (CORE-068-FR06) --
    # Crawl/Walk/Run graduated autonomy boundaries and security tags

    trust_crawl_boundary: int = 50
    trust_walk_boundary: int = 200
    trust_walk_sample_rate: float = 0.3
    trust_security_tags: tuple[str, ...] = (
        "auth", "secrets", "permissions", "encryption", "oauth", "jwt",
    )
    trust_locked: bool = False

    # -- 54. Self-Improving Ceremony (CORE-069-FR07) --
    # Feedback loop thresholds for ceremony depth adjustments

    ceremony_feedback_min_samples: int = 10
    ceremony_feedback_score_threshold: float = 80.0
    ceremony_feedback_quality_threshold: float = 0.9
    ceremony_feedback_escalation_threshold: float = 60.0
    ceremony_feedback_escalation_window: int = 5

    # -- Domain Sub-Config Properties (PRD-CORE-071-FR01) --
    # Type-narrowed access: ``config.build.build_check_enabled``
    # Flat access preserved: ``config.build_check_enabled``

    @property
    def build(self) -> BuildConfig:
        """Build verification and mutation testing sub-config."""
        return BuildConfig(**{
            name: getattr(self, name)
            for name in BuildConfig.model_fields
            if hasattr(self, name)
        })

    @property
    def memory(self) -> MemoryConfig:
        """Learning storage and retrieval sub-config."""
        return MemoryConfig(**{
            name: getattr(self, name)
            for name in MemoryConfig.model_fields
            if hasattr(self, name)
        })

    @property
    def telemetry_settings(self) -> TelemetryConfig:
        """Telemetry and OTEL sub-config (avoids ``telemetry`` field conflict)."""
        return TelemetryConfig(**{
            name: getattr(self, name)
            for name in TelemetryConfig.model_fields
            if hasattr(self, name)
        })

    @property
    def orchestration(self) -> OrchestrationConfig:
        """Wave/shard orchestration sub-config."""
        return OrchestrationConfig(**{
            name: getattr(self, name)
            for name in OrchestrationConfig.model_fields
            if hasattr(self, name)
        })

    @property
    def scoring(self) -> ScoringConfig:
        """Scoring weights and decay parameters sub-config."""
        return ScoringConfig(**{
            name: getattr(self, name)
            for name in ScoringConfig.model_fields
            if hasattr(self, name)
        })

    @property
    def trust(self) -> TrustConfig:
        """Progressive trust model sub-config."""
        return TrustConfig(**{
            name: getattr(self, name)
            for name in TrustConfig.model_fields
            if hasattr(self, name)
        })

    @property
    def ceremony_feedback(self) -> CeremonyFeedbackConfig:
        """Self-improving ceremony feedback sub-config."""
        return CeremonyFeedbackConfig(**{
            name: getattr(self, name)
            for name in CeremonyFeedbackConfig.model_fields
            if hasattr(self, name)
        })

    @property
    def paths(self) -> PathsConfig:
        """Directory structure and path defaults sub-config."""
        return PathsConfig(**{
            name: getattr(self, name)
            for name in PathsConfig.model_fields
            if hasattr(self, name)
        })

    @property
    def effective_platform_urls(self) -> list[str]:
        """Merged list of all configured platform URLs (deduped, non-empty)."""
        urls: list[str] = []
        if self.platform_url:
            urls.append(self.platform_url)
        urls.extend(self.platform_urls)
        # Dedupe while preserving order
        seen: set[str] = set()
        result: list[str] = []
        for u in urls:
            normalized = u.rstrip("/")
            if normalized and normalized not in seen:
                seen.add(normalized)
                result.append(normalized)
        return result
