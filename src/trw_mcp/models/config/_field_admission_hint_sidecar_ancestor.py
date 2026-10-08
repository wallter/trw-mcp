"""Admission records for the T2 ancestor-sidecar tunables (8.2, slice S1).

Belongs to the ``_field_admission_registry.py`` data table, which merges this
mapping into :data:`~trw_mcp.models.config._field_admission.FIELD_ADMISSIONS`.
Split out, like every per-PRD admission table, so the registry stays under the
module-size gate.

Imports nothing from the rest of ``trw_mcp.models.config`` except the record
type, so it cannot create an import cycle with ``TRWConfig``.
"""

from __future__ import annotations

from trw_mcp.models.config._field_admission_registry_types import ConfigAdmission

_OWNER = "T2-ANCESTOR-SIDECAR (8.2)"
_DOCS = "trw-mcp/CHANGELOG.md (ancestor sidecar entry); rollout census in the internal pre-registration"
_CONSUMER = (
    "trw_mcp.tools._sidecar_substrate.resolve_current_sidecar -> "
    "trw_mcp.tools._sidecar_ancestry.find_ancestor_sidecar (pre-edit hint, trw_code hint mode)"
)
_TESTS = "trw-mcp/tests/test_sidecar_ancestry.py"
_REBUILD_CONSUMER = "trw_mcp.tools._distill_spawn.request_rebuild_if_due (trw_mcp.tools._post_commit)"
_REBUILD_TESTS = "trw-mcp/tests/test_sidecar_rebuild_request.py"

HINT_SIDECAR_ANCESTOR_ADMISSIONS: dict[str, ConfigAdmission] = {
    "hint_sidecar_ancestor_enabled": ConfigAdmission(
        field_name="hint_sidecar_ancestor_enabled",
        owner=_OWNER,
        consumer=_CONSUMER,
        default_rationale=(
            "Defaults True (lead decision 2026-09-26): with only exact-HEAD sidecars accepted, 0 of 2527 "
            "recorded hints in this repo were T2. On, a proven-ancestor batch sidecar within "
            "hint_sidecar_max_commits_behind serves the hint as hint_available_stale, with an AS-OF line and "
            "the content-dependent fields dropped for a target changed since the sidecar. False is the "
            "rollback path and restores the exact-HEAD-only lookup byte for byte."
        ),
        interaction_analysis=(
            "Master gate read before hint_sidecar_max_commits_behind; off, the bound is never consulted and no "
            "extra git subprocess runs. Only reached when no exact-HEAD sidecar exists and the entitlement "
            "gate allowed the feature, so it never changes a fresh hint or the tier-gated path."
        ),
        deprecation_plan=(
            "Revert criterion: the pre-registered 7-day hint-delivery census. On FAIL the default flips to "
            "False and the threshold is not lowered after the fact. Removal: after two releases default-on "
            "with the census at or above threshold and no stale-as-current incident, delete the flag and the "
            "exact-HEAD-only branch."
        ),
        docs_pointer=_DOCS,
        test_pointer=f"{_TESTS}::test_flag_off_never_reads_an_ancestor",
        budget_decision="admitted",
    ),
    "hint_sidecar_max_commits_behind": ConfigAdmission(
        field_name="hint_sidecar_max_commits_behind",
        owner=_OWNER,
        consumer=_CONSUMER,
        default_rationale=(
            "Defaults to 500, the co-change history window (in commits) the sidecar builder reads; an ancestor "
            "further back than that describes a window that no longer overlaps HEAD's."
        ),
        interaction_analysis=(
            "Read only while hint_sidecar_ancestor_enabled is on. A candidate past the bound is refused as "
            "sidecar_too_far_behind; it does not change which exact-HEAD sidecar is read."
        ),
        deprecation_plan="Retain while the ancestor read path exists; removed with it.",
        docs_pointer=_DOCS,
        test_pointer=f"{_TESTS}::test_ancestor_past_the_bound_is_refused",
        budget_decision="admitted",
    ),
    "hint_sidecar_rebuild_after_commits": ConfigAdmission(
        field_name="hint_sidecar_rebuild_after_commits",
        owner=_OWNER,
        consumer=_REBUILD_CONSUMER,
        default_rationale=(
            "Defaults to 150 commits, 30% of the 500-commit ancestor bound: a rebuild starts well before the "
            "served sidecar falls out of the bound, and a build of several minutes is not started on every commit."
        ),
        interaction_analysis=(
            "Read only while hint_sidecar_refresh_enabled is on, and only for a served ancestor "
            "(hint_available_stale). Capped at hint_sidecar_max_commits_behind when read, so lowering that bound "
            "alone never makes the config invalid. A missing or too-far-behind sidecar requests a build regardless."
        ),
        deprecation_plan="Retain while the ancestor read path exists; removed with it.",
        docs_pointer=_DOCS,
        test_pointer=f"{_REBUILD_TESTS}::test_the_trigger_decides_from_the_lookup",
        budget_decision="admitted",
    ),
    "hint_sidecar_rebuild_min_interval_minutes": ConfigAdmission(
        field_name="hint_sidecar_rebuild_min_interval_minutes",
        owner=_OWNER,
        consumer=_REBUILD_CONSUMER,
        default_rationale=(
            "Defaults to 15 minutes: longer than one whole-repo build on this monorepo, so back-to-back edits "
            "request at most one build at a time even when the build's own lock is released between them."
        ),
        interaction_analysis=(
            "Read only while hint_sidecar_refresh_enabled is on. Measured from a timestamp file in the "
            "shared cache dir, so every worktree sharing that cache shares the interval. The build's flock "
            "single-flights concurrent builds independently of this."
        ),
        deprecation_plan="Retain while the detached rebuild exists; removed with it.",
        docs_pointer=_DOCS,
        test_pointer=f"{_REBUILD_TESTS}::test_each_refusal_spawns_nothing",
        budget_decision="admitted",
    ),
}
