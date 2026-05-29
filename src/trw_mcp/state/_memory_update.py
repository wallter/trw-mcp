"""Memory adapter — ``update_learning`` field-mapping logic.

Belongs to the ``memory_adapter.py`` facade. Re-exported there for back-compat.

Extracted as DIST-243 batch 59 to keep the parent module under the 350-LOC gate.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import structlog

from trw_mcp.state._memory_lookups import get_backend

logger = structlog.get_logger(__name__)

_VALID_STATUSES = {"active", "resolved", "obsolete", "obsolete_poisoned"}
_VALID_TYPES = {"incident", "pattern", "convention", "hypothesis", "workaround"}


def update_learning(
    trw_dir: Path,
    learning_id: str,
    *,
    status: str | None = None,
    detail: str | None = None,
    impact: float | None = None,
    summary: str | None = None,
    type: str | None = None,
    nudge_line: str | None = None,
    expires: str | None = None,
    confidence: str | None = None,
    task_type: str | None = None,
    domain: list[str] | None = None,
    phase_origin: str | None = None,
    phase_affinity: list[str] | None = None,
    team_origin: str | None = None,
    protection_tier: str | None = None,
    tags: list[str] | None = None,
) -> dict[str, str]:
    """Update a learning entry in SQLite.

    Return shape matches ``trw_learn_update`` output:
    ``{"learning_id", "changes", "status"}``.
    """
    backend = get_backend(trw_dir)
    existing = backend.get(learning_id)
    if existing is None:
        return {"error": f"Learning {learning_id} not found", "status": "not_found"}

    fields: dict[str, str | float | list[str] | dict[str, str]] = {}
    changes: list[str] = []

    if status is not None:
        if status not in _VALID_STATUSES:
            return {
                "error": f"Invalid status '{status}'. Must be one of: {_VALID_STATUSES}",
                "status": "invalid",
            }
        fields["status"] = status
        changes.append(f"status→{status}")
    if detail is not None:
        fields["detail"] = detail
        changes.append("detail updated")
    if summary is not None:
        fields["content"] = summary
        changes.append("summary updated")
    if impact is not None:
        if not 0.0 <= impact <= 1.0:
            return {"error": f"Impact must be 0.0-1.0, got {impact}", "status": "invalid"}
        fields["importance"] = impact
        changes.append(f"impact→{impact}")
    if type is not None:
        if type not in _VALID_TYPES:
            return {
                "error": f"Invalid type '{type}'. Must be one of: {_VALID_TYPES}",
                "status": "invalid",
            }
        fields["type"] = type
        changes.append(f"type→{type}")
    if nudge_line is not None:
        fields["nudge_line"] = nudge_line
        changes.append("nudge_line updated")
    if expires is not None:
        fields["expires"] = expires
        changes.append("expires updated")
    if confidence is not None:
        fields["confidence"] = confidence
        changes.append(f"confidence→{confidence}")
    if task_type is not None:
        fields["task_type"] = task_type
        changes.append(f"task_type→{task_type}")
    if domain is not None:
        fields["domain"] = domain
        changes.append("domain updated")
    if phase_origin is not None:
        fields["phase_origin"] = phase_origin
        changes.append(f"phase_origin→{phase_origin}" if phase_origin else "phase_origin cleared")
    if phase_affinity is not None:
        fields["phase_affinity"] = phase_affinity
        changes.append("phase_affinity updated")
    if team_origin is not None:
        fields["team_origin"] = team_origin
        changes.append(f"team_origin→{team_origin}" if team_origin else "team_origin cleared")
    if protection_tier is not None:
        fields["protection_tier"] = protection_tier
        changes.append(f"protection_tier→{protection_tier}")
    if tags is not None:
        fields["tags"] = tags
        changes.append("tags updated")

    if summary is not None or detail is not None:
        new_content = summary if summary is not None else existing.content
        new_detail = detail if detail is not None else existing.detail
        if existing.metadata.get("provenance_content_hash") or existing.metadata.get("content_hash"):
            new_metadata = dict(existing.metadata)
            new_metadata["provenance_content_hash"] = hashlib.sha256(f"{new_content}{new_detail}".encode()).hexdigest()
            fields["metadata"] = new_metadata

    if not changes:
        return {"learning_id": learning_id, "status": "no_changes"}

    backend.update(learning_id, **fields)

    logger.info("memory_update_learning", learning_id=learning_id, changes=changes)
    return {
        "learning_id": learning_id,
        "changes": ", ".join(changes),
        "status": "updated",
    }
