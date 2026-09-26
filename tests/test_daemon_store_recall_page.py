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
