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

    # F-002: when the caller omits token_budget, a sane default ceiling is
    # applied (anti-context-window-collapse guard) and surfaced as tokens_budget.
    assert result["tokens_budget"] is not None
    assert result["tokens_budget"] > 0
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
    # Distinct summaries so the dedup pass (F-DEDUP-001) treats them as separate
    # findings — this test exercises deprioritization ordering, not dedup.
    entries = [
        _make_entry("L-1", impact=0.9, summary="finding one"),
        _make_entry("L-2", impact=0.8, summary="finding two"),
        _make_entry("L-3", impact=0.7, summary="finding three"),
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


def test_trw_recall_tool_forwards_token_budget(tmp_path: Path, monkeypatch) -> None:
    """F-002: the trw_recall MCP tool exposes token_budget and forwards it."""
    from tests.conftest import get_tools_sync, make_test_server

    trw_dir = tmp_path / ".trw"
    (trw_dir / "context").mkdir(parents=True)
    captured: dict[str, object] = {}

    def fake_execute_recall(*args, **kwargs):
        captured["token_budget"] = kwargs.get("token_budget")
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
            "tokens_budget": kwargs.get("token_budget"),
            "tokens_truncated": False,
            "duplicates_collapsed": 0,
        }

    monkeypatch.setattr("trw_mcp.tools.learning.resolve_trw_dir", lambda: trw_dir)
    monkeypatch.setattr("trw_mcp.tools._recall_impl.execute_recall", fake_execute_recall)

    recall_tool = get_tools_sync(make_test_server("learning"))["trw_recall"].fn
    recall_tool(query="test", token_budget=1234)

    assert captured["token_budget"] == 1234


def _make_dup_entry(entry_id: str) -> dict[str, object]:
    """Entry with byte-identical content/detail/summary across IDs (a near-dup)."""
    return {
        "id": entry_id,
        "summary": "identical retrospective finding",
        "content": "the same body of text repeated verbatim across copies",
        "detail": "the same body of text repeated verbatim across copies",
        "tags": [],
        "impact": 0.5,
        "created": "2026-01-01T00:00:00Z",
    }


def test_recall_collapses_byte_identical_entries_to_one(tmp_path: Path) -> None:
    """F-DEDUP-001: five byte-identical-content entries collapse to a single result."""
    from trw_mcp.tools._recall_impl import execute_recall

    config = _make_config()
    trw_dir = tmp_path / ".trw"
    trw_dir.mkdir()
    (trw_dir / "context").mkdir()
    entries = [_make_dup_entry(f"L-{i}") for i in range(5)]

    with (
        patch("trw_mcp.tools._recall_impl._detect_surface_phase", return_value=""),
        patch("trw_mcp.tools._recall_impl.log_surface_event"),
    ):
        result = execute_recall(
            query="test",
            trw_dir=trw_dir,
            config=config,
            max_results=5,
            _adapter_recall=lambda *a, **kw: entries,
            _adapter_update_access=lambda *a, **kw: None,
            _search_patterns=lambda *a, **kw: [],
            _rank_by_utility=lambda learnings, *a, **kw: learnings,
            _collect_context=lambda *a, **kw: {},
        )

    assert len(result["learnings"]) == 1
    assert result["duplicates_collapsed"] == 4


def test_recall_default_token_budget_caps_serialized_size(tmp_path: Path) -> None:
    """F-002: with no caller budget, a default ceiling bounds the result size."""
    import json

    from trw_mcp.tools._recall_impl import DEFAULT_RECALL_TOKEN_BUDGET, execute_recall

    config = _make_config()
    trw_dir = tmp_path / ".trw"
    trw_dir.mkdir()
    (trw_dir / "context").mkdir()
    # Many large DISTINCT entries (distinct so dedup keeps them) that would blow
    # the context window if returned uncapped.
    entries = [_make_sized_entry(f"L-{i}", 400) for i in range(50)]

    with (
        patch("trw_mcp.tools._recall_impl._detect_surface_phase", return_value=""),
        patch("trw_mcp.tools._recall_impl.log_surface_event"),
    ):
        result = execute_recall(
            query="test",
            trw_dir=trw_dir,
            config=config,
            max_results=50,
            compact=False,  # force full mode so the ceiling is tested on full payloads
            token_budget=None,  # exercise the default ceiling
            _adapter_recall=lambda *a, **kw: entries,
            _adapter_update_access=lambda *a, **kw: None,
            _search_patterns=lambda *a, **kw: [],
            _rank_by_utility=lambda learnings, *a, **kw: learnings,
            _collect_context=lambda *a, **kw: {},
        )

    assert result["tokens_budget"] == DEFAULT_RECALL_TOKEN_BUDGET
    assert result["tokens_truncated"] is True
    # Serialized result must be far smaller than the uncapped 50-entry payload.
    serialized = json.dumps(result["learnings"])
    assert result["tokens_used"] <= DEFAULT_RECALL_TOKEN_BUDGET
    assert len(serialized) < len(json.dumps(entries))


