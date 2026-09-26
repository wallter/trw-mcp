"""PRD-CORE-294 FR02 / NFR02: one recall, at most three stubs, one small learning block.

Driven through the registered ``trw_session_start`` tool with a spy in place of
the store's ``recall_learnings``, so every learning recall session_start makes
on any path (the primary recall, the deleted baseline, the deleted phase
auto-recall, the ceremony-status nudge) is counted.

PRD-CORE-280 slice e1: driven against a ``daemon_checkout`` (a real,
daemon-routed store) rather than a bare unpinned ``tmp_path`` -- an unpinned
project falls through to the doomed in-process default store implementation,
which every other session_start sub-step (assertion health, sanitize/maintain,
embed health, ...) also still reaches directly, so only a pinned, daemon-backed
checkout keeps this real end-to-end call off ``memory.db``. Two unrelated
session-pressure side effects of the recall step (learning-count and
last-access tracking) still reach that same in-process accessor directly, so
they are stubbed out below rather than left to fail the step under the E1
oracle.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from tests._ceremony_helpers import make_ceremony_server
from tests._memory_fixtures import DaemonCheckout
from trw_mcp.tools._recall_presenter import CLAIM_MAX_CHARS, SESSION_BYTE_BUDGET, SESSION_MAX_STUBS

#: The keys that make up the session_start learning block (NFR02).
_BLOCK_KEYS = ("learnings", "learnings_omitted", "query", "query_advisory", "store_count")
_STUB_KEYS = {"id", "claim", "anchor"}


def _rows(count: int, *, summary_chars: int = 60, anchor_chars: int = 20) -> list[dict[str, object]]:
    return [
        {
            "id": f"L-{index:04d}",
            "summary": f"learning {index} " + "s" * summary_chars,
            "detail": "d" * 400,
            "impact": 0.5,
            "status": "active",
            "anchors": [{"file": "src/" + "a" * anchor_chars + ".py", "symbol_name": "handler"}],
            # Internal scoring state trw_recall strips from every full row.
            "combined_score": 0.4,
            "outcome_history": [{"outcome": "pass"}],
        }
        for index in range(count)
    ]


def _session_start(
    monkeypatch: pytest.MonkeyPatch,
    daemon_checkout: DaemonCheckout,
    rows: list[dict[str, object]],
    **kwargs: Any,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    trw_dir = daemon_checkout.trw_dir
    tools = make_ceremony_server(monkeypatch, trw_dir.parent)
    (trw_dir / "context").mkdir(parents=True, exist_ok=True)
    calls: list[dict[str, Any]] = []

    def _spy(_trw_dir: Path, **recall_kwargs: Any) -> list[dict[str, object]]:
        calls.append(recall_kwargs)
        return [dict(row) for row in rows]

    with (
        patch("trw_mcp.tools.ceremony.resolve_trw_dir", return_value=trw_dir),
        patch("trw_mcp.tools.ceremony.find_active_run", return_value=None),
        patch("trw_mcp.state.memory_adapter.recall_learnings", side_effect=_spy),
    ):
        result = tools["trw_session_start"].fn(**kwargs)
    return result, calls


def _block_bytes(result: dict[str, Any]) -> int:
    block = {key: result[key] for key in _BLOCK_KEYS if key in result}
    return len(json.dumps(block, default=str).encode("utf-8"))


@pytest.mark.parametrize("query", ["retry backoff handler", "", "*"])
def test_session_start_runs_exactly_one_recall(
    monkeypatch: pytest.MonkeyPatch, daemon_checkout: DaemonCheckout, query: str
) -> None:
    result, calls = _session_start(monkeypatch, daemon_checkout, _rows(6), query=query)

    assert result["success"] is True
    assert len(calls) == 1, f"session_start ran {len(calls)} recalls: {calls}"
    # The deleted baseline was a query-independent '*' recall at min_impact 0.7.
    assert calls[0]["query"] == (query if query.strip() not in ("", "*") else "*")
    assert calls[0]["min_impact"] < 0.7


@pytest.mark.parametrize("count", [1, 3, 12])
def test_default_learnings_are_at_most_three_stubs_in_rank_order(
    monkeypatch: pytest.MonkeyPatch, daemon_checkout: DaemonCheckout, count: int
) -> None:
    rows = _rows(count)
    result, _calls = _session_start(monkeypatch, daemon_checkout, rows, query="handler")

    stubs = result["learnings"]
    assert 1 <= len(stubs) <= SESSION_MAX_STUBS
    assert all(set(stub) <= _STUB_KEYS and {"id", "claim"} <= set(stub) for stub in stubs)
    # Presentation only: the stubs are the head of the ranked list, in order.
    assert [stub["id"] for stub in stubs] == [row["id"] for row in rows[: len(stubs)]]
    assert result["learnings_count"] == len(stubs)
    assert result.get("learnings_omitted", 0) == count - len(stubs)
    assert stubs[0]["anchor"] == "src/" + "a" * 20 + ".py:handler"


@pytest.mark.parametrize(
    ("summary_chars", "anchor_chars", "query"),
    [(60, 20, "handler"), (5_000, 20, "handler"), (60, 3_000, "handler"), (5_000, 3_000, "q " * 900)],
)
def test_learning_block_fits_the_byte_budget(
    monkeypatch: pytest.MonkeyPatch,
    daemon_checkout: DaemonCheckout,
    summary_chars: int,
    anchor_chars: int,
    query: str,
) -> None:
    result, _calls = _session_start(
        monkeypatch,
        daemon_checkout,
        _rows(20, summary_chars=summary_chars, anchor_chars=anchor_chars),
        query=query,
    )

    assert _block_bytes(result) <= SESSION_BYTE_BUDGET
    assert result["learnings"], "the first stub is kept by cutting strings, never dropped for length"
    assert all(len(str(stub["claim"])) <= CLAIM_MAX_CHARS for stub in result["learnings"])


def test_verbose_returns_full_rows_with_internal_fields_stripped(
    monkeypatch: pytest.MonkeyPatch, daemon_checkout: DaemonCheckout
) -> None:
    rows = _rows(6, summary_chars=500)
    result, calls = _session_start(monkeypatch, daemon_checkout, rows, query="handler", verbose=True)

    assert len(calls) == 1
    learnings = result["learnings"]
    assert [row["id"] for row in learnings] == [row["id"] for row in rows]
    assert learnings[0]["summary"] == rows[0]["summary"]
    assert learnings[0]["detail"] == rows[0]["detail"]
    assert learnings[0]["anchors"] == rows[0]["anchors"]
    for row in learnings:
        assert "combined_score" not in row and "outcome_history" not in row
        assert not any(key.startswith("_") for key in row)
    assert "learnings_omitted" not in result


def test_empty_store_gives_an_empty_block(monkeypatch: pytest.MonkeyPatch, daemon_checkout: DaemonCheckout) -> None:
    result, calls = _session_start(monkeypatch, daemon_checkout, [])

    assert result["success"] is True
    assert result["learnings"] == []
    assert result["learnings_count"] == 0
    assert "learnings_omitted" not in result
    assert len(calls) == 1


def test_focused_query_is_echoed_and_zero_match_is_explained(
    monkeypatch: pytest.MonkeyPatch, daemon_checkout: DaemonCheckout
) -> None:
    hit, _ = _session_start(monkeypatch, daemon_checkout, _rows(2), query="retry backoff")
    assert hit["query"] == "retry backoff"
    assert "query_advisory" not in hit

    miss, _ = _session_start(monkeypatch, daemon_checkout, [], query="retry backoff")
    assert miss["learnings"] == []
    # Nothing is returned in place of a match any more, so the advisory must not claim a baseline.
    assert "matched 0" in miss["query_advisory"]
    assert "baseline" not in miss["query_advisory"]

    wildcard, _ = _session_start(monkeypatch, daemon_checkout, _rows(2))
    assert "query" not in wildcard
    assert "query_advisory" not in wildcard


@pytest.mark.parametrize("messenger", ["standard", "contextual"])
def test_the_ceremony_nudge_reuses_session_starts_recall(
    monkeypatch: pytest.MonkeyPatch, daemon_checkout: DaemonCheckout, messenger: str
) -> None:
    """The nudge riding on the session_start response never recalls on its own.

    Both nudge paths that draw a learning are forced open: the learnings pool
    (picked whenever its weight allows) and the contextual watch-out (a modified
    file anchors it). The response's learning block is the session's one
    recall; a second learning in the nudge would repeat it at extra cost.
    """
    from types import SimpleNamespace

    trw_dir = daemon_checkout.trw_dir
    tools = make_ceremony_server(monkeypatch, trw_dir.parent)
    (trw_dir / "context").mkdir(parents=True, exist_ok=True)
    (trw_dir / "config.yaml").write_text(
        f"project_namespace: {daemon_checkout.namespace}\n"
        f"nudge_enabled: true\nceremony_mode: full\nnudge_messenger: {messenger}\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("TRW_NUDGE_MESSENGER", messenger)
    calls: list[dict[str, Any]] = []

    def _spy(_trw_dir: Path, **recall_kwargs: Any) -> list[dict[str, object]]:
        calls.append(recall_kwargs)
        return [dict(row) for row in _rows(6)]

    touched = SimpleNamespace(modified_files=["src/handler.py"], inferred_domains=set())
    with (
        patch("trw_mcp.tools.ceremony.resolve_trw_dir", return_value=trw_dir),
        patch("trw_mcp.tools.ceremony.find_active_run", return_value=None),
        patch("trw_mcp.state.memory_adapter.recall_learnings", side_effect=_spy),
        patch(
            "trw_mcp.state.ceremony_nudge._select_nudge_pool",
            side_effect=lambda _state, weights, *_a, **_k: "learnings" if weights.learnings else "workflow",
        ),
        patch("trw_mcp.state.recall_context.build_recall_context", return_value=touched),
    ):
        result = tools["trw_session_start"].fn(query="handler")

    assert result["success"] is True
    assert "nudge_content" in result, "the nudge must still render; only its own recall goes"
    assert len(calls) == 1, f"session_start ran {len(calls)} recalls: {calls}"
    assert "Watch-out" not in str(result["nudge_content"])
