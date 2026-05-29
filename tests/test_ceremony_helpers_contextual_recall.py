"""Tests for phase-contextual ceremony recall helpers."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from trw_mcp.models.config import TRWConfig
from trw_mcp.tools._ceremony_helpers import _phase_contextual_recall
from ._ceremony_helpers_support import run_dir, trw_dir  # noqa: F401


class TestPhaseContextualRecall:
    """Phase-contextual auto-recall with ranking."""

    def test_returns_empty_when_no_entries(
        self,
        trw_dir: Path,
        config: TRWConfig,
    ) -> None:
        with patch("trw_mcp.state.memory_adapter.recall_learnings", return_value=[]):
            result = _phase_contextual_recall(trw_dir, "", config, None, None)
        assert result == []

    def test_includes_phase_tags_from_run_status(
        self,
        trw_dir: Path,
        config: TRWConfig,
        run_dir: Path,
    ) -> None:
        mock_entries = [
            {"id": "L-001", "summary": "Test", "impact": 0.6, "tags": ["gotcha"]},
        ]
        run_status = {"phase": "implement", "task_name": "my-task"}

        with patch(
            "trw_mcp.state.memory_adapter.recall_learnings",
            return_value=mock_entries,
        ) as mock_recall:
            result = _phase_contextual_recall(
                trw_dir,
                "",
                config,
                run_dir,
                run_status,
            )

        call_kwargs = mock_recall.call_args
        assert call_kwargs is not None
        tags_arg = call_kwargs.kwargs.get("tags") or call_kwargs[1].get("tags")
        assert tags_arg is not None
        assert "gotcha" in tags_arg
        assert len(result) == 1
        assert result[0]["id"] == "L-001"

    def test_ranks_and_caps_results(
        self,
        trw_dir: Path,
        config: TRWConfig,
    ) -> None:
        mock_entries = [
            {"id": f"L-{index:03d}", "summary": f"Entry {index}", "impact": 0.5 + index * 0.01} for index in range(20)
        ]

        with patch(
            "trw_mcp.state.memory_adapter.recall_learnings",
            return_value=mock_entries,
        ):
            result = _phase_contextual_recall(trw_dir, "", config, None, None)

        assert len(result) <= config.auto_recall_max_results
        for entry in result:
            assert "id" in entry
            assert "summary" in entry
            assert "impact" in entry

    def test_focused_query_adds_tokens(
        self,
        trw_dir: Path,
        config: TRWConfig,
    ) -> None:
        with patch(
            "trw_mcp.state.memory_adapter.recall_learnings",
            return_value=[],
        ) as mock_recall:
            _phase_contextual_recall(trw_dir, "testing gotchas", config, None, None)

        call_args = mock_recall.call_args
        query_arg = call_args.kwargs.get("query") or call_args[1].get("query")
        assert "testing" in str(query_arg)
        assert "gotchas" in str(query_arg)

    def test_uses_compact_mode(
        self,
        trw_dir: Path,
        config: TRWConfig,
    ) -> None:
        """Phase-contextual recall must use compact=True to limit response size."""
        with patch(
            "trw_mcp.state.memory_adapter.recall_learnings",
            return_value=[],
        ) as mock_recall:
            _phase_contextual_recall(trw_dir, "", config, None, None)

        call_kwargs = mock_recall.call_args
        assert call_kwargs is not None
        compact_arg = call_kwargs.kwargs.get("compact")
        assert compact_arg is True

    def test_max_results_is_bounded(
        self,
        trw_dir: Path,
        config: TRWConfig,
    ) -> None:
        """Phase-contextual recall must not use max_results=0 (unlimited)."""
        with patch(
            "trw_mcp.state.memory_adapter.recall_learnings",
            return_value=[],
        ) as mock_recall:
            _phase_contextual_recall(trw_dir, "", config, None, None)

        call_kwargs = mock_recall.call_args
        assert call_kwargs is not None
        max_results_arg = call_kwargs.kwargs.get("max_results")
        assert max_results_arg is not None
        assert max_results_arg > 0
        assert max_results_arg <= config.auto_recall_max_results * 3

    def test_phase_contextual_recall_writes_propensity_log(
        self,
        trw_dir: Path,
        config: TRWConfig,
    ) -> None:
        """Phase auto-recall writes deterministic propensity entries for capped results."""
        mock_entries = [
            {"id": "L-001", "summary": "Entry 1", "impact": 0.7},
            {"id": "L-002", "summary": "Entry 2", "impact": 0.6},
        ]

        with patch(
            "trw_mcp.state.memory_adapter.recall_learnings",
            return_value=mock_entries,
        ):
            result = _phase_contextual_recall(
                trw_dir,
                "",
                config,
                None,
                {"phase": "implement", "task_name": "auth-fix"},
            )

        assert len(result) == 2
        log_path = trw_dir / "logs" / "propensity.jsonl"
        lines = [json.loads(line) for line in log_path.read_text().strip().split("\n") if line.strip()]
        assert [line["selected"] for line in lines] == ["L-001", "L-002"]
        assert lines[0]["context_phase"] == "IMPLEMENT"
        assert lines[0]["context_task_type"] == "phase_auto_recall"
        assert lines[0]["candidate_set"] == ["L-001", "L-002"]
