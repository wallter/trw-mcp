"""One daemon lookup for a whole pulled page (SYNC-FIND-MANY). Belongs to :mod:`trw_mcp.sync.pull`.

``merge_team_learnings`` used to ask the daemon once per learning whether it already held the row; 200 unchanged
rows were 200 round trips, 37 s under load, competing with recall for the daemon's workers.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import structlog
from trw_memory.models.memory import MemoryEntry

from trw_mcp.state._store_selection import MemoryStore

logger = structlog.get_logger(__name__)

__all__ = ["prefetch_existing"]


def prefetch_existing(
    store: MemoryStore, target: str, team_learnings: list[dict[str, Any]], local_id_of: Callable[[str], str]
) -> Callable[[str, str], MemoryEntry | None] | None:
    """A ``(source_learning_id, local_id) -> row`` lookup built from one call; ``None`` when the call fails.

    ``None`` sends each row back to its own ``find_synced``: a daemon from before the batched tool refuses it, and
    a store that is down fails there too, loudly.
    """
    sources = {str(item.get("source_learning_id", "")).strip() for item in team_learnings} - {""}
    if not sources:
        return None
    local = [local_id_of(source) for source in sources]
    try:
        rows = store.find_synced_many(target, sorted(sources), sorted({*sources, *local}))
    except Exception:  # trw-fail-silent-allow: logged; the per-row lookup is the fallback and a down store fails there
        logger.warning("sync_team_merge_batched_find_unavailable", event_type="sync_team_merge", exc_info=True)
        return None
    by_remote = {row.remote_id: row for row in rows if row.remote_id}
    by_id = {row.id: row for row in rows}
    return lambda source, local_id: by_remote.get(source) or by_id.get(local_id) or by_id.get(source)
