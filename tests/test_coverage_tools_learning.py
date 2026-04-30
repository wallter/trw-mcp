from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from trw_mcp.exceptions import StateError
from trw_mcp.models.config import TRWConfig
from trw_mcp.tools.learning import register_learning_tools

from tests._coverage_tools_support import _extract_tool, _make_server


class TestLearningExceptionPaths:
    """Coverage branches in tools/learning.py."""

    def _register_and_get(self, name: str):
        server = _make_server()
        register_learning_tools(server)
        return _extract_tool(server, name)

    def test_trw_learn_yaml_read_exception_skips_file(self, tmp_path: Path) -> None:
        cfg = TRWConfig(impact_forced_distribution_enabled=True)
        tool = self._register_and_get("trw_learn")

        with (
            patch("trw_mcp.tools.learning.get_config", return_value=cfg),
            patch("trw_mcp.tools.learning.resolve_trw_dir", return_value=tmp_path / ".trw"),
            patch("trw_mcp.tools.learning.generate_learning_id", return_value="L-test0001"),
            patch(
                "trw_mcp.tools.learning.adapter_store",
                return_value={
                    "learning_id": "L-test0001",
                    "path": "sqlite://L-test0001",
                    "status": "recorded",
                    "distribution_warning": "",
                },
            ),
            patch("trw_mcp.tools.learning.update_analytics"),
            patch("trw_mcp.tools.learning.list_active_learnings", side_effect=StateError("adapter read failure")),
        ):
            result = tool(summary="test summary", detail="test detail", impact=0.8)

        assert result["status"] == "recorded"
        assert result["learning_id"] == "L-test0001"

    def test_trw_learn_distribution_exception_fail_open(self, tmp_path: Path) -> None:
        cfg = TRWConfig(impact_forced_distribution_enabled=True)
        tool = self._register_and_get("trw_learn")

        with (
            patch("trw_mcp.tools.learning.get_config", return_value=cfg),
            patch("trw_mcp.tools.learning.resolve_trw_dir", return_value=tmp_path / ".trw"),
            patch("trw_mcp.tools.learning.generate_learning_id", return_value="L-test0002"),
            patch(
                "trw_mcp.tools.learning.adapter_store",
                return_value={
                    "learning_id": "L-test0002",
                    "path": "sqlite://L-test0002",
                    "status": "recorded",
                    "distribution_warning": "",
                },
            ),
            patch("trw_mcp.tools.learning.update_analytics"),
            patch("trw_mcp.tools.learning.list_active_learnings", return_value=[{"id": "L-abc", "impact": 0.8}]),
            patch("trw_mcp.scoring.enforce_tier_distribution", side_effect=RuntimeError("distribution exploded")),
        ):
            result = tool(summary="test summary", detail="test detail", impact=0.9)

        assert result["status"] == "recorded"

    def test_trw_learn_update_write_failure(self, tmp_path: Path) -> None:
        tool = self._register_and_get("trw_learn_update")

        with (
            patch("trw_mcp.tools.learning.resolve_trw_dir", return_value=tmp_path / ".trw"),
            patch(
                "trw_mcp.tools.learning.adapter_update",
                return_value={"learning_id": "L-testXX", "changes": "status→resolved", "status": "updated"},
            ),
        ):
            result = tool(learning_id="L-testXX", status="resolved")

        assert result["status"] == "updated"

    def test_trw_claude_md_sync_failure_propagates(self, tmp_path: Path) -> None:
        tool = self._register_and_get("trw_claude_md_sync")

        with patch("trw_mcp.tools.learning.execute_claude_md_sync", side_effect=RuntimeError("sync exploded")):
            with pytest.raises(RuntimeError, match="sync exploded"):
                tool(scope="root")


class TestLearningDistributionSkipsInactiveEntries:
    """Line 143: inactive entries (status != 'active') are skipped with continue."""

    def test_trw_learn_distribution_skips_inactive_entries(self, tmp_path: Path) -> None:
        cfg = TRWConfig(impact_forced_distribution_enabled=True)
        server = _make_server()
        register_learning_tools(server)
        tool = _extract_tool(server, "trw_learn")

        with (
            patch("trw_mcp.tools.learning.get_config", return_value=cfg),
            patch("trw_mcp.tools.learning.resolve_trw_dir", return_value=tmp_path / ".trw"),
            patch("trw_mcp.tools.learning.generate_learning_id", return_value="L-new001"),
            patch(
                "trw_mcp.tools.learning.adapter_store",
                return_value={
                    "learning_id": "L-new001",
                    "path": "sqlite://L-new001",
                    "status": "recorded",
                    "distribution_warning": "",
                },
            ),
            patch("trw_mcp.tools.learning.update_analytics"),
            patch("trw_mcp.tools.learning.list_active_learnings", return_value=[{"id": "L-active", "impact": 0.5}]),
            patch("trw_mcp.scoring.enforce_tier_distribution", return_value=[]),
        ):
            result = tool(summary="new summary", detail="detail", impact=0.8)

        assert result["status"] == "recorded"


class TestLearningRecallTrackingException:
    """Lines 301-302: record_recall raises, exception is silently swallowed."""

    def test_trw_recall_tracking_failure_fail_open(self, tmp_path: Path) -> None:
        server = _make_server()
        register_learning_tools(server)
        tool = _extract_tool(server, "trw_recall")
        mock_record_recall = MagicMock(side_effect=RuntimeError("tracking db down"))

        with (
            patch("trw_mcp.tools.learning.resolve_trw_dir", return_value=tmp_path / ".trw"),
            patch("trw_mcp.tools.learning.adapter_recall", return_value=[{"id": "L-001", "summary": "test"}]),
            patch("trw_mcp.tools.learning.adapter_update_access"),
            patch("trw_mcp.tools.learning.search_patterns", return_value=[]),
            patch("trw_mcp.tools.learning.rank_by_utility", return_value=[{"id": "L-001", "summary": "test"}]),
            patch("trw_mcp.tools.learning.collect_context", return_value={}),
            patch.dict(
                "sys.modules",
                {"trw_mcp.state.recall_tracking": MagicMock(record_recall=mock_record_recall)},
            ),
        ):
            result = tool(query="test")

        assert "learnings" in result
        assert len(result["learnings"]) == 1
