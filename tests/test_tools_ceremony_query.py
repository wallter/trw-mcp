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
        fake_memory_store: object,
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
        """Non-empty query makes ONE recall, with the query (PRD-CORE-294 FR02), returns 'query' key."""
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
        assert [call["query"] for call in call_log] == ["authentication JWT"]
        assert [stub["id"] for stub in result["learnings"]] == ["L-focus001"]

    def test_zero_focused_matches_emits_explanatory_advisory(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A focused query that matches nothing still returns the baseline, which
        is impact-ranked and query-INDEPENDENT. Without an advisory, ``query_matched:
        0`` is unexplained and the agent reads the baseline as query hits."""
        tools = _make_ceremony_server(monkeypatch, tmp_path)
        trw_dir = tmp_path / ".trw"
        (trw_dir / "learnings" / "entries").mkdir(parents=True)
        (trw_dir / "context").mkdir(parents=True)

        def _fake_recall(
            _trw_d: Any,
            *,
            query: str = "*",
            min_impact: float = 0.0,
            max_results: int = 25,
            compact: bool = False,
            tags: Any = None,
            status: Any = None,
        ) -> list[dict[str, object]]:
            if query == "*":
                return [{"id": "L-base001", "summary": "Unrelated baseline", "impact": 0.95}]
            return []

        with (
            patch("trw_mcp.tools.ceremony.resolve_trw_dir", return_value=trw_dir),
            patch("trw_mcp.tools.ceremony.find_active_run", return_value=None),
            patch("trw_mcp.state.memory_adapter.recall_learnings", side_effect=_fake_recall),
        ):
            result = tools["trw_session_start"].fn(query="framework rewrite simplification core surface")

        # No baseline stands in for the missing matches any more: the block is empty.
        assert result["learnings"] == []
        advisory = str(result["query_advisory"])
        assert "0 entries" in advisory
        assert "trw_recall" in advisory
        # Survives compact mode (the default) — it must not be trimmed away.
        assert result["compact"] is True

    def test_nonzero_focused_matches_omit_advisory(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The advisory carries no signal when the query matched — token-budget
        rule: advisory fields are omitted rather than emitted empty."""
        tools = _make_ceremony_server(monkeypatch, tmp_path)
        trw_dir = tmp_path / ".trw"
        (trw_dir / "learnings" / "entries").mkdir(parents=True)
        (trw_dir / "context").mkdir(parents=True)

        def _fake_recall(
            _trw_d: Any,
            *,
            query: str = "*",
            min_impact: float = 0.0,
            max_results: int = 25,
            compact: bool = False,
            tags: Any = None,
            status: Any = None,
        ) -> list[dict[str, object]]:
            return [{"id": "L-hit001", "summary": "A real hit", "impact": 0.6}]

        with (
            patch("trw_mcp.tools.ceremony.resolve_trw_dir", return_value=trw_dir),
            patch("trw_mcp.tools.ceremony.find_active_run", return_value=None),
            patch("trw_mcp.state.memory_adapter.recall_learnings", side_effect=_fake_recall),
        ):
            result = tools["trw_session_start"].fn(query="auth")

        assert result["learnings"]
        assert "query_advisory" not in result

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

        # PRD-CORE-263-FR01: ``recall`` is declared ``critical`` in the
        # session-start step table. Its step body no longer swallows the
        # exception into a fail-open warning with a green payload — it raises a
        # typed ``SessionStartStepError`` and the runner's critical branch
        # degrades the payload: ``success`` is false and ``errors`` names the
        # step, while the failure is still recorded as an observable
        # degradation.
        assert result["success"] is False
        assert any("recall" in e for e in result["errors"])
        recall_degradations = [d for d in result.get("degradations", []) if d["step"] == "recall"]
        assert len(recall_degradations) == 1
        assert "recall boom" in recall_degradations[0]["message"]
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
