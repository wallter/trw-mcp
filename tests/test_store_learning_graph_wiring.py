"""PRD-FIX-COMPOUNDING-2 FR05 — a stored learning reaches the knowledge graph.

The regression anchor for RC-1/RC-4 of the 2026-06-02 knowledge-graph-empty
postmortem: ``store_learning`` never enriched the graph, and no test asserted a
relation after a store. The store owns enrichment now -- the daemon's
``memory_store`` indexes tags and schedules the graph update with the vector it
stored -- so these tests store through a real daemon checkout and read the
relation back through the store:

- two learnings sharing 2+ tags derive each other as ``tag_cooccurrence``
  neighbours (PRD-CORE-245 FR07: derived from the ``memory_tags`` index, never
  a materialised edge);
- the first learning in a namespace has no neighbour.

The enrichment internals live with the store: one embed per store and the graph
update receiving that vector (``trw-memory/tests/test_store_delegated_surface.py``),
and a failing graph dispatch never failing the store
(``trw-memory/tests/test_tools_store_gaps.py``).
"""

from __future__ import annotations

from pathlib import Path

from tests._memory_fixtures import DaemonCheckout
from tests._memory_store_fake import FakeMemoryStore
from trw_mcp.state._store_selection import selected_store
from trw_mcp.state.memory_adapter import store_learning


def _tag_neighbours(checkout: DaemonCheckout, learning_id: str) -> list[tuple[str, str]]:
    store, namespace = selected_store(checkout.trw_dir)
    rows, _ = store.graph_related(namespace, learning_id, 1, ["tag_cooccurrence"], 10)
    return [(str(row["id"]), str(row["edge_type"])) for row in rows]


def test_two_learnings_sharing_tags_derive_each_other(daemon_checkout: DaemonCheckout) -> None:
    trw_dir = daemon_checkout.trw_dir
    store_learning(
        trw_dir,
        "L-graph-1",
        "Postgres connection pooling tuning",
        "Pool size",
        tags=["postgres", "performance", "database"],
    )
    store_learning(
        trw_dir,
        "L-graph-2",
        "Postgres index strategy",
        "Composite indexes",
        tags=["postgres", "performance", "indexing"],
    )

    assert _tag_neighbours(daemon_checkout, "L-graph-1") == [("L-graph-2", "tag_cooccurrence")]
    assert _tag_neighbours(daemon_checkout, "L-graph-2") == [("L-graph-1", "tag_cooccurrence")]


def test_the_first_learning_has_no_neighbour(daemon_checkout: DaemonCheckout) -> None:
    store_learning(daemon_checkout.trw_dir, "L-solo-1", "Lone entry", "No siblings", tags=["alpha", "beta"])

    assert _tag_neighbours(daemon_checkout, "L-solo-1") == []


def test_store_learning_return_shape_unchanged(tmp_path: Path, fake_memory_store: FakeMemoryStore) -> None:
    """Regression: graph wiring must not change the result dict shape."""
    result = store_learning(tmp_path / ".trw", "L-shape-1", "s", "d")
    assert set(result.keys()) == {"learning_id", "path", "status", "distribution_warning"}
