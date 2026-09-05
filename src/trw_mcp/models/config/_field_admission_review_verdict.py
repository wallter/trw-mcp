"""Admission record for the PRD-CORE-255 review-verdict TTL field.

Belongs to the ``_field_admission_registry.py`` data table, which merges this
mapping into :data:`~trw_mcp.models.config._field_admission.FIELD_ADMISSIONS`.
Split out for the same reason every other per-PRD admission table is: the
registry grows once per new public field and would otherwise drift past the
module-size gate each time one is admitted.

Imports nothing from the rest of ``trw_mcp.models.config`` except the record
type, so it cannot create an import cycle with ``TRWConfig``.
"""

from __future__ import annotations

from trw_mcp.models.config._field_admission_registry_types import ConfigAdmission

_DOCS = "docs/requirements-aare-f/prds/PRD-CORE-255-review-verdict-binding-expiry-safety-critical-adversarial-gate.md"

#: PRD-CORE-255 admits exactly TWO public fields: the receipt TTL (FR01) and the
#: operator sign-off TTL added by the 2026-09-04 FR04 amendment. The
#: safety-critical gate's own inputs are the PRD ``safety_critical`` frontmatter
#: flag and the run's declared scope — properties of the WORK, not of the
#: installation — so they are deliberately not config fields; adding an on/off
#: switch beside them would be the dormant-flag pattern the operator rule forbids.
REVIEW_VERDICT_ADMISSIONS: dict[str, ConfigAdmission] = {
    "review_verdict_ttl_hours": ConfigAdmission(
        field_name="review_verdict_ttl_hours",
        owner="PRD-CORE-255-FR01",
        consumer="trw_mcp.state._evidence_gates.review_verdict_is_expired (validate_review_receipt)",
        default_rationale=(
            "Defaults to 24 hours. The PRD-CORE-205 content binding already invalidates a receipt "
            "whose reviewed BYTES moved; it says nothing about elapsed time, so a receipt over an "
            "untouched scope stayed positive forever. 24h is short enough that a verdict recorded "
            "in a previous work cycle cannot authorize today's delivery, and long enough to sit "
            "inside a normal review-to-deliver latency. Bounded ge=1/le=8760 so 0 (which would "
            "expire every receipt the instant it is written, wedging every run) and an effectively "
            "unbounded value are both rejected at config load rather than silently clamped."
        ),
        interaction_analysis=(
            "Read at exactly one point: after the content-binding freshness check inside "
            "validate_review_receipt, which every review gate reader reaches through "
            "load_latest_review_evidence. The two axes are independent and both must pass, so "
            "raising this value can never resurrect a content-stale receipt and lowering it can "
            "never bypass the binding check. An unreadable config or an unparseable completed_at "
            "timestamp is treated as EXPIRED (fail-closed, NFR01), so no value and no failure mode "
            "restores the pre-CORE-255 never-expires posture. The expired state is reported with "
            "its own reason code (review_verdict_expired), distinguishable in logs and tests from "
            "the binding check's bound_content_changed."
        ),
        deprecation_plan=(
            "Retain as the time axis of receipt validity; removal reinstates 'a green verdict "
            "outlives the finding that should invalidate it', which is the defect this field "
            "exists to make impossible."
        ),
        docs_pointer=_DOCS,
        test_pointer="trw-mcp/tests/test_review_verdict_ttl.py::test_receipt_expires_after_ttl_window",
        budget_decision="admitted",
    ),
    "review_signoff_ttl_hours": ConfigAdmission(
        field_name="review_signoff_ttl_hours",
        owner="PRD-CORE-255-FR04",
        consumer="trw_mcp.state.review_signoffs.configured_ttl_hours (append_review_signoff + resolve_review_signoff)",
        default_rationale=(
            "Defaults to 24 hours, matching review_verdict_ttl_hours so an operator sign-off can "
            "never outlive the review verdict it authorizes. Before the 2026-09-04 amendment an "
            "'operator' reviewer receipt was any non-empty caller-supplied string, so the reviewed "
            "agent could mint its own sign-off; the replacement is a signed, scope-bound record with "
            "a bounded life, and this field is that bound. It is applied twice — as the default TTL "
            "at mint time and as a hard cap at verification — so a hand-edited record claiming a "
            "longer window is refused (operator_approval_ttl_exceeded) rather than honored. Bounded "
            "ge=1/le=720: 0 would expire every approval the instant it is written and wedge the "
            "operator path entirely, and 30 days is the longest sign-off that can still honestly be "
            "said to describe the code being delivered."
        ),
        interaction_analysis=(
            "Read at exactly one point, configured_ttl_hours, reached from both the mint path and "
            "the resolver. Independent of review_verdict_ttl_hours (receipt age) and of the "
            "PRD-CORE-205 content binding (reviewed bytes): all three must pass, so raising this "
            "value cannot resurrect a stale receipt or a moved scope, and lowering it cannot bypass "
            "either. A config read that RAISES refuses the approval with its own reason code "
            "(operator_approval_policy_unreadable) instead of substituting a cap, so no failure mode "
            "restores the pre-amendment 'any string is an operator sign-off' posture."
        ),
        deprecation_plan=(
            "Retain as the life of an operator sign-off; removal reinstates an unbounded approval, "
            "i.e. a one-time sign-off that authorizes safety-critical deliveries forever."
        ),
        docs_pointer=_DOCS,
        test_pointer=(
            "trw-mcp/tests/test_review_signoffs.py::TestOperatorSignoffResolution::"
            "test_window_longer_than_configured_ttl_is_refused"
        ),
        budget_decision="admitted",
    ),
}

__all__ = ["REVIEW_VERDICT_ADMISSIONS"]