def test_recall_compact_mode_strips_detail_field(tmp_path: Path) -> None:
    """F-003: compact mode is requested at fetch and detail is not in the output."""
    captured: dict[str, object] = {}

    from trw_mcp.tools._recall_impl import execute_recall

    config = _make_config()
    trw_dir = tmp_path / ".trw"
    trw_dir.mkdir()
    (trw_dir / "context").mkdir()

    def capturing_recall(*_a: object, **kw: object) -> list[dict[str, object]]:
        captured["compact"] = kw.get("compact")
        return [
            {
                "id": "L-1",
                "summary": "a finding",
                "detail": "very long detail body " * 50,
                "tags": [],
                "impact": 0.5,
                "status": "active",
            }
        ]

    with (
        patch("trw_mcp.tools._recall_impl._detect_surface_phase", return_value=""),
        patch("trw_mcp.tools._recall_impl.log_surface_event"),
    ):
        result = execute_recall(
            query="test",
            trw_dir=trw_dir,
            config=config,
            compact=True,
            _adapter_recall=capturing_recall,
            _adapter_update_access=lambda *a, **kw: None,
            _search_patterns=lambda *a, **kw: [],
            _rank_by_utility=lambda learnings, *a, **kw: learnings,
            _collect_context=lambda *a, **kw: {},
        )

    # compact requested at the fetch boundary (so detail is never deserialized)
    assert captured["compact"] is True
    # and detail does not appear in any returned entry
    assert all("detail" not in entry for entry in result["learnings"])


def test_recall_passes_prefetch_cap_to_backend(tmp_path: Path) -> None:
    """F-001: backend fetch is capped at max_results * PREFETCH_MULTIPLIER."""
    captured: dict[str, object] = {}

    from trw_mcp.tools._recall_impl import PREFETCH_MULTIPLIER, execute_recall

    config = _make_config()
    trw_dir = tmp_path / ".trw"
    trw_dir.mkdir()
    (trw_dir / "context").mkdir()

    def capturing_recall(*_a: object, **kw: object) -> list[dict[str, object]]:
        captured["max_results"] = kw.get("max_results")
        return []

    with (
        patch("trw_mcp.tools._recall_impl._detect_surface_phase", return_value=""),
        patch("trw_mcp.tools._recall_impl.log_surface_event"),
    ):
        execute_recall(
            query="test",
            trw_dir=trw_dir,
            config=config,
            max_results=7,
            _adapter_recall=capturing_recall,
            _adapter_update_access=lambda *a, **kw: None,
            _search_patterns=lambda *a, **kw: [],
            _rank_by_utility=lambda learnings, *a, **kw: learnings,
            _collect_context=lambda *a, **kw: {},
        )

    assert captured["max_results"] == 7 * PREFETCH_MULTIPLIER


def test_tokens_used_reflects_post_max_results_cap(tmp_path: Path) -> None:
    """tokens_used must match entries actually returned after max_results cap.

    When the token budget allows more entries than max_results, the reported
    tokens_used must equal the sum for the capped set — not the pre-cap set.
    This prevents callers from seeing a tokens_used > sum(returned entries).
    """
    from trw_memory.retrieval.token_budget import estimate_entry_tokens

    from trw_mcp.tools._recall_impl import execute_recall

    config = _make_config()
    trw_dir = tmp_path / ".trw"
    trw_dir.mkdir()
    (trw_dir / "context").mkdir()
    # 10 small distinct entries that all fit within a large budget
    entries = [_make_entry(f"L-{i}", impact=0.5, summary=f"distinct finding {i}") for i in range(10)]

    with (
        patch("trw_mcp.tools._recall_impl._detect_surface_phase", return_value=""),
        patch("trw_mcp.tools._recall_impl.log_surface_event"),
    ):
        result = execute_recall(
            query="test",
            trw_dir=trw_dir,
            config=config,
            token_budget=100_000,  # large budget so budget does NOT truncate
            max_results=3,  # but max_results caps output to 3
            _adapter_recall=lambda *a, **kw: entries,
            _adapter_update_access=lambda *a, **kw: None,
            _search_patterns=lambda *a, **kw: [],
            _rank_by_utility=lambda learnings, *a, **kw: learnings,
            _collect_context=lambda *a, **kw: {},
        )

    returned = result["learnings"]
    assert len(returned) == 3, "max_results=3 should limit output to 3 entries"
    expected_tokens = sum(estimate_entry_tokens(e) for e in returned)
    assert result["tokens_used"] == expected_tokens, (
        f"tokens_used ({result['tokens_used']}) should equal sum of returned "
        f"entries ({expected_tokens}), not pre-cap set"
    )
    assert result["tokens_truncated"] is False  # budget was large, no budget truncation
