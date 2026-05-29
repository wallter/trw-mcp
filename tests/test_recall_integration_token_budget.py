"""Recall token-budget and result-capping integration tests."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from tests._recall_integration_support import _make_config, _make_entry, _make_sized_entry


def test_trw_recall_token_budget_metadata(tmp_path: Path) -> None:
    """execute_recall with token_budget returns token metadata."""
    from trw_mcp.tools._recall_impl import execute_recall

    config = _make_config()
    trw_dir = tmp_path / ".trw"
    trw_dir.mkdir()
    (trw_dir / "context").mkdir()
    entries = [_make_sized_entry("L-1", 10)]

    with (
        patch("trw_mcp.tools._recall_impl._detect_surface_phase", return_value=""),
        patch("trw_mcp.tools._recall_impl.log_surface_event"),
    ):
        result = execute_recall(
            query="test",
            trw_dir=trw_dir,
            config=config,
            token_budget=4000,
            _adapter_recall=lambda *a, **kw: entries,
            _adapter_update_access=lambda *a, **kw: None,
            _search_patterns=lambda *a, **kw: [],
            _rank_by_utility=lambda learnings, *a, **kw: learnings,
            _collect_context=lambda *a, **kw: {},
        )

    assert "tokens_used" in result
    assert "tokens_budget" in result
    assert result["tokens_budget"] == 4000
    assert isinstance(result["tokens_used"], int)
    assert isinstance(result["tokens_truncated"], bool)


def test_trw_recall_token_budget_truncates(tmp_path: Path) -> None:
    """execute_recall truncates results exceeding token_budget."""
    from trw_mcp.tools._recall_impl import execute_recall

    config = _make_config()
    trw_dir = tmp_path / ".trw"
    trw_dir.mkdir()
    (trw_dir / "context").mkdir()
    entries = [_make_sized_entry(f"L-{i}", 75) for i in range(10)]

    with (
        patch("trw_mcp.tools._recall_impl._detect_surface_phase", return_value=""),
        patch("trw_mcp.tools._recall_impl.log_surface_event"),
    ):
        result = execute_recall(
            query="test",
            trw_dir=trw_dir,
            config=config,
            token_budget=250,
            _adapter_recall=lambda *a, **kw: entries,
            _adapter_update_access=lambda *a, **kw: None,
            _search_patterns=lambda *a, **kw: [],
            _rank_by_utility=lambda learnings, *a, **kw: learnings,
            _collect_context=lambda *a, **kw: {},
        )

    assert result["tokens_truncated"] is True
    assert len(result["learnings"]) < 10


def test_trw_recall_token_budget_none_informational(tmp_path: Path) -> None:
    """token_budget=None still computes tokens_used."""
    from trw_mcp.tools._recall_impl import execute_recall

    config = _make_config()
    trw_dir = tmp_path / ".trw"
    trw_dir.mkdir()
    (trw_dir / "context").mkdir()

    with (
        patch("trw_mcp.tools._recall_impl._detect_surface_phase", return_value=""),
        patch("trw_mcp.tools._recall_impl.log_surface_event"),
    ):
        result = execute_recall(
            query="test",
            trw_dir=trw_dir,
            config=config,
            token_budget=None,
            _adapter_recall=lambda *a, **kw: [_make_sized_entry("L-1", 10)],
            _adapter_update_access=lambda *a, **kw: None,
            _search_patterns=lambda *a, **kw: [],
            _rank_by_utility=lambda learnings, *a, **kw: learnings,
            _collect_context=lambda *a, **kw: {},
        )

    assert result["tokens_budget"] is None
    assert result["tokens_truncated"] is False
    assert result["tokens_used"] > 0


def test_trw_recall_token_budget_invalid_raises(tmp_path: Path) -> None:
    """token_budget <= 0 raises ValueError."""
    from trw_mcp.tools._recall_impl import execute_recall

    config = _make_config()
    trw_dir = tmp_path / ".trw"
    trw_dir.mkdir()

    with pytest.raises(ValueError, match="token_budget must be positive"):
        execute_recall(
            query="test",
            trw_dir=trw_dir,
            config=config,
            token_budget=0,
            _adapter_recall=lambda *a, **kw: [],
            _adapter_update_access=lambda *a, **kw: None,
            _search_patterns=lambda *a, **kw: [],
            _rank_by_utility=lambda learnings, *a, **kw: learnings,
            _collect_context=lambda *a, **kw: {},
        )


def test_execute_recall_deprioritizes_injected_results_before_cap(tmp_path: Path) -> None:
    """Already-in-context learnings should not consume the primary result slots."""
    from trw_mcp.tools._recall_impl import execute_recall

    config = _make_config()
    trw_dir = tmp_path / ".trw"
    trw_dir.mkdir()
    (trw_dir / "context").mkdir()
    entries = [
        _make_entry("L-1", impact=0.9),
        _make_entry("L-2", impact=0.8),
        _make_entry("L-3", impact=0.7),
    ]

    with (
        patch("trw_mcp.tools._recall_impl._detect_surface_phase", return_value=""),
        patch("trw_mcp.tools._recall_impl.log_surface_event"),
    ):
        result = execute_recall(
            query="test",
            trw_dir=trw_dir,
            config=config,
            max_results=2,
            deprioritized_ids={"L-1"},
            _adapter_recall=lambda *a, **kw: entries,
            _adapter_update_access=lambda *a, **kw: None,
            _search_patterns=lambda *a, **kw: [],
            _rank_by_utility=lambda learnings, *a, **kw: learnings,
            _collect_context=lambda *a, **kw: {},
        )

    assert [entry["id"] for entry in result["learnings"]] == ["L-2", "L-3"]


def test_trw_recall_passes_injected_ids_to_execute_recall(tmp_path: Path, monkeypatch) -> None:
    """trw_recall should wire injected IDs into execute_recall before result capping."""
    from tests.conftest import get_tools_sync, make_test_server

    trw_dir = tmp_path / ".trw"
    (trw_dir / "context").mkdir(parents=True)
    (trw_dir / "context" / "injected_learning_ids.txt").write_text("L-1\nL-2\n", encoding="utf-8")
    captured: dict[str, object] = {}

    def fake_execute_recall(*args, **kwargs):
        captured["deprioritized_ids"] = kwargs.get("deprioritized_ids")
        return {
            "query": "test",
            "learnings": [],
            "patterns": [],
            "context": {},
            "total_matches": 0,
            "total_available": 0,
            "compact": False,
            "max_results": kwargs.get("max_results"),
            "topic_filter_ignored": False,
            "tokens_used": 0,
            "tokens_budget": None,
            "tokens_truncated": False,
        }

    monkeypatch.setattr("trw_mcp.tools.learning.resolve_trw_dir", lambda: trw_dir)
    monkeypatch.setattr("trw_mcp.tools._recall_impl.execute_recall", fake_execute_recall)

    recall_tool = get_tools_sync(make_test_server("learning"))["trw_recall"].fn
    recall_tool(query="test", max_results=2)

    assert captured["deprioritized_ids"] == {"L-1", "L-2"}
