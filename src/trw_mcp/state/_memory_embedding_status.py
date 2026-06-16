"""Embedding readiness status builder for memory connection."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any


def build_embeddings_status(
    *,
    allow_initialize: bool,
    coverage_probe: bool,
    embed_failures: int,
    embedder_checked: bool,
    embedder_unavailable_reason: str,
    get_embedder: Callable[[], Any],
    get_initialized_embedder: Callable[[], Any],
    peek_backend: Callable[[], Any],
    append_wal_health: Callable[[dict[str, object]], None],
    logger: Any,
) -> dict[str, object]:
    """Check embedding readiness and return status for session_start advisory.

    Args:
        allow_initialize: Whether to initialize the embedder (cold load) if not yet checked.
            Set False on the MCP hot path to avoid blocking model loads.
        coverage_probe: When True, probe existing_vector_ids()/count() on the live backend
            and compute a coverage_ratio. When below embeddings_coverage_warn_threshold,
            adds an advisory (PRD-FIX-COMPOUNDING-3-FR02). Default False for backward compat.

    Returns a dict with:
    - ``enabled``: whether config has embeddings_enabled=True
    - ``available``: whether deps are installed and model loads
    - ``advisory``: human-readable message (empty when everything is fine)
    - ``recent_failures``: count of embed failures since process start (FR07)
    - ``coverage_ratio``: (optional, when coverage_probe=True) vector count / entry count
    - ``wal_size_mb``: (optional) WAL file size when above threshold (FR06)
    - ``wal_advisory``: (optional) human-readable WAL size warning (FR06)
    """
    from trw_mcp.models.config import get_config

    cfg = get_config()
    if not cfg.embeddings_enabled:
        result: dict[str, object] = {
            "enabled": False,
            "available": False,
            "advisory": "",
            "recent_failures": embed_failures,
        }
        append_wal_health(result)
        return result

    if not allow_initialize and not embedder_checked:
        result = {
            "enabled": True,
            "available": False,
            "advisory": "Embeddings enabled; cold model initialization was deferred on the MCP hot path.",
            "recent_failures": embed_failures,
            "initialization_deferred": True,
        }
        append_wal_health(result)
        return result

    embedder = get_embedder() if allow_initialize else get_initialized_embedder()
    if embedder is not None:
        advisory = ""
        result = {
            "enabled": True,
            "available": True,
            "advisory": advisory,
            "recent_failures": embed_failures,
        }
        # PRD-FIX-COMPOUNDING-3-FR02: Optional coverage probe
        if coverage_probe:
            backend = peek_backend()
            if backend is not None:
                try:
                    vec_count = len(backend.existing_vector_ids())
                    total = backend.count()
                    coverage_ratio: float = vec_count / total if total > 0 else 1.0
                    result["coverage_ratio"] = coverage_ratio
                    warn_threshold = getattr(cfg, "embeddings_coverage_warn_threshold", 0.10)
                    if coverage_ratio < warn_threshold:
                        advisory = (
                            f"Vector coverage is low: {vec_count}/{total} entries have embeddings "
                            f"({coverage_ratio:.1%}). Run 'update-project' to backfill vectors. "
                            f"Hybrid KNN recall is degraded until backfill completes."
                        )
                        result["advisory"] = advisory
                        logger.warning(
                            "embeddings_coverage_low",
                            coverage_ratio=round(coverage_ratio, 4),
                            vec_count=vec_count,
                            total=total,
                            warn_threshold=warn_threshold,
                        )
                except Exception:  # justified: fail-open, coverage probe is advisory only
                    logger.debug("embeddings_coverage_probe_failed", exc_info=True)
        append_wal_health(result)
        return result

    reason = embedder_unavailable_reason or "sentence-transformers is not installed"
    result = {
        "enabled": True,
        "available": False,
        "advisory": f"Embeddings enabled but unavailable: {reason}. Run: pip install trw-memory[embeddings]",
        "recent_failures": embed_failures,
    }
    append_wal_health(result)
    return result


__all__ = ["build_embeddings_status"]
