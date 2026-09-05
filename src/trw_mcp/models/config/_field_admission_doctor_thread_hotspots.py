"""Admission records for the doctor thread-hotspot WARN threshold (PRD-FIX-131 follow-up).

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

_DOCS = "docs/requirements-aare-f/prds/PRD-FIX-131.md"
_TESTS = "trw-mcp/tests/test_doctor_thread_hotspots.py::test_warn_when_both_thresholds_are_exceeded"
_CONSUMER = (
    "trw_mcp.server._subcommands_doctor._check_thread_hotspots -> "
    "trw_mcp.server._doctor_thread_hotspots.thread_hotspot_row"
)

DOCTOR_THREAD_HOTSPOT_ADMISSIONS: dict[str, ConfigAdmission] = {
    "doctor_thread_hotspot_share": ConfigAdmission(
        field_name="doctor_thread_hotspot_share",
        owner="PRD-FIX-131 (operator-visibility follow-up, 2026-09-05)",
        consumer=_CONSUMER,
        default_rationale=(
            "Defaults to 0.5 (half of a server's process uptime). Measured incident: three live "
            "trw-mcp servers each had one worker thread at 70-85% CPU utilization over 2-2.5h "
            "lifetimes while py-spy/gdb were unavailable (ptrace_scope=1, no sudo) and trw_deliver "
            "hung for 1,800s. 0.5 sits below every observed incident share with headroom against a "
            "briefly busy-but-healthy thread that has not yet cleared half the process's lifetime."
        ),
        interaction_analysis=(
            "Read only by thread_hotspot_row, which WARNs exactly when the hottest thread's CPU "
            "seconds clears BOTH this share (relative to /proc-measured process uptime) AND "
            "doctor_thread_hotspot_min_seconds (absolute floor) -- a conjunction, not either alone, "
            "so a short-lived server cannot trip the row on share and a long-quiet server cannot "
            "trip it on raw seconds. Orthogonal to every other doctor row and to every WAL/writer "
            "threshold in _fields_boot_maintenance.py / _field_admission_writer_pressure.py -- this "
            "check opens no SQLite connection and reads no writer-registry heartbeat."
        ),
        deprecation_plan=(
            "Retain. Removing it forces a fixed threshold back into source, which is what the "
            "configurable-knob principle exists to prevent; tighten only with a fresh incident "
            "measurement."
        ),
        docs_pointer=_DOCS,
        test_pointer=_TESTS,
        budget_decision="admitted",
    ),
    "doctor_thread_hotspot_min_seconds": ConfigAdmission(
        field_name="doctor_thread_hotspot_min_seconds",
        owner="PRD-FIX-131 (operator-visibility follow-up, 2026-09-05)",
        consumer=_CONSUMER,
        default_rationale=(
            "Defaults to 300s (5 minutes). Below the observed incident magnitude (thousands of "
            "seconds) by more than an order of magnitude, so a genuinely new hotspot is caught long "
            "before it approaches the hours-long incidents that motivated this row, while a server "
            "up for under five minutes cannot trip a WARN purely from startup work."
        ),
        interaction_analysis=(
            "Read only alongside doctor_thread_hotspot_share (see that field's interaction_analysis "
            "for the conjunction). Independent of every WAL/checkpoint/writer-pressure threshold in "
            "_fields_boot_maintenance.py -- this check opens no SQLite connection and reads no "
            "writer-registry heartbeat."
        ),
        deprecation_plan=(
            "Retain. Same rationale as doctor_thread_hotspot_share -- both bounds close the same "
            "operator-visibility gap and neither is meaningful without the other."
        ),
        docs_pointer=_DOCS,
        test_pointer=_TESTS,
        budget_decision="admitted",
    ),
}

__all__ = ["DOCTOR_THREAD_HOTSPOT_ADMISSIONS"]
