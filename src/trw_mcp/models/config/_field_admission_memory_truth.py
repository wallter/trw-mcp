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
        owner="PRD-CORE-244-FR04 (contradiction as the missing negative signal)",
        consumer="trw_mcp.scoring._correlation.apply_contradiction_penalty (via tools._recall_assertion_verification._verify_assertions)",
        default_rationale=(
            "0.4 sits between the existing tests_failed magnitude of 0.3 and phase_gate_failed at 0.5 in "
            "scoring/_reward_resolution.py. That is a PLACEMENT argument, not a calibrated one: no prior "
            "data exists because the signal did not exist — outcome.failing was computed on every recall "
            "and discarded. OQ-02 records the calibration debt rather than dressing the number as measured."
        ),
        interaction_analysis=(
            "Applied through the SAME _update_entry_q_values / _update_entry_history pair process_outcome "
            "uses, with discount=1.0 (no recency decay: a contradiction found NOW is about the entry, not "
            "about how recently it was recalled). It therefore composes additively with the session-wide "
            "reward rather than replacing it, and is bounded by q_learning_rate exactly as every other "
            "reward is. At 0.0 the penalty is a no-op observation, which still increments q_observations — "
            "so it cannot be used to disable the signal while pretending it ran."
        ),
        deprecation_plan=(
            "Retain until explicit feedback has a non-zero population. helpful_count was measured at 0 of "
            "9,366 rows, so removing this leaves the bandit with no per-entry negative channel at all."
        ),
        docs_pointer="docs/requirements-aare-f/prds/PRD-CORE-244-memory-truth-invariants.md",
        test_pointer=(
            "trw-mcp/tests/test_recall_assertion_verification.py::"
            "TestContradictionPenalty::test_failing_assertion_applies_contradiction_penalty"
        ),
        budget_decision="admitted",
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
        owner="PRD-CORE-244-FR03 (the durable timestamp that makes the pass cacheable)",
        consumer="trw_mcp.tools._verification_cache.cached_verdict (via tools._recall_assertion_verification._verify_assertions)",
        default_rationale=(
            "3600s bounds how stale a reused verdict can be to one hour, which is shorter than any "
            "plausible unattended session, while removing the repeated per-recall filesystem scan that "
            "dominated the pass. 0 disables reuse entirely and every recall re-verifies."
        ),
        interaction_analysis=(
            "Consulted only when verification_checked_at is non-empty, so it can never apply to an entry no "
            "pass has examined. A hit skips run_verification_pass AND its persist, so it also suppresses "
            "the FR04 contradiction penalty for that entry within the window — the penalty is applied once "
            "per window rather than once per recall, which is the same once-per-day intent FR04 already "
            "carries. Independent of assertion_stale_threshold_days, which decides the verdict rather than "
            "how long one is reused."
        ),
        deprecation_plan=(
            "Retain while recall runs verification inline. Removing it restores a full filesystem "
            "verification on every recall for every candidate entry."
        ),
        docs_pointer="docs/requirements-aare-f/prds/PRD-CORE-244-memory-truth-invariants.md",
        test_pointer="trw-mcp/tests/test_trw_recall_verification.py::test_warm_verdict_is_reused_within_ttl",
        budget_decision="admitted",
    ),
}

__all__ = ["MEMORY_TRUTH_ADMISSIONS"]
