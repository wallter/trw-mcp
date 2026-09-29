"""Admission records for the ANCHOR-HUB-DOWNRANK tunables.

Belongs to the ``_field_admission_registry.py`` data table, which merges this
mapping into :data:`~trw_mcp.models.config._field_admission.FIELD_ADMISSIONS`.
Split out, like every per-PRD admission table, so the registry stays under the
module-size gate.

Imports nothing from the rest of ``trw_mcp.models.config`` except the record
type, so it cannot create an import cycle with ``TRWConfig``.
"""

from __future__ import annotations

from trw_mcp.models.config._field_admission_registry_types import ConfigAdmission

_DOCS = "trw-mcp/CHANGELOG.md (ANCHOR-HUB-DOWNRANK entry); calibration and measurement plan in the internal pre-registration"
_CONSUMER = "trw_mcp.tools._learnings_collector.collect_learnings (pre-edit hint, trw_code hint mode)"

HINT_HUB_ADMISSIONS: dict[str, ConfigAdmission] = {
    "hint_hub_downrank": ConfigAdmission(
        field_name="hint_hub_downrank",
        owner="ANCHOR-HUB-DOWNRANK",
        consumer=_CONSUMER,
        default_rationale=(
            "Defaults True: it only reorders the hint on hub files, where anchor-only lessons were "
            "answer-relevant about 7% of the time (ANCHOR-TIGHTEN result); every non-hub file's hint is "
            "byte-identical. False is the rollback path and restores the anchored-first order exactly."
        ),
        interaction_analysis=(
            "Master gate read before hint_hub_threshold; off, the threshold is never consulted and the "
            "anchored page keeps its 2 x top_n limit. Has no effect when the daemon does not serve "
            "memory_anchored, because then no file has anchored rows."
        ),
        deprecation_plan="Retain until the post-release paired measurement in the prereg decides adopt or revert.",
        docs_pointer=_DOCS,
        test_pointer="trw-mcp/tests/test_hint_hub_downrank.py::test_flag_off_keeps_anchored_first_on_a_hub",
        budget_decision="admitted",
    ),
    "hint_hub_threshold": ConfigAdmission(
        field_name="hint_hub_threshold",
        owner="ANCHOR-HUB-DOWNRANK",
        consumer=_CONSUMER,
        default_rationale=(
            "Defaults to 67, the pooled 95th percentile of active anchored lessons per file on the httpx + click "
            "benchmark stores; it marks click core.py (320), httpx _client.py (99) and _models.py (68)."
        ),
        interaction_analysis=(
            "Read only while hint_hub_downrank is on. Also sizes the anchored page (threshold + 1 rows) "
            "the hint fetches per edit. A fixed count does not scale with store size: a much larger "
            "store needs a larger value (follow-up ANCHOR-HUB-RELATIVE)."
        ),
        deprecation_plan="Retain; replaced only if a relative hub test (share of namespace postings) ships.",
        docs_pointer=_DOCS,
        test_pointer="trw-mcp/tests/test_hint_hub_downrank.py::test_threshold_boundary",
        budget_decision="admitted",
    ),
}
