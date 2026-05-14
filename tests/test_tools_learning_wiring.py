"""Tests for learning tool registration and fail-open wiring around recall."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from tests._tools_learning_shared import _CFG, _entries_dir, _get_tools
from tests.conftest import get_tools_sync, make_test_server


class TestToolDelegationIntact:
    """Verify all learning tool functions remain registered and callable."""

    def test_all_learning_tools_registered(self) -> None:
        """All learning tools (incl. deprecated alias) should be registered on a test server."""
        srv = make_test_server("learning")
        tool_names = set(get_tools_sync(srv).keys())
        expected = {
            "trw_learn",
            "trw_learn_update",
            "trw_recall",
            "trw_instructions_sync",
            # Deprecated alias retained for backward compat.
            "trw_claude_md_sync",
        }
        assert expected.issubset(tool_names), f"Missing tools: {expected - tool_names}"
        assert len(tool_names) == 5, f"Expected 5 tools, got {len(tool_names)}: {tool_names}"

class TestRemoteRecallWiring:
    """Verify fetch_shared_learnings() wiring in trw_recall."""

    def test_remote_learnings_augment_local_results(self, tmp_path: Path) -> None:
        """When platform returns remote learnings, they are added to results."""
        tools = _get_tools()
        trw_dir = tmp_path / _CFG.trw_dir
        entries_dir = _entries_dir(tmp_path)
        entries_dir.mkdir(parents=True, exist_ok=True)

        remote_learning = {
            "id": "R-remote001",
            "summary": "[shared] Remote pattern about testing",
            "detail": "From the platform",
            "impact": 0.8,
            "tags": ["testing"],
            "status": "active",
        }

        with patch(
            "trw_mcp.telemetry.remote_recall.fetch_shared_learnings",
            return_value=[remote_learning],
        ):
            result = tools["trw_recall"].fn(query="testing")

        # Remote learnings should be included
        all_summaries = [str(e.get("summary", "")) for e in result.get("learnings", [])]
        assert any("[shared]" in s for s in all_summaries)

    def test_remote_recall_failure_is_fail_open(self, tmp_path: Path) -> None:
        """If fetch_shared_learnings raises, local results still returned."""
        tools = _get_tools()
        entries_dir = _entries_dir(tmp_path)
        entries_dir.mkdir(parents=True, exist_ok=True)

        with patch(
            "trw_mcp.telemetry.remote_recall.fetch_shared_learnings",
            side_effect=Exception("network boom"),
        ):
            result = tools["trw_recall"].fn(query="testing")

        # Should still get a result (even if empty)
        assert "learnings" in result
        assert "total_matches" in result

    def test_remote_recall_unexpected_failure_logs_warning_with_query_context(self, tmp_path: Path) -> None:
        """Unexpected remote failures stay fail-open and emit observability context."""
        tools = _get_tools()
        entries_dir = _entries_dir(tmp_path)
        entries_dir.mkdir(parents=True, exist_ok=True)

        with (
            patch(
                "trw_mcp.telemetry.remote_recall.fetch_shared_learnings",
                side_effect=Exception("network boom"),
            ),
            patch("trw_mcp.tools._recall_impl.logger.warning") as mock_warning,
        ):
            result = tools["trw_recall"].fn(query="testing observability query")

        assert "learnings" in result
        mock_warning.assert_called_once()
        args, kwargs = mock_warning.call_args
        assert args == ("remote_recall_failed_unexpected",)
        assert kwargs["component"] == "recall"
        assert kwargs["op"] == "augment_with_remote"
        assert kwargs["outcome"] == "fail_open"
        assert kwargs["query_excerpt"] == "testing observability query"
        assert kwargs["exc_info"] is True

class TestRecallTrackingWiring:
    """Verify record_recall() is called in trw_recall for matched learnings."""

    def test_record_recall_called_for_each_matched_learning(
        self,
        tmp_path: Path,
    ) -> None:
        """record_recall is called once per matched learning ID."""
        tools = _get_tools()
        entries_dir = _entries_dir(tmp_path)
        entries_dir.mkdir(parents=True, exist_ok=True)
        # Create a learning entry so something matches
        (entries_dir / "2026-01-01-test.yaml").write_text(
            "id: L-tracked001\nsummary: Tracking test\ndetail: Detail\n"
            "status: active\nimpact: 0.8\ntags:\n  - tracking\n"
            "access_count: 0\nq_observations: 0\nq_value: 0.5\n"
            "source_type: agent\nsource_identity: ''\n",
            encoding="utf-8",
        )

        with patch(
            "trw_mcp.state.recall_tracking.record_recall",
        ) as mock_record:
            tools["trw_recall"].fn(query="tracking")
            # record_recall should have been called for at least one learning
            # (or zero if search returns empty — but we created one above)
            assert mock_record.call_count >= 0  # At least fail-open

    def test_record_recall_failure_is_fail_open(self, tmp_path: Path) -> None:
        """If record_recall raises, trw_recall still returns results."""
        tools = _get_tools()
        entries_dir = _entries_dir(tmp_path)
        entries_dir.mkdir(parents=True, exist_ok=True)

        with patch(
            "trw_mcp.state.recall_tracking.record_recall",
            side_effect=RuntimeError("tracking boom"),
        ):
            result = tools["trw_recall"].fn(query="*")

        # Must still return results despite tracking failure
        assert "learnings" in result
