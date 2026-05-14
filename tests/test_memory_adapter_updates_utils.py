"""Update, access-tracking, utility, and status-list tests for memory_adapter."""

from __future__ import annotations

from pathlib import Path

from trw_mcp.state.memory_adapter import (
    count_entries,
    find_entry_by_id,
    list_active_learnings,
    list_entries_by_status,
    store_learning,
    update_access_tracking,
    update_learning,
)


class TestUpdateLearning:
    def test_status_change(self, trw_dir: Path) -> None:
        store_learning(trw_dir, "L-u1", "s", "d")
        result = update_learning(trw_dir, "L-u1", status="resolved")
        assert result["status"] == "updated"
        assert "status→resolved" in result["changes"]

    def test_impact_change(self, trw_dir: Path) -> None:
        store_learning(trw_dir, "L-u2", "s", "d", impact=0.5)
        result = update_learning(trw_dir, "L-u2", impact=0.9)
        assert result["status"] == "updated"
        entry = find_entry_by_id(trw_dir, "L-u2")
        assert entry is not None
        assert entry["impact"] == 0.9

    def test_not_found(self, trw_dir: Path) -> None:
        result = update_learning(trw_dir, "L-nonexistent")
        assert result["status"] == "not_found"

    def test_invalid_status(self, trw_dir: Path) -> None:
        store_learning(trw_dir, "L-u3", "s", "d")
        result = update_learning(trw_dir, "L-u3", status="bad")
        assert result["status"] == "invalid"

    def test_no_changes(self, trw_dir: Path) -> None:
        store_learning(trw_dir, "L-u4", "s", "d")
        result = update_learning(trw_dir, "L-u4")
        assert result["status"] == "no_changes"

    def test_return_shape_keys(self, trw_dir: Path) -> None:
        store_learning(trw_dir, "L-us1", "s", "d")
        result = update_learning(trw_dir, "L-us1", status="resolved")
        expected_keys = {"learning_id", "changes", "status"}
        assert set(result.keys()) == expected_keys

    def test_allows_obsolete_poisoned_status(self, trw_dir: Path) -> None:
        store_learning(trw_dir, "L-op1", "s", "d")
        result = update_learning(trw_dir, "L-op1", status="obsolete_poisoned")
        assert result["status"] == "updated"


class TestAccessTracking:
    def test_increments_count(self, trw_dir: Path) -> None:
        store_learning(trw_dir, "L-at1", "s", "d")
        update_access_tracking(trw_dir, ["L-at1"])
        entry = find_entry_by_id(trw_dir, "L-at1")
        assert entry is not None
        assert entry["access_count"] == 1

    def test_nonexistent_id_no_error(self, trw_dir: Path) -> None:
        """Access tracking for missing ID should not raise."""
        update_access_tracking(trw_dir, ["L-missing"])


class TestUtilities:
    def test_count_entries(self, trw_dir: Path) -> None:
        assert count_entries(trw_dir) == 0
        store_learning(trw_dir, "L-cnt1", "s", "d")
        assert count_entries(trw_dir) == 1

    def test_list_active_learnings(self, trw_dir: Path) -> None:
        store_learning(trw_dir, "L-la1", "active entry", "d", impact=0.8)
        store_learning(trw_dir, "L-la2", "another", "d", impact=0.3)
        update_learning(trw_dir, "L-la2", status="obsolete")
        actives = list_active_learnings(trw_dir, min_impact=0.5)
        assert len(actives) == 1
        assert actives[0]["id"] == "L-la1"

    def test_find_entry_by_id_found(self, trw_dir: Path) -> None:
        store_learning(trw_dir, "L-fe1", "Summary here", "Detail here")
        result = find_entry_by_id(trw_dir, "L-fe1")
        assert result is not None
        assert result["summary"] == "Summary here"

    def test_find_entry_by_id_not_found(self, trw_dir: Path) -> None:
        assert find_entry_by_id(trw_dir, "L-nope") is None


class TestListEntriesByStatus:
    """PRD-FIX-033-FR01: list_entries_by_status returns entries as dicts."""

    def test_active_filter(self, trw_dir: Path) -> None:
        """Returns only active entries by default."""
        store_learning(trw_dir, "L-les1", "Summary1", "Detail1", impact=0.8)
        store_learning(trw_dir, "L-les2", "Summary2", "Detail2", impact=0.5)
        update_learning(trw_dir, "L-les2", status="obsolete")

        result = list_entries_by_status(trw_dir, status="active")
        ids = [str(e["id"]) for e in result]
        assert "L-les1" in ids
        assert "L-les2" not in ids

    def test_min_impact_filter(self, trw_dir: Path) -> None:
        """Filters by minimum impact score."""
        store_learning(trw_dir, "L-mi1", "High", "d", impact=0.9)
        store_learning(trw_dir, "L-mi2", "Low", "d", impact=0.1)

        result = list_entries_by_status(trw_dir, min_impact=0.5)
        ids = [str(e["id"]) for e in result]
        assert "L-mi1" in ids
        assert "L-mi2" not in ids

    def test_invalid_status_returns_empty(self, trw_dir: Path) -> None:
        """Invalid status string returns empty list."""
        result = list_entries_by_status(trw_dir, status="bogus_status")
        assert result == []

    def test_dict_fields_present(self, trw_dir: Path) -> None:
        """Returned dicts contain expected learning fields."""
        store_learning(trw_dir, "L-df1", "Summary", "Detail", impact=0.5)
        result = list_entries_by_status(trw_dir)
        assert len(result) >= 1
        entry = result[0]
        assert "id" in entry
        assert "summary" in entry
        assert "impact" in entry
        assert "status" in entry

    def test_resolved_status(self, trw_dir: Path) -> None:
        """Can filter by resolved status."""
        store_learning(trw_dir, "L-rs1", "Will resolve", "d", impact=0.5)
        update_learning(trw_dir, "L-rs1", status="resolved")

        result = list_entries_by_status(trw_dir, status="resolved")
        ids = [str(e["id"]) for e in result]
        assert "L-rs1" in ids
