"""Module-size gate for the scoring ``_complexity`` facade and its siblings.

The adaptive-ceremony logic was decomposed (PRD-CORE-060) into a thin
``_complexity.py`` facade (classification + phase contracts) plus the cohesive
``_tier_score.py`` sibling Module (tier-aware ceremony scoring). Both files
MUST stay at or below 350 raw lines so the decomposition sticks and
``_complexity.py`` cannot re-monolith.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_MAX_LINES = 350

_SCORING_DIR = Path(__file__).resolve().parents[1] / "src" / "trw_mcp" / "scoring"

_COMPLEXITY_MODULES = (
    "_complexity.py",
    "_tier_score.py",
)


def _line_count(path: Path) -> int:
    with path.open("r", encoding="utf-8") as handle:
        return sum(1 for _ in handle)


@pytest.mark.parametrize("module_name", _COMPLEXITY_MODULES)
def test_complexity_module_under_max_lines(module_name: str) -> None:
    """Each complexity scoring module is under the 350-line ceiling."""
    path = _SCORING_DIR / module_name
    assert path.exists(), f"{module_name} missing from scoring package"
    lines = _line_count(path)
    assert lines <= _MAX_LINES, f"{module_name} has {lines} raw lines (ceiling {_MAX_LINES})"


def test_back_compat_imports_preserved() -> None:
    """Tier-score symbols stay importable from both the facade and ``_complexity``."""
    from trw_mcp.scoring import _TIER_EXPECTATIONS as facade_table
    from trw_mcp.scoring import compute_tier_ceremony_score as facade_score
    from trw_mcp.scoring._complexity import _TIER_EXPECTATIONS as complexity_table
    from trw_mcp.scoring._complexity import compute_tier_ceremony_score as complexity_score
    from trw_mcp.scoring._tier_score import compute_tier_ceremony_score as tier_score

    # All three import paths resolve to the same object (single source of truth).
    assert facade_score is complexity_score is tier_score
    assert facade_table is complexity_table
