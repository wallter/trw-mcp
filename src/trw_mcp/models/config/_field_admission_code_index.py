"""Admission record for the PRD-CORE-300-FR15 code-index budgets.

Belongs to the ``_field_admission_registry.py`` data table, which merges this
mapping into :data:`~trw_mcp.models.config._field_admission.FIELD_ADMISSIONS`.
One nested policy object carries all seven budgets, so the public config grows
by one field rather than seven.

Imports nothing from the rest of ``trw_mcp.models.config`` except the record
type, so it cannot create an import cycle with ``TRWConfig``.
"""

from __future__ import annotations

from trw_mcp.models.config._field_admission_registry_types import ConfigAdmission

CODE_INDEX_ADMISSIONS: dict[str, ConfigAdmission] = {
    "code_index_bounds": ConfigAdmission(
        field_name="code_index_bounds",
        owner="PRD-CORE-300-FR15",
        consumer=(
            "trw_mcp.tools.code_index.build_code_index (build budgets, via the "
            "trw-mcp code index CLI command) and trw_mcp.tools.code_search._bounds "
            "(query budgets)"
        ),
        default_rationale=(
            "Defaults cover this monorepo (about 21k indexable files outside worktrees) with headroom: "
            "500k entries, 50k files, 512 MiB of source and 300 s per build; 200k candidate rows, 256 KiB "
            "of response and 20 s per query, the last kept under the client tool timeout. On 2026-09-24 "
            "an unbounded build walked 289,760 files and a query took the MCP server to 62 GB."
        ),
        interaction_analysis=(
            "Applies after code_index_exclude_dirs and code_index_max_file_bytes have pruned the walk, so "
            "it bounds what survives them. A crossed build budget leaves the published store and manifest "
            "unchanged; a crossed query budget fails that query only. Peak memory is not a budget: it is "
            "measured by the test suite on a store ten times query_max_rows."
        ),
        deprecation_plan="Retain; removing it restores the unbounded walk and query that exhausted memory.",
        docs_pointer="docs/requirements-aare-f/prds/PRD-CORE-300-minimum-mcp-tool-surface.md",
        test_pointer="trw-mcp/tests/test_code_index_bounds.py",
        budget_decision="admitted",
    ),
}

__all__ = ["CODE_INDEX_ADMISSIONS"]
