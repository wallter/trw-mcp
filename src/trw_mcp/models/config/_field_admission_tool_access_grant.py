"""Admission record for the tool-access grant TTL ceiling (PRD-FIX-119 follow-up).

Belongs to the ``_field_admission_registry.py`` data table, which merges this
mapping into the aggregate ``FIELD_ADMISSIONS``. Split out for the same reason
every other per-PRD admission table is: the registry grows once per new public
field and would otherwise drift past the module-size gate each time one is
admitted.

Imports nothing from the rest of ``trw_mcp.models.config`` except the record
type, so it cannot create an import cycle with ``TRWConfig``.
"""

from __future__ import annotations

from trw_mcp.models.config._field_admission_registry_types import ConfigAdmission

_DOCS = "docs/requirements-aare-f/prds/PRD-FIX-119-review-tool-surface-freeze.md"
_TESTS = "trw-mcp/tests/test_phase_overrides.py::test_the_grant_ttl_ceiling_is_operator_configurable"
_CONSUMER = "trw_mcp.tools.phase_overrides._max_ttl_seconds -> grant_override (the TTL clamp)"

TOOL_ACCESS_GRANT_ADMISSIONS: dict[str, ConfigAdmission] = {
    "tool_access_grant_max_ttl_seconds": ConfigAdmission(
        field_name="tool_access_grant_max_ttl_seconds",
        owner="PRD-FIX-119 follow-up (2026-09-10, from submission sub_hJ96RkVjxsLXwqWA)",
        consumer=_CONSUMER,
        default_rationale=(
            "Defaults to 300, which is the value this ceiling had as a hardcoded module constant "
            "(_MAX_TTL_SECONDS) since the tool shipped -- so the default changes nothing and only "
            "the reachability does. It became a field because the one situation that needs a "
            "different value is precisely the one the constant made unreachable: a client that "
            "cannot receive an in-session tools/list refresh may need the single-use grant to "
            "outlive a human's reconnect, and how long that takes is a property of that client, "
            "not of TRW. The floor of 30 keeps a grant short-lived enough that an unused one "
            "expires promptly; the ceiling of 3600 stops an unusable grant lingering for a day."
        ),
        interaction_analysis=(
            "Read only by _max_ttl_seconds, which both supplies the default TTL when a caller "
            "passes none and clamps a larger request down. Raising it lengthens the window in "
            "which one masked tool is callable by one session; it does NOT widen WHICH tools can "
            "be granted (that is _is_registered_tool plus the phase policy) nor how many calls a "
            "grant permits (always one -- consume_override pops it). Independent of "
            "COMPACTION_GATE_EXEMPT_TOOLS: a granted call is still evaluated by the ceremony "
            "middleware and can still be blocked there. Grants are process-local and in-memory, "
            "so a server restart discards them regardless of this value."
        ),
        deprecation_plan=(
            "Retain while phase masking exists. If the masking middleware is ever replaced by "
            "boot-time deregistration, the override mechanism and this ceiling retire together."
        ),
        docs_pointer=_DOCS,
        test_pointer=_TESTS,
        budget_decision="admitted",
    ),
}

__all__ = ["TOOL_ACCESS_GRANT_ADMISSIONS"]
