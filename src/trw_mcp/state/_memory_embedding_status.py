"""Embedding readiness status builder for memory connection."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any


def build_embeddings_status(
    *,
    embedder_unavailable_reason: str,
    get_embedder: Callable[[], Any],
    append_wal_health: Callable[[dict[str, object]], None],
) -> dict[str, object]:
    """Report whether this install can embed, for ``update-project``'s warning.

    Loads the local embedder when embeddings are enabled. Session start does not
    call this: the daemon owns recall's model and its store's vectors, and
    session start reports the coverage the daemon measures (pipeline health).

    Returns a dict with:
    - ``enabled``: whether config has embeddings_enabled=True
    - ``available``: whether deps are installed and the model loads
    - ``advisory``: human-readable message (empty when everything is fine)
    - ``wal_size_mb`` / ``wal_advisory``: (optional) WAL size when above threshold (FR06)
    """
    from trw_mcp.models.config import get_config

    if not get_config().embeddings_enabled:
        result: dict[str, object] = {"enabled": False, "available": False, "advisory": ""}
    elif get_embedder() is not None:
        result = {"enabled": True, "available": True, "advisory": ""}
    else:
        reason = embedder_unavailable_reason or "sentence-transformers is not installed"
        result = {
            "enabled": True,
            "available": False,
            "advisory": f"Embeddings enabled but unavailable: {reason}. Run: pip install trw-memory[embeddings]",
        }
    append_wal_health(result)
    return result


__all__ = ["build_embeddings_status"]
