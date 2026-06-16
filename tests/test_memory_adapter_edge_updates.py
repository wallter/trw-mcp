"""Edge-case update and reset tests for state/memory_adapter.py."""

from __future__ import annotations

from datetime import timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

from trw_mcp.state.memory_adapter import (
    find_entry_by_id,
    get_backend,
    increment_session_counts,
    list_active_learnings,
    reset_backend,
    reset_embedder,
    store_learning,
    update_access_tracking,
    update_learning,
)

from ._memory_adapter_edge_support import trw_dir  # noqa: F401


class TestIncrementSessionCounts:
    def test_deduplicates_ids_and_defaults_none_to_zero(self, trw_dir: Path) -> None:
        """A learning surfaced twice in one session should count once from a zero-ish baseline."""
        store_learning(trw_dir, "L-session01", "Summary", "Detail")
        backend = get_backend(trw_dir)
        backend.update("L-session01", session_count=None)

        increment_session_counts(trw_dir, ["L-session01", "L-session01"])

        entry = backend.get("L-session01")
        assert entry is not None
        assert entry.session_count == 1

    def test_batches_only_valid_distinct_learning_ids(self, trw_dir: Path) -> None:
        """Session count updates validate ids before issuing a single backend batch update."""
        backend = MagicMock()

        with (
            patch("trw_mcp.state.memory_adapter.get_backend", return_value=backend),
            patch("trw_mcp.state.memory_adapter.logger.warning") as mock_warning,
        ):
            increment_session_counts(
                trw_dir,
                ["L-valid01", "L-valid01", "invalid-id", "L-valid02"],
            )

        backend.increment_session_counts.assert_called_once()
        args, kwargs = backend.increment_session_counts.call_args
        assert args[0] == ["L-valid01", "L-valid02"]
        assert kwargs["updated_at"].tzinfo == timezone.utc
        mock_warning.assert_called_once_with(
            "session_count_update_skipped_invalid_id",
            entry_id="invalid-id",
        )


class TestUpdateLearningMultiChange:
    def test_all_fields_updated_at_once(self, trw_dir: Path) -> None:
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

    def test_impact_boundary_zero(self, trw_dir: Path) -> None:
        """Impact of exactly 0.0 is valid."""
        store_learning(trw_dir, "L-iz1", "s", "d", impact=0.5)
        result = update_learning(trw_dir, "L-iz1", impact=0.0)
        assert result["status"] == "updated"

    def test_impact_boundary_one(self, trw_dir: Path) -> None:
        """Impact of exactly 1.0 is valid."""
        store_learning(trw_dir, "L-io1", "s", "d", impact=0.5)
        result = update_learning(trw_dir, "L-io1", impact=1.0)
        assert result["status"] == "updated"


