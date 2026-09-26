"""Edge-case update and reset tests for state/memory_adapter.py.

PRD-CORE-280: ``increment_session_counts`` and ``update_access_tracking``
(and their batch-SQL / per-entry-fallback internals) are gone, replaced by
the single ``record_surfaced`` store-seam call. Behaviour-level coverage for
that contract (dedupe, mixed valid/missing ids, empty-list no-op, double
increment, the ``session_start`` flag) lives in one place,
``tests/test_record_surfaced.py``, rather than here.
"""

from __future__ import annotations

from pathlib import Path

from trw_mcp.state.memory_adapter import (
    find_entry_by_id,
    list_active_learnings,
    store_learning,
    update_learning,
)

from ._memory_adapter_edge_support import trw_dir  # noqa: F401
from ._memory_store_fake import FakeMemoryStore


class TestUpdateLearningMultiChange:
    def test_all_fields_updated_at_once(self, fake_memory_store: FakeMemoryStore, trw_dir: Path) -> None:
        """Updating status, detail, summary, and impact in one call."""
        store_learning(trw_dir, "L-mc1", "Original summary", "Original detail", impact=0.5)
        result = update_learning(
            trw_dir,
            "L-mc1",
            status="resolved",
            detail="New detail",
            summary="New summary",
            impact=0.9,
        )
        assert result["status"] == "updated"
        changes = result["changes"]
        assert "status→resolved" in changes
        assert "detail updated" in changes
        assert "summary updated" in changes
        assert "impact→0.9" in changes

        entry = find_entry_by_id(trw_dir, "L-mc1")
        assert entry is not None
        assert entry["summary"] == "New summary"
        assert entry["impact"] == 0.9
        assert entry["status"] == "resolved"

    def test_impact_boundary_zero(self, fake_memory_store: FakeMemoryStore, trw_dir: Path) -> None:
        """Impact of exactly 0.0 is valid."""
        store_learning(trw_dir, "L-iz1", "s", "d", impact=0.5)
        result = update_learning(trw_dir, "L-iz1", impact=0.0)
        assert result["status"] == "updated"

    def test_impact_boundary_one(self, fake_memory_store: FakeMemoryStore, trw_dir: Path) -> None:
        """Impact of exactly 1.0 is valid."""
        store_learning(trw_dir, "L-io1", "s", "d", impact=0.5)
        result = update_learning(trw_dir, "L-io1", impact=1.0)
        assert result["status"] == "updated"


class TestListActiveLearningsBoundary:
    def test_zero_min_impact_returns_all_active(self, fake_memory_store: FakeMemoryStore, trw_dir: Path) -> None:
        """min_impact=0.0 returns all active entries regardless of impact."""
        store_learning(trw_dir, "L-al1", "Low impact", "d", impact=0.1)
        store_learning(trw_dir, "L-al2", "High impact", "d", impact=0.9)
        results = list_active_learnings(trw_dir, min_impact=0.0)
        ids = [str(r["id"]) for r in results]
        assert "L-al1" in ids
        assert "L-al2" in ids

    def test_high_min_impact_filters_low_entries(self, fake_memory_store: FakeMemoryStore, trw_dir: Path) -> None:
        """min_impact=0.8 excludes low-impact entries."""
        store_learning(trw_dir, "L-al3", "Low", "d", impact=0.3)
        store_learning(trw_dir, "L-al4", "High", "d", impact=0.9)
        results = list_active_learnings(trw_dir, min_impact=0.8)
        ids = [str(r["id"]) for r in results]
        assert "L-al3" not in ids
        assert "L-al4" in ids

    def test_default_limit_parameter(self, fake_memory_store: FakeMemoryStore, trw_dir: Path) -> None:
        """list_active_learnings works with default limit (no explicit limit)."""
        store_learning(trw_dir, "L-dl1", "Default limit", "d")
        results = list_active_learnings(trw_dir)
        assert len(results) >= 1
