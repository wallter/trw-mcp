"""Admission record for the PRD-CORE-321 requirement-drift gate mode.

Belongs to the ``_field_admission_registry.py`` data table, which merges this
mapping into :data:`~trw_mcp.models.config._field_admission.FIELD_ADMISSIONS`.
Split out, like every other per-PRD admission table, because the registry sits
near the module-size gate.

One field is admitted: ``requirement_drift_gate``. Imports nothing from the rest
of ``trw_mcp.models.config`` except the record type, so it cannot create an
import cycle with ``TRWConfig``.
"""

from __future__ import annotations

from trw_mcp.models.config._field_admission_registry_types import ConfigAdmission

REQUIREMENT_DRIFT_ADMISSIONS: dict[str, ConfigAdmission] = {
    "requirement_drift_gate": ConfigAdmission(
        field_name="requirement_drift_gate",
        owner="PRD-CORE-321-FR05",
        consumer="trw_mcp.tools._deliver_requirement_drift._effective_mode",
        default_rationale=(
            "None keeps the per-PRD default: block for a PRD that declares safety_critical: true now "
            "or in any approved-or-later version of its git history (or whose flag cannot be read), warn "
            "for every other PRD. CONSTITUTION nudge ethics: a cosmetic rewording in a non-safety-critical "
            "PRD does not justify a hard block by default. An existing config without the key loads "
            "unchanged (NFR03)."
        ),
        interaction_analysis=(
            "Decides only each scoped PRD's effective mode. A block still needs deliver_gate_mode to block "
            "the run's task type (gate_mode_blocks_task: block_coding/block_all and coding/rca/eval), so "
            "deliver_gate_mode=advisory or a non-build task type downgrades every block to "
            "requirement_drift_warning. The PRD-CORE-191 acceptable-failure record is the only override of a "
            "standing block. Under block mode an amendment row must also carry Owner and an unexpired Expiry "
            "(FR04)."
        ),
        deprecation_plan=(
            "Retain. Removing it leaves the safety-critical default with no project-level way to refuse "
            "completion on every PRD (PRD-MAP #3's 'completion is refused') or to opt down to warn-only."
        ),
        docs_pointer="docs/requirements-aare-f/prds/PRD-CORE-321.md",
        test_pointer="trw-mcp/tests/test_requirement_drift_deliver.py::test_block_mode_cells",
        budget_decision="admitted",
    ),
}

__all__ = ["REQUIREMENT_DRIFT_ADMISSIONS"]
