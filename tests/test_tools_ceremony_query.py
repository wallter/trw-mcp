"""Integration tests for trw_session_start query behavior."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from tests._ceremony_helpers import make_ceremony_server as _make_ceremony_server
from trw_mcp.models.config import TRWConfig


@pytest.mark.integration
class TestSessionStartWithQuery:
    """trw_session_start(query=...) focused hybrid recall tests."""

    def test_query_empty_is_default_behavior(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Empty string query uses default wildcard — no 'query' key in result."""
        tools = _make_ceremony_server(monkeypatch, tmp_path)
        trw_dir = tmp_path / ".trw"
        (trw_dir / "learnings" / "entries").mkdir(parents=True)
        (trw_dir / "context").mkdir(parents=True)

        with (
            patch("trw_mcp.tools.ceremony.resolve_trw_dir", return_value=trw_dir),
            patch("trw_mcp.tools.ceremony.find_active_run", return_value=None),
        ):
            result = tools["trw_session_start"].fn()

        assert result["success"] is True
        assert "query" not in result

    def test_query_triggers_focused_recall(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Non-empty query makes 2 adapter_recall calls, returns 'query' key."""
        tools = _make_ceremony_server(monkeypatch, tmp_path)
        trw_dir = tmp_path / ".trw"
        (trw_dir / "learnings" / "entries").mkdir(parents=True)
        (trw_dir / "context").mkdir(parents=True)

        call_log: list[dict[str, Any]] = []

        def _fake_recall(
            _trw_d: Any,
            *,
            query: str = "*",
            min_impact: float = 0.0,
            max_results: int = 25,
            compact: bool = False,
            tags: Any = None,
            status: Any = None,
            allow_cold_embedding_init: bool = True,
        ) -> list[dict[str, object]]:
            call_log.append({"query": query, "min_impact": min_impact})
            if query == "*":
                return [{"id": "L-base001", "summary": "Baseline", "impact": 0.8}]
            return [{"id": "L-focus001", "summary": "Focused", "impact": 0.4}]

        with (
            patch("trw_mcp.tools.ceremony.resolve_trw_dir", return_value=trw_dir),
            patch("trw_mcp.tools.ceremony.find_active_run", return_value=None),
            patch("trw_mcp.state.memory_adapter.recall_learnings", side_effect=_fake_recall),
        ):
            result = tools["trw_session_start"].fn(query="authentication JWT")

        assert result["query"] == "authentication JWT"
        assert int(str(result["query_matched"])) >= 1
        assert len(call_log) >= 2
        assert any(call["query"] == "authentication JWT" for call in call_log)
        assert any(call["query"] == "*" for call in call_log)

    def test_query_deduplicates_across_recalls(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Same ID from both recalls appears only once in merged results."""
        tools = _make_ceremony_server(monkeypatch, tmp_path)
        trw_dir = tmp_path / ".trw"
        (trw_dir / "learnings" / "entries").mkdir(parents=True)
        (trw_dir / "context").mkdir(parents=True)

        shared_entry: dict[str, object] = {"id": "L-shared", "summary": "Shared", "impact": 0.8}
        focused_only: dict[str, object] = {"id": "L-focus", "summary": "Focused", "impact": 0.4}
        baseline_only: dict[str, object] = {"id": "L-base", "summary": "Baseline", "impact": 0.9}

        def _fake_recall(
            _trw_d: Any,
            *,
            query: str = "*",
            min_impact: float = 0.0,
            max_results: int = 25,
            compact: bool = False,
            tags: Any = None,
            status: Any = None,
            allow_cold_embedding_init: bool = True,
        ) -> list[dict[str, object]]:
            if query == "*":
                return [shared_entry, baseline_only]
            return [focused_only, shared_entry]

        with (
            patch("trw_mcp.tools.ceremony.resolve_trw_dir", return_value=trw_dir),
            patch("trw_mcp.tools.ceremony.find_active_run", return_value=None),
            patch("trw_mcp.state.memory_adapter.recall_learnings", side_effect=_fake_recall),
        ):
            result = tools["trw_session_start"].fn(query="auth")

        learnings = result["learnings"]
        assert isinstance(learnings, list)
        ids = [str(entry.get("id", "")) for entry in learnings]
        assert ids.count("L-shared") == 1
        assert ids.index("L-focus") < ids.index("L-base")

    def test_query_recall_failure_falls_back(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Exception in recall is handled gracefully — result still returned."""
        tools = _make_ceremony_server(monkeypatch, tmp_path)
        trw_dir = tmp_path / ".trw"
        (trw_dir / "learnings" / "entries").mkdir(parents=True)
        (trw_dir / "context").mkdir(parents=True)

        with (
            patch(
                "trw_mcp.tools.ceremony.resolve_trw_dir",
                side_effect=Exception("recall boom"),
            ),
            patch("trw_mcp.tools.ceremony.find_active_run", return_value=None),
        ):
            result = tools["trw_session_start"].fn(query="auth")

        # Recall is fail-open by contract: a recall-only failure (here injected
        # via resolve_trw_dir, which the recall step calls) is surfaced as a
        # non-fatal warning and must NOT flip success into a misleading retry.
        assert result["success"] is True
        assert any("recall" in w for w in result.get("warnings", []))
        assert "recall" not in " ".join(result.get("errors", []))
        assert result["learnings"] == []
        assert "run" in result

    def test_query_merged_into_auto_recall(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Auto-recall query includes user query tokens + phase context."""
        tools = _make_ceremony_server(monkeypatch, tmp_path)
        trw_dir = tmp_path / ".trw"
        (trw_dir / "learnings" / "entries").mkdir(parents=True)
        (trw_dir / "context").mkdir(parents=True)
        all_queries: list[str] = []

        def _fake_recall(
            _trw_d: Any,
            *,
            query: str = "*",
            min_impact: float = 0.0,
            max_results: int = 25,
            compact: bool = False,
            tags: Any = None,
            status: Any = None,
            allow_cold_embedding_init: bool = True,
        ) -> list[dict[str, object]]:
            all_queries.append(query)
            return []

        run_dir = tmp_path / "docs" / "task" / "runs" / "20260228T000000Z-test"
        (run_dir / "meta").mkdir(parents=True)
        (run_dir / "meta" / "run.yaml").write_text(
            "run_id: test\nstatus: active\nphase: implement\ntask_name: auth-feature\n",
            encoding="utf-8",
        )
        (run_dir / "meta" / "events.jsonl").write_text("", encoding="utf-8")

        with (
            patch("trw_mcp.tools.ceremony.resolve_trw_dir", return_value=trw_dir),
            patch("trw_mcp.tools.ceremony.find_active_run", return_value=run_dir),
            patch("trw_mcp.state.memory_adapter.recall_learnings", side_effect=_fake_recall),
            patch("trw_mcp.tools.ceremony.get_config", return_value=TRWConfig(auto_recall_enabled=True)),
        ):
            _result = tools["trw_session_start"].fn(query="JWT validation")

        assert len(all_queries) >= 1
        has_user_tokens = any("JWT" in query or "validation" in query for query in all_queries)
        assert has_user_tokens, f"Expected user tokens in recall queries: {all_queries}"
