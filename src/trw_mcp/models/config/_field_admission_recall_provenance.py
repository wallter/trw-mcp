"""Admission record for the PRD-CORE-326 recall-provenance switch.

Belongs to the ``_field_admission_registry.py`` data table, which merges this
mapping into :data:`~trw_mcp.models.config._field_admission.FIELD_ADMISSIONS`.
Split out, as every per-change admission table is, so the registry stays under
the module-size gate.

Imports nothing from the rest of ``trw_mcp.models.config`` except the record
type, so it cannot create an import cycle with ``TRWConfig``.
"""

from __future__ import annotations

from trw_mcp.models.config._field_admission_registry_types import ConfigAdmission

RECALL_PROVENANCE_ADMISSIONS: dict[str, ConfigAdmission] = {
    "recall_provenance_inline": ConfigAdmission(
        field_name="recall_provenance_inline",
        owner="PRD-CORE-326",
        consumer=(
            "trw_mcp.tools._recall_impl.execute_recall, which passes it to "
            "trw_mcp.tools._recall_presenter.present(provenance=...) for every trw_recall stub"
        ),
        default_rationale=(
            "Defaults to True: CONSTITUTION §2 asks an agent to check provenance and scope before "
            "reusing a recalled claim, and the stub is where it reads them. A common-case row "
            "(source agent, the checkout's own namespace, open window) costs 0 bytes either way."
        ),
        interaction_analysis=(
            "Removes exactly the source, scope and superseded_by stub keys and nothing else: "
            "CORE-312's freshness flag, the byte budget and ranking are untouched. trw_session_start "
            "never reads it; its present() call keeps the provenance=False default, so session stubs "
            "are identical whatever the value. trw_recall(ids=[...]) full rows are unaffected."
        ),
        deprecation_plan=(
            "Retain as the operator's off switch for the provenance keys, which cost stub slots on "
            "a provenance-heavy corpus."
        ),
        docs_pointer="docs/requirements-aare-f/prds/PRD-CORE-326.md",
        test_pointer="trw-mcp/tests/test_response_token_budget.py::test_kill_switch_removes_only_provenance_keys",
        budget_decision="admitted",
    ),
}

__all__ = ["RECALL_PROVENANCE_ADMISSIONS"]
