"""PRD-CORE-300-FR11 (S9): ``trw_recall(graph_id=...)`` is the graph mode.

The standalone graph-related MCP tool was deleted; its behaviour
survives as ``tools.knowledge.graph_related`` and this graph mode returns
exactly what it returned. See ``trw-memory/tests/test_tools_graph_related.py``
for the underlying traversal contract (namespace scope, breadth bound).
"""

from __future__ import annotations

from typing import Any

from trw_memory.models.memory import MemoryEntry

from tests._memory_fixtures import FAKE_NAMESPACE
from tests._memory_store_fake import FakeMemoryStore
from tests.conftest import extract_tool_fn, make_test_server
from trw_mcp.tools.knowledge import graph_related


def _recall() -> Any:
    return extract_tool_fn(make_test_server("learning"), "trw_recall")


def test_recall_graph_mode_returns_exactly_what_graph_related_returns(
    fake_memory_store: FakeMemoryStore,
) -> None:
    fake_memory_store.put("root", FAKE_NAMESPACE, {"entry_id": "L-root"})
    fake_memory_store.rows[(FAKE_NAMESPACE, "L-active")] = MemoryEntry(
        id="L-active", content="active", namespace=FAKE_NAMESPACE, tags=["graph"], importance=0.7
    )
    fake_memory_store.graph_edges[(FAKE_NAMESPACE, "L-root")] = [("L-active", "related_to", 0.8)]

    direct = graph_related("L-root", depth=2, limit=5)
    via_recall = _recall()(graph_id="L-root", options={"graph_depth": 2, "graph_limit": 5})

    assert via_recall == direct
    assert direct["found"] is True
    assert direct["related"][0]["id"] == "L-active"


def test_recall_graph_mode_unknown_id_reports_not_found(fake_memory_store: FakeMemoryStore) -> None:
    result = _recall()(graph_id="L-missing")

    assert result == {
        "learning_id": "L-missing",
        "related": [],
        "count": 0,
        "found": False,
        "truncated": False,
    }


def test_recall_graph_mode_edge_types_filter_forwards_to_graph_related(
    fake_memory_store: FakeMemoryStore,
) -> None:
    fake_memory_store.put("root", FAKE_NAMESPACE, {"entry_id": "L-root"})
    fake_memory_store.rows[(FAKE_NAMESPACE, "L-active")] = MemoryEntry(
        id="L-active", content="active", namespace=FAKE_NAMESPACE, tags=["graph"], importance=0.7
    )
    fake_memory_store.graph_edges[(FAKE_NAMESPACE, "L-root")] = [("L-active", "related_to", 0.8)]

    result = _recall()(graph_id="L-root", options={"graph_edge_types": ["related_to"]})

    assert result["count"] == 1
    assert ("graph_related", (FAKE_NAMESPACE, "L-root", 1, 50)) in fake_memory_store.calls


def test_recall_graph_mode_rejects_unsupported_edge_type(fake_memory_store: FakeMemoryStore) -> None:
    import pytest
    from fastmcp.exceptions import ToolError

    with pytest.raises((ValueError, ToolError)):
        _recall()(graph_id="L-root", options={"graph_edge_types": ["not_a_real_edge_type"]})
