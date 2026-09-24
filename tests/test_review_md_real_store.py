"""REVIEW.md against a real store: a learning carrying ANY review tag is flagged (L-hzMb).

The recall store ANDs its ``tags`` filter, so passing all six review tags in one
recall matched only a learning carrying every one of them. REVIEW.md was empty in
practice, and the empty section read as "no learnings" rather than "the filter
matched nothing". The mocked tests in ``test_review_md.py`` could not see it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests._memory_fixtures import DaemonCheckout
from trw_mcp.models.config import reload_config
from trw_mcp.state.claude_md._review_md import _REVIEW_TAGS
from trw_mcp.state.claude_md._sync import generate_review_md

pytestmark = pytest.mark.unit


@pytest.fixture
def trw_dir(daemon_checkout: DaemonCheckout, monkeypatch: pytest.MonkeyPatch) -> Path:
    for key in ("TRW_DEDUP_ENABLED", "TRW_EMBEDDINGS_ENABLED"):
        monkeypatch.setenv(key, "false")
    reload_config()
    yield daemon_checkout.trw_dir
    reload_config()


def _store(trw_dir: Path, lid: str, summary: str, tags: list[str], impact: float = 0.9) -> None:
    from trw_mcp.state.memory_adapter import store_learning

    store_learning(trw_dir, lid, summary, "detail", tags=tags, impact=impact)


@pytest.mark.parametrize("tag", _REVIEW_TAGS)
def test_a_learning_with_one_review_tag_is_flagged(trw_dir: Path, tag: str) -> None:
    _store(trw_dir, "L-one", f"Only tagged {tag}", [tag])

    result = generate_review_md(trw_dir, trw_dir.parent)

    assert result["rules_count"] == 1
    assert f"Only tagged {tag} (L-one)" in (trw_dir.parent / "REVIEW.md").read_text(encoding="utf-8")


def test_learnings_with_different_review_tags_are_each_flagged_once(trw_dir: Path) -> None:
    _store(trw_dir, "L-a", "Gotcha one", ["gotcha"])
    _store(trw_dir, "L-b", "Security one", ["security", "bug"])
    _store(trw_dir, "L-c", "Unrelated", ["docs"])

    generate_review_md(trw_dir, trw_dir.parent)

    text = (trw_dir.parent / "REVIEW.md").read_text(encoding="utf-8")
    assert text.count("(L-a)") == 1
    assert text.count("(L-b)") == 1
    assert "(L-c)" not in text


def test_an_older_high_impact_learning_beats_newer_lower_ones(trw_dir: Path) -> None:
    """Selection is the top 20 by impact across the union, not the 20 newest per tag."""
    _store(trw_dir, "L-aaa-old", "Old but critical", ["gotcha"], impact=0.95)
    for n in range(25):
        _store(trw_dir, f"L-new{n:02d}", f"Newer {n}", ["gotcha"], impact=0.75)

    result = generate_review_md(trw_dir, trw_dir.parent)

    text = (trw_dir.parent / "REVIEW.md").read_text(encoding="utf-8")
    assert result["rules_count"] == 20
    assert "Old but critical (L-aaa-old)" in text


def test_an_empty_match_names_the_filter(trw_dir: Path) -> None:
    """An empty section must say what it searched for, not read as 'no learnings'."""
    _store(trw_dir, "L-low", "Below threshold", ["gotcha"], impact=0.3)

    result = generate_review_md(trw_dir, trw_dir.parent)

    text = (trw_dir.parent / "REVIEW.md").read_text(encoding="utf-8")
    assert result["rules_count"] == 0
    assert "any of: " + ", ".join(_REVIEW_TAGS) in text
    assert "impact >= 0.7" in text
