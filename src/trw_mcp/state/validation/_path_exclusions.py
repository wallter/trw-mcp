"""Single source of truth for repo path-index exclude directories.

Both the PRD integrity bare-filename basename index
(``_prd_integrity_paths.py``) and the grounding scorer file census
(``_prd_scoring_grounding.py``) must prune the SAME junk trees so their repo
scans agree. Previously each module declared its own frozenset with a "kept in
sync" comment; a single edit to one site silently broke the invariant. This
constant makes the parity structural — there is exactly one set.

The path-grounding / integrity scans run on the ``trw_prd_validate`` MCP hot
path. Runtime state, dependency trees, build outputs, and browser artifacts can
contain hundreds of thousands of files; descending them made validation latency
scale with generated artifacts rather than with the PRD under validation.
Project-specific bulk trees (e.g. eval data corpora) are added per-project via
the ``path_index_exclude_dirs`` config knob rather than hardcoded here.
"""

from __future__ import annotations

PATH_INDEX_EXCLUDE_DIRS: frozenset[str] = frozenset(
    {
        "node_modules",
        ".venv",
        "venv",
        ".git",
        "__pycache__",
        ".mypy_cache",
        ".pytest_cache",
        ".next",
        ".ruff_cache",
        "coverage",
        "dist",
        "build",
        "target",
        "test-results",
        ".trw",
        # Portable, unambiguous tooling / build-artifact / worktree trees.
        # These names are near-universally tool-owned (never real source), so
        # pruning them is safe as a code default. Generic names like "results"
        # or "embeddings" that COULD hide real source live in the per-project
        # ``path_index_exclude_dirs`` knob instead, never here.
        ".hypothesis",
        "htmlcov",
        ".tox",
        "site-packages",
        "worktrees",
    }
)
