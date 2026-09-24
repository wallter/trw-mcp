"""Admission records for the PRD-CORE-244 memory-truth fields and PRD-CORE-280 store selection.

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
    # memory_decay_cutoff_days / memory_decay_batch_size retired in batch 23b:
    # PRD-CORE-280 slice e3 moved decay onto a daemon RPC (``maintain()``) that
    # takes no per-call parameters, so trw-mcp has had no way to reach either
    # knob since e3 landed. See config-retired-keys.json.
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
            "trw_learn's update mode. A written expires is then honoured by BOTH ranking paths (scoring._decay's "
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
        consumer="trw_mcp.tools._maintain_verify.run_maintain_verify_for_project (passes it to "
        "trw_memory.lifecycle.verification_pass.run_maintain_verify, whose _apply_verdict applies it)",
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
    # PRD-CORE-280 FR01: which store serves this checkout.
    "project_namespace": ConfigAdmission(
        field_name="project_namespace",
        owner="PRD-CORE-280-FR01",
        consumer="trw_mcp.state._store_selection.selected_store",
        default_rationale=(
            "Defaults to empty, meaning the checkout is unmigrated and keeps its own .trw/memory store "
            "(behaviour unchanged). ``memory migrate`` (PRD-CORE-298) writes the stable project namespace "
            "here; a set value is the only signal that the rows moved to the daemon store."
        ),
        interaction_analysis=(
            "Read only by selected_store, the one resolver every memory read and write goes through. "
            "A set value never falls back to the project file: until the daemon store lands, memory "
            "tools raise StoreUnavailableError, so a migrated checkout cannot silently fork its rows."
        ),
        deprecation_plan="Retained: it is the migration marker for the lifetime of the daemon store.",
        docs_pointer="docs/requirements-aare-f/prds/PRD-CORE-280.md",
        test_pointer=(
            "trw-mcp/tests/test_store_selection.py::test_a_pinned_checkout_fails_closed_and_never_opens_the_project_file"
        ),
        budget_decision="admitted",
    ),
}

__all__ = ["MEMORY_TRUTH_ADMISSIONS"]
