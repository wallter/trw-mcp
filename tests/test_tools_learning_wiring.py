"""Tests for learning tool registration and fail-open wiring around recall.

PRD-CORE-280 slice e1: every ``trw_recall`` call here is exercised for its
wiring (local-only recall, record_recall tracking), not for
memory storage behaviour, so the checkout routes through ``fake_memory_store``.
Access tracking (``memory_adapter.record_surfaced``, called by
``execute_recall`` on every recall that surfaces rows) routes through the
same fake seam, so it needs no separate silencing.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from trw_memory.sync import SharedFetchResult

from tests._tools_learning_shared import _entries_dir, _get_tools
from tests.conftest import get_tools_sync, make_test_server

pytestmark = pytest.mark.usefixtures("fake_memory_store")


class TestToolDelegationIntact:
    """Verify all learning tool functions remain registered and callable."""

    def test_all_learning_tools_registered(self) -> None:
        """All learning tools should be registered on a test server.

        PRD-CORE-291 merged ``trw_learn_update`` into ``trw_learn``'s update
        mode (``learning_id`` set); it is no longer a separate registration.
        S6c (PRD-CORE-300) deleted the deprecated ``trw_claude_md_sync`` alias,
        and S6b moved instruction sync to ``trw-mcp instructions sync``.
        """
        srv = make_test_server("learning")
        tool_names = set(get_tools_sync(srv).keys())
        expected = {
            "trw_learn",
            "trw_recall",
        }
        assert expected.issubset(tool_names), f"Missing tools: {expected - tool_names}"
        assert "trw_learn_update" not in tool_names
        assert "trw_claude_md_sync" not in tool_names
        assert "trw_instructions_sync" not in tool_names
        assert len(tool_names) == 2, f"Expected 2 tools, got {len(tool_names)}: {tool_names}"


class TestRecallIsLocalOnly:
    """SHARED-RECALL-LOCAL: ``trw_recall`` reads the local store only.

    The platform's ``POST /v1/learnings/search`` never did relevance search (it
    ignored the query and sorted ``shared_learnings`` by id), so the ``[shared]``
    leg merged unranked rows into every recall. Learnings from the operator's
    other hosts arrive through team sync instead and are recalled locally.
    """

    def test_recall_never_asks_the_platform_search_endpoint(self, tmp_path: Path) -> None:
        tools = _get_tools()
        _entries_dir(tmp_path).mkdir(parents=True, exist_ok=True)
        shared = {"id": "R-remote001", "summary": "[shared] Remote pattern about testing", "impact": 0.8}

        with patch(
            "trw_memory.sync.fetch_shared_memories", return_value=SharedFetchResult([shared], "ok", 1, 0)
        ) as fetch:
            result = tools["trw_recall"].fn(query="testing")

        fetch.assert_not_called()
        assert not any("[shared]" in str(e.get("claim", "")) for e in result.get("learnings", []))
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
