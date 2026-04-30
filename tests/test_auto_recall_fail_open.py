"""Fail-open and limit behavior tests for auto-recall."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from tests._ceremony_helpers import make_ceremony_server as _make_ceremony_server
from tests._test_auto_recall_support import _setup_trw_dir


class TestAutoRecallFailOpen:
    """Auto-recall errors must not crash trw_session_start."""

    def test_error_failopen(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When search_entries raises in auto-recall step, session_start still succeeds."""
        tools = _make_ceremony_server(monkeypatch, tmp_path)
        trw_dir = _setup_trw_dir(tmp_path)

        call_count = {"n": 0}

        def _failing_recall(
            trw_dir_arg: Any,
            **kwargs: Any,
        ) -> list[dict[str, object]]:
            call_count["n"] += 1
            if call_count["n"] > 1:
                raise RuntimeError("search engine down")
            return []

        with (
            patch("trw_mcp.tools.ceremony.resolve_trw_dir", return_value=trw_dir),
            patch("trw_mcp.tools.ceremony.find_active_run", return_value=None),
            patch(
                "trw_mcp.state.memory_adapter.recall_learnings",
                side_effect=_failing_recall,
            ),
        ):
            result = tools["trw_session_start"].fn()

        assert "auto_recalled" not in result
        assert result["success"] is True
        assert "timestamp" in result

    def test_error_failopen_with_active_run(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Auto-recall error with active run does not affect run status."""
        tools = _make_ceremony_server(monkeypatch, tmp_path)
        trw_dir = _setup_trw_dir(tmp_path)

        run_dir = tmp_path / "docs" / "task" / "runs" / "20260226T120000Z-test"
        meta = run_dir / "meta"
        meta.mkdir(parents=True)
        (meta / "run.yaml").write_text(
            "run_id: test-run\nstatus: active\nphase: research\ntask_name: test\n",
            encoding="utf-8",
        )
        (meta / "events.jsonl").write_text("", encoding="utf-8")

        call_count = {"n": 0}

        def _failing_recall(
            trw_dir_arg: Any,
            **kwargs: Any,
        ) -> list[dict[str, object]]:
            call_count["n"] += 1
            if call_count["n"] > 1:
                raise Exception("recall boom")
            return []

        with (
            patch("trw_mcp.tools.ceremony.resolve_trw_dir", return_value=trw_dir),
            patch("trw_mcp.tools.ceremony.find_active_run", return_value=run_dir),
            patch(
                "trw_mcp.state.memory_adapter.recall_learnings",
                side_effect=_failing_recall,
            ),
        ):
            result = tools["trw_session_start"].fn()

        assert "run" in result
        assert result["run"]["status"] == "active"
        assert result["run"]["phase"] == "research"
        assert "auto_recalled" not in result


class TestAutoRecallMaxResults:
    """Auto-recall respects max_results config."""

    def test_respects_max_results(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """auto_recall_max_results limits returned entries."""
        from trw_mcp.models.config import TRWConfig

        mock_cfg = TRWConfig(auto_recall_max_results=2)
        monkeypatch.setattr("trw_mcp.tools.ceremony.get_config", lambda: mock_cfg)

        tools = _make_ceremony_server(monkeypatch, tmp_path)
        trw_dir = _setup_trw_dir(tmp_path)

        mock_entries = [
            {"id": f"L-{i}", "summary": f"Learning {i}", "impact": 0.8 - i * 0.05, "tags": [], "status": "active"}
            for i in range(5)
        ]

        def _fake_recall(
            trw_dir_arg: Any,
            **kwargs: Any,
        ) -> list[dict[str, object]]:
            return mock_entries

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
        assert result["auto_recall_count"] == 2
        assert len(result["auto_recalled"]) == 2
