"""Admission record for the HINT-RECALL-BUDGET tunable.

Belongs to the ``_field_admission_registry.py`` data table, which merges this
mapping into :data:`~trw_mcp.models.config._field_admission.FIELD_ADMISSIONS`.
Split out, like every per-PRD admission table, so the registry stays under the
module-size gate.

Imports nothing from the rest of ``trw_mcp.models.config`` except the record
type, so it cannot create an import cycle with ``TRWConfig``.
"""

from __future__ import annotations

from trw_mcp.models.config._field_admission_registry_types import ConfigAdmission

_OWNER = "HINT-RECALL-BUDGET"
_DOCS = "trw-mcp/CHANGELOG.md (HINT-RECALL-BUDGET entry)"
_CONSUMER = (
    "trw_mcp.tools._before_edit_hint_core.compute_before_edit_hint -> _collect_learnings "
    "(pre-edit hint, trw_code hint mode)"
)
_TESTS = "trw-mcp/tests/test_hint_recall_budget.py"

HINT_RECALL_BUDGET_ADMISSIONS: dict[str, ConfigAdmission] = {
    "hint_recall_deadline_ms": ConfigAdmission(
        field_name="hint_recall_deadline_ms",
        owner=_OWNER,
        consumer=_CONSUMER,
        default_rationale=(
            "Defaults to 600ms: measured against a 2522-entry daemon store the unbounded T1 recall cost "
            "4.76s of a 5.98s hint (3 page-growth round trips at ~1.5s each), which starved the hook's "
            "2.4s PreToolUse budget before the fast T2 sidecar half ever ran. 600ms leaves headroom for "
            "the sidecar read within that budget while still trying a real recall first."
        ),
        interaction_analysis=(
            "Read only by the pre-edit hint's own recall call, which also forces a single page (no "
            "take_hits growth loop) independent of this budget. trw_recall and trw_session_start build "
            "their own RecallSpec without single_page set and never read this field, so raising or "
            "lowering it never changes their latency or result set."
        ),
        deprecation_plan="Retain while the pre-edit hint recalls learnings inline; removed only if that recall moves off the hot hook path.",
        docs_pointer=_DOCS,
        test_pointer=f"{_TESTS}::test_slow_store_times_out_and_still_returns_the_sidecar_hint",
        budget_decision="admitted",
    ),
}
