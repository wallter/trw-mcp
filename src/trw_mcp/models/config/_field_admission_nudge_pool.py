"""Admission record for the PRD-CORE-335 nudge pool-weight override.

Belongs to the ``_field_admission_registry.py`` data table, which merges this
mapping into :data:`~trw_mcp.models.config._field_admission.FIELD_ADMISSIONS`.
Split out, like every other per-PRD admission table, because the registry sits
near the module-size gate.

One field is admitted: ``nudge_pool_weights``. Imports nothing from the rest of
``trw_mcp.models.config`` except the record type, so it cannot create an import
cycle with ``TRWConfig``.
"""

from __future__ import annotations

from trw_mcp.models.config._field_admission_registry_types import ConfigAdmission

NUDGE_POOL_ADMISSIONS: dict[str, ConfigAdmission] = {
    "nudge_pool_weights": ConfigAdmission(
        field_name="nudge_pool_weights",
        owner="PRD-CORE-335-FR01",
        consumer=(
            "trw_mcp.models.config._main.TRWConfig.effective_nudge_pool_weights, read by "
            "trw_mcp.tools._ceremony_status_pool.select_pool and the trw_status nudge_pool_weights display"
        ),
        default_rationale=(
            "None keeps today's routing: the pinned run's task_profile tuple when one exists, else the "
            "client profile's NudgePoolWeights. A config without the key loads and routes unchanged (FR03)."
        ),
        interaction_analysis=(
            "One nested NudgePoolWeights object, not four flat fields (the shape PRD-QUAL-131-FR05 removed "
            "as dead); its existing sum-to-100 validator applies unchanged (NFR01). When set it wins over "
            "the run task_profile tuple and the client profile (tier 1 of 3). It changes only the weights "
            "select_pool draws from: nudge_density and the nudge_pool_cooldown_* fields still filter the "
            "draw, the learnings pool is still zeroed on a session_start/recall response, and the "
            "build-failure/P0 context bypass still wins."
        ),
        deprecation_plan=(
            "Retain. Removing it returns pool routing to a whole-client-profile switch, the one lever of "
            "pool/density/cooldown with no project-level control."
        ),
        docs_pointer="docs/requirements-aare-f/prds/PRD-CORE-335.md",
        test_pointer="trw-mcp/tests/test_nudge_pool_weight_override.py::test_override_changes_routing_distribution",
        budget_decision="admitted",
    ),
}

__all__ = ["NUDGE_POOL_ADMISSIONS"]
