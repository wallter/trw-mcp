"""Edge-case tests for learning_injection wrappers."""

from __future__ import annotations

from unittest.mock import patch

import pytest


class TestResolveTrwDir:
    """Verify the lazy import wrapper delegates to resolve_trw_dir."""

    def test_delegates_to_state_paths(self, tmp_path) -> None:
        from trw_mcp.state.learning_injection import _resolve_trw_dir

        with patch(
            "trw_mcp.state._paths.resolve_trw_dir",
            return_value=tmp_path,
        ):
            result = _resolve_trw_dir()
        assert result == tmp_path


class TestRecallLearningsWrapper:
    """Verify the thin wrapper resolves trw_dir and delegates."""

    def test_delegates_to_unfiltered_adapter(self, tmp_path) -> None:
        """The live collector path preserves the adapter's unfiltered default."""
        from trw_mcp.state.learning_injection import recall_learnings

        expected = [{"id": "L-002", "summary": "collector"}]

        with (
            patch(
                "trw_mcp.state._paths.resolve_trw_dir",
                return_value=tmp_path,
            ),
            patch(
                "trw_mcp.state.memory_adapter.recall_learnings",
                return_value=expected,
            ) as mock_adapter,
        ):
            result = recall_learnings(
                "collector query",
                max_results=5,
            )

        assert result == expected
        mock_adapter.assert_called_once_with(
            tmp_path,
            query="collector query",
            tags=None,
            min_impact=0.0,
            max_results=5,
        )

    def test_propagates_adapter_exception(self, tmp_path) -> None:
        from trw_mcp.state.learning_injection import recall_learnings

        with (
            patch(
                "trw_mcp.state._paths.resolve_trw_dir",
                return_value=tmp_path,
            ),
            patch(
                "trw_mcp.state.memory_adapter.recall_learnings",
                side_effect=RuntimeError("boom"),
            ),
        ):
            with pytest.raises(RuntimeError, match="boom"):
                recall_learnings("q")
