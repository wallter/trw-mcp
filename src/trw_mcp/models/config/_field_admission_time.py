"""Admission records for PRD-CORE-338 wall-clock tracking.

Belongs to the ``_field_admission_registry.py`` data table, which merges this
mapping into :data:`~trw_mcp.models.config._field_admission.FIELD_ADMISSIONS`.
"""

from __future__ import annotations

from trw_mcp.models.config._field_admission_registry_types import ConfigAdmission

_DOCS = "docs/requirements-aare-f/prds/PRD-CORE-338.md"
_OWNER = "PRD-CORE-338"

TIME_ADMISSIONS: dict[str, ConfigAdmission] = {
    "time_tracking_enabled": ConfigAdmission(
        field_name="time_tracking_enabled",
        owner=f"{_OWNER}-FR02",
        consumer="trw_mcp.state.timekeeping.is_tracked, asked by trw_status, trw_checkpoint and trw_deliver",
        default_rationale=(
            "Defaults to True: the predicate is already task-scaled (COMPREHENSIVE, formation member, "
            "declared target, or a multi-slice PRD), so untracked runs pay nothing and the switch "
            "exists only as the rollback lever."
        ),
        interaction_analysis=(
            "False makes every run untracked, so no surface adds a time block or elapsed_seconds. "
            "The phase re-entry fix and FR06's clock_mismatch warning are independent of it."
        ),
        deprecation_plan="Retain as the kill switch until the PE2 ceremony profile absorbs it as its time_tracking field.",
        docs_pointer=_DOCS,
        test_pointer="trw-mcp/tests/test_timekeeping.py::test_kill_switch_makes_every_run_untracked",
        budget_decision="admitted",
    ),
    "display_timezone": ConfigAdmission(
        field_name="display_timezone",
        owner=f"{_OWNER}-OQ2",
        consumer="trw_mcp.state.timekeeping.time_block, rendering eta_local in the trw_status time block",
        default_rationale=(
            "Defaults to UTC, which adds nothing to the response; the operator has not yet chosen a "
            "local zone (OQ-2), and every stored or compared time stays UTC regardless."
        ),
        interaction_analysis=(
            "Only affects tracked runs with an ETA range: a non-UTC IANA zone adds one eta_local "
            "string. An unknown zone renders nothing rather than guessing."
        ),
        deprecation_plan="Retain; revisit when OQ-2 is decided.",
        docs_pointer=_DOCS,
        test_pointer="trw-mcp/tests/test_timekeeping.py::test_time_block_byte_budget",
        budget_decision="admitted",
    ),
    "formation_activity_stall_seconds": ConfigAdmission(
        field_name="formation_activity_stall_seconds",
        owner=f"{_OWNER}-FR07",
        consumer="trw_mcp.formation._stall.stall_scan via formation.settings(), read by trw_status and the comms watch loop",
        default_rationale="1800 s is the R8 charter's 30-minute no-activity stall rule (C7).",
        interaction_analysis=(
            "Only adds an advisory activity_stale finding for a joined, non-terminal member whose run "
            "recorded no event for strictly longer than this; the mail/heartbeat/call stalls keep their "
            "own 600 s bound. Bounded below at 60 s so a typo cannot flag every member."
        ),
        deprecation_plan="Retain; the stall scan's other bounds are constants, this one is a per-team working rhythm.",
        docs_pointer=_DOCS,
        test_pointer="trw-mcp/tests/comms/test_formation_stall.py::test_activity_stale_threshold_is_the_config_knob",
        budget_decision="admitted",
    ),
}

__all__ = ["TIME_ADMISSIONS"]
