"""Analytics YAML fallback tests for surfaced and existing learnings."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from tests._analytics_yaml_paths_support import _setup_trw, _write_entry
from trw_mcp.state.analytics import (
    has_existing_mechanical_learning,
    has_existing_success_learning,
    surface_validated_learnings,
)


class TestSurfaceValidatedLearningsYamlFallback:
    """Test YAML fallback in surface_validated_learnings."""

    def test_yaml_fallback_no_entries_dir_returns_empty(self, tmp_path: Path) -> None:
        """Line 412: YAML fallback returns [] when entries_dir does not exist."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir(parents=True, exist_ok=True)

        with patch(
            "trw_mcp.state.memory_adapter.list_active_learnings",
            side_effect=RuntimeError("sqlite broken"),
        ):
            result = surface_validated_learnings(
                trw_dir,
                q_threshold=0.5,
                cold_start_threshold=3,
            )
        assert result == []

    def test_yaml_fallback_returns_validated_entries(self, tmp_path: Path) -> None:
        """Lines 406-431: when SQLite fails, YAML fallback returns validated learnings."""
        trw_dir = _setup_trw(tmp_path)
        entries_dir = trw_dir / "learnings" / "entries"

        _write_entry(
            entries_dir,
            "validated-1",
            summary="validated learning",
            q_value=0.8,
            q_observations=5,
        )
        _write_entry(
            entries_dir,
            "not-validated",
            summary="low q learning",
            q_value=0.1,
            q_observations=1,
        )
        _write_entry(
            entries_dir,
            "cold-start",
            summary="cold start learning",
            q_value=0.9,
            q_observations=0,
        )
        _write_entry(
            entries_dir,
            "resolved-entry",
            summary="resolved",
            status="resolved",
            q_value=0.9,
            q_observations=5,
        )

        with patch(
            "trw_mcp.state.memory_adapter.list_active_learnings",
            side_effect=ImportError("no sqlite"),
        ):
            result = surface_validated_learnings(
                trw_dir,
                q_threshold=0.5,
                cold_start_threshold=3,
            )

        assert len(result) == 1
        assert result[0]["learning_id"] == "validated-1"
        assert result[0]["q_value"] == 0.8
        assert result[0]["q_observations"] == 5

    def test_yaml_fallback_sorted_by_q_value(self, tmp_path: Path) -> None:
        """Lines 406-431: results are sorted by q_value descending."""
        trw_dir = _setup_trw(tmp_path)
        entries_dir = trw_dir / "learnings" / "entries"

        _write_entry(entries_dir, "low-q", q_value=0.6, q_observations=5)
        _write_entry(entries_dir, "high-q", q_value=0.9, q_observations=5)
        _write_entry(entries_dir, "mid-q", q_value=0.75, q_observations=5)

        with patch(
            "trw_mcp.state.memory_adapter.list_active_learnings",
            side_effect=ImportError("no sqlite"),
        ):
            result = surface_validated_learnings(
                trw_dir,
                q_threshold=0.5,
                cold_start_threshold=3,
            )

        assert len(result) == 3
        assert result[0]["learning_id"] == "high-q"
        assert result[1]["learning_id"] == "mid-q"
        assert result[2]["learning_id"] == "low-q"


class TestHasExistingSuccessLearning:
    """Test has_existing_success_learning match branches."""

    def test_sqlite_exception_falls_through_to_yaml(self, tmp_path: Path) -> None:
        """Lines 459-460: SQLite exception falls through to YAML scan."""
        trw_dir = _setup_trw(tmp_path)
        entries_dir = trw_dir / "learnings" / "entries"

        _write_entry(
            entries_dir,
            "success-1",
            summary="Success: reflection complete 3x in session",
        )

        with patch(
            "trw_mcp.state.memory_adapter.list_active_learnings",
            side_effect=RuntimeError("sqlite broken"),
        ):
            result = has_existing_success_learning(
                trw_dir,
                "Success: reflection complete 3x in session",
            )
        assert result is True

    def test_sqlite_match_returns_true(self, tmp_path: Path) -> None:
        """Lines 459-460: SQLite path finds a matching summary prefix."""
        trw_dir = _setup_trw(tmp_path)

        mock_list = MagicMock(
            return_value=[
                {"summary": "Success: reflection complete 3x in session"},
            ]
        )
        with patch(
            "trw_mcp.state.memory_adapter.list_active_learnings",
            mock_list,
        ):
            result = has_existing_success_learning(
                trw_dir,
                "Success: reflection complete 3x in session",
            )
        assert result is True

    def test_yaml_match_returns_true(self, tmp_path: Path) -> None:
        """Line 469: YAML fallback finds a matching summary."""
        trw_dir = _setup_trw(tmp_path)
        entries_dir = trw_dir / "learnings" / "entries"

        _write_entry(
            entries_dir,
            "success-1",
            summary="Success: reflection complete 3x in session",
        )

        mock_list = MagicMock(return_value=[])
        with patch(
            "trw_mcp.state.memory_adapter.list_active_learnings",
            mock_list,
        ):
            result = has_existing_success_learning(
                trw_dir,
                "Success: reflection complete 3x in session",
            )
        assert result is True


class TestHasExistingMechanicalLearning:
    """Test has_existing_mechanical_learning SQLite match branch."""

    def test_sqlite_exception_falls_through_to_yaml(self, tmp_path: Path) -> None:
        """Lines 498-499: SQLite exception falls through to YAML scan."""
        trw_dir = _setup_trw(tmp_path)
        entries_dir = trw_dir / "learnings" / "entries"

        _write_entry(
            entries_dir,
            "mech-1",
            summary="Repeated operation: file_modified 5x",
        )

        with patch(
            "trw_mcp.state.memory_adapter.list_active_learnings",
            side_effect=RuntimeError("sqlite broken"),
        ):
            result = has_existing_mechanical_learning(
                trw_dir,
                "Repeated operation: file_modified",
            )
        assert result is True

    def test_sqlite_match_returns_true(self, tmp_path: Path) -> None:
        """Lines 498-499: SQLite path finds a matching prefix."""
        trw_dir = _setup_trw(tmp_path)

        mock_list = MagicMock(
            return_value=[
                {"summary": "Repeated operation: file_modified 5x"},
            ]
        )
        with patch(
            "trw_mcp.state.memory_adapter.list_active_learnings",
            mock_list,
        ):
            result = has_existing_mechanical_learning(
                trw_dir,
                "Repeated operation: file_modified",
            )
        assert result is True

    def test_sqlite_no_match_falls_through(self, tmp_path: Path) -> None:
        """SQLite path returns no match, falls through to YAML."""
        trw_dir = _setup_trw(tmp_path)
        entries_dir = trw_dir / "learnings" / "entries"

        _write_entry(
            entries_dir,
            "mech-1",
            summary="Repeated operation: checkpoint 3x",
        )

        mock_list = MagicMock(
            return_value=[
                {"summary": "unrelated learning"},
            ]
        )
        with patch(
            "trw_mcp.state.memory_adapter.list_active_learnings",
            mock_list,
        ):
            result = has_existing_mechanical_learning(
                trw_dir,
                "Repeated operation: checkpoint",
            )
        assert result is True
