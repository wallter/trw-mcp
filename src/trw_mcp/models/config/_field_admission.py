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

This module and its ``_field_admission_registry`` sibling (which holds the
``ConfigAdmission`` record type and the explicit admissions) import NOTHING
from the rest of ``trw_mcp.models.config``, so neither can create an import
cycle with ``TRWConfig``; callers pass the live field names in. ``_defaults``
re-exports the public API as the FR05 facade.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from pydantic import BaseModel, ConfigDict

from trw_mcp.models.config._field_admission_consumers import (
    SELF_REFERENTIAL_WITH_READER_CEILING as SELF_REFERENTIAL_WITH_READER_CEILING,
)
from trw_mcp.models.config._field_admission_consumers import (
    ConsumerClaimReport as ConsumerClaimReport,
)
from trw_mcp.models.config._field_admission_consumers import (
    verify_consumer_claims as verify_consumer_claims,
)
from trw_mcp.models.config._field_admission_registry import (
    FIELD_ADMISSIONS as FIELD_ADMISSIONS,
)
from trw_mcp.models.config._field_admission_registry import (
    BudgetDecision as BudgetDecision,
)
from trw_mcp.models.config._field_admission_registry import (
    ConfigAdmission as ConfigAdmission,
)

#: NFR04 completion target for top-level configuration fields.
PUBLIC_FIELD_BUDGET: int = 370

#: Remediation guidance surfaced when a new public field lacks admission
#: metadata. Names both approved equivalent-tuning mechanisms (FR05 §Description
#: "Equivalent tuning uses nested policy or derived value").
EQUIVALENT_TUNING_GUIDANCE: str = (
    "Equivalent tuning must use a nested policy object or a derived value rather "
    "than a new top-level public field (PRD-CORE-218-FR05)."
)


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
#: activation, less the two more (``nudge_urgency_mode`` /
#: ``nudge_dedup_enabled``) removed in 2.0.0 by WD-02, and less 44 further
#: fields removed under PRD-CORE-291 (slice 2) per
#: ``.trw/compliance/config-field-consumers-baseline.json``, and less
#: ``phase_exposure_enabled`` (PRD-CORE-300 S11a deleted phase exposure),
#: ``retrieval_embedding_dim`` (unread since PRD-CORE-302 FR05) and
#: ``retrieval_embedding_model`` (the daemon's MEMORY_EMBEDDING_MODEL chooses) — each a net
#: surface REDUCTION, not a new admission. A field NOT in this
#: set and NOT in :data:`FIELD_ADMISSIONS` is a NEW public field that must pay
#: the full admission budget. This is a committed receipt, NOT derived from the
#: live model at runtime — deriving it would let any new field auto-admit and
#: defeat the gate.
LEGACY_ADMITTED_FIELDS: frozenset[str] = frozenset(
    """
aaref_version
access_count_utility_boost_cap
additional_repo_roots
agents_enabled
agents_md_enabled
agents_md_learning_injection
agents_md_learning_max
agents_md_learning_min_impact
ambiguity_rate_max
assertion_failure_penalty
assertion_stale_threshold_days
auto_checkpoint_enabled
auto_checkpoint_pre_compact
auto_checkpoint_tool_interval
auto_recall_enabled
auto_recall_max_results
auto_recall_max_tokens
auto_recall_min_score
auto_upgrade
backend_api_key
backend_url
boot_gc_deferred
build_check_coverage_min
build_check_enabled
build_gate_enforcement
ceremony_feedback_escalation_threshold
ceremony_feedback_escalation_window
ceremony_feedback_min_samples
ceremony_feedback_quality_threshold
ceremony_feedback_score_threshold
ceremony_mode
changelog_advisory_enabled
checkpoint_suggest_hours
claude_md_max_lines
cleanup_on_boot
code_index_exclude_dirs
code_index_include_extensions
code_index_max_file_bytes
compact_instructions_template
completeness_min
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
compliance_review_retention_days
context_dir
cross_model_provider
cross_model_review_enabled
cross_model_review_timeout_secs
ctx_isolation_enabled
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
embeddings_coverage_warn_threshold
embeddings_enabled
entries_dir
events_file
evidence_receipt_mode
extra_prd_categories
feedback
framework_md_enabled
framework_version
frameworks_dir
hooks_enabled
hybrid_rrf_k
impact_decay_half_life_days
impact_forced_distribution_enabled
impact_high_threshold_pct
impact_tier_critical_cap
impact_tier_high_cap
index_auto_sync_on_status_change
installation_id
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
learning_max_entries
learning_outcome_correlation_scope
learning_outcome_correlation_window_minutes
learning_promotion_impact
learning_recall_enabled
learning_repeated_op_threshold
learning_sharing_enabled
learning_utility_delete_threshold
learning_utility_prune_threshold
learnings_dir
llm_default_model
llm_usage_log_enabled
llm_usage_log_file
logs_dir
max_auto_lines
max_consolidated_tags
memory_cold_threshold_days
memory_consolidation_enabled
memory_consolidation_max_per_cycle
memory_consolidation_min_cluster
memory_consolidation_similarity_threshold
memory_hot_max_entries
memory_hot_ttl_days
memory_retention_days
memory_score_w1
memory_score_w2
memory_score_w3
meta_tune
meta_tune_enabled
migration_gate_enabled
model_family
nudge_budget_chars
nudge_density
nudge_enabled
nudge_messenger
nudge_pool_cooldown_after
nudge_pool_cooldown_calls
nudge_pool_cooldown_wall_clock_max_hours
nudge_variant
otel_capture_messages
otel_enabled
otel_semconv
parallelism_max
path_index_exclude_dirs
path_index_max_files
path_index_max_seconds
patterns_dir
phase_gate_enforcement
pin_ttl_hours
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
recall_internal_fields
recall_max_results
recall_receipt_max_entries
recall_user_tier_cap
recall_utility_lambda
receipts_dir
reflect_max_success_patterns
reflections_dir
response_format
reversion_rate_concerning
reversion_rate_elevated
review_confidence_threshold
review_gate_mode
review_mandate_advisory_enabled
risk_scaling_enabled
run_auto_close_enabled
run_stale_ttl_hours
run_staleness_grace_hours
run_staleness_hours
runs_root
scoring_default_days_unused
scout_blast_radius_threshold
scout_churn_commit_threshold
scout_enabled
scripts_dir
security
self_review_blocking
semantic_checks_enabled
session_start_recall_enabled
skills_enabled
source_human_utility_boost
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
timebox_hours
traceability_coverage_min
trust_crawl_boundary
trust_locked
trust_security_tags
trust_walk_boundary
trust_walk_sample_rate
trw_dir
update_channel
validation_density_weight
validation_draft_threshold
validation_implementation_readiness_weight
validation_review_threshold
validation_skeleton_threshold
validation_structure_weight
validation_traceability_weight
version_check_interval_seconds
wal_checkpoint_threshold_mb
wiring_gate_mode
""".split()  # noqa: SIM905 - compact immutable baseline keeps module below the LOC gate
)


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
