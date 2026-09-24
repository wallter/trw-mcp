"""Memory adapter — ``update_learning`` over the checkout's store and trw-memory's correction.

Belongs to the ``memory_adapter.py`` facade. Re-exported there for back-compat.

Extracted as DIST-243 batch 59 to keep the parent module under the 350-LOC gate.
"""

from __future__ import annotations

from pathlib import Path

import structlog

from trw_mcp.exceptions import NamespaceEnumerationError
from trw_mcp.state import _store_selection

logger = structlog.get_logger(__name__)


def update_learning(trw_dir: Path, learning_id: str, **fields: object) -> dict[str, str]:
    """Correct a learning by id in whichever namespace owns it (PRD-CORE-294 FR03, PRD-CORE-280 FR01).

    The checkout's store finds the owning namespace (project first, then the
    user store when one is present -- PRD-CORE-185) and applies the patch.
    Validation, patch semantics, the verified-promotion gate, supersession and
    the tier-mirror refresh live in ``trw_memory.lifecycle.correction.apply_correction``,
    which ``memory_update`` also calls. Return shape matches trw_learn's update
    mode: ``{"learning_id", "changes", "status"}`` or ``{"error", "status"}``.
    """
    from trw_memory.lifecycle.correction import parse_patch

    patch = parse_patch(fields)
    if isinstance(patch, dict):
        return patch
    store, _ = _store_selection.selected_store(trw_dir)
    try:
        return store.correct(learning_id, patch)
    except NamespaceEnumerationError as exc:
        # "we could not look everywhere" is not "it is not there". Reporting it
        # as not_found would tell the agent its learning is gone and invite a
        # duplicate write into a store that already holds the row.
        logger.warning("update_learning_lookup_unavailable", learning_id=learning_id, exc_info=True)
        return {
            "error": f"Could not determine whether {learning_id} exists: {exc}",
            "status": "lookup_unavailable",
        }


def stored_tags(trw_dir: Path, learning_id: str) -> list[str] | None:
    """The tag set the owning store now holds for ``learning_id``, or None when it has no such row.

    A ``tags_add`` merges inside the store's transaction, so only the store knows the
    resulting set; the YAML backup copies it from here rather than guessing.

    Raises:
        NamespaceEnumerationError: the stores could not be searched.
    """
    store, _ = _store_selection.selected_store(trw_dir)
    entry = store.get(learning_id)
    return None if entry is None else list(entry.tags)
