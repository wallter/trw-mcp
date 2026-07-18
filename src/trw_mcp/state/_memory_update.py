"""Memory adapter — ``update_learning`` field-mapping logic.

Belongs to the ``memory_adapter.py`` facade. Re-exported there for back-compat.

Extracted as DIST-243 batch 59 to keep the parent module under the 350-LOC gate.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

from trw_mcp.state._memory_lookups import get_backend

if TYPE_CHECKING:
    from trw_memory.models.memory import MemoryEntry
    from trw_memory.storage.sqlite_backend import SQLiteBackend

logger = structlog.get_logger(__name__)


def _resolve_owning_backend(trw_dir: Path, learning_id: str) -> tuple[SQLiteBackend, MemoryEntry | None]:
    """Locate the backend that owns ``learning_id`` and return it with the entry.

    PRD-CORE-185: ``update_learning`` historically queried ONLY the project
    backend, so a portable (user-tier) learning -- stored in the box-wide user
    store by ``store_learning``'s ``is_user_write`` dispatch -- returned
    ``not_found`` and could never be updated. This mirrors that dispatch on the
    read side: try the project backend first (unchanged behavior, byte-identical
    when the entry lives there) and, only on a miss AND when a user-scope store
    is present, fall back to the user backend so user-tier entries are
    updatable. Returns ``(backend, entry)`` for whichever store owns the entry,
    or ``(project_backend, None)`` when neither has it.
    """
    project_backend = get_backend(trw_dir)
    existing = project_backend.get(learning_id)
    if existing is not None:
        return project_backend, existing

    # Project miss: consult the user store only when one is actually present,
    # so project-only installs keep the original single-backend behavior and
    # never provision a user store just to answer an update.
    from trw_mcp.state._tier_routing import user_scope_present

    if not user_scope_present():
        return project_backend, None

    from trw_mcp.state._user_tier import get_user_backend

    user_backend = get_user_backend()
    user_entry = user_backend.get(learning_id)
    if user_entry is not None:
        return user_backend, user_entry
    return project_backend, None


_VALID_STATUSES = {"active", "resolved", "obsolete", "obsolete_poisoned"}
_VALID_TYPES = {"incident", "pattern", "convention", "hypothesis", "workaround"}
# Mirror the enum sets the trw_learn_update tool validates against
# (tools/learning.py). Validated here too so any internal caller of the state
# facade -- not just the MCP tool -- cannot persist an invalid value.
_VALID_CONFIDENCES = {"unverified", "low", "medium", "high", "verified"}
_VALID_PROTECTION_TIERS = {"critical", "high", "normal", "low", "protected", "permanent"}
_VALID_FEEDBACK = {"helpful", "unhelpful"}


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
    supersedes: str | None = None,
    assertions: list[dict[str, object]] | None = None,
    feedback: str | None = None,
) -> dict[str, str]:
    """Update a learning entry in SQLite.

    Return shape matches ``trw_learn_update`` output:
    ``{"learning_id", "changes", "status"}``.

    PRD-CORE-194 FR04 (OQ4 resolution): when ``supersedes`` names a prior record
    id, this update is an explicit correction/replacement — close the PRIOR
    record's validity window (set its ``invalid_from`` = now + ``invalidated_by``
    = ``learning_id``, the updating record) and retain it (never delete). The
    supersession branch fires ONLY on an explicit ``supersedes=`` argument, never
    on a routine field edit.
    """
    if feedback is not None and feedback not in _VALID_FEEDBACK:
        return {
            "error": f"Invalid feedback '{feedback}'. Must be one of: {_VALID_FEEDBACK}",
            "status": "invalid",
        }
    backend, existing = _resolve_owning_backend(trw_dir, learning_id)
    if existing is None:
        return {"error": f"Learning {learning_id} not found", "status": "not_found"}

    fields: dict[str, object] = {}
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
        if confidence not in _VALID_CONFIDENCES:
            return {
                "error": f"Invalid confidence '{confidence}'. Must be one of: {_VALID_CONFIDENCES}",
                "status": "invalid",
            }
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
        if protection_tier not in _VALID_PROTECTION_TIERS:
            return {
                "error": (f"Invalid protection_tier '{protection_tier}'. Must be one of: {_VALID_PROTECTION_TIERS}"),
                "status": "invalid",
            }
        fields["protection_tier"] = protection_tier
        changes.append(f"protection_tier→{protection_tier}")
    if tags is not None:
        fields["tags"] = tags
        changes.append("tags updated")
    if assertions is not None:
        from trw_memory.models.memory import Assertion

        fields["assertions"] = [Assertion.model_validate(assertion, strict=False) for assertion in assertions]
        changes.append("assertions updated")

    if summary is not None or detail is not None:
        new_content = summary if summary is not None else existing.content
        new_detail = detail if detail is not None else existing.detail
        if existing.metadata.get("provenance_content_hash") or existing.metadata.get("content_hash"):
            new_metadata = dict(existing.metadata)
            new_metadata["provenance_content_hash"] = hashlib.sha256(f"{new_content}{new_detail}".encode()).hexdigest()
            fields["metadata"] = new_metadata

    # PRD-CORE-194 FR04: explicit supersession. Close the PRIOR record's window
    # (it is replaced BY this learning_id). Resolve the prior through the same
    # owning-backend dispatch so a user-tier prior is also closeable. A missing
    # or already-closed prior is a safe no-op (the primary edit still applies).
    if supersedes is not None and supersedes != learning_id:
        prior_backend, prior = _resolve_owning_backend(trw_dir, supersedes)
        if prior is None:
            logger.info("supersession_prior_not_found", supersedes=supersedes, by=learning_id)
        elif prior.invalid_from is not None:
            logger.info("supersession_prior_already_closed", supersedes=supersedes)
        else:
            now = datetime.now(timezone.utc)
            prior_backend.update(supersedes, invalid_from=now, invalidated_by=learning_id)
            changes.append(f"supersedes→{supersedes}")
            logger.info("supersession_window_closed", prior=supersedes, by=learning_id)

    if feedback is not None:
        # Keep the read and increment inside the backend's serialized write
        # transaction. A bare get()+update() pair loses votes when concurrent
        # callers read the same old counter.
        with backend.transaction():
            current = backend.get(learning_id)
            if current is None:
                return {"error": f"Learning {learning_id} not found", "status": "not_found"}
            counter = "helpful_count" if feedback == "helpful" else "unhelpful_count"
            fields[counter] = getattr(current, counter) + 1
            backend.update(learning_id, **fields)
        changes.append(f"feedback→{feedback}")
    elif fields:
        backend.update(learning_id, **fields)

    if not changes:
        return {"learning_id": learning_id, "status": "no_changes"}

    logger.info("memory_update_learning", learning_id=learning_id, changes=changes)
    return {
        "learning_id": learning_id,
        "changes": ", ".join(changes),
        "status": "updated",
    }
