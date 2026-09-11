"""Interactive acquisition must not join an unbounded cold model load.

Keep the underlying adapter's explicit cold-init behavior available to batch
and embedding callers. Only the public recall tool uses this readiness policy.
"""

from __future__ import annotations

from collections.abc import Callable
from functools import partial
from typing import TypeVar

import structlog

logger = structlog.get_logger(__name__)
_Result = TypeVar("_Result")


def prepare_interactive_recall(
    adapter: Callable[..., _Result], *, embeddings_enabled: bool, query: str
) -> tuple[Callable[..., _Result], str | None]:
    """Return a no-cold-load adapter and an optional readiness-time advisory.

    Readiness may change while the query runs, so the advisory does not claim
    which ranking algorithm ultimately ran. An already-ready model is still
    used by the normal adapter. Warm-up keeps its offline/single-flight guards.
    """
    from trw_mcp.state._memory_connection import get_initialized_embedder

    warning = None
    if embeddings_enabled and query.strip() not in {"", "*"} and get_initialized_embedder() is None:
        warning = (
            "The semantic model was not ready when recall started; cold initialization was not awaited. "
            "Keyword fallback may have been used. This response does not establish semantic coverage."
        )
        from trw_mcp.state._surface_role import reviewer_role_active

        if not reviewer_role_active():
            try:
                from trw_mcp.state._memory_connection import _schedule_embedder_warmup

                _schedule_embedder_warmup()
            except Exception:  # justified: optional warm-up must not block available retrieval
                logger.debug("interactive_recall_warmup_unavailable", exc_info=True)
    return partial(adapter, allow_cold_embedding_init=False), warning
