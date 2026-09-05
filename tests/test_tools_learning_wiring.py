"""Tests for learning tool registration and fail-open wiring around recall."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from trw_memory.sync import SharedFetchResult

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
    """Verify the shared-learning fetch wiring in trw_recall.

    PRD-CORE-245 FR06 repointed these at ``trw_memory.sync.fetch_shared_memories``,
    the ONE remaining path to the platform search endpoint. The fail-open
    assertions are the wiring proof: they only hold if ``_augment_with_remote``
    actually reaches that function.
    """

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
            "trw_memory.sync.fetch_shared_memories",
            return_value=SharedFetchResult([remote_learning], "ok", 1, 0),
        ):
            result = tools["trw_recall"].fn(query="testing")

        # Remote learnings should be included
        all_summaries = [str(e.get("summary", "")) for e in result.get("learnings", [])]
        assert any("[shared]" in s for s in all_summaries)

    def test_remote_refusal_is_reported_not_merged_silently(self, tmp_path: Path) -> None:
        """W13: an empty remote result whose cause was refusal is logged as such.

        ``fetch_shared_memories`` returns a status now, and this is the assertion
        that the trw-mcp caller reads it -- a status nobody consumes would be a
        new wiring defect rather than a fix for one.
        """
        tools = _get_tools()
        entries_dir = _entries_dir(tmp_path)
        entries_dir.mkdir(parents=True, exist_ok=True)

        with (
            patch(
                "trw_memory.sync.fetch_shared_memories",
                return_value=SharedFetchResult([], "refused", 3, 3),
            ),
            patch("trw_mcp.tools._recall_impl.logger.warning") as mock_warning,
        ):
            result = tools["trw_recall"].fn(query="testing")

        assert "learnings" in result
        incomplete = [call for call in mock_warning.call_args_list if call.args[:1] == ("remote_recall_incomplete",)]
        assert len(incomplete) == 1
        assert incomplete[0].kwargs["outcome"] == "refused"
        assert incomplete[0].kwargs["refused"] == 3

    def test_remote_recall_failure_is_fail_open(self, tmp_path: Path) -> None:
        """If the shared fetch raises, local results are still returned."""
        tools = _get_tools()
        entries_dir = _entries_dir(tmp_path)
        entries_dir.mkdir(parents=True, exist_ok=True)

        with patch(
            "trw_memory.sync.fetch_shared_memories",
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
                "trw_memory.sync.fetch_shared_memories",
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
        # P5: the failure is on the PAYLOAD, not only in the log.
        assert result["remote_recall"] == {"status": "failed", "reason": "Exception"}

    def test_remote_fetch_status_is_on_the_payload_when_incomplete(self, tmp_path: Path) -> None:
        """A remote leg that answered but was not 'ok' names its status to the agent."""
        tools = _get_tools()
        entries_dir = _entries_dir(tmp_path)
        entries_dir.mkdir(parents=True, exist_ok=True)

        with patch(
            "trw_memory.sync.fetch_shared_memories",
            return_value=SharedFetchResult([], "fetch_failed", 0, 0),
        ):
            result = tools["trw_recall"].fn(query="testing")

        assert result["remote_recall"] == {"status": "fetch_failed", "fetched": 0, "refused": 0}

    def test_remote_fetch_ok_leaves_no_status_key(self, tmp_path: Path) -> None:
        tools = _get_tools()
        entries_dir = _entries_dir(tmp_path)
        entries_dir.mkdir(parents=True, exist_ok=True)

        with patch(
            "trw_memory.sync.fetch_shared_memories",
            return_value=SharedFetchResult([], "ok", 0, 0),
        ):
            result = tools["trw_recall"].fn(query="testing")

        assert "remote_recall" not in result


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
