"""PRD scoring — file-path grounding penalty (PRD-QUAL-063).

Belongs to the ``_prd_scoring.py`` facade. Re-exported there for
back-compat (test_validation_grounding.py imports both helpers).

Two helpers:
- ``get_project_files`` — cached filesystem listing for fast grounding checks
- ``compute_grounding_penalty`` — multiplicative penalty for hallucinated paths

Extracted as DIST-243 batch 54.
"""

from __future__ import annotations

import functools
import os
from pathlib import Path

from trw_mcp.state.validation._prd_scoring_traceability import (
    _IMPL_REF_RE,
    _TEST_REF_RE,
    _collect_reference_matches,
    _normalize_reference_token,
)


@functools.lru_cache(maxsize=8)
def get_project_files(project_root: Path) -> frozenset[str]:
    """Cache the set of all repository files relative to project_root."""
    files = set()
    for root, _, filenames in os.walk(project_root):
        if ".git" in root or "node_modules" in root or ".venv" in root:
            continue
        rel_root = Path(root).relative_to(project_root)
        for name in filenames:
            files.add(str(rel_root / name) if str(rel_root) != "." else name)
    return frozenset(files)


def compute_grounding_penalty(content: str, project_root: Path | None) -> tuple[float, list[str]]:
    """Compute multiplicative penalty for hallucinated file paths (PRD-QUAL-063).

    Checks all backtick-wrapped paths against the cached project file listing.
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
        project_files = get_project_files(project_root)
        for ref in all_refs:
            clean_ref = ref.strip("` ").split()[0]
            clean_ref = _normalize_reference_token(clean_ref)
            if "(new)" in ref.lower() or "new:" in ref.lower() or "new " in ref.lower():
                continue
            if clean_ref not in project_files:
                hallucinated.append(clean_ref)
        penalty = 0.9 ** len(hallucinated)
        return penalty, sorted(hallucinated)
    except Exception:  # justified: fail-open, missing filesystem context should not zero traceability scoring
        return 1.0, []
