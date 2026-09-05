"""Admission record for the PRD-CORE-257 deferral bound.

Belongs to the ``_field_admission_registry.py`` data table, which merges this
mapping into the aggregate ``FIELD_ADMISSIONS``. Split out for the same reason
every other per-PRD admission table is: the registry grows once per new public
field and would otherwise drift past the module-size gate each time one is
admitted.

One field is admitted here. ``session_start_writer_pressure_threshold`` is NOT
among them — it predates PRD-CORE-218 and stays on the frozen legacy baseline;
FR01 only changes its default and gives it a description, which changes no
admission class.

Imports nothing from the rest of ``trw_mcp.models.config`` except the record
type, so it cannot create an import cycle with ``TRWConfig``.
"""

from __future__ import annotations

from trw_mcp.models.config._field_admission_registry_types import ConfigAdmission

_DOCS = "docs/requirements-aare-f/prds/PRD-CORE-257-bounded-writer-pressure-deferral.md"

WRITER_PRESSURE_ADMISSIONS: dict[str, ConfigAdmission] = {
    "session_start_max_deferral_hours": ConfigAdmission(
        field_name="session_start_max_deferral_hours",
        owner="PRD-CORE-257-FR02",
        consumer="trw_mcp.state.deferral_ledger.step_deferral_decision",
        default_rationale=(
            "Defaults to 6 hours. Before this field a deferral had no age and no end: on the "
            "measured boxes embeddings backfill, stale-run close, the auto-upgrade check, the "
            "learn-journal drain and session-start side effects had not run in any deferred "
            "session, and nothing said for how long. Six hours targets four executions of each "
            "covered step per 24 hours under permanent pressure while capping the cost at one "
            "forced pass per step per six hours, whose worst case is the single 30 s SQLite "
            "busy-timeout wait the pressure controls exist to avoid stacking. The figure is a "
            "policy default, not a measured optimum — the cost of a FORCED embeddings backfill "
            "under real contention has not been measured — so it is deliberately one global "
            "bound rather than a per-step table until the census log supplies that measurement."
        ),
        interaction_analysis=(
            "Read only by the deferral ledger, which consults it in two places: expiry "
            "(age_hours >= bound forces the step to run despite pressure) and the single-winner "
            "claim (a running_since_ts older than the bound is stale, so a process that dies "
            "mid-step cannot wedge the ledger). It cannot disable the bound at any permitted "
            "value: the ge=1/le=168 constraints mean the longest a step can be held is one "
            "week and the shortest is one hour. 'Never defer' is already expressed by "
            "session_start_defer_under_writer_pressure, so this is not a second switch for the "
            "same meaning and 0 is rejected rather than treated as 'off'. Orthogonal to "
            "session_start_writer_pressure_threshold, which decides WHETHER a step defers; this "
            "one decides how long that decision may stand. Deliberately not pin_ttl_hours: that "
            "field evicts dead pins rather than bounding live work, and reusing its 24-hour "
            "value would couple two unrelated policies."
        ),
        deprecation_plan=(
            "Retain. Removing it restores unbounded deferral, which is silent cancellation of "
            "maintenance under a condition that never clears — the defect PRD-CORE-257 exists "
            "to close. Split into per-step bounds only if a forced backfill is shown to "
            "dominate session-start latency."
        ),
        docs_pointer=_DOCS,
        test_pointer=(
            "trw-mcp/tests/test_session_start_runtime_pressure.py::test_deferral_ledger_expiry_runs_step_anyway"
        ),
        budget_decision="admitted",
    ),
}

__all__ = ["WRITER_PRESSURE_ADMISSIONS"]
