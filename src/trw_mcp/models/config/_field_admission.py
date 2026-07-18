"""PRD-CORE-218-FR05: public configuration admission budget.

Every PUBLIC ``TRWConfig`` field must carry admission metadata — owner,
consumer, default rationale, interaction analysis, deprecation plan, docs
pointer, test pointer, and a budget decision — or the config contract test
(``tests/test_config_fields.py::test_prd_core_218_fr05``) fails the build.

Two admission classes exist:

- ``legacy-admitted``: the frozen baseline census of every field that was
  already public at the PRD-CORE-218 implementation commit. These are admitted
  as-is, pending the NFR04 consolidation to <=370 top-level fields, but no new
  dependents may be added.
- ``admitted``: a NEW field added after CORE-218. It requires a FULL, explicit
  :class:`ConfigAdmission` entry in :data:`FIELD_ADMISSIONS` (or supplied via
  ``verify_field_admissions(..., extra_admissions=...)``). A new public field
  without such an entry is rejected — equivalent tuning must use a nested
  policy object or a derived value instead of a new top-level public field.

This module imports NOTHING from the rest of ``trw_mcp.models.config`` so it
can never create an import cycle with ``TRWConfig``; callers pass the live
field names in.  ``_defaults`` re-exports the public API as the FR05 facade.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Literal

from pydantic import BaseModel, ConfigDict

BudgetDecision = Literal["admitted", "legacy-admitted", "rejected", "deferred"]

#: NFR04 completion target for top-level configuration fields.
PUBLIC_FIELD_BUDGET: int = 370

#: Remediation guidance surfaced when a new public field lacks admission
#: metadata. Names both approved equivalent-tuning mechanisms (FR05 §Description
#: "Equivalent tuning uses nested policy or derived value").
EQUIVALENT_TUNING_GUIDANCE: str = (
    "Equivalent tuning must use a nested policy object or a derived value rather "
    "than a new top-level public field (PRD-CORE-218-FR05)."
)


class ConfigAdmission(BaseModel):
    """Full admission record for one public configuration field."""

    model_config = ConfigDict(frozen=True)

    field_name: str
    owner: str
    consumer: str
    default_rationale: str
    interaction_analysis: str
    deprecation_plan: str
    docs_pointer: str
    test_pointer: str
    budget_decision: BudgetDecision


class FieldAdmissionReport(BaseModel):
    """Typed outcome of a public-field admission audit."""

    model_config = ConfigDict(frozen=True)

    total_fields: int
    admitted_count: int
    missing: tuple[str, ...]
    budget_target: int
    over_budget: int
    budget_decision: BudgetDecision
    ok: bool
    message: str


def _legacy_admission(field_name: str) -> ConfigAdmission:
    """Build the shared ``legacy-admitted`` record for a baseline field."""
    return ConfigAdmission(
        field_name=field_name,
        owner="PRD-CORE-218 baseline census",
        consumer="TRWConfig",
        default_rationale="Pre-CORE-218 public field; admitted as frozen baseline pending consolidation.",
        interaction_analysis=(
            "Legacy field; interactions not individually re-analyzed. Consolidation candidate under "
            "NFR04 (target <=370 top-level fields)."
        ),
        deprecation_plan="Retain until the NFR04 consolidation wave; no new dependents may be added.",
        docs_pointer="docs/requirements-aare-f/prds/PRD-CORE-218.md",
        test_pointer="trw-mcp/tests/test_config_fields.py::test_prd_core_218_fr05",
        budget_decision="legacy-admitted",
    )


#: Frozen baseline census of the public ``TRWConfig`` fields admitted at the
#: PRD-CORE-218 implementation commit, less the two legacy CORE-125 fields
#: (``tool_exposure_mode`` / ``tool_exposure_list``) removed at FR03/FR04
#: activation — a net surface REDUCTION, not a new admission. A field NOT in this
#: set and NOT in :data:`FIELD_ADMISSIONS` is a NEW public field that must pay
#: the full admission budget. This is a committed receipt, NOT derived from the
#: live model at runtime — deriving it would let any new field auto-admit and
#: defeat the gate.
LEGACY_ADMITTED_FIELDS: frozenset[str] = frozenset(
    """
