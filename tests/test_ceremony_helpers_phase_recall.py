"""Tests for phase-to-tag mapping and session recall helpers."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from trw_mcp.models.config import TRWConfig
from trw_mcp.state.persistence import FileStateReader
from trw_mcp.tools._ceremony_helpers import _phase_to_tags, perform_session_recalls
from ._ceremony_helpers_support import trw_dir  # noqa: F401

from ._ceremony_helpers_support import trw_dir  # noqa: F401

from ._ceremony_helpers_support import trw_dir  # noqa: F401


class TestPhaseToTags:
    """Phase-to-tag mapping for auto-recall."""

    def test_known_phase_returns_tags(self) -> None:
        tags = _phase_to_tags("implement")
        assert "gotcha" in tags
        assert "testing" in tags
        assert "pattern" in tags

    def test_unknown_phase_returns_empty(self) -> None:
        assert _phase_to_tags("nonexistent") == []

    def test_case_insensitive(self) -> None:
        assert _phase_to_tags("RESEARCH") == _phase_to_tags("research")

    def test_all_phases_have_entries(self) -> None:
        phases = ["research", "plan", "implement", "validate", "review", "deliver"]
        for phase in phases:
            tags = _phase_to_tags(phase)
            assert len(tags) > 0, f"Phase {phase} should have tags"


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
            patch(
                "trw_mcp.tools._ceremony_helpers.adapter_recall",
                return_value=mock_entries,
            )
            if False
            else patch(
                "trw_mcp.state.memory_adapter.recall_learnings",
                return_value=mock_entries,
            ),
            patch("trw_mcp.state.memory_adapter.update_access_tracking"),
            patch("trw_mcp.tools._session_recall_helpers.log_recall_receipt"),
        ):
            learnings, auto_recalled, extra = perform_session_recalls(
                trw_dir,
                "",
                config,
                reader,
            )

        assert len(learnings) == 2
        assert extra["total_available"] == 2
        assert auto_recalled == []

    def test_focused_recall_deduplicates(
        self,
        trw_dir: Path,
        config: TRWConfig,
        reader: FileStateReader,
    ) -> None:
        focused = [
            {"id": "L-001", "summary": "Focused hit", "impact": 0.5},
            {"id": "L-002", "summary": "Focused hit 2", "impact": 0.4},
        ]
        baseline = [
            {"id": "L-001", "summary": "Focused hit", "impact": 0.5},
            {"id": "L-003", "summary": "Baseline only", "impact": 0.9},
        ]

        call_count = 0

        def mock_recall(*args: object, **kwargs: object) -> list[dict[str, object]]:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return focused
            return baseline

        with (
            patch("trw_mcp.state.memory_adapter.recall_learnings", side_effect=mock_recall),
            patch("trw_mcp.state.memory_adapter.update_access_tracking"),
            patch("trw_mcp.tools._session_recall_helpers.log_recall_receipt"),
        ):
            learnings, _, extra = perform_session_recalls(
                trw_dir,
                "test query",
                config,
                reader,
            )

        assert len(learnings) == 3
        ids = [str(entry["id"]) for entry in learnings]
        assert ids == ["L-001", "L-002", "L-003"]
        assert extra["query"] == "test query"
        assert "query_matched" in extra

    def test_updates_access_tracking(
        self,
        trw_dir: Path,
        config: TRWConfig,
        reader: FileStateReader,
    ) -> None:
        mock_entries = [{"id": "L-001", "summary": "X", "impact": 0.8}]
        mock_update = MagicMock()

        with (
            patch("trw_mcp.state.memory_adapter.recall_learnings", return_value=mock_entries),
            patch("trw_mcp.state.memory_adapter.update_access_tracking", mock_update),
            patch("trw_mcp.tools._session_recall_helpers.log_recall_receipt"),
        ):
            perform_session_recalls(trw_dir, "", config, reader)

        mock_update.assert_called_once_with(trw_dir, ["L-001"])

    def test_increments_session_counts_for_surfaced_learnings(
        self,
        trw_dir: Path,
        config: TRWConfig,
        reader: FileStateReader,
    ) -> None:
        mock_entries = [{"id": "L-001", "summary": "X", "impact": 0.8}]
        mock_increment = MagicMock()

        with (
            patch("trw_mcp.state.memory_adapter.recall_learnings", return_value=mock_entries),
            patch("trw_mcp.state.memory_adapter.update_access_tracking"),
            patch("trw_mcp.state.memory_adapter.increment_session_counts", mock_increment),
            patch("trw_mcp.tools._session_recall_helpers.log_recall_receipt"),
        ):
            perform_session_recalls(trw_dir, "", config, reader)

        mock_increment.assert_called_once_with(trw_dir, ["L-001"])

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
            patch("trw_mcp.state.memory_adapter.update_access_tracking"),
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
