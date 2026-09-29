"""Admission records for the HINT-DELIVERY-CANARY tunables.

Belongs to the ``_field_admission_registry.py`` data table, which merges this
mapping into :data:`~trw_mcp.models.config._field_admission.FIELD_ADMISSIONS`.
Split out, like every per-PRD admission table, so the registry stays under the
module-size gate.

Imports nothing from the rest of ``trw_mcp.models.config`` except the record
type, so it cannot create an import cycle with ``TRWConfig``.
"""

from __future__ import annotations

from trw_mcp.models.config._field_admission_registry_types import ConfigAdmission

_CONSUMER = "trw_mcp.server._doctor_hint_delivery.hint_delivery_row (`trw-mcp doctor` row)"

HINT_DELIVERY_ADMISSIONS: dict[str, ConfigAdmission] = {
    "hint_delivery_canary_window": ConfigAdmission(
        field_name="hint_delivery_canary_window",
        owner="HINT-DELIVERY-CANARY",
        consumer=_CONSUMER,
        default_rationale=(
            "Defaults to 200: large enough that a fallback-dominated run shows up without reading a "
            "project's entire hint-record history, on an operator checkout that recorded thousands of "
            "records over two weeks."
        ),
        interaction_analysis=(
            "Bounds how many `.trw/context/cc03-hints/*.json` files the row reads (newest by mtime); "
            "unrelated to hint_hub_threshold, which sizes an anchored-lesson page, not a hint-record window."
        ),
        deprecation_plan="Retain; the row is read-only and has no removal condition.",
        docs_pointer="trw-mcp/CHANGELOG.md (HINT-DELIVERY-CANARY entry)",
        test_pointer="trw-mcp/tests/test_doctor_hint_delivery.py::test_window_knob_is_read",
        budget_decision="admitted",
    ),
    "hint_delivery_fallback_warn_share": ConfigAdmission(
        field_name="hint_delivery_fallback_warn_share",
        owner="HINT-DELIVERY-CANARY",
        consumer=_CONSUMER,
        default_rationale=(
            "Defaults to 0.5: half or more of a recent window falling back (exception_fallback + "
            "timeout_fallback) is the threshold at which the row asks an operator to look, chosen as a "
            "round majority share rather than measured against a labeled corpus."
        ),
        interaction_analysis="Read together with hint_delivery_canary_window; both size the same window.",
        deprecation_plan="Retain; the row is read-only and has no removal condition.",
        docs_pointer="trw-mcp/CHANGELOG.md (HINT-DELIVERY-CANARY entry)",
        test_pointer="trw-mcp/tests/test_doctor_hint_delivery.py::test_fallback_share_warn_knob",
        budget_decision="admitted",
    ),
    "hint_delivery_receipt_stale_days": ConfigAdmission(
        field_name="hint_delivery_receipt_stale_days",
        owner="HINT-DELIVERY-CANARY",
        consumer=_CONSUMER,
        default_rationale=(
            "Defaults to 7 days: the post-commit sidecar refresh runs on every commit when enabled, so a "
            "week of silence on a trw-distill-installed checkout is already conspicuous."
        ),
        interaction_analysis="Only consulted when importlib.util.find_spec('trw_distill') resolves; no effect otherwise.",
        deprecation_plan="Retain; the row is read-only and has no removal condition.",
        docs_pointer="trw-mcp/CHANGELOG.md (HINT-DELIVERY-CANARY entry)",
        test_pointer="trw-mcp/tests/test_doctor_hint_delivery.py::test_receipt_age_warns_when_stale",
        budget_decision="admitted",
    ),
}
