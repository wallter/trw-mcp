"""Admission records for the PRD-CORE-355 dispatch fan-out safety fields.

Its own table module, merged by ``_field_admission_registry.py`` (the pattern
PRD-FIX-123 established). Imports nothing from ``trw_mcp.models.config`` except
the record type, so it cannot create an import cycle with ``TRWConfig``.

All three are off by default: with the cap at 0 and require-effort false, no
slot file is created and dispatch behaves as it did before (PRD-CORE-355-NFR01).
"""

from __future__ import annotations

from trw_mcp.models.config._field_admission_registry_types import ConfigAdmission

_PRD = "docs/requirements-aare-f/prds/PRD-CORE-355.md"
_TESTS = "trw-mcp/tests/test_dispatch_concurrency_cap.py"

DISPATCH_FANOUT_SAFETY_ADMISSIONS: dict[str, ConfigAdmission] = {
    "dispatch_max_concurrent_children": ConfigAdmission(
        field_name="dispatch_max_concurrent_children",
        owner="PRD-CORE-355-FR02",
        consumer="trw_mcp.dispatch._slots.slot_settings -> dispatch._runner.dispatch (dispatch_slot)",
        default_rationale=(
            "0 (off). The cap is an operator act: a bound that refuses or delays children must be "
            "chosen for the machine it runs on, so an unset value changes nothing and touches no file."
        ),
        interaction_analysis=(
            "Per user, not per machine: slot files live under ~/.trw/runtime/dispatch-slots. Processes "
            "with different caps share slot-0..N-1, so the largest active cap is the effective bound, "
            "and a process with cap 0 is uncounted. Acquired before the per-client credential lock; a "
            "nested dispatch (TRW_DISPATCH_SLOT_HELD set) never waits. Unenforced on Windows."
        ),
        deprecation_plan="Retire if dispatch admission moves to a resource-aware scheduler.",
        docs_pointer=_PRD,
        test_pointer=_TESTS,
        budget_decision="admitted",
    ),
    "dispatch_slot_wait_s": ConfigAdmission(
        field_name="dispatch_slot_wait_s",
        owner="PRD-CORE-355-FR03",
        consumer="trw_mcp.dispatch._slots.slot_settings -> dispatch._runner.dispatch (dispatch_slot)",
        default_rationale=(
            "600 s, the default dispatch timeout: a caller waits at most about one child's run for a "
            "slot, then receives a named concurrency_cap refusal instead of hanging."
        ),
        interaction_analysis=(
            "Read only when dispatch_max_concurrent_children > 0. A background job waits at most half "
            "its timeout so the job watchdog (prelaunch + 1.5x timeout) never kills a queued job. A "
            "nested dispatch ignores it and makes a single non-blocking attempt."
        ),
        deprecation_plan="Retire together with dispatch_max_concurrent_children.",
        docs_pointer=_PRD,
        test_pointer=_TESTS,
        budget_decision="admitted",
    ),
    "dispatch_require_effort": ConfigAdmission(
        field_name="dispatch_require_effort",
        owner="PRD-CORE-355-FR06",
        consumer="trw_mcp.dispatch._resolve.resolve_dispatch_request -> dispatch._policy.require_effort",
        default_rationale=(
            "False. Requiring an effort refuses dispatches that run today at the harness default, so "
            "it is the operator's choice to turn that into an error."
        ),
        interaction_analysis=(
            "Satisfied by --effort, dispatch_default_effort or the role's task-class row. A client "
            "with no effort carrier, or a Haiku model (which takes none), is never refused for it."
        ),
        deprecation_plan="Retire if effort becomes mandatory on every dispatch path.",
        docs_pointer=_PRD,
        test_pointer=_TESTS,
        budget_decision="admitted",
    ),
}

__all__ = ["DISPATCH_FANOUT_SAFETY_ADMISSIONS"]
