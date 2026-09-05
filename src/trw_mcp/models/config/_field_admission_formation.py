"""Admission records for the PRD-CORE-265 formation fields.

Its own table module rather than five more entries in
``_field_admission_registry.py``: that file sits close to the 350 effective-LOC
gate, so every domain that admits fields brings its own table and the registry
merges it (the pattern PRD-FIX-123 established).

Imports nothing from the rest of ``trw_mcp.models.config`` except the record
type, so it cannot create an import cycle with ``TRWConfig``.
"""

from __future__ import annotations

from trw_mcp.models.config._field_admission_registry_types import ConfigAdmission

FORMATION_ADMISSIONS: dict[str, ConfigAdmission] = {
    "formation_ownership_enforcement": ConfigAdmission(
        field_name="formation_ownership_enforcement",
        owner="PRD-CORE-265-FR09",
        consumer="scripts/check_formation_ownership.py -> trw_mcp.formation.owner_of (via scripts/git-commit-scoped.sh)",
        default_rationale=(
            "'refuse'. The commit boundary is the only ownership surface every supported client "
            "crosses, so it is the PRIMARY enforcement point and defaults to enforcing. A default "
            "of 'warn' would make ownership advisory everywhere at once, which is the state this "
            "PRD exists to leave."
        ),
        interaction_analysis=(
            "Read once per scoped commit, only when a formation manifest resolves for the calling "
            "run; with no formation active the check is a no-op and exit status is unchanged. "
            "'warn' prints the identical message to stderr and lets the commit proceed, which is "
            "the documented rollback for a legitimate cross-cutting edit. It does not interact "
            "with formation_hook_ownership_mode: the hook is advisory in every configuration, so "
            "no combination of the two can make the hook the enforcing surface."
        ),
        deprecation_plan=(
            "Retain. Removing it would either hard-wire enforcement (no rollback for a false "
            "refusal) or delete it (no enforcement on the one client-neutral boundary)."
        ),
        docs_pointer=("docs/requirements-aare-f/prds/PRD-CORE-265-formation-manifest-ownership-join-brief-status.md"),
        test_pointer="trw-mcp/tests/test_git_commit_workflow.py::test_scoped_commit_refuses_foreign_owned_path",
        budget_decision="admitted",
    ),
    "formation_hook_ownership_mode": ConfigAdmission(
        field_name="formation_hook_ownership_mode",
        owner="PRD-CORE-265-FR10",
        consumer="trw-mcp/src/trw_mcp/data/hooks/lib-intent-guard.sh -> trw_mcp.formation.owner_of",
        default_rationale=(
            "'warn'. The vocabulary admits 'warn' and 'off' and deliberately excludes 'block': "
            "PreToolUse delivery is not reliable on every supported client, and a gate that fires "
            "on some clients and not others teaches agents to distrust it. Advisory by "
            "construction, so the default costs nothing and surfaces a collision early."
        ),
        interaction_analysis=(
            "Read by the bundled intent-guard hook on a write whose target path resolves inside a "
            "formation. The guard exits zero in both settings, so no value of this field can "
            "change a hook decision from allow to block; 'off' suppresses the warning only. It "
            "never interacts with the intent-contract fail-closed path, which decides before this "
            "advisory runs."
        ),
        deprecation_plan=(
            "Retain while any supported client ships unreliable PreToolUse delivery. Removing it "
            "would leave no way to silence a duplicate of the commit-boundary message."
        ),
        docs_pointer=("docs/requirements-aare-f/prds/PRD-CORE-265-formation-manifest-ownership-join-brief-status.md"),
        test_pointer=(
            "trw-mcp/tests/hooks/test_hook_ownership_decisions.py"
            "::test_intent_guard_warns_but_never_blocks_on_foreign_owned_path"
        ),
        budget_decision="admitted",
    ),
    "formation_deliver_gate": ConfigAdmission(
        field_name="formation_deliver_gate",
        owner="PRD-CORE-265-FR11",
        consumer="trw_mcp.tools._deliver_gate_dispatch.evaluate_delivery_gates -> trw_mcp.formation.status",
        default_rationale=(
            "'block'. An orchestrator that delivers while a joined member is mid-implementation is "
            "making a completion claim that outruns its evidence, which the value hierarchy ranks "
            "above velocity. 'advisory' surfaces the same condition as a warning and is the "
            "rollback lever named in the PRD's phase-3 criterion."
        ),
        interaction_analysis=(
            "Evaluated inside the existing deliver-gate cascade as a STRUCTURED gate, so its only "
            "escape is a PRD-CORE-191 acceptable-failure record — the same override every hard "
            "gate honours, with the same ledger. It fires only on the ORCHESTRATOR run of an "
            "active formation; a member run delivering on its own is untouched, so it cannot "
            "deadlock a formation by blocking the members it is waiting for."
        ),
        deprecation_plan=(
            "Retain. A gate that blocks wrongly with no config-level kill path is worse than none, "
            "because it trains operators to override."
        ),
        docs_pointer=("docs/requirements-aare-f/prds/PRD-CORE-265-formation-manifest-ownership-join-brief-status.md"),
        test_pointer=(
            "trw-mcp/tests/test_deliver_gate_dispatch.py"
            "::test_formation_gate_blocks_on_non_terminal_member_and_honours_structured_override"
        ),
        budget_decision="admitted",
    ),
    "formation_status_member_limit": ConfigAdmission(
        field_name="formation_status_member_limit",
        owner="PRD-CORE-265-FR07",
        consumer="trw_mcp.formation.status -> trw_mcp.formation._status.member_rows",
        default_rationale=(
            "16, bounded 1..64. 16 is the largest formation the PRD's 2-second status SLO was "
            "stated for; the ceiling bounds the per-member run reads a mistaken or hostile "
            "manifest could demand. A literal would have made the SLO unverifiable against the "
            "configuration it was measured under."
        ),
        interaction_analysis=(
            "Read once per status roll-up. Members beyond the limit are not rendered; they are "
            "still validated by the manifest model and still counted by the FR11 gate, which "
            "enumerates non-terminal members from the manifest rather than from the rendered "
            "rows — so lowering this field cannot be used to hide a member from the deliver gate."
        ),
        deprecation_plan="Retain as the roll-up bound; removal reinstates an unbounded read fan-out.",
        docs_pointer=("docs/requirements-aare-f/prds/PRD-CORE-265-formation-manifest-ownership-join-brief-status.md"),
        test_pointer=(
            "trw-mcp/tests/test_orchestration_branches_tools.py"
            "::test_formation_status_is_read_only_and_ingests_no_learnings"
        ),
        budget_decision="admitted",
    ),
    "formation_manifest_lock_timeout_seconds": ConfigAdmission(
        field_name="formation_manifest_lock_timeout_seconds",
        owner="PRD-CORE-265-FR04",
        consumer="trw_mcp.formation.join / revise -> trw_mcp.formation._store.rewrite_manifest",
        default_rationale=(
            "10 seconds, bounded 0.5..120. Long enough to absorb concurrent joins at the member "
            "cap, short enough that a crashed lock holder reports a refusal rather than feeling "
            "like a hang. A timeout REFUSES and says so; it never proceeds unlocked (NFR02)."
        ),
        interaction_analysis=(
            "Bounds the non-blocking acquire loop around every manifest write. It cannot affect "
            "read paths — owner_of, brief, and status take no lock — so raising it cannot slow a "
            "commit or a hook, and lowering it cannot corrupt a manifest: the only consequence of "
            "expiry is a refused write."
        ),
        deprecation_plan=(
            "Retain. A blocking lock with no timeout is the alternative, and it would wedge every "
            "future join behind one crashed process with no message."
        ),
        docs_pointer=("docs/requirements-aare-f/prds/PRD-CORE-265-formation-manifest-ownership-join-brief-status.md"),
        test_pointer=(
            "trw-mcp/tests/test_orchestration_init_advanced.py::test_join_formation_is_atomic_and_refuses_unknown_member"
        ),
        budget_decision="admitted",
    ),
}

__all__ = ["FORMATION_ADMISSIONS"]