aaref_version
access_count_utility_boost_cap
adaptation_auto_approve_threshold
adaptation_enabled
additional_repo_roots
agents_enabled
agents_md_enabled
agents_md_learning_injection
agents_md_learning_max
agents_md_learning_min_impact
ambiguity_rate_max
api_fuzz_base_url
api_fuzz_enabled
api_fuzz_level
api_fuzz_timeout_secs
assertion_failure_penalty
assertion_stale_threshold_days
atdd_enabled
audit_pattern_promotion_threshold
auto_checkpoint_enabled
auto_checkpoint_pre_compact
auto_checkpoint_tool_interval
auto_recall_enabled
auto_recall_max_results
auto_recall_max_tokens
auto_recall_min_score
auto_simplify_enabled
auto_upgrade
backend_api_key
backend_url
boot_gc_deferred
build_check_coverage_min
build_check_enabled
build_check_mypy_args
build_check_pytest_args
build_check_pytest_cmd
build_check_timeout_secs
build_freshness_window_secs
build_gate_enforcement
ceremony_alert_consecutive
ceremony_alert_threshold
ceremony_feedback_escalation_threshold
ceremony_feedback_escalation_window
ceremony_feedback_min_samples
ceremony_feedback_quality_threshold
ceremony_feedback_score_threshold
ceremony_mode
changelog_advisory_enabled
checkpoint_secs
checkpoint_suggest_hours
checkpoints_file
claude_md_max_lines
cleanup_on_boot
code_index_enabled
code_index_exclude_dirs
code_index_include_extensions
code_index_max_file_bytes
comment_check_enabled
commit_fr_trailer_enabled
compact_after_turns
compact_instructions_template
completeness_min
completion_hooks_blocking
complexity_hard_override_threshold
complexity_tier_comprehensive
complexity_tier_minimal
complexity_weight_architecture_change
complexity_weight_cross_cutting
complexity_weight_external_integration
complexity_weight_files_affected_max
complexity_weight_large_refactoring
complexity_weight_novel_patterns
compliance_changelog_filename
compliance_dir
compliance_history_file
compliance_long_session_event_threshold
compliance_pass_threshold
compliance_review_retention_days
compliance_strictness
compliance_warning_threshold
confidence_threshold
consensus_quorum
consistency_validation_min
context_dir
cross_model_provider
cross_model_review_block_on_critical
cross_model_review_enabled
cross_model_review_timeout_secs
ctx_isolation_enabled
debt_actionable_threshold
debt_auto_promote_threshold
debt_budget_critical_ratio
debt_budget_high_ratio
debt_decay_assessment_rate
debt_decay_base_score
debt_decay_daily_rate
debt_default_wave_size
debt_id_prefix
debt_initial_decay_score
debt_registry_filename
debug
dedup_enabled
dedup_merge_threshold
dedup_skip_threshold
deferred_batch_max_seconds
deferred_lock_stale_seconds
deferred_step_max_seconds
deliver_gate_mode
deliver_gate_task_type_overrides
deliver_graph_backfill_deadline_seconds
deliver_graph_backfill_enabled
delivery_busy_timeout_ms
delivery_operations_mode
delivery_queue_depth_max
delivery_stale_lease_minutes
density_weight_default
density_weight_functional_requirements
density_weight_problem_statement
density_weight_traceability_matrix
dispatch_default_client
dispatch_default_models
dispatch_default_read_only
dispatch_default_timeout_s
dispatch_enabled_clients
dispatch_role_client
dry_check_enabled
dry_check_min_block_size
embeddings_auto_backfill_on_low_coverage
embeddings_coverage_warn_threshold
embeddings_enabled
enforcement_variant
entries_dir
events_file
evidence_receipt_mode
external_store_recall_cap
extra_prd_categories
extra_read_stores
feedback
finding_dedup_threshold
findings_dir
findings_entries_dir
findings_registry_file
framework_md_enabled
framework_overhead_threshold
framework_version
frameworks_dir
gate_architecture_score_penalty
gate_convergence_epsilon
gate_critic_overhead_multiplier
gate_debate_context_multiplier
gate_default_type
gate_early_stop_confidence
gate_escalation_enabled
gate_max_rounds
gate_max_total_judges
gate_strategy
gate_tokens_per_1k_chars
gate_tokens_per_vote
grooming_max_iterations
grooming_partial_density_threshold
grooming_placeholder_density_threshold
grooming_research_scope
grooming_target_completeness
hooks_enabled
hybrid_bm25_candidates
hybrid_reranking_enabled
hybrid_rrf_importance_alpha
hybrid_rrf_k
hybrid_search_candidate_pool_size
hybrid_vector_candidates
impact_decay_half_life_days
impact_forced_distribution_enabled
impact_high_threshold_pct
impact_tier_critical_cap
impact_tier_high_cap
incremental_validation_enabled
index_auto_sync_on_status_change
installation_id
instruction_external_filename
instruction_externalize
instruction_size_gate_mode
intel_cache_enabled
intel_cache_ttl_seconds
knowledge_jaccard_threshold
knowledge_min_cluster_size
knowledge_output_dir
knowledge_sync_threshold
learning_auto_prune_cap
learning_auto_prune_max_seconds
learning_auto_prune_min_interval_hours
learning_auto_prune_on_deliver
learning_decay_half_life_days
learning_decay_use_exponent
learning_injection_preview_chars
learning_max_entries
learning_outcome_correlation_scope
learning_outcome_correlation_window_minutes
learning_outcome_history_cap
learning_promotion_impact
learning_prune_age_days
learning_recall_enabled
learning_repeated_op_threshold
learning_sharing_enabled
learning_utility_delete_threshold
learning_utility_prune_threshold
learnings_dir
llm_default_model
llm_enabled
llm_usage_log_enabled
llm_usage_log_file
llm_utility_filter_enabled
logs_dir
max_adaptations_per_run
max_audit_cycles
max_auto_lines
max_child_depth
max_cluster_size
max_consolidated_tags
max_research_waves
max_shards_added_per_adaptation
max_total_waves
mcp_server_instructions_enabled
memory_cold_threshold_days
memory_consolidation_enabled
memory_consolidation_interval_days
memory_consolidation_max_per_cycle
memory_consolidation_min_cluster
memory_consolidation_similarity_threshold
memory_hot_max_entries
memory_hot_ttl_days
memory_retention_days
memory_score_w1
memory_score_w2
memory_score_w3
memory_store_path
meta_tune
meta_tune_enabled
migration_gate_enabled
min_shards_floor
min_shards_target
minimal_after_turns
model_family
mutation_critical_paths
mutation_enabled
mutation_experimental_paths
mutation_threshold
mutation_threshold_critical
mutation_threshold_experimental
mutation_timeout_secs
nudge_budget_chars
nudge_dedup_enabled
nudge_density
nudge_enabled
nudge_messenger
nudge_pool_cooldown_after
nudge_pool_cooldown_calls
nudge_pool_cooldown_wall_clock_max_hours
nudge_pool_weight_ceremony
nudge_pool_weight_context
nudge_pool_weight_learnings
nudge_pool_weight_workflow
nudge_urgency_mode
nudge_variant
observation_masking
otel_capture_messages
otel_enabled
otel_endpoint
otel_semconv
outcome_weight_learning_rate
outcome_weight_p0_defects
outcome_weight_rework
outcome_weight_velocity
parallelism_max
path_index_exclude_dirs
path_index_max_files
path_index_max_seconds
patterns_dir
pause_after_compaction
phase_cap_deliver
phase_cap_implement
phase_cap_plan
phase_cap_research
phase_cap_review
phase_cap_validate
phase_exposure_enabled
phase_gate_enforcement
phase_transition_withhold_rate
pin_ttl_hours
pipeline_health_bandit_probe_enabled
pipeline_health_bandit_stale_days
pipeline_health_gate_enabled
pipeline_health_gate_failure_threshold
pipeline_health_gate_graph_min_corpus
pipeline_health_gate_stale_hours
platform_api_key
platform_telemetry_enabled
platform_url
platform_urls
prd_min_content_density
prd_required_status_for_implement
prd_transition_gate
prd_validate_budget_seconds
prd_validation_cache_maintenance_interval
prd_validation_cache_max_entries
prd_validation_cache_max_entry_bytes
prd_validation_cache_max_total_bytes
prds_relative_path
pricing_table_path
profile_system_enabled
provenance_enabled
proximal_reward_weight
q_cold_start_threshold
q_learning_rate
q_recurrence_bonus
recall_compact_fields
recall_internal_fields
recall_max_results
recall_receipt_max_entries
recall_user_tier_cap
recall_utility_lambda
receipts_dir
reflect_max_positive_learnings
reflect_max_success_patterns
reflect_q_value_threshold
reflect_sequence_lookback
reflections_dir
response_format
retrieval_embedding_dim
retrieval_embedding_model
retrieval_fallback_enabled
reversion_rate_concerning
reversion_rate_elevated
review_confidence_threshold
review_gate_mode
review_mandate_advisory_enabled
risk_scaling_enabled
run_archive_hours
run_auto_close_age_days
run_auto_close_enabled
run_stale_ttl_hours
run_staleness_grace_hours
run_staleness_hours
runs_root
scoring_default_days_unused
scoring_error_fallback_reward
scoring_error_keywords
scoring_recency_discount_floor
scout_blast_radius_threshold
scout_churn_commit_threshold
scout_enabled
scout_max_mode3_rate
scratch_dir
scripts_dir
security
security_check_enabled
self_review_blocking
semantic_checks_enabled
session_start_defer_under_writer_pressure
session_start_recall_enabled
session_start_recent_bypass_days
session_start_recent_bypass_min_impact
session_start_writer_pressure_threshold
simplifier_backup_dir
simplifier_verification_timeout_secs
simplifier_wave_size
skill_active_cap
skill_contribution_cold_start
skill_contribution_half_life_days
skill_duplicate_max_skills
skill_duplicate_similarity_threshold
skill_retirement_floor
skill_retirement_windows
skill_surface_tracking_enabled
skills_enabled
source_human_utility_boost
source_package_name
source_package_path
sprint_code_simplifier_wave_size
sprint_commit_pattern
sprint_integration_branch_pattern
strict_input_criteria
sub_claude_md_max_lines
sync_health_failure_threshold
sync_health_stale_hours
sync_interval_seconds
sync_pull_timeout_seconds
sync_push_batch_size
sync_push_timeout_seconds
target_platforms
task_root
team_sync_enabled
telemetry
telemetry_enabled
telemetry_file
templates_dir
test_map_filename
test_skeleton_dir
tests_relative_path
timebox_hours
tool_descriptions_variant
traceability_coverage_min
trust_crawl_boundary
trust_locked
trust_security_tags
trust_walk_boundary
trust_walk_sample_rate
trw_dir
update_channel
user_tier_enabled
validation_density_weight
validation_draft_threshold
validation_ears_weight
validation_fk_optimal_max
validation_fk_optimal_min
validation_implementation_readiness_weight
validation_readability_weight
validation_review_threshold
validation_skeleton_threshold
validation_smell_weight
validation_structure_weight
validation_traceability_weight
velocity_alert_min_runs
velocity_alert_r_squared_min
velocity_confounder_jump_ratio
velocity_effective_q_threshold
velocity_history_max_entries
velocity_sign_test_alpha
velocity_stable_threshold
version_check_interval_seconds
wal_checkpoint_threshold_mb
wiring_gate_mode
worktree_dir
""".split()  # noqa: SIM905 - compact immutable baseline keeps module below the LOC gate
)

#: Explicit full-metadata admissions for public fields added by PRD-CORE-218
#: itself (or later). Every current field outside the frozen legacy baseline
#: MUST have a complete :class:`ConfigAdmission` entry here or the gate rejects
#: it. ``tool_resolution_mode`` is FR04's standard/all selector — the SOLE tool
#: exposure authority (the legacy CORE-125 tool_exposure_mode/list fields and
#: their TOOL_PRESETS vocabulary were removed at activation).
FIELD_ADMISSIONS: dict[str, ConfigAdmission] = {
    "tool_resolution_mode": ConfigAdmission(
        field_name="tool_resolution_mode",
        owner="PRD-CORE-218-FR04",
        consumer="trw_mcp.middleware.surface_authority (SurfaceAuthorityMiddleware) via resolve_tool_surface",
        default_rationale=(
            "Defaults to 'standard' so the bounded kernel+task-pack surface is the default; "
            "'all' is an explicit operator selection (FR04)."
        ),
        interaction_analysis=(
            "Two-valued selector consumed by the manifest tool-surface resolver and enforced by "
            "SurfaceAuthorityMiddleware; 'all' remains subject to phase-exposure/policy/authorization "
            "(NFR02). It is now the sole exposure authority — the legacy tool_exposure_mode was removed."
        ),
        deprecation_plan="Retain; the sole tool-exposure authority after the CORE-125 preset filter removal.",
        docs_pointer="docs/requirements-aare-f/prds/PRD-CORE-218.md#prd-core-218-fr04",
        test_pointer="trw-mcp/tests/test_tool_presets.py::test_prd_core_218_fr04",
        budget_decision="admitted",
    ),
    "telemetry_log_max_bytes": ConfigAdmission(
        field_name="telemetry_log_max_bytes",
        owner="PRD-CORE-181-FR04",
        consumer="trw_mcp.telemetry.publisher.rotate_pipeline_telemetry_log",
        default_rationale=(
            "Defaults to 10 MiB (10485760), matching the per-JSONL rotation threshold used "
            "elsewhere in state/; promotes the former TRW_TELEMETRY_LOG_MAX_BYTES env-only knob to a "
            "typed field so the bound is validated (gt=0) and configurable via config.yaml."
        ),
        interaction_analysis=(
            "Single-purpose size threshold read only by the deliver-step telemetry-log rotation "
            "maintenance path; no interaction with other config fields. The TRW_TELEMETRY_LOG_MAX_BYTES "
            "env var still overrides it via BaseSettings env_prefix precedence."
        ),
        deprecation_plan="Retain; the sole FR04 rotation knob. Env override folds in via BaseSettings.",
        docs_pointer="docs/requirements-aare-f/prds/PRD-CORE-181.md",
        test_pointer=(
            "trw-mcp/tests/test_jsonl_rotation_parity.py::test_telemetry_log_default_threshold_reads_config_field"
        ),
        budget_decision="admitted",
    ),
}


def build_field_admissions(
    extra_admissions: Mapping[str, ConfigAdmission] | None = None,
) -> dict[str, ConfigAdmission]:
    """Return the full admission map: legacy baseline + explicit + ``extra``."""
    admissions: dict[str, ConfigAdmission] = {name: _legacy_admission(name) for name in LEGACY_ADMITTED_FIELDS}
    admissions.update(FIELD_ADMISSIONS)
    if extra_admissions:
        admissions.update(extra_admissions)
    return admissions


def verify_field_admissions(
    field_names: Iterable[str],
    *,
    extra_admissions: Mapping[str, ConfigAdmission] | None = None,
) -> FieldAdmissionReport:
    """Audit ``field_names`` against the admission registry (FR05 gate).

    A field is admitted iff it is in the frozen legacy baseline, in
    :data:`FIELD_ADMISSIONS`, or in ``extra_admissions``. Any field without an
    admission record is reported as ``missing`` and the report is not ``ok`` —
    the config contract test turns a non-ok report into a build failure.
    """
    admissions = build_field_admissions(extra_admissions)
    names = tuple(sorted(field_names))
    missing = tuple(name for name in names if name not in admissions)
    over_budget = max(len(names) - PUBLIC_FIELD_BUDGET, 0)
    ok = not missing

    if ok:
        budget_note = f"over budget by {over_budget}" if over_budget else "within budget"
        message = (
            f"{len(names)} public fields admitted "
            f"({len(FIELD_ADMISSIONS)} explicit, {len(LEGACY_ADMITTED_FIELDS)} legacy-admitted). "
            f"NFR04 budget target <= {PUBLIC_FIELD_BUDGET}; currently {len(names)} ({budget_note})."
        )
    else:
        message = (
            f"{len(missing)} public config field(s) lack admission metadata: {list(missing)}. "
            "Every public field requires owner, consumer, default rationale, interaction analysis, "
            "deprecation plan, docs pointer, test pointer, and a budget decision "
            f"(PRD-CORE-218-FR05). {EQUIVALENT_TUNING_GUIDANCE}"
        )

    return FieldAdmissionReport(
        total_fields=len(names),
        admitted_count=len(names) - len(missing),
        missing=missing,
        budget_target=PUBLIC_FIELD_BUDGET,
        over_budget=over_budget,
        budget_decision="legacy-admitted" if ok else "rejected",
        ok=ok,
        message=message,
    )
