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

    def test_active_status_routes_through_injection_factory(self, tmp_path) -> None:
        """PRD-FIX-085 FR05 wiring: status='active' goes via the named factory.

        The injection path must flow through ``recall_for_learning_injection``
        (the centralized factory) rather than assembling an ad-hoc adapter call
        — this is the wiring that eliminates the previously-orphaned factory.
        """
        from trw_mcp.state.learning_injection import recall_learnings

        expected = [{"id": "L-001", "summary": "test"}]

        with (
            patch(
                "trw_mcp.state._paths.resolve_trw_dir",
                return_value=tmp_path,
            ),
            patch(
                "trw_mcp.state.recall_factories.recall_for_learning_injection",
                return_value=expected,
            ) as mock_factory,
        ):
            result = recall_learnings(
                "test query",
                tags=["foo"],
                min_impact=0.3,
                max_results=10,
                status="active",
            )

        assert result == expected
        mock_factory.assert_called_once_with(
            tmp_path,
            "test query",
            tags=["foo"],
            min_impact=0.3,
            max_results=10,
        )

    def test_non_active_status_bypasses_factory_and_hits_adapter(self, tmp_path) -> None:
        """Unfiltered callers (status=None, e.g. _learnings_collector) keep direct adapter behaviour.

        Routing only the active path through the factory preserves the
        no-status-filter contract that ``_learnings_collector`` relies on.
        """
        from trw_mcp.state.learning_injection import recall_learnings

        expected = [{"id": "L-002", "summary": "collector"}]

        with (
            patch(
                "trw_mcp.state._paths.resolve_trw_dir",
                return_value=tmp_path,
            ),
            patch(
                "trw_mcp.state.recall_factories.recall_for_learning_injection",
            ) as mock_factory,
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
        mock_factory.assert_not_called()
        mock_adapter.assert_called_once_with(
            tmp_path,
            query="collector query",
            tags=None,
            min_impact=0.0,
            max_results=5,
            status=None,
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
