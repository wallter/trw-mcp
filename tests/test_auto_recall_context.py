"""Context and config gating tests for auto-recall."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from tests._ceremony_helpers import make_ceremony_server as _make_ceremony_server
from tests._test_auto_recall_support import _setup_trw_dir


class TestAutoRecallDisabled:
    """Auto-recall disabled via config flag."""

    def test_no_auto_recall_when_disabled(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When auto_recall_enabled=False, no auto_recalled key in result."""
        from trw_mcp.models.config import TRWConfig, _reset_config

        disabled_config = TRWConfig(auto_recall_enabled=False)
        _reset_config(disabled_config)

        tools = _make_ceremony_server(monkeypatch, tmp_path)
        trw_dir = _setup_trw_dir(tmp_path)

        with (
            patch("trw_mcp.tools.ceremony.resolve_trw_dir", return_value=trw_dir),
            patch("trw_mcp.tools.ceremony.find_active_run", return_value=None),
        ):
            result = tools["trw_session_start"].fn()

        assert "auto_recalled" not in result


class TestAutoRecallWithActiveRun:
    """Auto-recall uses task context from active run."""

    def test_uses_task_and_phase_as_query(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When active run has task+phase, those form the query tokens."""
        tools = _make_ceremony_server(monkeypatch, tmp_path)
        trw_dir = _setup_trw_dir(tmp_path)

        run_dir = tmp_path / "docs" / "task" / "runs" / "20260226T120000Z-test"
        meta = run_dir / "meta"
        meta.mkdir(parents=True)
        (meta / "run.yaml").write_text(
            "run_id: test-run\nstatus: active\nphase: implement\ntask: fix-scoring\n",
            encoding="utf-8",
        )
        (meta / "events.jsonl").write_text("", encoding="utf-8")

        captured_calls: list[dict[str, Any]] = []

        def _fake_recall(
            trw_dir_arg: Any,
            *,
            query: str = "*",
            tags: Any = None,
            min_impact: float = 0.0,
            max_results: int = 25,
            compact: bool = False,
            **kwargs: Any,
        ) -> list[dict[str, object]]:
            captured_calls.append({"query": query, "tags": tags, "min_impact": min_impact})
            return [{"id": "L-x1", "summary": "Scoring fix tip", "impact": 0.9, "tags": ["gotcha"], "status": "active"}]

        with (
            patch("trw_mcp.tools.ceremony.resolve_trw_dir", return_value=trw_dir),
            patch("trw_mcp.tools.ceremony.find_active_run", return_value=run_dir),
            patch(
                "trw_mcp.state.memory_adapter.recall_learnings",
                side_effect=_fake_recall,
            ),
        ):
            result = tools["trw_session_start"].fn()

        assert "auto_recalled" in result
        assert result["auto_recall_count"] == 1
        auto_recall_call = None
        for call in captured_calls:
            if call["min_impact"] == 0.5:
                auto_recall_call = call
                break
        assert auto_recall_call is not None
        assert "fix-scoring" in auto_recall_call["query"]
        assert "implement" in auto_recall_call["query"]
        assert auto_recall_call.get("tags") == ["gotcha", "testing", "pattern"]

    def test_uses_wildcard_when_no_task_context(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When no active run, query tokens default to empty (wildcard)."""
        tools = _make_ceremony_server(monkeypatch, tmp_path)
        trw_dir = _setup_trw_dir(tmp_path)

        captured_calls: list[dict[str, Any]] = []

        def _fake_recall(
            trw_dir_arg: Any,
            *,
            query: str = "*",
            tags: Any = None,
            min_impact: float = 0.0,
            max_results: int = 25,
            compact: bool = False,
            **kwargs: Any,
        ) -> list[dict[str, object]]:
            captured_calls.append({"query": query, "tags": tags, "min_impact": min_impact})
            return [{"id": "L-y1", "summary": "General tip", "impact": 0.8, "tags": ["pattern"], "status": "active"}]

        with (
            patch("trw_mcp.tools.ceremony.resolve_trw_dir", return_value=trw_dir),
            patch("trw_mcp.tools.ceremony.find_active_run", return_value=None),
            patch(
                "trw_mcp.state.memory_adapter.recall_learnings",
                side_effect=_fake_recall,
            ),
        ):
            result = tools["trw_session_start"].fn()

        assert "auto_recalled" in result
        auto_recall_call = None
        for call in captured_calls:
            if call.get("min_impact") == 0.5:
                auto_recall_call = call
                break
        assert auto_recall_call is not None
        assert auto_recall_call["query"] == "*"
        assert auto_recall_call.get("tags") is None
