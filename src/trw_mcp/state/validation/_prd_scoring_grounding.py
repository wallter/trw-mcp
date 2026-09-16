"""PRD scoring — file-path grounding penalty (PRD-QUAL-063).

Belongs to the ``_prd_scoring.py`` facade. Re-exported there for back-compat.

Public helper:
- ``compute_grounding_penalty`` — multiplicative penalty for hallucinated paths

Extracted as DIST-243 batch 54.
"""

from __future__ import annotations

import re
from pathlib import Path

import structlog

from trw_mcp.models.config import get_config
from trw_mcp.state.validation._path_exclusions import PATH_INDEX_EXCLUDE_DIRS
from trw_mcp.state.validation._prd_path_markers import has_trailing_planned_marker
from trw_mcp.state.validation._prd_scoring_traceability import (
    _IMPL_REF_RE,
    _TEST_REF_RE,
    _normalize_reference_token,
)

logger = structlog.get_logger(__name__)

# Trailing ``:line`` or ``:line:col`` anchor on a path reference
# (e.g. ``src/foo.py:42`` / ``src/foo.py:42:5``). Stripped before the
# existence probe so a legitimately-existing file cited with a line anchor is
# not misread as a hallucinated path.
_LINE_SUFFIX_RE = re.compile(r":\d+(?::\d+)?$")

# Module-level alias of the single shared exclude-dir constant. Retained under
# the historical name as a patch seam; parity with the integrity walk is now
# structural (one constant), not a "kept in sync" convention.
_PROJECT_FILE_EXCLUDE_DIRS: frozenset[str] = PATH_INDEX_EXCLUDE_DIRS


def _clean_reference_token(ref: str) -> str:
    clean_ref = ref.strip("` ").split()[0]
    clean_ref = _normalize_reference_token(clean_ref)
    # Strip a trailing :line or :line:col anchor (e.g. ``src/foo.py:42``) so a
    # path-qualified reference to an existing file is not treated as missing.
    return _LINE_SUFFIX_RE.sub("", clean_ref)


def _reference_existence_requirements(content: str) -> dict[str, bool]:
    """Map each scoring reference to whether any occurrence requires existence.

    A trailing planned marker applies only to its own occurrence. If the same
    path appears elsewhere unmarked, that occurrence remains eligible for the
    grounding penalty.
    """
    requirements: dict[str, bool] = {}
    for pattern in (_IMPL_REF_RE, _TEST_REF_RE):
        for match in pattern.finditer(content):
            clean_ref = _clean_reference_token(match.group(0).strip("`"))
            if clean_ref:
                requirements[clean_ref] = requirements.get(clean_ref, False) or not has_trailing_planned_marker(
                    content, match.end()
                )
    return requirements


def _resolve_extra_roots(project_root: Path, extra_roots: list[Path] | None) -> list[Path]:
    """Resolve sibling-repo roots to consult in addition to *project_root*.

    When *extra_roots* is explicitly supplied it is used verbatim (a test
    seam). Otherwise the SAME ``additional_repo_roots`` config knob the PRD
    integrity checker consults is honored (DRY — one knob, not new plumbing),
    resolving relative entries against *project_root*. Defaults to an empty
    list so the single-repo contract is byte-identical.
    """
    if extra_roots is not None:
        return list(extra_roots)
    try:
        raw_roots = getattr(get_config(), "additional_repo_roots", []) or []
    except Exception:  # justified: config unavailable must not break scoring
        logger.warning("grounding_extra_roots_config_unavailable", exc_info=True)
        return []
    resolved: list[Path] = []
    for raw in raw_roots:
        candidate = Path(str(raw))
        resolved.append(candidate if candidate.is_absolute() else project_root / candidate)
    return resolved


def _exists_under_any_root(rel_path: str, roots: list[Path]) -> bool:
    """True when *rel_path* resolves inside and exists under any of *roots*.

    Each root keeps the path-escape guard (``relative_to``) so a traversal
    attempt that resolves outside every root is never counted as present.
    """
    for root in roots:
        try:
            full_path = (root / rel_path).resolve()
            full_path.relative_to(root.resolve())
        except ValueError:
            continue
        if full_path.exists():
            return True
    return False


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


def compute_grounding_penalty(
    content: str,
    project_root: Path | None,
    *,
    extra_roots: list[Path] | None = None,
) -> tuple[float, list[str]]:
    """Compute multiplicative penalty for hallucinated file paths (PRD-QUAL-063).

    Checks direct repo-relative references from this PRD with bounded
    per-reference filesystem probes.

    Earlier versions built a full repository file census via ``os.walk`` for
    every cold process. In this monorepo that crossed generated ``.trw`` and
    research artifacts, making ``trw_prd_validate`` vulnerable to multi-minute
    hangs. The scorer now scales with the number of references in the PRD, not
    with total workspace size. Bare filenames and wildcards are left to the
    PRD integrity checker, which has its own pruned basename index.

    Residual false-positive hardening (feedback sub_5qbmT6WPNoP58rlv item 7):

    - A trailing ``:line`` / ``:line:col`` anchor is stripped before the probe,
      so ``src/foo.py:42`` resolves to the existing ``src/foo.py``.
    - Greenfield annotations placed OUTSIDE the backticks — the TRW convention
      of a backticked path followed by ``(new)`` — exempt the reference (see
      :func:`_reference_existence_requirements`).
    - References are considered present when they exist under *project_root*
      OR any sibling-repo root. Roots come from the SAME
      ``additional_repo_roots`` config knob the PRD integrity checker uses
      (DRY); *extra_roots* overrides that resolution for tests.

    Returns:
        tuple[penalty_multiplier, list[hallucinated_paths]]
    """
    if not project_root:
        return 1.0, []
    roots = [project_root, *_resolve_extra_roots(project_root, extra_roots)]
    reference_requirements = _reference_existence_requirements(content)
    hallucinated: list[str] = []
    try:
        for clean_ref, requires_existence in reference_requirements.items():
            # Greenfield annotation OUTSIDE the backticks — to-be-created file.
            if not requires_existence:
                continue
            if not _is_direct_repo_reference(clean_ref):
                continue
            if not _exists_under_any_root(clean_ref, roots):
                hallucinated.append(clean_ref)
        penalty = 0.9 ** len(hallucinated)
        return penalty, sorted(hallucinated)
    except Exception:  # justified: fail-open, missing filesystem context should not zero traceability scoring
        return 1.0, []
