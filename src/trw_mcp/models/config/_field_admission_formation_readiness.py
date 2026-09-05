"""Admission record for the PRD-CORE-266 formation-readiness tunable.

Belongs to the ``_field_admission_registry.py`` data table, which merges this
mapping into :data:`~trw_mcp.models.config._field_admission.FIELD_ADMISSIONS`.
Split out for the same reason every other per-PRD admission table is: the
registry grows once per new public field and would otherwise drift past the
module-size gate each time one is admitted.

One field is admitted: the per-probe bound on the doctor's live version probe.
The other per-client facts PRD-CORE-266 introduces are deliberately NOT config
fields — they are registry data with a recorded verification, because a
capability an operator can retype in YAML is a capability nothing verified.

Imports nothing from the rest of ``trw_mcp.models.config`` except the record
type, so it cannot create an import cycle with ``TRWConfig``.
"""

from __future__ import annotations

from trw_mcp.models.config._field_admission_registry_types import ConfigAdmission

FORMATION_READINESS_ADMISSIONS: dict[str, ConfigAdmission] = {
    "dispatch_version_probe_timeout_s": ConfigAdmission(
        field_name="dispatch_version_probe_timeout_s",
        owner="PRD-CORE-266-NFR01",
        consumer="trw_mcp.server._doctor_formation_readiness.formation_readiness_report -> _probe_version",
        default_rationale=(
            "Defaults to 5 s. A warm coding-agent CLI prints its version banner in roughly 0.3 s, "
            "so 5 s is an order of magnitude of headroom for a cold node-based binary while staying "
            "an order of magnitude below dispatch_default_timeout_s. The value exists to bound a "
            "HUNG binary, not to trim a working one, which is why the default is generous rather "
            "than tight."
        ),
        interaction_analysis=(
            "Bounds each probe INDIVIDUALLY, so the worst case for the whole check is the number of "
            "enabled dispatch clients times this value; it is the only reason a doctor run cannot be "
            "held open indefinitely by one wedged CLI. Independent of dispatch_default_timeout_s, "
            "which bounds a real dispatch rather than a diagnostic. It never changes a VERDICT "
            "except by turning a hang into an explicit not_measured row — the probe is skipped "
            "entirely when the binary does not resolve, so raising it costs nothing on a box that "
            "has no clients installed."
        ),
        deprecation_plan=(
            "Retain; removing it puts a literal back in the probe, which is precisely the shape "
            "that leaves an operator on a slow box unable to fix a spurious not_measured row "
            "without editing shipped code."
        ),
        docs_pointer=(
            "docs/requirements-aare-f/prds/PRD-CORE-266-dispatch-client-registry-growth-and-formation-readiness.md"
        ),
        test_pointer=(
            "trw-mcp/tests/test_config_dispatch.py::test_version_probe_timeout_knob_bounds_the_readiness_row"
        ),
        budget_decision="admitted",
    ),
}

__all__ = ["FORMATION_READINESS_ADMISSIONS"]