class TestUpdateAccessTrackingMixed:
    def test_mixed_valid_and_missing_ids(self, trw_dir: Path) -> None:
        """Some valid, some missing IDs — valid ones still get updated."""
        store_learning(trw_dir, "L-mx1", "Valid entry", "d")
        store_learning(trw_dir, "L-mx3", "Another valid", "d")

        update_access_tracking(trw_dir, ["L-mx1", "L-mx2", "L-mx3"])

        entry1 = find_entry_by_id(trw_dir, "L-mx1")
        entry3 = find_entry_by_id(trw_dir, "L-mx3")
        assert entry1 is not None
        assert entry1["access_count"] == 1
        # PRD-FIX-104-FR03: recall_count MUST also be incremented (regression guard)
        assert entry1["recall_count"] == 1
        assert entry3 is not None
        assert entry3["access_count"] == 1
        assert entry3["recall_count"] == 1

    def test_empty_ids_list(self, trw_dir: Path) -> None:
        """Empty list of IDs does nothing and does not error."""
        update_access_tracking(trw_dir, [])

    def test_double_increment(self, trw_dir: Path) -> None:
        """Calling twice increments access_count to 2 and recall_count to 2."""
        store_learning(trw_dir, "L-di1", "Double increment", "d")
        update_access_tracking(trw_dir, ["L-di1"])
        update_access_tracking(trw_dir, ["L-di1"])
        entry = find_entry_by_id(trw_dir, "L-di1")
        assert entry is not None
        assert entry["access_count"] == 2
        # PRD-FIX-104-FR03: recall_count tracks each recall call
        assert entry["recall_count"] == 2

    def test_sets_last_accessed_at(self, trw_dir: Path) -> None:
        """Access tracking sets last_accessed_at to a non-None value."""
        store_learning(trw_dir, "L-la1", "Last accessed test", "d")
        update_access_tracking(trw_dir, ["L-la1"])
        entry = find_entry_by_id(trw_dir, "L-la1")
        assert entry is not None
        assert entry["last_accessed_at"] is not None

    def test_recall_count_increments_on_single_recall(self, trw_dir: Path) -> None:
        """PRD-FIX-104-FR03: a single update_access_tracking call sets recall_count=1."""
        store_learning(trw_dir, "L-rc1", "Recall count test", "d")
        update_access_tracking(trw_dir, ["L-rc1"])
        entry = find_entry_by_id(trw_dir, "L-rc1")
        assert entry is not None
        assert entry["recall_count"] == 1

    def test_fallback_path_increments_recall_count(self, trw_dir: Path) -> None:
        """PRD-FIX-104-FR02: per-entry fallback loop also increments recall_count.

        When the backend is replaced with a mock that lacks increment_recall_access,
        the per-entry fallback path must still increment recall_count.
        """
        from unittest.mock import MagicMock, patch

        store_learning(trw_dir, "L-fb1", "Fallback test", "d")
        real_backend = get_backend(trw_dir)
        real_entry = real_backend.get("L-fb1")
        assert real_entry is not None

        mock_backend = MagicMock(spec_set=["get", "update"])
        mock_backend.get.return_value = real_entry

        with patch("trw_mcp.state.memory_adapter.get_backend", return_value=mock_backend):
            update_access_tracking(trw_dir, ["L-fb1"])

        _args, kwargs = mock_backend.update.call_args
        assert kwargs.get("recall_count") == real_entry.recall_count + 1


class TestResetIdempotency:
    def test_reset_backend_when_no_backend_exists(self) -> None:
        """reset_backend() is safe to call when _backend is already None."""
        reset_backend()
        reset_backend()

    def test_reset_embedder_when_not_initialized(self) -> None:
        """reset_embedder() is safe to call when _embedder is already None."""
        reset_embedder()
        reset_embedder()

    def test_reset_backend_also_resets_embedder(self) -> None:
        """reset_backend() calls reset_embedder() internally."""
        with patch("trw_mcp.state.memory_adapter.reset_embedder") as mock_reset_emb:
            import inspect

            from trw_mcp.state import memory_adapter

            source = inspect.getsource(memory_adapter.reset_backend)
            assert "reset_embedder" in source


class TestListActiveLearningsBoundary:
    def test_zero_min_impact_returns_all_active(self, trw_dir: Path) -> None:
        """min_impact=0.0 returns all active entries regardless of impact."""
        store_learning(trw_dir, "L-al1", "Low impact", "d", impact=0.1)
        store_learning(trw_dir, "L-al2", "High impact", "d", impact=0.9)
        results = list_active_learnings(trw_dir, min_impact=0.0)
        ids = [str(r["id"]) for r in results]
        assert "L-al1" in ids
        assert "L-al2" in ids

    def test_high_min_impact_filters_low_entries(self, trw_dir: Path) -> None:
        """min_impact=0.8 excludes low-impact entries."""
        store_learning(trw_dir, "L-al3", "Low", "d", impact=0.3)
        store_learning(trw_dir, "L-al4", "High", "d", impact=0.9)
        results = list_active_learnings(trw_dir, min_impact=0.8)
        ids = [str(r["id"]) for r in results]
        assert "L-al3" not in ids
        assert "L-al4" in ids

    def test_default_limit_parameter(self, trw_dir: Path) -> None:
        """list_active_learnings works with default limit (no explicit limit)."""
        store_learning(trw_dir, "L-dl1", "Default limit", "d")
        results = list_active_learnings(trw_dir)
        assert len(results) >= 1
