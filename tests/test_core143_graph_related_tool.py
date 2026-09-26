"""PRD-CORE-143 MCP knowledge-graph traversal tests.

The traversal itself (namespace scope, active-only rows, the breadth bound) runs in
the store and is pinned by ``trw-memory/tests/test_tools_graph_related.py``. Here the
:func:`graph_related` callable must validate its bounds, resolve the root through
the checkout's store, and shape the store's rows. PRD-CORE-300-FR11 (S9) deleted the
standalone graph-related MCP tool; its registration is now pinned by
``tests/test_recall_graph_mode.py`` (``trw_recall(graph_id=...)``).
"""

from __future__ import annotations

import pytest
from trw_memory.models.memory import MemoryEntry

from tests._memory_fixtures import FAKE_NAMESPACE
from tests._memory_store_fake import FakeMemoryStore
from trw_mcp.tools.knowledge import graph_related


def test_graph_related_reads_the_roots_own_namespace_and_shapes_its_rows(fake_memory_store: FakeMemoryStore) -> None:
    fake_memory_store.put("root", FAKE_NAMESPACE, {"entry_id": "L-root"})
    fake_memory_store.rows[(FAKE_NAMESPACE, "L-active")] = MemoryEntry(
        id="L-active", content="active", namespace=FAKE_NAMESPACE, tags=["graph"], importance=0.7
    )
    fake_memory_store.graph_edges[(FAKE_NAMESPACE, "L-root")] = [("L-active", "related_to", 0.8)]

    result = graph_related("L-root", depth=2, limit=5)

    assert ("graph_related", (FAKE_NAMESPACE, "L-root", 2, 5)) in fake_memory_store.calls
    assert result == {
        "learning_id": "L-root",
        "namespace": FAKE_NAMESPACE,
        "related": [
            {
                "id": "L-active",
                "summary": "active",
                "importance": 0.7,
                "tags": ["graph"],
                "edge_type": "related_to",
                "weight": 0.8,
                "depth": 1,
            }
        ],
        "count": 1,
        "found": True,
        "truncated": False,
    }


def test_graph_related_reports_the_stores_truncation(fake_memory_store: FakeMemoryStore) -> None:
    fake_memory_store.put("root", FAKE_NAMESPACE, {"entry_id": "L-root"})
    for index in range(4):
        fake_memory_store.put(f"L-{index}", FAKE_NAMESPACE, {"entry_id": f"L-{index}"})
    fake_memory_store.graph_edges[(FAKE_NAMESPACE, "L-root")] = [(f"L-{i}", "related_to", 0.8) for i in range(4)]

    result = graph_related("L-root", limit=3)

    assert result["count"] == 3
    assert result["truncated"] is True


def test_graph_related_unknown_id_is_typed_empty(fake_memory_store: FakeMemoryStore) -> None:
    assert graph_related("L-missing") == {
        "learning_id": "L-missing",
        "related": [],
        "count": 0,
        "found": False,
        "truncated": False,
    }


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        ({"depth": 0}, "depth"),
        ({"depth": 4}, "depth"),
        ({"edge_types": ["not-real"]}, "unsupported edge_types"),
        ({"limit": 0}, "limit must be between"),
        ({"limit": 101}, "limit must be between"),
    ],
)
def test_graph_related_refuses_an_unbounded_traversal_before_the_store(
    fake_memory_store: FakeMemoryStore, arguments: dict[str, object], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        graph_related("L-root", **arguments)  # type: ignore[arg-type]

    assert fake_memory_store.calls == []
