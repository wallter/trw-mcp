"""Admission record for the PRD-CORE-249 project-handoff location field.

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

_DOCS = "docs/requirements-aare-f/prds/PRD-CORE-249-deferral-handoff-and-plan-acceptance-gate.md"

#: PRD-CORE-249 admits exactly ONE public field. The two readback bounds
#: (HANDOFF_READBACK_MAX_ITEMS, HANDOFF_BLOCK_MAX_BYTES) are deliberately module
#: constants rather than fields — v1 invariants on the DeliveryLimits precedent —
#: because the public surface is already over its NFR04 budget target and a bound
#: nobody is expected to tune does not earn a place on it.
PROJECT_HANDOFF_ADMISSIONS: dict[str, ConfigAdmission] = {
    "project_handoff_path": ConfigAdmission(
        field_name="project_handoff_path",
        owner="PRD-CORE-249-FR01",
        consumer="trw_mcp.tools._project_handoff.resolve_handoff_path",
        default_rationale=(
            "Defaults to '.trw/HANDOFF.md'. The installer creates '.trw/' in every TRW project, so "
            "the default resolves everywhere; 'docs/documentation/improvement-backlog.md' exists in "
            "exactly one repository, and defaulting to it would have the framework fabricate a docs "
            "tree inside arbitrary user projects. A flat file at a fixed location also gives the "
            "managed block exactly one owner, with no ambiguity about which backlog is authoritative."
        ),
        interaction_analysis=(
            "Read by exactly one resolver, which both the deliver-time write (FR02) and the "
            "session_start readback (FR03) call, so the two halves can never disagree about where "
            "the file is. It is a LOCATION override only: no value disables the write, because an "
            "unset-means-off knob is a dormant feature flag. A lexical validator refuses absolute "
            "and '..'-escaping values at load; the resolver re-checks containment after symlink "
            "resolution (NFR03). Independent of every other path field — nothing else targets it."
        ),
        deprecation_plan=(
            "Retain; the sole handoff-location authority. Removing it hard-codes '.trw/HANDOFF.md' "
            "and takes the location choice away from projects that keep a docs-tree backlog."
        ),
        docs_pointer=_DOCS,
        test_pointer="trw-mcp/tests/test_core249_project_handoff.py::test_default_and_override_resolution",
        budget_decision="admitted",
    ),
}

__all__ = ["PROJECT_HANDOFF_ADMISSIONS"]
