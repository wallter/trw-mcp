"""Admission record for the PRD-FIX-124 auto-recall scan cap.

Belongs to the ``_field_admission_registry.py`` data table, which merges this
mapping into :data:`FIELD_ADMISSIONS`. Split out for the same reason the
registry itself was split from ``_field_admission.py`` and the PRD-FIX-123 table
was split from the registry: the table grows once per new public field, and the
registry already sits within a handful of lines of the module-size gate.

Imports nothing from the rest of ``trw_mcp.models.config`` except the record
type, so it cannot create an import cycle with ``TRWConfig``.
"""

from __future__ import annotations

from trw_mcp.models.config._field_admission_registry_types import ConfigAdmission

_DOCS = "docs/documentation/operational-knowledge/auto-recall-calibration.md"
_HOOK = "trw_mcp.data.hooks.user-prompt-submit.sh (embedded auto-recall scorer)"

#: ``auto_recall_min_score`` is not here: it is a legacy-admitted field whose
#: DEFAULT PRD-FIX-124-FR06 recalibrates (0.7 -> 0.35). Only the scan cap is a
#: new public field, promoted from a magic ``MAX_SCAN_FILES = 500`` inside the
#: hook's heredoc that was reachable only through an undocumented env var.
AUTO_RECALL_ADMISSIONS: dict[str, ConfigAdmission] = {
    "auto_recall_scan_cap": ConfigAdmission(
        field_name="auto_recall_scan_cap",
        owner="PRD-FIX-124-FR07",
        consumer=_HOOK,
        default_rationale=(
            "Defaults to 10000 so a whole store is scored rather than a recency window. The "
            "value it replaces (500, hard-coded in the hook) covered 7.8% of this repo's "
            "6,436-entry store, and because the cap is applied AFTER an mtime sort the excluded "
            "92.2% were excluded by age, never by irrelevance — a six-month-old learning could "
            "not be a candidate no matter how exactly it matched. 10000 reads that store whole "
            "in ~190ms warm against the hook's 500ms deadline, leaving roughly half the budget "
            "as headroom. Bounded ge=1 so 0 or a negative value cannot silently disable recall; "
            "an unparseable value falls back to this default rather than propagating."
        ),
        interaction_analysis=(
            "Read only by the UserPromptSubmit auto-recall scan, where it bounds the entry "
            "population that is both scored AND used as the IDF document-frequency base, so "
            "raising it makes term weights more informative as well as widening the candidate "
            "set. It interacts with the 500ms deadline rather than with another field: when the "
            "scan cannot finish, the scorer emits the best matches accumulated so far and "
            "records decision=deadline (FR08), so an over-large cap degrades to truncation "
            "rather than to a missed prompt. TRW_AUTO_RECALL_SCAN_CAP remains the documented "
            "per-invocation override, mirroring the other four auto_recall_* knobs."
        ),
        deprecation_plan=(
            "Retain; it is the sole scan-size knob and the FR07 rollback lever for the "
            "cold-cache latency risk. Removing it reinstates a magic number in a shipped hook."
        ),
        docs_pointer=_DOCS,
        test_pointer="trw-mcp/tests/test_auto_recall_scoring.py::test_scan_cap_is_typed_config",
        budget_decision="admitted",
    ),
}

__all__ = ["AUTO_RECALL_ADMISSIONS"]
