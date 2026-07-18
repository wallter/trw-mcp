"""PRD-CORE-143 MCP knowledge-graph traversal tests."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch

import pytest
from fastmcp import FastMCP
from trw_memory.graph import _upsert_edge
from trw_memory.models.memory import MemoryEntry, MemoryStatus
from trw_memory.storage.sqlite_backend import SQLiteBackend

from tests.conftest import get_tools_sync
from trw_mcp.tools.knowledge import graph_related, register_knowledge_tools


@pytest.fixture
def backend(tmp_path: Path) -> Iterator[SQLiteBackend]:
    store = SQLiteBackend(tmp_path / "graph.db")
    try:
        yield store
    finally:
        store.close()


def _edge(backend: SQLiteBackend, source: str, target: str, edge_type: str = "related_to") -> None:
    _upsert_edge(backend._conn, source, target, edge_type, 0.8, "2026-07-12T00:00:00+00:00")
    backend._conn.commit()


def test_graph_related_is_registered_on_mcp_surface() -> None:
    server = FastMCP("core143")
    register_knowledge_tools(server)

    assert "trw_graph_related" in get_tools_sync(server)


def test_graph_related_returns_active_namespace_scoped_neighbours(backend: SQLiteBackend) -> None:
    backend.store(MemoryEntry(id="L-root", content="root", namespace="project:a"))
    backend.store(MemoryEntry(id="L-active", content="active", namespace="project:a", tags=["graph"]))
    backend.store(MemoryEntry(id="L-obsolete", content="old", namespace="project:a", status=MemoryStatus.OBSOLETE))
    backend.store(MemoryEntry(id="L-foreign", content="foreign", namespace="project:b"))
    _edge(backend, "L-root", "L-active")
    _edge(backend, "L-root", "L-obsolete")
    _edge(backend, "L-root", "L-foreign")

    with patch("trw_mcp.tools.knowledge.get_backend", return_value=backend):
        result = graph_related("L-root")

    assert result["found"] is True
    assert result["namespace"] == "project:a"
    assert result["count"] == 1
    assert [item["id"] for item in result["related"]] == ["L-active"]


def test_graph_related_unknown_id_is_typed_empty(backend: SQLiteBackend) -> None:
    with patch("trw_mcp.tools.knowledge.get_backend", return_value=backend):
        assert graph_related("L-missing") == {
            "learning_id": "L-missing",
            "related": [],
            "count": 0,
            "found": False,
            "truncated": False,
        }


@pytest.mark.parametrize("depth", [0, 4])
def test_graph_related_rejects_unbounded_depth(backend: SQLiteBackend, depth: int) -> None:
    with patch("trw_mcp.tools.knowledge.get_backend", return_value=backend), pytest.raises(ValueError, match="depth"):
        graph_related("L-root", depth=depth)


def test_graph_related_rejects_unknown_edge_type(backend: SQLiteBackend) -> None:
    with (
        patch("trw_mcp.tools.knowledge.get_backend", return_value=backend),
        pytest.raises(ValueError, match="unsupported edge_types"),
    ):
        graph_related("L-root", edge_types=["not-real"])


def test_graph_related_caps_dense_neighbourhood_and_reports_truncation(backend: SQLiteBackend) -> None:
    """NFR01: public traversal is bounded by breadth as well as depth."""
    backend.store(MemoryEntry(id="L-root", content="root", namespace="project:a"))
    for index in range(8):
        learning_id = f"L-{index}"
        backend.store(MemoryEntry(id=learning_id, content=learning_id, namespace="project:a"))
        _edge(backend, "L-root", learning_id)

    with patch("trw_mcp.tools.knowledge.get_backend", return_value=backend):
        result = graph_related("L-root", limit=3)

    assert result["count"] == 3
    assert len(result["related"]) == 3
    assert result["truncated"] is True


@pytest.mark.parametrize("limit", [0, 101])
def test_graph_related_rejects_unbounded_limit(backend: SQLiteBackend, limit: int) -> None:
    with (
        patch("trw_mcp.tools.knowledge.get_backend", return_value=backend),
        pytest.raises(ValueError, match="limit must be between"),
    ):
        graph_related("L-root", limit=limit)
