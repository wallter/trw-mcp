"""Edge-case recall tests for state/memory_adapter.py.

PRD-CORE-280 slice e1: the corruption/recovery classes that used to live here
(``test_store_learning_retries_once_after_corruption``,
``test_store_learning_user_tier_retries_once_after_corruption``,
``test_store_learning_does_not_retry_after_strict_refusal``,
``test_recover_and_reset_backend_propagates_strict_refusal``,
``test_recall_learnings_defers_recovery_after_corruption``,
``test_recall_learnings_skips_when_recovery_in_progress``) patched the SQLite
singleton accessor / ``_recover_and_reset_backend`` to exercise the interim
SQLite store's corruption-retry branch directly. That interim store class is
one of the names the next slice deletes outright, so that branch has no
future; deleted rather than ported. ``TestGetBackendDirectoryCreation`` tested
the singleton accessor itself and is deleted for the same reason.

The remaining tests seed rows directly with ``store.put(..., "default", ...)``
— the namespace the fake's ``recall()`` searches — rather than through
``store_learning``, since the fixture pins ``store_learning`` writes to
``FAKE_NAMESPACE`` instead.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from trw_mcp.state.memory_adapter import recall_learnings, update_learning

from ._memory_store_fake import FakeMemoryStore


@pytest.fixture
def trw_dir(tmp_project: Path) -> Path:
    """Return the isolated .trw directory for memory-adapter tests."""
    trw = tmp_project / ".trw"
    (trw / "memory").mkdir(exist_ok=True)
    return trw


class TestRecallLearningsBoundary:
    def test_empty_string_query_treated_as_wildcard(self, fake_memory_store: FakeMemoryStore, trw_dir: Path) -> None:
        """Empty string query is treated as wildcard (returns all entries)."""
        fake_memory_store.put("Entry one", "default", {"entry_id": "L-eq1", "detail": "d1"})
        fake_memory_store.put("Entry two", "default", {"entry_id": "L-eq2", "detail": "d2"})
        results = recall_learnings(trw_dir, "")
        assert len(results) == 2

    def test_whitespace_query_treated_as_wildcard(self, fake_memory_store: FakeMemoryStore, trw_dir: Path) -> None:
        """Whitespace-only query is treated as wildcard."""
        fake_memory_store.put("Only entry", "default", {"entry_id": "L-wq1", "detail": "d"})
        results = recall_learnings(trw_dir, "   ")
        assert len(results) == 1

    def test_max_results_zero_uses_default_limit(self, fake_memory_store: FakeMemoryStore, trw_dir: Path) -> None:
        """max_results=0 falls back to _MAX_ENTRIES for wildcard queries."""
        fake_memory_store.put("Max results test", "default", {"entry_id": "L-mr1", "detail": "d"})
        results = recall_learnings(trw_dir, "*", max_results=0)
        assert len(results) >= 1

    def test_max_results_zero_on_keyword_search(self, fake_memory_store: FakeMemoryStore, trw_dir: Path) -> None:
        """max_results=0 falls back to _MAX_ENTRIES for keyword queries."""
        fake_memory_store.put("Keyword max test", "default", {"entry_id": "L-mk1", "detail": "d"})
        results = recall_learnings(trw_dir, "Keyword", max_results=0)
        assert isinstance(results, list)

    def test_status_filter_with_keyword_search(self, fake_memory_store: FakeMemoryStore, trw_dir: Path) -> None:
        """Status filter is applied during keyword search, not just wildcard."""
        fake_memory_store.put("Active keyword entry", "default", {"entry_id": "L-sf1", "detail": "d"})
        fake_memory_store.put("Obsolete keyword entry", "default", {"entry_id": "L-sf2", "detail": "d"})
        update_learning(trw_dir, "L-sf2", status="obsolete")
        results = recall_learnings(trw_dir, "keyword", status="active")
        ids = [str(r["id"]) for r in results]
        assert "L-sf1" in ids

    def test_tag_filter_not_applied_on_keyword_search(self, fake_memory_store: FakeMemoryStore, trw_dir: Path) -> None:
        """Tag filter on keyword search is handled by the store's search, not the wildcard path."""
        fake_memory_store.put("Tagged entry", "default", {"entry_id": "L-tf1", "detail": "d", "tags": ["python"]})
        fake_memory_store.put("Untagged entry", "default", {"entry_id": "L-tf2", "detail": "d", "tags": ["rust"]})
        results = recall_learnings(trw_dir, "entry", tags=["python"])
        assert isinstance(results, list)


class TestRecallMinImpactPostFilter:
    def test_min_impact_filters_after_backend_query(self, fake_memory_store: FakeMemoryStore, trw_dir: Path) -> None:
        """min_impact is applied as a post-filter on the converted dicts."""
        fake_memory_store.put("Low impact recall", "default", {"entry_id": "L-pf1", "detail": "d", "importance": 0.2})
        fake_memory_store.put("High impact recall", "default", {"entry_id": "L-pf2", "detail": "d", "importance": 0.8})
        results = recall_learnings(trw_dir, "*", min_impact=0.5)
        ids = [str(r["id"]) for r in results]
        assert "L-pf1" not in ids
        assert "L-pf2" in ids

    def test_min_impact_on_keyword_search(self, fake_memory_store: FakeMemoryStore, trw_dir: Path) -> None:
        """min_impact filter works with keyword search too."""
        fake_memory_store.put("Keyword filter low", "default", {"entry_id": "L-kf1", "detail": "d", "importance": 0.1})
        fake_memory_store.put("Keyword filter high", "default", {"entry_id": "L-kf2", "detail": "d", "importance": 0.9})
        results = recall_learnings(trw_dir, "Keyword filter", min_impact=0.5)
        for r in results:
            assert float(str(r["impact"])) >= 0.5
