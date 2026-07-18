"""Agent-facing knowledge-graph traversal tools.

Knowledge synchronization remains an internal delivery operation. This module
exposes only bounded, read-only traversal from a known learning ID.
"""

from __future__ import annotations

from fastmcp import FastMCP
from trw_memory.graph import MAX_TRAVERSAL_DEPTH, VALID_EDGE_TYPES, graph_query
from trw_memory.models.memory import MemoryStatus
from typing_extensions import TypedDict

from trw_mcp.state.memory_adapter import get_backend

_DEFAULT_RELATED_LIMIT = 50
_MAX_RELATED_LIMIT = 100


class GraphRelatedItem(TypedDict):
    id: str
    summary: str
    importance: float
    tags: list[str]
    edge_type: str
    weight: float
    depth: int


class GraphRelatedResult(TypedDict, total=False):
    learning_id: str
    namespace: str
    related: list[GraphRelatedItem]
    count: int
    found: bool
    truncated: bool


def graph_related(
    learning_id: str,
    *,
    depth: int = 1,
    edge_types: list[str] | None = None,
    limit: int = _DEFAULT_RELATED_LIMIT,
) -> GraphRelatedResult:
    """Return active graph neighbours for one learning, scoped to its namespace."""
    normalized_id = learning_id.strip()
    if not normalized_id:
        raise ValueError("learning_id must not be empty")
    if not 1 <= depth <= MAX_TRAVERSAL_DEPTH:
        raise ValueError(f"depth must be between 1 and {MAX_TRAVERSAL_DEPTH}")
    if edge_types is not None:
        invalid = sorted(set(edge_types) - VALID_EDGE_TYPES)
        if invalid:
            raise ValueError(f"unsupported edge_types: {', '.join(invalid)}")
    if not 1 <= limit <= _MAX_RELATED_LIMIT:
        raise ValueError(f"limit must be between 1 and {_MAX_RELATED_LIMIT}")

    backend = get_backend()
    root = backend.get(normalized_id)
    if root is None:
        return {
            "learning_id": normalized_id,
            "related": [],
            "count": 0,
            "found": False,
            "truncated": False,
        }

    nodes = graph_query(
        backend._conn,
        [normalized_id],
        depth=depth,
        edge_types=edge_types,
        namespace=root.namespace,
        max_nodes=limit + 1,
    )
    truncated = len(nodes) > limit
    related: list[GraphRelatedItem] = []
    for node in nodes[:limit]:
        entry = backend.get(str(node["id"]))
        if entry is None or entry.status != MemoryStatus.ACTIVE:
            continue
        related.append(
            {
                "id": entry.id,
                "summary": entry.content,
                "importance": entry.importance,
                "tags": entry.tags,
                "edge_type": str(node["edge_type"]),
                "weight": float(node["weight"]),
                "depth": int(node["depth"]),
            }
        )
    return {
        "learning_id": normalized_id,
        "namespace": root.namespace,
        "related": related,
        "count": len(related),
        "found": True,
        "truncated": truncated,
    }


def register_knowledge_tools(server: FastMCP) -> None:
    """Register bounded read-only knowledge-graph traversal."""

    @server.tool(output_schema=None)
    def trw_graph_related(
        learning_id: str,
        depth: int = 1,
        edge_types: list[str] | None = None,
        limit: int = _DEFAULT_RELATED_LIMIT,
    ) -> GraphRelatedResult:
        """Find active learnings connected to a learning in the knowledge graph.

        Use when:
        - A recalled learning looks useful and you want its supporting,
          conflicting, co-anchored, or semantically related neighbours.
        - You need bounded graph context without broadening the recall query.

        Traversal is restricted to the root learning's namespace and depth is
        capped at three hops. Unknown IDs return ``found=false``.
        """
        return graph_related(learning_id, depth=depth, edge_types=edge_types, limit=limit)


__all__ = ["GraphRelatedItem", "GraphRelatedResult", "graph_related", "register_knowledge_tools"]
