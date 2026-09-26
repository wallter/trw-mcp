"""Memory adapter — lookup, list, count and access-tracking helpers.

Belongs to the ``memory_adapter.py`` facade. Re-exported there for back-compat.

Eight read-side / maintenance helpers that wrap the trw-memory backend.

Extracted as DIST-243 batch 43 to keep the parent ``memory_adapter.py``
module under the 350 effective-LOC ceiling.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Literal, cast

import structlog
from trw_memory.models.memory import MemoryStatus

from trw_mcp.models.config import get_config
from trw_mcp.state._constants import DEFAULT_LIST_LIMIT
from trw_mcp.state._memory_transforms import _memory_to_learning_dict
from trw_mcp.state._recall_gate import passive_learnings_allowed


def _warn(event: str, **kwargs: Any) -> None:
    """Route warning logs through memory_adapter.logger so test patches stick."""
    from trw_mcp.state import memory_adapter

    memory_adapter.logger.warning(event, **kwargs)


logger = structlog.get_logger(__name__)


def find_entry_by_id(trw_dir: Path, learning_id: str) -> dict[str, object] | None:
    """Look up a single learning entry by ID.

    The element is a ``LearningEntryDict`` (the recall-layer contract);
    widened to ``dict[str, object]`` at the boundary to match the existing
    public signature consumed across scoring/tools.
    """
    from trw_mcp.state._store_selection import selected_store

    entry = selected_store(trw_dir)[0].get(learning_id)
    return cast("dict[str, object]", _memory_to_learning_dict(entry)) if entry is not None else None


def list_active_learnings(
    trw_dir: Path,
    *,
    min_impact: float = 0.0,
    limit: int = DEFAULT_LIST_LIMIT,
    purpose: Literal["delivery", "maintenance"] = "delivery",
) -> list[dict[str, object]]:
    """List active entries for promotion, analytics and maintenance.

    Every read obeys the master recall switch except one that names
    ``purpose="maintenance"`` because its result never reaches the agent; an
    unknown purpose is gated (fail closed).
    """
    if purpose != "maintenance" and not passive_learnings_allowed():
        return []
    return _listed(trw_dir, MemoryStatus.ACTIVE.value, min_impact, limit)


def _listed(trw_dir: Path, status: str | None, min_impact: float, limit: int) -> list[dict[str, object]]:
    from trw_mcp.state._store_selection import selected_store

    store, namespace = selected_store(trw_dir)
    entries = store.list_entries(namespace, status=status, limit=limit)
    return [
        cast("dict[str, object]", _memory_to_learning_dict(entry))
        for entry in entries
        if entry.importance >= min_impact and entry.metadata.get("system_canary") != "true"
    ]


def list_entries_by_status(
    trw_dir: Path,
    *,
    status: str = "active",
    min_impact: float = 0.0,
    limit: int = DEFAULT_LIST_LIMIT,
) -> list[dict[str, object]]:
    """Bulk listing by status (PRD-FIX-033 FR01 — single SQLite query)."""
    try:
        mem_status = MemoryStatus(status)
    except ValueError:
        return []
    return _listed(trw_dir, mem_status.value, min_impact, limit)


def find_yaml_path_for_entry(trw_dir: Path, entry_id: str) -> Path | None:
    """Resolve YAML file path for an entry_id (PRD-FIX-033 FR05)."""
    cfg = get_config()
    entries_dir = trw_dir / cfg.learnings_dir / cfg.entries_dir
    if not entries_dir.exists():
        return None
    sanitized = re.sub(r"[^a-zA-Z0-9_\-]", "-", entry_id)
    candidate = entries_dir / f"{sanitized}.yaml"
    if candidate.exists():
        return candidate
    for yaml_file in entries_dir.glob("*.yaml"):
        if yaml_file.name == "index.yaml":
            continue
        if sanitized in yaml_file.stem or entry_id in yaml_file.stem:
            return yaml_file
    return None


def count_entries(trw_dir: Path) -> int:
    """Return total entry count (excluding system canaries)."""
    from trw_mcp.state._store_selection import selected_store

    store, namespace = selected_store(trw_dir)
    entries = store.list_entries(namespace, limit=100_000)
    return sum(entry.metadata.get("system_canary") != "true" for entry in entries)


def record_surfaced(trw_dir: Path, learning_ids: list[str], *, session_start: bool = False) -> None:
    """Count the learnings a caller showed as accessed, and as surfaced when *session_start*.

    PRD-FIX-104: one store call bumps access_count and recall_count together, so
    feedback-decay scoring sees every recall. Only the rows actually shown are
    counted, never the over-fetched candidates. The store resolves which
    namespace owns each id, so a federated recall needs no ownership walk here.
    Telemetry: an unreachable or refusing store is logged, never raised.
    """
    from trw_mcp.state._store_selection import selected_store

    unique_ids = list(dict.fromkeys(lid for lid in learning_ids if lid))
    if not unique_ids:
        return
    try:
        selected_store(trw_dir)[0].record_surfaced(unique_ids, session_start=session_start)
    except (RuntimeError, ValueError, OSError):  # trw-fail-silent-allow: access telemetry must not break recall; logged
        _warn("access_tracking_failed", exc_info=True, entry_ids=unique_ids, session_start=session_start)
