"""Result transformation between trw-memory MemoryEntry and learning dicts.

Converts between the internal :class:`MemoryEntry` model (SQLite-backed) and
the external dict shapes expected by tool callers (FRAMEWORK.md, hooks, etc.).

This module is an internal implementation detail of ``memory_adapter.py``.
External code should import from ``memory_adapter`` (the public facade).
"""

from __future__ import annotations

from trw_memory.models.memory import Assertion, MemoryEntry, MemoryStatus

from trw_mcp.state._constants import DEFAULT_NAMESPACE

_NAMESPACE = DEFAULT_NAMESPACE


def _memory_to_learning_dict(entry: MemoryEntry, *, compact: bool = False) -> dict[str, object]:
    """Convert a :class:`MemoryEntry` to the dict shape returned by trw_recall.

    The returned dict matches the YAML-era learning entry format so callers
    (FRAMEWORK.md, hooks, etc.) see no API change.

    Args:
        entry: Memory entry from SQLite.
        compact: When True, return only essential fields.

    Returns:
        Dict with ``id``, ``summary``, ``tags``, ``impact``, ``status``, etc.
    """
    base: dict[str, object] = {
        "id": entry.id,
        "summary": entry.content,
        "tags": entry.tags,
        "impact": entry.importance,
        "status": entry.status.value if isinstance(entry.status, MemoryStatus) else str(entry.status),
    }
    if compact:
        return base

    base.update(
        {
            "detail": entry.detail,
            "evidence": entry.evidence,
            "source_type": entry.source,
            "source_identity": entry.source_identity,
            "created": entry.created_at.date().isoformat() if entry.created_at else "",
            "updated": entry.updated_at.date().isoformat() if entry.updated_at else "",
            "access_count": entry.access_count,
            "last_accessed_at": (entry.last_accessed_at.date().isoformat() if entry.last_accessed_at else None),
            "q_value": entry.q_value,
            "q_observations": entry.q_observations,
            "recurrence": entry.recurrence,
            "outcome_history": entry.outcome_history,
            "shard_id": entry.metadata.get("shard_id", None),
        }
    )
    # Include assertions when present (PRD-CORE-086)
    if entry.assertions:
        base["assertions"] = [a.model_dump() for a in entry.assertions]
    return base


def _learning_to_memory_entry(
    learning_id: str,
    summary: str,
    detail: str,
    *,
    tags: list[str] | None = None,
    evidence: list[str] | None = None,
    impact: float = 0.5,
    shard_id: str | None = None,
    source_type: str = "agent",
    source_identity: str = "",
    assertions: list[dict[str, str]] | None = None,
) -> MemoryEntry:
    """Build a :class:`MemoryEntry` from trw_learn parameters.

    Pre-seeds q_value from impact score so high-impact learnings start
    with an elevated Q-value before any observations accumulate.
    """
    from trw_mcp.scoring._correlation import compute_initial_q_value

    metadata: dict[str, str] = {}
    if shard_id:
        metadata["shard_id"] = shard_id

    # Validate and attach assertions (PRD-CORE-086)
    assertion_objects: list[Assertion] = []
    if assertions:
        for a in assertions:
            assertion_objects.append(Assertion.model_validate(a))

    return MemoryEntry(
        id=learning_id,
        content=summary,
        detail=detail,
        tags=tags or [],
        evidence=evidence or [],
        importance=impact,
        source=source_type,
        source_identity=source_identity,
        namespace=_NAMESPACE,
        metadata=metadata,
        q_value=compute_initial_q_value(impact),
        assertions=assertion_objects,
    )
