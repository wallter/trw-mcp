"""Analytics YAML fallback tests for surfaced and existing learnings."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from tests._analytics_yaml_paths_support import _setup_trw, _write_entry
from trw_mcp.state.analytics import (
    has_existing_mechanical_learning,
)


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
