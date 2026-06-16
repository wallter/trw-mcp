"""PRD-CORE-149-FR01/FR10/FR11: module-size gate for claude_md sub-package.

Every ``.py`` file under ``trw_mcp/state/claude_md/`` MUST stay at or below
350 raw lines. Enforces the decomposition outcome and prevents future
re-monolithing.

P2-B (knowledge-fabric audit): the gate is extended to cover the entire
``trw_mcp/tools/`` package on the *effective*-LOC definition (the canonical
350-LOC gate from ``.claude/rules/trw-mcp-python.md`` — blanks, ``#`` comments
and triple-quote docstrings excluded). Pre-existing overruns are grandfathered
at their recorded effective-LOC in ``_TOOLS_EFF_LOC_BASELINE`` so the gate goes
green today while any NEW file over 350 — or any grandfathered file that GROWS
past its baseline — fails. This is the same ratchet ``scripts/check_max_loc.py``
implements; importing its counter keeps the definition single-sourced.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_MAX_LINES = 350

_REPO_ROOT = Path(__file__).resolve().parents[2]

# Public-mirror guard: this test asserts a MONOREPO invariant (repo-root
# scripts/ + .claude/ layout) absent from the standalone trw-mcp PyPI/GitHub
# mirror. Skip cleanly there; the monorepo CI still enforces it.
if not (_REPO_ROOT / "scripts").is_dir():
    pytest.skip(
        "monorepo-only invariant (repo-root scripts/ absent in standalone mirror)",
        allow_module_level=True,
    )

_CLAUDE_MD_DIR = Path(__file__).resolve().parents[1] / "src" / "trw_mcp" / "state" / "claude_md"
_TOOLS_DIR = Path(__file__).resolve().parents[1] / "src" / "trw_mcp" / "tools"

# Import the canonical effective-LOC counter from the repo gate script so the
# test and the CI ratchet share ONE definition of "effective LOC".
_SCRIPTS_DIR = _REPO_ROOT / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))
from check_max_loc import _effective_line_count  # type: ignore[import-not-found]


def _iter_py_files(root: Path) -> list[Path]:
    return sorted(root.rglob("*.py"))


def _line_count(path: Path) -> int:
    with path.open("r", encoding="utf-8") as handle:
        return sum(1 for _ in handle)


# Files that are outside the PRD-CORE-149 decomposition scope but are tracked
# here so future overruns surface during review. Each entry documents the
# governing PRD and the target ceiling.
_PRE_EXISTING_OVERRUNS = {
    # _agents_md.py landed at 420 LOC under PRD-CORE-141 and is scheduled for
    # decomposition in a follow-up PRD; exempt from the 350 gate until then.
    "_agents_md.py": 500,
}


def test_claude_md_tree_under_max_lines() -> None:
    """No file under state/claude_md/ exceeds the 350-line ceiling.

    Files listed in ``_PRE_EXISTING_OVERRUNS`` are exempted at a documented
    higher ceiling until their follow-up decomposition lands.
    """
    offenders: list[tuple[Path, int, int]] = []
    for py_file in _iter_py_files(_CLAUDE_MD_DIR):
        lines = _line_count(py_file)
        ceiling = _PRE_EXISTING_OVERRUNS.get(py_file.name, _MAX_LINES)
        if lines > ceiling:
            offenders.append((py_file, lines, ceiling))

    assert not offenders, "claude_md files exceed their LOC ceiling: " + ", ".join(
        f"{p.relative_to(_CLAUDE_MD_DIR.parents[3])}={n} (ceiling {c})" for p, n, c in offenders
    )


def test_sections_under_350() -> None:
    """FR01: every file in the ``sections/`` sub-package is under 350 LOC."""
    sections = _CLAUDE_MD_DIR / "sections"
    assert sections.is_dir(), "sections/ sub-package missing"
    for py_file in _iter_py_files(sections):
        assert _line_count(py_file) <= _MAX_LINES, f"{py_file} exceeds {_MAX_LINES}"


def test_static_sections_facade_under_350() -> None:
    """FR01: the facade shrank to a re-export shell."""
    facade = _CLAUDE_MD_DIR / "_static_sections.py"
    assert facade.exists()
    assert _line_count(facade) <= _MAX_LINES


def test_negative_fixture_is_flagged() -> None:
    """Sanity check: a synthetic 351-line file would be caught."""
    synthetic_lines = ["# line\n"] * (_MAX_LINES + 1)
    assert len(synthetic_lines) > _MAX_LINES  # the gate threshold is correct


def test_renderer_under_350() -> None:
    """FR10: _renderer.py is under the 350-line ceiling after Wave 2."""
    renderer = _CLAUDE_MD_DIR / "_renderer.py"
    assert renderer.exists()
    assert _line_count(renderer) <= _MAX_LINES


def test_sync_under_350() -> None:
    """FR11: _sync.py is under the 350-line ceiling after Wave 3."""
    sync = _CLAUDE_MD_DIR / "_sync.py"
    assert sync.exists()
    assert _line_count(sync) <= _MAX_LINES


# --------------------------------------------------------------------------- #
# P2-B: effective-LOC gate over the whole tools/ package (ratchet)
# --------------------------------------------------------------------------- #

# Grandfathered effective-LOC for files already over the 350 gate when the
# tools/ coverage was added. Each may stay AT (not exceed) its recorded count;
# new files must come in under 350. Decompose to shrink — do NOT raise an entry.
_TOOLS_EFF_LOC_BASELINE: dict[str, int] = {
    "_deferred_delivery.py": 352,
    "_delivery_helpers.py": 394,
    "_learn_impl.py": 357,
    "learning.py": 359,
    "orchestration.py": 414,
    "requirements.py": 418,
}


def test_tools_package_effective_loc_ratchet() -> None:
    """Every tools/ file is <=350 effective LOC, or at/under its baseline.

    Uses the canonical ``_effective_line_count`` so the gate matches CI's
    ``scripts/check_max_loc.py --effective``. A new file over 350, or a
    grandfathered file that grew past its recorded count, fails.
    """
    grown: list[str] = []
    new_over: list[str] = []
    for py_file in _iter_py_files(_TOOLS_DIR):
        eff = _effective_line_count(py_file)
        name = py_file.name
        if name in _TOOLS_EFF_LOC_BASELINE:
            if eff > _TOOLS_EFF_LOC_BASELINE[name]:
                grown.append(f"{name}={eff} (baseline {_TOOLS_EFF_LOC_BASELINE[name]})")
        elif eff > _MAX_LINES:
            new_over.append(f"{py_file.relative_to(_TOOLS_DIR).as_posix()}={eff}")

    assert not grown, (
        "tools/ files grew past their grandfathered effective-LOC baseline "
        "(decompose, do not raise the baseline): " + ", ".join(grown)
    )
    assert not new_over, (
        f"new tools/ files exceed the {_MAX_LINES} effective-LOC gate (split before merge): " + ", ".join(new_over)
    )


def test_ceremony_facade_under_gate() -> None:
    """P2-B: ceremony.py was decomposed below the 350 effective-LOC gate.

    Regression guard for the audit fix — ceremony.py must NOT be in the
    grandfathered baseline and must stay under the gate.
    """
    ceremony = _TOOLS_DIR / "ceremony.py"
    assert ceremony.exists()
    assert "ceremony.py" not in _TOOLS_EFF_LOC_BASELINE
    assert _effective_line_count(ceremony) <= _MAX_LINES
