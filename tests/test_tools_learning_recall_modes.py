"""Tests for recall ranking and default stub payload shaping.

PRD-CORE-294 FR01 deleted the compact/ultra_compact/token_budget recall
modes: the default response is now always the byte-budgeted stub shape
``{id, claim, anchor?}``. The mode-shaping tests that used to live in this
file (compact strip, wildcard auto-compact, wildcard compact override,
ultra_compact minimal payload, ultra_compact summary truncation) tested a
now-deleted branch and are gone rather than adapted — there is no
equivalent behavior left to assert. Ranking and cap tests are preserved.

PRD-CORE-280 slice e1: none of these care how memory behaves (pure
ranking/paging mechanics), so they route through a fake store rather than the
in-process SQLite store the tools would otherwise open via the unpinned
``TRW_PROJECT_ROOT`` ``set_project_root`` sets up.

Known fake quirk (per the e1 lead): ``tests/_memory_fixtures.py``'s
``fake_memory_store`` fixture pins the project namespace to ``FAKE_NAMESPACE``
("project:test"), but ``FakeMemoryStore.recall`` searches "default" only --
so a row ``store_learning`` wrote under the pinned namespace is invisible to
``trw_recall``. Rather than edit the shared fixture (other batches touch it
too), this file defines its own ``fake_memory_store`` override that pins
"default" instead, matching what ``FakeMemoryStore.recall`` actually searches.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests._memory_store_fake import FakeMemoryStore
from tests._tools_learning_shared import (
    _get_tools,
    set_project_root,  # noqa: F401 -- autouse fixture disables dedup (f4ca661c9 flipped embeddings_enabled default True)
)

pytestmark = pytest.mark.usefixtures("fake_memory_store")


@pytest.fixture
def fake_memory_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FakeMemoryStore:
    """Override of ``tests._memory_fixtures.fake_memory_store`` pinned to "default".

    See the module docstring's "Known fake quirk" note: these tests need
    ``FakeMemoryStore.recall`` (which only searches "default") to find what
    ``store_learning`` writes under the namespace ``selected_store`` returns.
    """
    from trw_mcp.state import _store_selection

    store = FakeMemoryStore()
    monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setattr(_store_selection, "selected_store", lambda _trw_dir: (store, "default"))
    return store


class TestRecallUtilityRanking:
    """Tests for PRD-CORE-004 Phase 1b — utility re-ranking in trw_recall."""

    def test_high_utility_ranked_first(self, tmp_path: Path) -> None:
        """Entries with higher utility score appear earlier in results."""
        tools = _get_tools()

        # Create two entries with same keyword but different utility
        tools["trw_learn"].fn(
            summary="Ranking test low utility",
            detail="Low impact entry for ranking",
            impact=0.2,
        )
        tools["trw_learn"].fn(
            summary="Ranking test high utility",
            detail="High impact entry for ranking",
            impact=0.9,
        )

        result = tools["trw_recall"].fn(query="ranking test")
        assert len(result["learnings"]) == 2
        # Higher impact should rank first (lambda blends utility into score).
        # PRD-CORE-294 FR01: default rows are stubs whose "claim" is the
        # one-line summary, not a "summary" field.
        claims = [str(entry.get("claim", "")) for entry in result["learnings"]]
        high_idx = next(i for i, s in enumerate(claims) if "high" in s)
        low_idx = next(i for i, s in enumerate(claims) if "low" in s)
        assert high_idx < low_idx

    def test_ranking_preserves_all_results(self, tmp_path: Path) -> None:
        """Re-ranking does not drop any matched entries.

        ``total_matches`` reflects the ranked/deduped/capped set (PRD-CORE-294
        FR01), independent of how many stubs the byte budget presents, so it
        is the right field to assert "nothing was dropped from ranking".
        """
        tools = _get_tools()

        for i in range(5):
            tools["trw_learn"].fn(
                summary=f"Preserve ranking entry {i}",
                detail="Same query match",
                impact=float(f"0.{i + 1}"),
            )

        result = tools["trw_recall"].fn(query="preserve ranking entry")
        assert result["total_matches"] == 5


class TestRecallCap:
    """Tests for PRD-FIX-013 — bounded recall via max_results."""

    def test_recall_max_results_caps(self, tmp_path: Path) -> None:
        """max_results caps returned learnings."""
        tools = _get_tools()
        for i in range(10):
            tools["trw_learn"].fn(
                summary=f"Cap test entry number {i}",
                detail=f"Detail {i}",
                impact=0.8,
            )

        result = tools["trw_recall"].fn(query="cap test entry", max_results=5)
        assert len(result["learnings"]) == 5
        assert result["total_matches"] == 5

    def test_recall_max_results_zero_unlimited(self, tmp_path: Path) -> None:
        """max_results=0 returns all matches (subject to the byte budget)."""
        tools = _get_tools()
        for i in range(10):
            tools["trw_learn"].fn(
                summary=f"Unlimited test entry num {i}",
                detail=f"Detail {i}",
                impact=0.8,
            )

        result = tools["trw_recall"].fn(
            query="unlimited test entry",
            max_results=0,
        )
        assert result["total_matches"] == 10
        # All 10 small stubs fit the default 3,000-byte budget.
        assert len(result["learnings"]) == 10
