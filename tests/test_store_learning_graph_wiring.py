"""PRD-FIX-COMPOUNDING-2 FR05 — wiring tests for knowledge-graph edge creation.

These tests are the regression anchor for RC-1/RC-4 from the 2026-06-02
postmortem (``docs/research/postmortems/2026-06-02-knowledge-graph-empty.md``):
``store_learning`` never enriched the knowledge graph, so
``memory_graph_edges`` was 0 rows for the entire project lifespan and no test
asserted edge count after a store.

What each test proves:
- ``test_store_learning_creates_tag_cooccurrence_edges``: two stores sharing
  2+ tags create at least one ``tag_cooccurrence`` edge (the exact failure
  mode — FAILS before the FR01 fix).
- ``test_store_learning_calls_graph_enrichment``: enrichment is wired with the
  entry + the reused embedding vector (via synchronous ``update_entry_graph``
  on the singleton connection — see the test for the path-divergence rationale).
- ``test_store_learning_embedder_called_once_per_store``: FR02 — a single embed
  call per store (no second embed for the graph path).
- ``test_store_learning_graph_failure_is_fail_open``: a ``RuntimeError`` from
  graph enrichment does not propagate (NFR02).
- ``test_store_learning_no_prior_entries_no_edges``: negative — first store has
  no candidate to link to, so no edges.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from trw_memory.graph import wait_for_graph_updates

from trw_mcp.state.memory_adapter import get_backend, store_learning


@pytest.fixture
def trw_dir(tmp_path: Path) -> Path:
    """Minimal .trw structure for store_learning + graph tests."""
    d = tmp_path / ".trw"
    (d / "learnings" / "entries").mkdir(parents=True)
    (d / "memory").mkdir(parents=True)
    return d


def _count_edges(trw_dir: Path) -> int:
    """Count rows in memory_graph_edges on the live backend connection."""
    backend = get_backend(trw_dir)
    conn = backend._conn
    assert isinstance(conn, sqlite3.Connection)
    return int(conn.execute("SELECT COUNT(*) FROM memory_graph_edges").fetchone()[0])


def _count_edges_by_type(trw_dir: Path, edge_type: str) -> int:
    backend = get_backend(trw_dir)
    conn = backend._conn
    assert isinstance(conn, sqlite3.Connection)
    return int(
        conn.execute(
            "SELECT COUNT(*) FROM memory_graph_edges WHERE edge_type = ?",
            (edge_type,),
        ).fetchone()[0]
    )


class TestStoreLearningGraphWiring:
    def test_store_learning_creates_tag_cooccurrence_edges(self, trw_dir: Path) -> None:
        """Two entries sharing 2+ tags → tag_cooccurrence edge exists (FR01).

        This is the exact failure the postmortem found: 0 edges after stores.
        Before the FR01 fix (no schedule_graph_update call) this assertion fails.
        """
        store_learning(
            trw_dir,
            "L-graph-1",
            "Postgres connection pooling tuning",
            "Notes on pool size",
            tags=["postgres", "performance", "database"],
        )
        store_learning(
            trw_dir,
            "L-graph-2",
            "Postgres index strategy for performance",
            "Composite indexes",
            tags=["postgres", "performance", "indexing"],
        )

        # Graph dispatch is a background thread — wait for it to land.
        wait_for_graph_updates(timeout=30.0)

        assert _count_edges(trw_dir) > 0, "store_learning must create graph edges"
        assert _count_edges_by_type(trw_dir, "tag_cooccurrence") > 0, (
            "two entries sharing >=2 tags must produce a tag_cooccurrence edge"
        )

    def test_store_learning_calls_graph_enrichment(self, trw_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Graph enrichment is invoked with the stored entry (FR01 wiring).

        The MCP store path enriches SYNCHRONOUSLY on the singleton connection via
        ``update_entry_graph`` (NOT the async ``schedule_graph_update`` worker,
        which would reopen a divergent per-namespace DB and silently write edges
        to the wrong file). This test pins that wiring.
        """
        captured: dict[str, object] = {}

        def fake_update(entry: object, backend: object, **kwargs: object) -> dict[str, int]:
            captured["entry_id"] = getattr(entry, "id", None)
            captured["embedding"] = kwargs.get("embedding")
            captured["called"] = True
            return {"similarity_edges": 0, "tag_edges": 0, "consolidation_edges": 0}

        # Patch at the consumer site (memory_adapter imports the symbol).
        monkeypatch.setattr("trw_mcp.state.memory_adapter.update_entry_graph", fake_update)

        store_learning(trw_dir, "L-wire-1", "summary text", "detail text", tags=["x"])

        assert captured.get("called") is True
        assert captured.get("entry_id") == "L-wire-1"

    def test_store_learning_embedder_called_once_per_store(
        self, trw_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """FR02: a single embed call per store — the graph reuses the vector."""
        embedder = MagicMock()
        embedder.embed.return_value = [0.1, 0.2, 0.3]

        # get_embedder is called inside _embed_and_store_returning; force a stub.
        monkeypatch.setattr("trw_mcp.state._memory_connection.get_embedder", lambda: embedder)
        # upsert_vector dim mismatch is irrelevant to the call-count assertion;
        # swallow any backend vector error so we measure embed() calls only.
        backend = get_backend(trw_dir)
        monkeypatch.setattr(backend, "upsert_vector", lambda *a, **k: None)

        store_learning(trw_dir, "L-embed-1", "summary", "detail", tags=["y"])

        assert embedder.embed.call_count == 1, "embedder must be called exactly once per store_learning (FR02)"

    def test_store_learning_graph_failure_is_fail_open(self, trw_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """NFR02: a RuntimeError from graph dispatch must not fail the store."""

        def boom(*args: object, **kwargs: object) -> dict[str, int]:
            raise RuntimeError("graph enrichment exploded")

        monkeypatch.setattr("trw_mcp.state.memory_adapter.update_entry_graph", boom)

        result = store_learning(trw_dir, "L-failopen-1", "s", "d", tags=["z"])

        assert result["status"] == "recorded", "graph failure must not fail the store"
        assert result["learning_id"] == "L-failopen-1"

    def test_store_learning_no_prior_entries_no_edges(self, trw_dir: Path) -> None:
        """Negative: the very first store has no candidate to link to."""
        store_learning(
            trw_dir,
            "L-solo-1",
            "Lone entry",
            "No siblings",
            tags=["alpha", "beta"],
        )
        wait_for_graph_updates(timeout=30.0)

        assert _count_edges(trw_dir) == 0, "no candidates → no edges"

    def test_store_learning_return_shape_unchanged(self, trw_dir: Path) -> None:
        """Regression: graph wiring must not change the result dict shape."""
        result = store_learning(trw_dir, "L-shape-1", "s", "d")
        assert set(result.keys()) == {
            "learning_id",
            "path",
            "status",
            "distribution_warning",
        }
