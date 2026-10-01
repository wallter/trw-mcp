from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tests._coverage_tools_support import _extract_tool, _make_server
from trw_mcp.models.config import TRWConfig
from trw_mcp.tools.learning import register_learning_tools


@pytest.mark.usefixtures("fake_memory_store")
class TestLearningExceptionPaths:
    """Coverage branches in tools/learning.py."""

    def _register_and_get(self, name: str):
        server = _make_server()
        register_learning_tools(server)
        return _extract_tool(server, name)

    def test_trw_learn_does_not_request_quota_active_listing(self, tmp_path: Path) -> None:
        cfg = TRWConfig()
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
            patch(
                "trw_mcp.tools.learning.list_active_learnings", side_effect=AssertionError("unexpected quota listing")
            ) as listing,
        ):
            result = tool(summary="test summary", detail="test detail", impact=0.8)

        assert result["status"] == "recorded"
        assert result["learning_id"] == "L-test0001"
        listing.assert_not_called()

    def test_trw_learn_update_write_failure(self, tmp_path: Path) -> None:
        """PRD-CORE-291 merged trw_learn_update into trw_learn's update mode."""
        tool = self._register_and_get("trw_learn")

        with (
            patch("trw_mcp.tools.learning.resolve_trw_dir", return_value=tmp_path / ".trw"),
            patch(
                "trw_mcp.tools.learning.adapter_update",
                return_value={"learning_id": "L-testXX", "changes": "status→resolved", "status": "updated"},
            ),
        ):
            result = tool(learning_id="L-testXX", status="resolved")

        assert result["status"] == "updated"

    def test_instructions_sync_failure_propagates(self, tmp_path: Path) -> None:
        """PRD-CORE-300 S6b folded the tool into ``trw-mcp instructions sync``."""
        from tests._tools_learning_shared import instructions_sync_fn

        with patch("trw_mcp.state.claude_md.execute_claude_md_sync", side_effect=RuntimeError("sync exploded")):
            with pytest.raises(RuntimeError, match="sync exploded"):
                instructions_sync_fn(scope="root")


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
            patch.dict(
                "sys.modules",
                {"trw_mcp.state.recall_tracking": MagicMock(record_recall=mock_record_recall)},
            ),
        ):
            result = tool(query="test")

        assert "learnings" in result
        assert len(result["learnings"]) == 1
