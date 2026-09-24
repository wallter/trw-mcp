"""Admission records for the PRD-CORE-290 token-economy fields.

Its own table module, merged by ``_field_admission_registry.py`` (the pattern
PRD-FIX-123 established). Imports nothing from ``trw_mcp.models.config`` except
the record type, so it cannot create an import cycle with ``TRWConfig``.
"""

from __future__ import annotations

from trw_mcp.models.config._field_admission_registry_types import ConfigAdmission

_PRD = "docs/requirements-aare-f/prds/PRD-CORE-290-formation-token-economy.md"

TOKEN_ECONOMY_ADMISSIONS: dict[str, ConfigAdmission] = {
    "dispatch_default_effort": ConfigAdmission(
        field_name="dispatch_default_effort",
        owner="PRD-CORE-290-FR03",
        consumer="trw_mcp.dispatch._resolve.resolve_dispatch_request -> dispatch._policy.resolve_effort",
        default_rationale=(
            "None. The task-class table (agents/task_policy.py) is the default; this is the operator "
            "override between an explicit request and that table, so an unset value must leave the "
            "table in charge rather than impose a level of its own."
        ),
        interaction_analysis=(
            "Outranked by an explicit effort on the request; outranks the role's table row. Typed to "
            "the portable ladder so a typo fails at config load. The applied value is still clamped "
            "to what the client's flag accepts, and a client without a flag gets none (recorded)."
        ),
        deprecation_plan="Retire if effort moves into a per-role operator policy object.",
        docs_pointer=_PRD,
        test_pointer="trw-mcp/tests/test_dispatch_policy_precedence.py",
        budget_decision="admitted",
    ),
    "dispatch_default_max_turns": ConfigAdmission(
        field_name="dispatch_default_max_turns",
        owner="PRD-CORE-290-FR04",
        consumer="trw_mcp.dispatch._resolve.resolve_dispatch_request -> dispatch._policy.resolve_max_turns",
        default_rationale=(
            "30. Bounds a dispatched child's spend by default; 0 disables it. Applied only through a "
            "client's verified turn-limit flag, so it never reaches a client that would reject it."
        ),
        interaction_analysis=(
            "Independent of dispatch_default_timeout_s (wall clock versus agent turns). A cap hit "
            "reports turn_cap_reached with a next-read pointer and is never a success."
        ),
        deprecation_plan="Retire if turn limits move into a per-role operator policy object.",
        docs_pointer=_PRD,
        test_pointer="trw-mcp/tests/test_dispatch_turn_and_report_caps.py",
        budget_decision="admitted",
    ),
    "agent_report_max_chars": ConfigAdmission(
        field_name="agent_report_max_chars",
        owner="PRD-CORE-290-FR04",
        consumer="trw_mcp.agents._report_cap.report_block -> tier_resolver.materialize_agent",
        default_rationale=(
            "800. Every rendered agent asks for a final report of at most this many characters that "
            "links durable findings; 0 renders no cap. Guidance to the model, not a host guarantee."
        ),
        interaction_analysis=(
            "Read from the target project's config once per init/update call, so the installer and "
            "the update path's hash comparison render the same text; changing it re-renders agents."
        ),
        deprecation_plan="Retire if report bounds move into a per-agent policy table.",
        docs_pointer=_PRD,
        test_pointer="trw-mcp/tests/test_dispatch_turn_and_report_caps.py",
        budget_decision="admitted",
    ),
}

__all__ = ["TOKEN_ECONOMY_ADMISSIONS"]
