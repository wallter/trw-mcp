"""PRD scoring — file-path grounding penalty (PRD-QUAL-063).

Belongs to the ``_prd_scoring.py`` facade. Re-exported there for
back-compat (test_validation_grounding.py imports both helpers).

Two helpers:
- ``get_project_files`` — pruned filesystem listing for diagnostics/tests
- ``compute_grounding_penalty`` — multiplicative penalty for hallucinated paths

Extracted as DIST-243 batch 54.
"""

from __future__ import annotations

import os
from pathlib import Path

from trw_mcp.state.validation._prd_scoring_traceability import (
    _IMPL_REF_RE,
    _TEST_REF_RE,
    _collect_reference_matches,
    _normalize_reference_token,
)

_PROJECT_FILE_EXCLUDE_DIRS: frozenset[str] = frozenset(
    {
        ".git",
        ".mypy_cache",
        ".next",
        ".pytest_cache",
        ".ruff_cache",
        ".trw",
        ".venv",
        "__pycache__",
        "build",
        "coverage",
        "dist",
        "node_modules",
        "target",
        "test-results",
        "venv",
    }
)
"""Subtrees that must never be descended during repo file census helpers.

The path-grounding scorer is invoked by ``trw_prd_validate`` on an MCP hot
path. Runtime state, dependency trees, build outputs, and browser artifacts can
contain hundreds of thousands of files; scanning them made validation latency
scale with generated artifacts rather than with the PRD being validated.
"""


def get_project_files(project_root: Path) -> frozenset[str]:
    """Return repository files relative to ``project_root``.

    This helper is kept for backward-compatible tests and diagnostics. The
    live PRD grounding path intentionally avoids calling it because validating
    one PRD only requires checking the references present in that PRD.
    """
    files: set[str] = set()
    for root, dirnames, filenames in os.walk(project_root):
        dirnames[:] = [name for name in dirnames if name not in _PROJECT_FILE_EXCLUDE_DIRS]
        rel_root = Path(root).relative_to(project_root)
        for name in filenames:
            files.add(str(rel_root / name) if str(rel_root) != "." else name)
    return frozenset(files)


def _clean_reference_token(ref: str) -> str:
    clean_ref = ref.strip("` ").split()[0]
    return _normalize_reference_token(clean_ref)


def _is_direct_repo_reference(ref: str) -> bool:
    """Return True when ``ref`` can be checked with one bounded ``exists`` call."""
    path = Path(ref)
    if not ref or ref.startswith(("/", "~")):
        return False
    if any(part in {"", ".", ".."} for part in path.parts):
        return False
    if not any(separator in ref for separator in ("/", "\\")):
        # Bare filenames are handled by PRD integrity's bounded basename index.
        # Do not trigger an O(repo files) census from the grounding scorer.
        return False
    return not any(char in ref for char in "*?[]{}")


def compute_grounding_penalty(content: str, project_root: Path | None) -> tuple[float, list[str]]:
    """Compute multiplicative penalty for hallucinated file paths (PRD-QUAL-063).

    Checks direct repo-relative references from this PRD with bounded
    per-reference filesystem probes.

    Earlier versions built a full repository file census via ``os.walk`` for
    every cold process. In this monorepo that crossed generated ``.trw`` and
    research artifacts, making ``trw_prd_validate`` vulnerable to multi-minute
    hangs. The scorer now scales with the number of references in the PRD, not
    with total workspace size. Bare filenames and wildcards are left to the
    PRD integrity checker, which has its own pruned basename index.

    Paths containing 'new: ' or ending with '(new)' are exempt.

    Returns:
        tuple[penalty_multiplier, list[hallucinated_paths]]
    """
    if not project_root:
        return 1.0, []
    impl_refs = _collect_reference_matches(content, _IMPL_REF_RE)
    test_refs = _collect_reference_matches(content, _TEST_REF_RE)
    all_refs = impl_refs | test_refs
    hallucinated: list[str] = []
    try:
        for ref in all_refs:
            clean_ref = _clean_reference_token(ref)
            if "(new)" in ref.lower() or "new:" in ref.lower() or "new " in ref.lower():
                continue
            if not _is_direct_repo_reference(clean_ref):
                continue
            full_path = (project_root / clean_ref).resolve()
            try:
                full_path.relative_to(project_root.resolve())
            except ValueError:
                hallucinated.append(clean_ref)
                continue
            if not full_path.exists():
                hallucinated.append(clean_ref)
        penalty = 0.9 ** len(hallucinated)
        return penalty, sorted(hallucinated)
    except Exception:  # justified: fail-open, missing filesystem context should not zero traceability scoring
        return 1.0, []
