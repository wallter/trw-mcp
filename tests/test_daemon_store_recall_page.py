"""A daemon-backed recall page never asks past the daemon's recall ceiling (C12 rc7).

trw_recall fetches ``max_results`` x5 and pages up to DEFAULT_LIST_LIMIT; memory_recall refuses a
``limit`` above ``MAX_RECALL_LIMIT``, and a refusal fails the whole recall.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from trw_memory.retrieval.recall_policy import MAX_RECALL_LIMIT

from trw_mcp.state._daemon_store import DaemonMemoryStore
from trw_mcp.state._store_selection import RecallSpec


class _Recall:
    def __init__(self) -> None:
        self.limits: list[int] = []

    async def recall(self, _query: str, _namespace: str, *, limit: int, **_kwargs: Any) -> dict[str, Any]:
        self.limits.append(limit)
        return {"memories": []}


def test_a_page_past_the_ceiling_asks_the_daemon_for_the_ceiling() -> None:
    client = _Recall()
    store = DaemonMemoryStore(client, "project:t")  # type: ignore[arg-type]

    store._page(RecallSpec(admission=MagicMock(mem_status=None), query="q"), "project:t", 5 * 2001)

    assert client.limits == [MAX_RECALL_LIMIT]


# --- PRD-CORE-332 FR05: an anchor_file recall is one memory_anchored page of the project namespace ---

_FILE = "httpx/_client.py"


def _recall_anchor(query: str = _FILE) -> list[str]:
    from trw_mcp.state.learning_injection import recall_learnings

    return [str(row["id"]) for row in recall_learnings(query, max_results=10, anchor_file=_FILE)]


def test_page_routes_anchor_file_to_memory_anchored(monkeypatch: Any, tmp_path: Any) -> None:
    from tests._anchor_daemon_fake import NAMESPACE, AnchoredDaemon, lesson, use_daemon

    daemon = AnchoredDaemon([lesson("L-a", "close the transport", anchors=(_FILE,))])
    use_daemon(monkeypatch, tmp_path, daemon)

    assert _recall_anchor() == ["L-a"]
    assert daemon.calls == [("memory_anchored", NAMESPACE)]
    assert daemon.files == [_FILE]


def test_anchor_recall_is_project_only(monkeypatch: Any, tmp_path: Any) -> None:
    from tests._anchor_daemon_fake import NAMESPACE, AnchoredDaemon, lesson, use_daemon
    from trw_mcp.state._tier_routing import USER_NAMESPACE

    rows = [
        lesson("L-proj", "project finding", anchors=(_FILE,)),
        lesson("L-user", "another repo's finding", anchors=(_FILE,), namespace=USER_NAMESPACE),
    ]
    daemon = AnchoredDaemon(rows)
    use_daemon(monkeypatch, tmp_path, daemon)

    assert _recall_anchor() == ["L-proj"]
    assert {namespace for _, namespace in daemon.calls} == {NAMESPACE}


def test_anchor_recall_keeps_the_daemon_order(monkeypatch: Any, tmp_path: Any) -> None:
    """A wildcard query would re-rank by utility; an anchor recall never does."""
    from tests._anchor_daemon_fake import AnchoredDaemon, lesson, use_daemon

    class _LowFirst(AnchoredDaemon):
        async def anchored(self, namespace: str, file: str, limit: int, status: str | None = None) -> Any:
            page = await super().anchored(namespace, file, limit, status)
            page["memories"].reverse()
            return page

    rows = [
        lesson("L-high", "high impact", anchors=(_FILE,), importance=0.95),
        lesson("L-low", "low impact", anchors=(_FILE,), importance=0.05),
    ]
    use_daemon(monkeypatch, tmp_path, _LowFirst(rows))

    assert _recall_anchor("*") == ["L-low", "L-high"]


def test_anchor_recall_admits_exactly_as_a_query_recall(monkeypatch: Any, tmp_path: Any) -> None:
    """Superseded, expired-window and canary rows drop out of both paths alike."""
    from datetime import datetime, timezone

    from tests._anchor_daemon_fake import AnchoredDaemon, lesson, use_daemon
    from trw_mcp.state.learning_injection import recall_learnings

    past = datetime(2020, 1, 1, tzinfo=timezone.utc)
    rows = [
        lesson("L-open", "needle open", anchors=(_FILE,)),
        lesson("L-resolved", "needle resolved", anchors=(_FILE,), status="resolved"),
        lesson(
            "L-gone", "needle superseded", anchors=(_FILE,), valid_from=past, invalid_from=past, invalidated_by="L-open"
        ),
        lesson("L-canary", "needle canary", anchors=(_FILE,), metadata={"system_canary": "true"}),
    ]
    use_daemon(monkeypatch, tmp_path, AnchoredDaemon(rows))

    by_query = sorted(str(row["id"]) for row in recall_learnings("needle", max_results=10))
    assert "L-gone" not in by_query and "L-canary" not in by_query
    assert sorted(_recall_anchor()) == by_query


# --- the rerank opt-out ---


class _Kwargs:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    async def recall(self, _query: str, _namespace: str, **kwargs: Any) -> dict[str, Any]:
        self.sent.append(kwargs)
        return {"memories": []}


def test_rerank_false_reaches_the_daemon_and_the_default_sends_nothing() -> None:
    """Only the opt-out is sent, so a default recall still works against a daemon that predates it."""
    client = _Kwargs()
    store = DaemonMemoryStore(client, "project:t")  # type: ignore[arg-type]
    admission = MagicMock(mem_status=None)

    store._page(RecallSpec(admission=admission, query="q"), "project:t", 10)
    store._page(RecallSpec(admission=admission, query="q", rerank=False), "project:t", 10)

    assert "rerank" not in client.sent[0]
    assert client.sent[1]["rerank"] is False


def test_the_collector_entry_point_threads_rerank_false_to_every_page(monkeypatch: Any, tmp_path: Any) -> None:
    from tests._anchor_daemon_fake import TextOnlyDaemon, lesson, use_daemon
    from trw_mcp.state.learning_injection import recall_learnings

    seen: list[object] = []

    class _Recording(TextOnlyDaemon):
        async def recall(self, query: str, namespace: str, **kwargs: Any) -> Any:
            seen.append(kwargs.pop("rerank", True))
            return await super().recall(query, namespace, **kwargs)

    use_daemon(monkeypatch, tmp_path, _Recording([lesson("L-a", "close the transport")]))

    recall_learnings("transport", max_results=10, rerank=False)

    assert seen and all(value is False for value in seen)


# --- the one-shot compat retry for a same-major daemon older than the rerank opt-out ---

#: What a daemon exported from trunk 2070e2599 (``memory_recall`` without ``rerank``) answered
#: ``memory_recall(..., rerank=False)`` with, captured verbatim through a FastMCP client.
_OLD_DAEMON_REFUSAL = (
    "1 validation error for call[memory_recall]\nrerank\n  Unexpected keyword argument "
    "[type=unexpected_keyword_argument, input_value=False, input_type=bool]\n"
    "    For further information visit https://errors.pydantic.dev/2.13/v/unexpected_keyword_argument"
)


class _OldDaemon:
    """Refuses ``rerank`` as the pre-opt-out daemon does; answers every other call with one row."""

    def __init__(self, refusal: str = _OLD_DAEMON_REFUSAL) -> None:
        self.sent: list[dict[str, Any]] = []
        self.refusal = refusal

    async def recall(self, _query: str, namespace: str, **kwargs: Any) -> dict[str, Any]:
        from fastmcp.exceptions import ToolError
        from trw_memory.models.memory import MemoryEntry

        self.sent.append(kwargs)
        if "rerank" in kwargs:
            raise ToolError(self.refusal)
        return {
            "memories": [
                MemoryEntry(id="L-old", content="from the old daemon", namespace=namespace).model_dump(mode="json")
            ]
        }


def _page(client: object, *, rerank: bool) -> list[str]:
    store = DaemonMemoryStore(client, "project:t")  # type: ignore[arg-type]
    spec = RecallSpec(admission=MagicMock(mem_status=None), query="q", rerank=rerank)
    return [row.id for row in store._page(spec, "project:t", 10)]


def test_a_refused_rerank_retries_the_same_page_once_without_it() -> None:
    client = _OldDaemon()

    assert _page(client, rerank=False) == ["L-old"]

    assert len(client.sent) == 2
    assert client.sent[0]["rerank"] is False
    assert client.sent[1] == {key: value for key, value in client.sent[0].items() if key != "rerank"}


def test_an_accepted_rerank_is_a_single_call() -> None:
    client = _Kwargs()

    assert _page(client, rerank=False) == []

    assert len(client.sent) == 1 and client.sent[0]["rerank"] is False


def test_any_other_refusal_is_not_retried() -> None:
    import pytest
    from fastmcp.exceptions import ToolError

    other = "1 validation error for call[memory_recall]\nlimit\n  Input should be less than or equal to 2000"
    client = _OldDaemon(refusal=other)

    with pytest.raises(ToolError):
        _page(client, rerank=False)
    assert len(client.sent) == 1


def test_fastmcp_unknown_argument_text_is_what_the_retry_matches() -> None:
    """Pins fastmcp's wording against a real server whose memory_recall predates ``rerank``."""
    import asyncio

    import pytest
    from fastmcp import Client, FastMCP
    from fastmcp.exceptions import ToolError

    from trw_mcp.state._daemon_recall_page import RERANK_REFUSED

    server = FastMCP("pre-rerank")

    @server.tool()
    def memory_recall(query: str, namespace: str, limit: int = 10) -> dict[str, Any]:
        return {"memories": []}

    async def _call() -> None:
        async with Client(server) as client:
            await client.call_tool("memory_recall", {"query": "q", "namespace": "n", "rerank": False})

    with pytest.raises(ToolError) as raised:
        asyncio.run(_call())
    assert RERANK_REFUSED.search(str(raised.value))
    assert RERANK_REFUSED.search(_OLD_DAEMON_REFUSAL)
