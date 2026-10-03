"""Admission records for the team-sync fields SHARED-RECALL-LOCAL and SYNC-PROJECT-IDENTITY added.

Belongs to the ``_field_admission_registry.py`` data table, which merges this
mapping into :data:`~trw_mcp.models.config._field_admission.FIELD_ADMISSIONS`.
"""

from __future__ import annotations

from trw_mcp.models.config._field_admission_registry_types import ConfigAdmission

TEAM_SYNC_ADMISSIONS: dict[str, ConfigAdmission] = {
    "team_sync_fresh_after_seconds": ConfigAdmission(
        field_name="team_sync_fresh_after_seconds",
        owner="SHARED-RECALL-LOCAL",
        consumer="trw_mcp.sync._fresh_pull.ensure_fresh, asked by session start and trw_recall",
        default_rationale=(
            "Defaults to 300 s, the sync loop's own interval: a pull older than that is caught up "
            "before recall reads, so a learning written on another host is recallable here within "
            "minutes without a long-lived server."
        ),
        interaction_analysis=(
            "Only acts with team_sync_enabled and a sync target; the pull stays behind the platform "
            "contact switch. 0 turns the pre-recall pull off and leaves the sync loop as the only "
            "puller. The caller never waits past the fixed budget (BUDGET_SECONDS)."
        ),
        deprecation_plan="Retain; fold into a sync policy object if the sync fields are consolidated.",
        docs_pointer="trw-mcp/src/trw_mcp/sync/_fresh_pull.py",
        test_pointer="trw-mcp/tests/test_sync_fresh_pull.py::test_the_threshold_is_configurable",
        budget_decision="admitted",
    ),
    "team_sync_all_projects": ConfigAdmission(
        field_name="team_sync_all_projects",
        owner="SYNC-PROJECT-IDENTITY",
        consumer="trw_mcp.tools._recall_order.order_ranked_for_response, asked by trw_recall",
        default_rationale=(
            "Defaults to false: a team learning that another project provably wrote (a different portable "
            "project id) ranks below one with no recorded project, so the operator's other repositories stop "
            "crowding this one's recall. Nothing is dropped or hidden: a query that names the other project "
            "still finds its rows."
        ),
        interaction_analysis=(
            "Only changes ordering in trw_recall; pull stores every row either way. true ranks other "
            "projects' rows like unrecorded ones (the earlier behaviour). A wrong or hostile project stamp "
            "therefore costs ranking only, and flipping this setting restores it."
        ),
        deprecation_plan="Retain; fold into a recall policy object if the recall fields are consolidated.",
        docs_pointer="trw-mcp/src/trw_mcp/tools/_recall_order.py",
        test_pointer="trw-mcp/tests/test_recall_project_scope.py::test_the_opt_in_ranks_other_projects_like_unrecorded_ones",
        budget_decision="admitted",
    ),
}
