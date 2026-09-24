"""``trw_learn``'s update mode: change an existing learning in place.

Belongs to the ``learning.py`` facade. The tool body resolves its collaborators
from ``learning``'s namespace (``adapter_update``, ``resolve_trw_dir`` ...) and
passes them in, so suites that patch ``trw_mcp.tools.learning.*`` keep seeing
their patch.

PARTIAL-UPDATE SENTINEL: every field defaults to ``None`` and the adapter reads
``None`` as "the caller did not ask me to touch this". Do not "helpfully"
default a missing key to an empty value; that would silently clear data the
caller never mentioned. Clearing stays explicit: ``""`` or ``[]``.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

from trw_mcp.tools._learn_arg_bags import LearnUpdateFields
from trw_mcp.tools._learning_module_helpers import (
    _coerce_learn_type,
    _coerce_tags,
    _sync_learning_yaml_backup,
)

if TYPE_CHECKING:
    from trw_mcp.models.config import TRWConfig
    from trw_mcp.state.persistence import FileStateWriter

__all__ = ["execute_learn_update"]

logger = structlog.get_logger(__name__)


def execute_learn_update(
    *,
    trw_dir: Path,
    config: TRWConfig,
    writer: FileStateWriter,
    adapter_update: Callable[..., dict[str, str]],
    project_root: Callable[[], Path],
    learning_id: str,
    status: str | None,
    summary: str | None,
    detail: str | None,
    impact: float | None,
    tags: list[str] | str | None,
    type: str | None,
    confidence: str | None,
    upd: LearnUpdateFields,
) -> dict[str, str]:
    """Apply one partial update; return ``{status, learning_id, changes}`` or ``{error, status}``."""
    # Only the string shape is coerced: an update REPLACES the tag set, so a list
    # with a stray non-string element must still fail validation loudly.
    if isinstance(tags, str):
        tags = _coerce_tags(tags)
    if type is not None:
        type = _coerce_learn_type(type)
    fields: dict[str, object] = {
        "status": status,
        "summary": summary,
        "detail": detail,
        "impact": impact,
        "tags": tags,
        "type": type,
        "confidence": confidence,
        **upd.model_dump(exclude={"reverify_anchors"}, exclude_none=True),
    }
    # Validate before any side effect: a rejected patch must not re-verify anchors.
    # trw-memory owns the patch contract (PRD-CORE-294 FR03); the adapter re-parses it.
    from trw_memory.lifecycle.correction import parse_patch

    patch = parse_patch(fields)
    if isinstance(patch, dict):
        return patch

    # Refresh anchor validity against the current tree BEFORE any other field update.
    if upd.reverify_anchors:
        from trw_mcp.tools._learn_anchors import reverify_entry_anchors

        reverify_entry_anchors(trw_dir, project_root(), learning_id)

    result = adapter_update(trw_dir, learning_id=learning_id, **fields)

    # Dual-write: also update the YAML backup for rollback safety.
    if result.get("status") == "updated":
        logger.info("learn_update_ok", id=learning_id, changes=result.get("changes", ""))
        backup = patch.model_dump(mode="json", exclude={"supersedes", "tags_add"})
        if patch.tags_add:
            # The merged set exists only in the store (the append ran in its transaction).
            from trw_mcp.exceptions import NamespaceEnumerationError
            from trw_mcp.state._memory_update import stored_tags

            try:
                backup["tags"] = stored_tags(trw_dir, learning_id)
            except NamespaceEnumerationError:
                # The store update already succeeded; only the rollback copy's tags go stale.
                logger.warning("learn_update_backup_tags_unavailable", id=learning_id, exc_info=True)
        _sync_learning_yaml_backup(trw_dir, config, writer, learning_id, backup)
    return result
