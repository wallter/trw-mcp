"""Knowledge-graph traversal — backs ``trw_recall``'s graph mode.

Knowledge synchronization remains an internal delivery operation. This module
exposes only bounded, read-only traversal from a known learning ID.

PRD-CORE-300-FR11 (S9) folded the standalone graph-related MCP tool
into ``trw_recall(graph_id=...)`` (``tools/learning.py``); :func:`graph_related`
is the surviving callable both the recall tool and the trw-memory tests call
directly.
"""

from __future__ import annotations

import structlog
from trw_memory.graph import MAX_TRAVERSAL_DEPTH, VALID_EDGE_TYPES
from typing_extensions import TypedDict

from trw_mcp.state import _store_selection
from trw_mcp.state._paths import resolve_trw_dir
from trw_mcp.state._store_selection import StoreUnavailableError

logger = structlog.get_logger(__name__)

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
    """Neighbours for one learning.

    ``found=False`` means the store was searched and does not hold the id. When
    the store could not be searched at all, ``lookup_status`` is
    ``"unavailable"`` and ``lookup_error`` names the cause — an unenumerable
    store used to answer with the same bare ``found=False`` as a genuine miss.
    """

    learning_id: str
    namespace: str
    related: list[GraphRelatedItem]
    count: int
    found: bool
    truncated: bool
    lookup_status: str
    lookup_error: str


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

    store, _ = _store_selection.selected_store(resolve_trw_dir())
    try:
        root = store.get(normalized_id)
    except StoreUnavailableError as exc:
        logger.warning("graph_related_lookup_unavailable", learning_id=normalized_id, exc_info=True)
        return {
            "learning_id": normalized_id,
            "related": [],
            "count": 0,
            "found": False,
            "truncated": False,
            "lookup_status": "unavailable",
            "lookup_error": str(exc),
        }
    if root is None:
        return {
            "learning_id": normalized_id,
            "related": [],
            "count": 0,
            "found": False,
            "truncated": False,
        }

    rows, truncated = store.graph_related(root.namespace, normalized_id, depth, edge_types, limit)
    related: list[GraphRelatedItem] = [
        {
            "id": str(row["id"]),
            "summary": str(row["content"]),
            "importance": float(row["importance"]),
            "tags": list(row["tags"]),
            "edge_type": str(row["edge_type"]),
            "weight": float(row["weight"]),
            "depth": int(row["depth"]),
        }
        for row in rows
    ]
    return {
        "learning_id": normalized_id,
        "namespace": root.namespace,
        "related": related,
        "count": len(related),
        "found": True,
        "truncated": truncated,
    }


__all__ = ["GraphRelatedItem", "GraphRelatedResult", "graph_related"]
