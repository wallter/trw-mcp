"""Projection from a trw-memory :class:`MemoryEntry` to a learning dict.

The external dict shapes tool callers see (FRAMEWORK.md, hooks, etc.) are built
here from the internal SQLite-backed model.

PRD-CORE-251 FR03 removed this module's other half. ``_learning_to_memory_entry``
built a :class:`MemoryEntry` by hand for a ``backend.store`` call that skipped
namespace validation, the namespace permission check and input validation
entirely; entries are now constructed inside
``trw_memory.tools.store.memory_store_impl`` through the PRD-CORE-245 FR08
chokepoint, and the trw-mcp-side argument marshalling that used to be tangled up
with construction lives in ``_store_arguments.py``.

This module is an internal implementation detail of ``memory_adapter.py``.
External code should import from ``memory_adapter`` (the public facade).
"""

from __future__ import annotations

from datetime import datetime
from typing import cast

import structlog
from trw_memory.models.memory import MemoryEntry, MemoryStatus

from trw_mcp.models.config._defaults import COMPACT_TAGS_CAP
from trw_mcp.models.typed_dicts import LearningEntryDict
from trw_mcp.state._recall_signals import current_recall_signals

logger = structlog.get_logger(__name__)


def _memory_to_learning_dict(entry: MemoryEntry, *, compact: bool = False) -> LearningEntryDict:
    """Convert a :class:`MemoryEntry` to the dict shape returned by trw_recall.

    The returned dict matches the YAML-era learning entry format so callers
    (FRAMEWORK.md, hooks, etc.) see no API change. The declared type is the
    canonical :class:`LearningEntryDict` contract; the field set built here is
    the single source of truth that ``LearningEntryDict`` mirrors. Recall
    callers therefore receive a typed element shape instead of a bare
    ``dict[str, object]``.

    Args:
        entry: Memory entry from SQLite.
        compact: When True, return only essential fields.

    Returns:
        ``LearningEntryDict`` with ``id``, ``summary``, ``tags``, ``impact``,
        ``status``, and (when not compact) the full extended field set.
    """
    tags = entry.tags[:COMPACT_TAGS_CAP] if compact else entry.tags
    base: dict[str, object] = {
        "id": entry.id,
        "summary": entry.content,
        "tags": tags,
        "impact": entry.importance,
        "status": entry.status.value if isinstance(entry.status, MemoryStatus) else str(entry.status),
    }
    # CORE-268: evidence must survive acquisition, including compact candidates.
    # Qualification belongs to consumers; no verifier or per-result lookup here.
    if entry.assertions:
        base["assertions"] = [a.model_dump(mode="json") for a in entry.assertions]
    if entry.anchors:
        base["anchors"] = [a.model_dump(mode="json") for a in entry.anchors]
    base["verification_status"] = getattr(entry, "verification_status", None)
    checked_at = getattr(entry, "verification_checked_at", None)
    base["verification_checked_at"] = checked_at.isoformat() if isinstance(checked_at, datetime) else checked_at
    base["anchor_validity"] = entry.anchor_validity
    if compact:
        signals = current_recall_signals()
        if signals is not None:
            signals.transfer(entry, base)
        return cast("LearningEntryDict", base)

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
            # PRD-FIX-104: expose recall_count so feedback_decay_score can fire in entry_utility
            "recall_count": entry.recall_count,
            "helpful_count": entry.helpful_count,
            "unhelpful_count": entry.unhelpful_count,
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

    # PRD-CORE-231-FR02: surface the PERSISTED staleness verdict so a stale
    # claim stays visibly stale in a fresh session, not only in the session that
    # happened to compute it. Omitted when None (the common case) to keep the
    # recall payload's token cost unchanged for healthy entries.
    # getattr: trw-memory versions independently of trw-mcp, so an installed
    # trw-memory predating the FR02 column has no such attribute — degrade to
    # "no verdict" instead of raising on every recall.
    verification_status = getattr(entry, "verification_status", None)
    if verification_status is not None:
        base["verification_status"] = verification_status

    # PRD-CORE-244-FR08: the three PRD-CORE-108 outcome-attribution fields
    # (sessions_surfaced, avg_rework_delta, outcome_correlation) were removed
    # from MemoryEntry and the schema-5 table — no producer had ever written
    # them (0 of 9,366 rows), so the projection advertised a rework-attribution
    # subsystem that did not exist. Reading them here after the model drop
    # raised AttributeError on EVERY recall.

    base["session_count"] = entry.session_count or 0

    # Bi-temporal validity (PRD-CORE-194 FR03): surface the superseded flag +
    # closer so agents see WHY a record is down-ranked. Only emitted for a closed
    # window — open entries are byte-identical to the pre-194 shape (back-compat).
    if entry.invalid_from is not None:
        base["superseded"] = True
        base["invalidated_by"] = entry.invalidated_by

    signals = current_recall_signals()
    if signals is not None:
        signals.transfer(entry, base)
    return cast("LearningEntryDict", base)
