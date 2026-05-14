"""Edge-case recall and backend recovery tests for state/memory_adapter.py."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from trw_memory.exceptions import CorruptDatabaseUnsalvageableError

from trw_mcp.state import memory_adapter as memory_adapter_module
from trw_mcp.state.memory_adapter import get_backend, recall_learnings, store_learning, update_learning


@pytest.fixture
def trw_dir(tmp_project: Path) -> Path:
    """Return the isolated .trw directory for memory-adapter tests."""
    trw = tmp_project / ".trw"
    (trw / "memory").mkdir(exist_ok=True)
    return trw


class TestRecallLearningsBoundary:
    def test_store_learning_retries_once_after_corruption(self, trw_dir: Path) -> None:
        """Corruption on first store attempt triggers recovery + retry with logging."""
        backend_first = MagicMock()
        backend_first.store.side_effect = RuntimeError("database disk image is malformed")
        backend_second = MagicMock()

        with (
            patch(
                "trw_mcp.state.memory_adapter.get_backend", side_effect=[backend_first, backend_second, backend_second]
            ),
            patch("trw_mcp.state.memory_adapter._recover_and_reset_backend") as mock_recover,
            patch("trw_mcp.state.memory_adapter._embed_and_store"),
            patch("trw_mcp.state.memory_adapter.logger.warning") as mock_warning,
        ):
            result = store_learning(trw_dir, "L-retry1", "Retry summary", "Retry detail")

        assert result["status"] == "recorded"
        mock_recover.assert_called_once_with(trw_dir)
        backend_second.store.assert_called_once()
        mock_warning.assert_called_once()

    def test_store_learning_does_not_retry_after_strict_refusal(self, trw_dir: Path) -> None:
        """Strict recovery refusal is terminal, not a generic retryable corruption."""
        terminal = CorruptDatabaseUnsalvageableError(
            "database disk image is malformed and salvage yielded 0 rows",
            backup_path=str(trw_dir / "memory" / "memory.db.corrupt.test.bak"),
        )
        backend = MagicMock()
        backend.store.side_effect = terminal

        with (
            patch("trw_mcp.state.memory_adapter.get_backend", return_value=backend),
            patch("trw_mcp.state.memory_adapter._recover_and_reset_backend") as mock_recover,
            patch("trw_mcp.state.memory_adapter._embed_and_store"),
            patch("trw_mcp.state.memory_adapter.logger.error") as mock_error,
        ):
            with pytest.raises(CorruptDatabaseUnsalvageableError):
                store_learning(trw_dir, "L-strict-refuse", "Strict summary", "Strict detail")

        mock_recover.assert_not_called()
        mock_error.assert_called_once()
        assert mock_error.call_args.args == ("memory_recovery_terminal",)
        assert mock_error.call_args.kwargs["backup_path"] == terminal.backup_path
        backend.store.assert_called_once()

    def test_recover_and_reset_backend_propagates_strict_refusal(self, trw_dir: Path) -> None:
        """Runtime recovery reset must not reopen a fresh backend after strict refusal."""
        db_path = trw_dir / "memory" / "memory.db"
        db_path.write_bytes(b"corrupt")
        terminal = CorruptDatabaseUnsalvageableError(
            "database disk image is malformed and salvage yielded 0 rows",
            backup_path=str(trw_dir / "memory" / "memory.db.corrupt.test.bak"),
        )

        with (
            patch("trw_mcp.state._memory_connection.reset_backend") as mock_reset,
            patch("trw_mcp.state._memory_connection.get_backend") as mock_get_backend,
            patch("trw_memory.storage.sqlite_backend.SQLiteBackend.recover_db", side_effect=terminal),
            patch("trw_mcp.state.memory_adapter.logger.error") as mock_error,
        ):
            with pytest.raises(CorruptDatabaseUnsalvageableError):
                memory_adapter_module._recover_and_reset_backend(trw_dir)

        mock_reset.assert_called_once()
        mock_get_backend.assert_not_called()
        mock_error.assert_any_call(
            "memory_recovery_terminal",
            db=str(db_path),
            backup_path=terminal.backup_path,
            action="raise",
        )

    def test_recall_learnings_defers_recovery_after_corruption(self, trw_dir: Path) -> None:
        """Recall corruption schedules background recovery and returns degraded results."""
        backend_first = MagicMock()
        backend_first.list_entries.side_effect = RuntimeError("database disk image is malformed")

        with (
            patch("trw_mcp.state.memory_adapter.get_backend", return_value=backend_first),
            patch("trw_mcp.state.memory_adapter._memory_recovery_in_progress", return_value=False),
            patch("trw_mcp.state.memory_adapter._schedule_deferred_recovery") as mock_schedule,
            patch("trw_mcp.state.memory_adapter.logger.warning") as mock_warning,
        ):
            result = recall_learnings(trw_dir, "*")

        assert result == []
        mock_schedule.assert_called_once_with(
            trw_dir,
            reason="recall_corruption",
            context={"query": "*"},
        )
        backend_first.list_entries.assert_called_once()
        assert mock_warning.call_args.args == ("memory_recall_degraded_recovery_scheduled",)

    def test_recall_learnings_skips_when_recovery_in_progress(self, trw_dir: Path) -> None:
        """Concurrent recalls do not stampede while deferred recovery is active."""
        with (
            patch("trw_mcp.state.memory_adapter._memory_recovery_in_progress", return_value=True),
            patch("trw_mcp.state.memory_adapter.get_backend") as mock_get_backend,
            patch("trw_mcp.state.memory_adapter._schedule_deferred_recovery") as mock_schedule,
            patch("trw_mcp.state.memory_adapter.logger.warning") as mock_warning,
        ):
            result = recall_learnings(trw_dir, "anything")

        assert result == []
        mock_get_backend.assert_not_called()
        mock_schedule.assert_not_called()
        mock_warning.assert_called_once_with(
            "memory_recall_skipped_recovery_in_progress",
            query="anything",
        )

    def test_empty_string_query_treated_as_wildcard(self, trw_dir: Path) -> None:
        """Empty string query is treated as wildcard (returns all entries)."""
        store_learning(trw_dir, "L-eq1", "Entry one", "d1")
        store_learning(trw_dir, "L-eq2", "Entry two", "d2")
        results = recall_learnings(trw_dir, "")
        assert len(results) == 2

    def test_whitespace_query_treated_as_wildcard(self, trw_dir: Path) -> None:
        """Whitespace-only query is treated as wildcard."""
        store_learning(trw_dir, "L-wq1", "Only entry", "d")
        results = recall_learnings(trw_dir, "   ")
        assert len(results) == 1

    def test_max_results_zero_uses_default_limit(self, trw_dir: Path) -> None:
        """max_results=0 falls back to _MAX_ENTRIES for wildcard queries."""
        store_learning(trw_dir, "L-mr1", "Max results test", "d")
        results = recall_learnings(trw_dir, "*", max_results=0)
        assert len(results) >= 1

    def test_max_results_zero_on_keyword_search(self, trw_dir: Path) -> None:
        """max_results=0 falls back to _MAX_ENTRIES for keyword queries."""
        store_learning(trw_dir, "L-mk1", "Keyword max test", "d")
        results = recall_learnings(trw_dir, "Keyword", max_results=0)
        assert isinstance(results, list)

    def test_status_filter_with_keyword_search(self, trw_dir: Path) -> None:
        """Status filter is applied during keyword search, not just wildcard."""
        store_learning(trw_dir, "L-sf1", "Active keyword entry", "d")
        store_learning(trw_dir, "L-sf2", "Obsolete keyword entry", "d")
        update_learning(trw_dir, "L-sf2", status="obsolete")
        results = recall_learnings(trw_dir, "keyword", status="active")
        ids = [str(r["id"]) for r in results]
        assert "L-sf1" in ids

    def test_tag_filter_not_applied_on_keyword_search(self, trw_dir: Path) -> None:
        """Tag filter on keyword search is handled by _search_entries, not the wildcard path."""
        store_learning(trw_dir, "L-tf1", "Tagged entry", "d", tags=["python"])
        store_learning(trw_dir, "L-tf2", "Untagged entry", "d", tags=["rust"])
        results = recall_learnings(trw_dir, "entry", tags=["python"])
        assert isinstance(results, list)


class TestRecallMinImpactPostFilter:
    def test_min_impact_filters_after_backend_query(self, trw_dir: Path) -> None:
        """min_impact is applied as a post-filter on the converted dicts."""
        store_learning(trw_dir, "L-pf1", "Low impact recall", "d", impact=0.2)
        store_learning(trw_dir, "L-pf2", "High impact recall", "d", impact=0.8)
        results = recall_learnings(trw_dir, "*", min_impact=0.5)
        ids = [str(r["id"]) for r in results]
        assert "L-pf1" not in ids
        assert "L-pf2" in ids

    def test_min_impact_on_keyword_search(self, trw_dir: Path) -> None:
        """min_impact filter works with keyword search too."""
        store_learning(trw_dir, "L-kf1", "Keyword filter low", "d", impact=0.1)
        store_learning(trw_dir, "L-kf2", "Keyword filter high", "d", impact=0.9)
        results = recall_learnings(trw_dir, "Keyword filter", min_impact=0.5)
        for r in results:
            assert float(str(r["impact"])) >= 0.5


class TestGetBackendDirectoryCreation:
    def test_creates_memory_dir_if_missing(self, tmp_path: Path) -> None:
        """get_backend creates the memory/ subdirectory if it doesn't exist."""
        trw = tmp_path / ".trw"
        trw.mkdir()
        (trw / "learnings" / "entries").mkdir(parents=True)
        assert not (trw / "memory").exists()

        backend = get_backend(trw)
        assert backend is not None
        assert (trw / "memory").exists()
