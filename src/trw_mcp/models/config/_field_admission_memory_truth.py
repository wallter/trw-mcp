"""Admission records for the PRD-CORE-244 memory-truth fields.

Belongs to the ``_field_admission_registry.py`` data table, which merges this
mapping into :data:`FIELD_ADMISSIONS`. Split out for the same reason the
instruction-write and auto-recall tables were: the registry grows once per new
public field, and five admissions landing at once would push it past the
module-size gate.

Imports nothing from the rest of ``trw_mcp.models.config`` except the record
type, so it cannot create an import cycle with ``TRWConfig``.
"""

from __future__ import annotations

from trw_mcp.models.config._field_admission_registry_types import ConfigAdmission

MEMORY_TRUTH_ADMISSIONS: dict[str, ConfigAdmission] = {
    "contradiction_penalty_reward": ConfigAdmission(
        field_name="contradiction_penalty_reward",
        owner="PRD-CORE-268 (explicit historical API compatibility)",
        consumer=(
            "trw_mcp.scoring._contradiction_penalty.apply_contradiction_penalty, called from "
            "trw_mcp.tools._delivery_helpers.check_delivery_gates (PRD-CORE-244-FR04, restored "
            "2026-09-11) and by explicit callers"
        ),
        default_rationale="Preserve the historical 0.4 API default; no measured usefulness claim.",
        interaction_analysis=(
            "Explicit invocation still updates historical Q observations. Default recall and "
            "maintenance do not call it; stored-evidence ranking uses assertion_failure_penalty. "
            "Historical Q is retained but excluded from default shared utility and tier scoring."
        ),
        deprecation_plan=(
            "Retain the explicit historical API. An automatic caller was restored DELIBERATELY on "
            "2026-09-11, not silently: PRD-CORE-268 removed FR04's recall call site over a LATENCY "
            "budget, which does not apply at the delivery gate where the verdict is already durable "
            "and nothing is on a hot path. The reward is additionally bounded to FRESH evidence "
            "(verification_cache_ttl_seconds) so a stale observation cannot decay an entry daily. "
            "Any FURTHER automatic caller still needs the same explicit justification."
        ),
        docs_pointer="docs/requirements-aare-f/prds/PRD-CORE-268.md",
        test_pointer="trw-mcp/tests/test_memory_attribution_retirement.py",
        budget_decision="legacy-admitted",
    ),
    "memory_decay_cutoff_days": ConfigAdmission(
        field_name="memory_decay_cutoff_days",
        owner="PRD-CORE-244-FR09 (importance decay acquires a caller and a matchable predicate)",
        consumer="trw_mcp.tools._deferred_steps_memory._step_memory_decay -> trw_memory._graph_decay.memory_decay_pass",
        default_rationale=(
            "90 days preserves the literal parameter default memory_decay_pass has always carried, so "
            "wiring it up is not also a silent retuning. It matches memory_cold_threshold_days, which is "
            "the other 'this entry has gone quiet' threshold in the same lifecycle."
        ),
        interaction_analysis=(
            "Compared against COALESCE(last_accessed_at, created_at), the same recency source the tier "
            "sweep reads, so decay and demotion cannot disagree about what 'unused' means. It is the only "
            "thing standing between apply_importance_boost (wired) and a one-directional ratchet: importance "
            "feeds compute_utility_score and prune-candidate selection, so a longer cutoff makes pruning "
            "strictly more conservative. Raising it to 3650 effectively disables decay without a flag."
        ),
        deprecation_plan=(
            "Retain. Removing it restores the hard-coded 90 and re-hides an operator decision about how "
            "long disuse must run before a record loses standing."
        ),
        docs_pointer="docs/requirements-aare-f/prds/PRD-CORE-244-memory-truth-invariants.md",
        test_pointer="trw-mcp/tests/test_tools_ceremony_deferred.py::TestMemoryDecayStep::test_decay_step_runs_and_lowers_importance",
        budget_decision="admitted",
    ),
    "memory_decay_batch_size": ConfigAdmission(
        field_name="memory_decay_batch_size",
        owner="PRD-CORE-244-FR09 (importance decay acquires a caller and a matchable predicate)",
        consumer="trw_mcp.tools._deferred_steps_memory._step_memory_decay -> trw_memory._graph_decay.memory_decay_pass",
        default_rationale=(
            "1000 preserves memory_decay_pass's own literal default and its internal hard clamp, so the "
            "wiring changes the caller and not the write volume. It bounds how long one pass holds the "
            "SQLite writer lock — the same failure mode auto-prune's deadline budget exists for."
        ),
        interaction_analysis=(
            "The pass returns 'remaining', so a store with more qualifying rows than the batch simply "
            "decays across successive deliveries rather than stalling one. Runs beside consolidate_cycle "
            "and TierManager.sweep in the same deferred step list and after them, so a row demoted this "
            "delivery is not also decayed in the same pass. The upper bound of 10,000 is above the "
            "function's own internal clamp, so raising it past that is a no-op rather than a foot-gun."
        ),
        deprecation_plan="Retain as the writer-lock bound; removal reinstates a hidden constant in a locked batch write.",
        docs_pointer="docs/requirements-aare-f/prds/PRD-CORE-244-memory-truth-invariants.md",
        test_pointer="trw-mcp/tests/test_tools_ceremony_deferred.py::TestMemoryDecayStep::test_decay_step_respects_configured_batch_size",
        budget_decision="admitted",
    ),
    "protection_tier_prune_discount": ConfigAdmission(
        field_name="protection_tier_prune_discount",
        owner="PRD-CORE-244-FR10 (protection_tier must protect on every destructive path)",
        consumer="trw_memory.lifecycle.protection.prune_threshold_multiplier (via scoring._recall_prune, state.analytics.dedup, state._tier_sweep)",
        default_rationale=(
            "{critical: 0.25, high: 0.5, normal: 1.0, low: 1.5} makes 'normal' the identity, so an "
            "unmarked entry is pruned exactly as it is today and the table cannot be blamed for a change "
            "nobody asked for. The 4x spread between critical and normal is the same order of magnitude as "
            "the tier vocabulary implies; protected and permanent are deliberately ABSENT because they are "
            "exempt outright, and listing them would suggest a large-enough utility drop could still remove them."
        ),
        interaction_analysis=(
            "Multiplies learning_utility_prune_threshold and learning_utility_delete_threshold at "
            "nomination time, so it composes with those rather than replacing them; an operator who "
            "lowers the thresholds still gets proportional tier protection. It governs AUTOMATIC removal "
            "only — trw_forget is untouched, because an operator naming an entry is entitled to remove it. "
            "Bounded [0.0, 4.0] per value: 0.0 is a full exemption expressed as a discount, and the ceiling "
            "stops a config from making a tier easier to remove than 'low'."
        ),
        deprecation_plan=(
            "Retain. Removing it returns protection_tier to what it was for its entire life: a word in a "
            "public tool docstring promising a guarantee that no code implemented."
        ),
        docs_pointer="docs/requirements-aare-f/prds/PRD-CORE-244-memory-truth-invariants.md",
        test_pointer=(
            "trw-mcp/tests/test_scoring_edge_cases_ranking_pruning.py::"
            "TestProtectionTierProtects::test_protection_tier_exempts_permanent_from_auto_prune"
        ),
        budget_decision="admitted",
    ),
    "state_learning_default_ttl_days": ConfigAdmission(
        field_name="state_learning_default_ttl_days",
        owner="PRD-CORE-244-FR05 (state-asserting learnings are offered a window, never given one)",
        consumer="trw_mcp.tools._state_assertion_hint.propose_validity_window (via tools._learn_impl.execute_learn)",
        default_rationale=(
            "{incident: 90, hypothesis: 30, workaround: 180} follows the per-type decay half-lives already "
            "in scoring/_decay.py: a hypothesis is meant to be validated or discarded quickly, a workaround "
            "outlives the incident it patches. convention and pattern are absent BY DESIGN — they record "
            "invariants — and their absence, not a separate flag, is how a type opts out."
        ),
        interaction_analysis=(
            "Feeds advisory text only. execute_learn does not modify the expires argument it forwards, so "
            "no configuration of this table can cause a window to be written; the author must call "
            "trw_learn_update. A written expires is then honoured by BOTH ranking paths (scoring._decay's "
            "utility floor and trw_memory.retrieval.validity_prior._is_open_at), which FR05 made agree. "
            "Adding a key here offers a window for a new type; it never retroactively stamps one, because "
            "the classifier runs at write time only."
        ),
        deprecation_plan=(
            "Retain while expires remains off the public trw_learn signature (removed 2026-07-28). If it "
            "returns, this becomes the default the parameter documents."
        ),
        docs_pointer="docs/requirements-aare-f/prds/PRD-CORE-244-memory-truth-invariants.md",
        test_pointer="trw-mcp/tests/test_state_assertion_hint.py::TestProposeValidityWindow::test_invariant_types_are_never_offered_a_window",
        budget_decision="admitted",
    ),
    "anchor_validity_verified_floor": ConfigAdmission(
        field_name="anchor_validity_verified_floor",
        owner="PRD-CORE-244-FR03 (a verification verdict gains a positive value)",
        consumer="trw_mcp.tools._verification_pass._apply_verdict (via run_verification_pass, called by "
        "tools._recall_assertion_verification._verify_assertions and tools._maintain_verify.run_maintain_verify)",
        default_rationale=(
            "1.0 is the strictest reading and the only one defensible without data: 'verified' is a claim "
            "the pass makes on the operator's behalf, so a single anchor that has drifted withholds it. "
            "Anything lower asserts that partial anchor drift is still a clean bill of health, which is "
            "precisely the class of default this PRD removes."
        ),
        interaction_analysis=(
            "Read once per entry, only after the anchor score has actually been recomputed — when anchors "
            "could not be scored at all the floor is not consulted and no positive verdict is reached. It "
            "cannot manufacture a 'stale' verdict either: staleness is decided solely by the "
            "assertion_stale_threshold_days rule, so the floor can only withhold 'verified', never convict."
        ),
        deprecation_plan=(
            "Retain; it is the sole knob standing between a recomputed anchor score and a positive verdict. "
            "Hardcoding it would reintroduce the magic number FR03 exists to type."
        ),
        docs_pointer="docs/requirements-aare-f/prds/PRD-CORE-244-memory-truth-invariants.md",
        test_pointer=(
            "trw-mcp/tests/test_trw_recall_verification.py::test_anchor_drift_below_floor_withholds_verified"
        ),
        budget_decision="admitted",
    ),
    "verification_cache_ttl_seconds": ConfigAdmission(
        field_name="verification_cache_ttl_seconds",
        owner="PRD-CORE-268-FR02 (qualified stored evidence, not inline verification)",
        consumer="trw_mcp.tools._recall_assertion_verification._verify_assertions -> _stored_claim_evidence.stored_claim_evidence",
        default_rationale="Retain the existing window as evidence-age qualification, not proof about the current tree.",
        interaction_analysis=(
            "UTC age is fresh only for 0 <= age < TTL with TTL > 0. Invalid/future/naive dates are unknown. "
            "Per-assertion dates own assertion evidence; aggregate verdicts cannot overwrite them. "
            "Expiry does not trigger a scan or erase a dated observed failure."
        ),
        deprecation_plan="Retain the existing input name without adding a second freshness switch.",
        docs_pointer="docs/requirements-aare-f/prds/PRD-CORE-268.md",
        test_pointer="trw-mcp/tests/test_core268_recall_evidence.py",
        budget_decision="admitted",
    ),
    "recall_verification_budget_ms": ConfigAdmission(
        field_name="recall_verification_budget_ms",
        owner="PRD-CORE-268-FR02 (retires PRD-CORE-267 inline verification)",
        consumer="None: compatibility parsing only; no runtime verification consumer",
        default_rationale="Keep old configuration files readable; this value no longer changes execution.",
        interaction_analysis="Neither recall nor maintain-verify receives a runtime deadline from this input.",
        deprecation_plan="Remove after explicit client-configuration migration; do not advertise a live budget.",
        docs_pointer="docs/requirements-aare-f/prds/PRD-CORE-268.md",
        test_pointer="trw-mcp/tests/test_recall_verification_budget.py",
        budget_decision="legacy-admitted",
    ),
    "anchor_shared_set_migration_threshold": ConfigAdmission(
        field_name="anchor_shared_set_migration_threshold",
        owner="PRD-CORE-267-FR03 (the one-off shared-anchor-set migration)",
        consumer="trw_mcp.tools._anchor_migration.clear_shared_anchor_sets (via the maintain-verify CLI)",
        default_rationale=(
            "Measured on the development store: 2,006 anchored rows across 370 distinct anchor sets, of "
            "which 344 hold seven members or fewer. The observed size distribution is empty at nine, so 10 "
            "sits below every fabricated cluster (the largest holds 381 entries) and above every plausible "
            "case of several learnings genuinely concerning the same symbols. Another store's distribution "
            "will differ, which is exactly why this is a field rather than a literal."
        ),
        interaction_analysis=(
            "Read once per migration invocation and only by an operator-run CLI — no server path consults "
            "it, so no value here can change tool behaviour. Raising it narrows the selection; lowering it "
            "widens it, and the dry-run default means the widened selection is reported before anything is "
            "written. Independent of anchor_validity_verified_floor: the migration clears anchors outright "
            "rather than scoring them, so a cleared entry is subsequently unscored (validity None) rather "
            "than scored zero."
        ),
        deprecation_plan=(
            "Retire once every store predating PRD-CORE-267's derivation fix has been migrated. The "
            "migration is idempotent, so leaving the field in place costs nothing."
        ),
        docs_pointer="docs/requirements-aare-f/prds/PRD-CORE-267-session-scoped-learning-anchors.md",
        test_pointer="trw-mcp/tests/test_anchor_migration.py::test_apply_clears_and_is_idempotent",
        budget_decision="admitted",
    ),
}

__all__ = ["MEMORY_TRUTH_ADMISSIONS"]
