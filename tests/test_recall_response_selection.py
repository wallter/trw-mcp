"""Response formatting preserves the evidence used by actual MCP selection.

Real execute_recall -> memory_adapter -> isolated SQLite. Remote/context and
write hooks are isolated; no adapter acquisition or utility scorer is replaced.

PRD-CORE-294 FR01 deleted the ``compact``/``ultra_compact``/``token_budget``
projection knobs this file used to exercise (execute_recall no longer accepts
them, and ``_search_patterns``/``_collect_context`` injection points are gone
along with the ``trw_mcp.state.recall_search`` module). The projection-field
tests that pinned those knobs' behavior are deleted with them; the direct-id
lookup test below covers behavior that still exists.
"""

import asyncio

import pytest
from trw_memory.daemon.client import DaemonClient

from tests._memory_fixtures import DaemonCheckout
from trw_mcp.models.config import get_config
from trw_mcp.tools import _recall_impl


@pytest.fixture
def store(daemon_checkout: DaemonCheckout, monkeypatch):
    monkeypatch.setenv("TRW_SURFACE_ROLE", "reviewer")
    monkeypatch.setattr(_recall_impl, "_augment_with_remote", lambda query, rows: (rows, None))
    monkeypatch.setattr(_recall_impl, "build_recall_context", lambda *args, **kwargs: None)
    return daemon_checkout


def seed(client: DaemonClient, namespace: str, competitors: int) -> None:
    async def _seed() -> None:
        for index in range(competitors):
            await client.store(
                f"Sourceprobe episodic observation {index}",
                namespace,
                importance=0.95,
                entry_id=f"L-e{index:04d}",
                metadata={"source_kind": "episodic"},
            )
        await client.store(
            "Sourceprobe durable instruction",
            namespace,
            importance=0.2,
            entry_id="L-d1234",
            metadata={"source_kind": "semantic_memory"},
        )

    asyncio.run(_seed())


def recall(trw_dir, query="Sourceprobe", limit=1, **kwargs):
    return _recall_impl.execute_recall(
        query,
        trw_dir,
        get_config(),
        max_results=limit,
        include_tiers=["project"],
        **kwargs,
    )


def test_direct_id_lookup_remains_available_beyond_prefetch(store: DaemonCheckout):
    seed(store.client, store.namespace, _recall_impl.PREFETCH_MULTIPLIER + 3)
    rows = recall(store.trw_dir, query="L-d1234")["learnings"]
    assert [row["id"] for row in rows] == ["L-d1234"]
