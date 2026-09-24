"""Tests for the session_start recall helper."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from tests._memory_store_fake import FakeMemoryStore
from trw_mcp.models.config import TRWConfig
from trw_mcp.state.persistence import FileStateReader
from trw_mcp.tools._ceremony_helpers import perform_session_recalls


class TestPerformSessionRecalls:
    """Core recall logic with dedup and access tracking."""

    def test_wildcard_recall_returns_learnings(
        self,
        trw_dir: Path,
        config: TRWConfig,
        reader: FileStateReader,
    ) -> None:
        mock_entries = [
            {"id": "L-001", "summary": "Test 1", "impact": 0.8},
            {"id": "L-002", "summary": "Test 2", "impact": 0.9},
        ]
        with (
            # Was `patch("..._ceremony_helpers.adapter_recall", ...) if False
            # else patch(...)`. The `if False` arm was never constructed, and
            # `_ceremony_helpers` has no `adapter_recall` attribute anyway — it
            # would have raised had it ever been reached. Only the live arm
            # remains.
            patch(
                "trw_mcp.state.memory_adapter.recall_learnings",
                return_value=mock_entries,
            ),
            patch("trw_mcp.tools._session_recall_helpers.log_recall_receipt"),
        ):
            learnings, extra = perform_session_recalls(
                trw_dir,
                "",
                config,
                reader,
            )

        assert [stub["id"] for stub in learnings] == ["L-001", "L-002"]
        assert "learnings_omitted" not in extra

    def test_updates_access_tracking(
        self,
        trw_dir: Path,
        config: TRWConfig,
        reader: FileStateReader,
    ) -> None:
        """``record_surfaced`` is the ONE call: it counts access AND session_start together."""
        mock_entries = [{"id": "L-001", "summary": "X", "impact": 0.8}]
        mock_record_surfaced = MagicMock()

        with (
            patch("trw_mcp.state.memory_adapter.recall_learnings", return_value=mock_entries),
            patch("trw_mcp.state.memory_adapter.record_surfaced", mock_record_surfaced),
            patch("trw_mcp.tools._session_recall_helpers.log_recall_receipt"),
        ):
            perform_session_recalls(trw_dir, "", config, reader)

        mock_record_surfaced.assert_called_once_with(trw_dir, ["L-001"], session_start=True)

    def test_increments_session_counts_for_surfaced_learnings(
        self,
        trw_dir: Path,
        config: TRWConfig,
        reader: FileStateReader,
        fake_memory_store: FakeMemoryStore,
    ) -> None:
        """End to end (real ``record_surfaced``, not a mock): the row's session_count is bumped once."""
        fake_memory_store.put("X", "default", {"entry_id": "L-001", "detail": "d"})
        mock_entries = [{"id": "L-001", "summary": "X", "impact": 0.8}]

        with (
            patch("trw_mcp.state.memory_adapter.recall_learnings", return_value=mock_entries),
            patch("trw_mcp.tools._session_recall_helpers.log_recall_receipt"),
        ):
            perform_session_recalls(trw_dir, "", config, reader)

        row = next(e for (_ns, eid), e in fake_memory_store.rows.items() if eid == "L-001")
        assert row.session_count == 1
        assert row.access_count == 1

    def test_writes_propensity_log_for_session_start_surfaces(
        self,
        trw_dir: Path,
        config: TRWConfig,
        reader: FileStateReader,
    ) -> None:
        """Session-start recall writes deterministic propensity entries for surfaced learnings."""
        mock_entries = [
            {"id": "L-001", "summary": "Test 1", "impact": 0.8},
            {"id": "L-002", "summary": "Test 2", "impact": 0.9},
        ]

        with (
            patch("trw_mcp.state.memory_adapter.recall_learnings", return_value=mock_entries),
            patch("trw_mcp.tools._session_recall_helpers.log_recall_receipt"),
        ):
            perform_session_recalls(trw_dir, "", config, reader)

        log_path = trw_dir / "logs" / "propensity.jsonl"
        lines = [json.loads(line) for line in log_path.read_text().strip().split("\n") if line.strip()]
        assert [line["selected"] for line in lines] == ["L-001", "L-002"]
        assert lines[0]["candidate_set"] == ["L-001", "L-002"]
        assert lines[1]["candidate_set"] == ["L-002"]
        assert lines[0]["context_task_type"] == "session_start"
        assert lines[0]["context_session_progress"] == "early"
