"""Module-size gate for the scoring ``_recall`` facade and its siblings.

The recall scoring logic was decomposed (PRD-FIX-010 / PRD-CORE-102 /
PRD-CORE-116) into a thin ``_recall.py`` facade plus cohesive sibling modules
(``_recall_context``, ``_recall_domains``, ``_recall_prune``). Every one of
those files MUST stay at or below 350 raw lines so the decomposition sticks and
``_recall.py`` cannot re-monolith.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_MAX_LINES = 350

_SCORING_DIR = Path(__file__).resolve().parents[1] / "src" / "trw_mcp" / "scoring"

_RECALL_MODULES = (
    "_recall.py",
    "_recall_context.py",
    "_recall_domains.py",
    "_recall_prune.py",
)


def _line_count(path: Path) -> int:
    with path.open("r", encoding="utf-8") as handle:
        return sum(1 for _ in handle)


@pytest.mark.parametrize("module_name", _RECALL_MODULES)
def test_recall_module_under_max_lines(module_name: str) -> None:
    """Each recall scoring module is under the 350-line ceiling."""
    path = _SCORING_DIR / module_name
    assert path.exists(), f"{module_name} missing from scoring package"
    lines = _line_count(path)
    assert lines <= _MAX_LINES, f"{module_name} has {lines} raw lines (ceiling {_MAX_LINES})"
