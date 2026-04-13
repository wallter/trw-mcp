"""Result transformation between trw-memory MemoryEntry and learning dicts.

Converts between the internal :class:`MemoryEntry` model (SQLite-backed) and
the external dict shapes expected by tool callers (FRAMEWORK.md, hooks, etc.).

This module is an internal implementation detail of ``memory_adapter.py``.
External code should import from ``memory_adapter`` (the public facade).
"""

from __future__ import annotations

from typing import Literal, cast

import structlog
from trw_memory.models.memory import (
    Anchor,
    Assertion,
    Confidence,
    MemoryEntry,
    MemoryStatus,
    MemoryType,
    ProtectionTier,
)

from trw_mcp.models.config._defaults import COMPACT_TAGS_CAP
from trw_mcp.state._constants import DEFAULT_NAMESPACE

_NAMESPACE = DEFAULT_NAMESPACE

# Re-export from canonical source for backward compatibility
from trw_mcp.state._constants import VALID_SOURCES as _VALID_SOURCES  # noqa: E402

_SourceType = Literal["human", "agent", "tool", "consolidated", "team_sync"]
logger = structlog.get_logger(__name__)


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
    tags = entry.tags[:COMPACT_TAGS_CAP] if compact else entry.tags
    base: dict[str, object] = {
        "id": entry.id,
        "summary": entry.content,
        "tags": tags,
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
            "client_profile": entry.client_profile,
            "model_id": entry.model_id,
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

    # Meta-learning typed classification (PRD-CORE-110)
    base["type"] = entry.type
    base["nudge_line"] = entry.nudge_line
    base["expires"] = entry.expires
    base["confidence"] = entry.confidence
    base["task_type"] = entry.task_type
    base["domain"] = list(entry.domain)
    base["phase_origin"] = entry.phase_origin
    base["phase_affinity"] = list(entry.phase_affinity)
    base["team_origin"] = entry.team_origin
    base["protection_tier"] = entry.protection_tier

    # Code-grounded anchors (PRD-CORE-111)
    if entry.anchors:
        base["anchors"] = [a.model_dump() for a in entry.anchors]
    base["anchor_validity"] = entry.anchor_validity

    # Outcome attribution fields (PRD-CORE-108)
    base["sessions_surfaced"] = entry.sessions_surfaced
    base["avg_rework_delta"] = entry.avg_rework_delta
    base["outcome_correlation"] = entry.outcome_correlation

    base["session_count"] = entry.session_count or 0

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
    client_profile: str = "",
    model_id: str = "",
    assertions: list[dict[str, str]] | None = None,
    # PRD-CORE-110: Typed learning fields
    type: str = "pattern",
    nudge_line: str = "",
    expires: str = "",
    confidence: str = "unverified",
    task_type: str = "",
    domain: list[str] | None = None,
    phase_origin: str = "",
    phase_affinity: list[str] | None = None,
    team_origin: str = "",
    protection_tier: str = "normal",
    # PRD-CORE-111: Code-grounded anchors
    anchors: list[dict[str, object]] | None = None,
    anchor_validity: float = 1.0,
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
        assertion_objects.extend(Assertion.model_validate(a, strict=False) for a in assertions)

    # Validate anchors (PRD-CORE-111)
    anchor_objects: list[Anchor] = []
    if anchors:
        for a in anchors:
            try:
                # Convert absolute paths to relative (Anchor rejects absolute)
                anchor_data = dict(a)
                file_val = str(anchor_data.get("file", ""))
                if file_val.startswith("/"):
                    anchor_data["file"] = file_val.lstrip("/")
                anchor_objects.append(Anchor.model_validate(anchor_data))
            except Exception:  # noqa: PERF203  # justified: fail-open, skip invalid anchors
                logger.debug("invalid_anchor_skipped", anchor=a, exc_info=True)

    return MemoryEntry(
        id=learning_id,
        content=summary,
        detail=detail,
        tags=tags or [],
        evidence=evidence or [],
        importance=impact,
        source=cast("_SourceType", source_type if source_type in _VALID_SOURCES else "agent"),
        source_identity=source_identity,
        client_profile=client_profile,
        model_id=model_id,
        namespace=_NAMESPACE,
        metadata=metadata,
        q_value=compute_initial_q_value(impact),
        assertions=assertion_objects,
        # PRD-CORE-110: Typed learning fields - convert strings to enums
        type=MemoryType(type) if isinstance(type, str) else type,
        nudge_line=nudge_line,
        expires=expires,
        confidence=Confidence(confidence) if isinstance(confidence, str) else confidence,
        task_type=task_type,
        domain=domain or [],
        phase_origin=phase_origin,
        phase_affinity=phase_affinity or [],
        team_origin=team_origin,
        protection_tier=ProtectionTier(protection_tier) if isinstance(protection_tier, str) else protection_tier,
        # PRD-CORE-111: Code-grounded anchors
        anchors=anchor_objects,
        anchor_validity=anchor_validity,
    )
