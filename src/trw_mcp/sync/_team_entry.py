"""Lift a pulled team learning into a local :class:`MemoryEntry`.

Belongs to the ``pull.py`` facade; called from ``merge_team_learnings``.
Extracted by PRD-CORE-245 when threading the pull target's namespace through the
merge pushed ``pull.py`` past the 350 effective-LOC gate.

The contract worth stating: this is a **deserialiser of remote state**. It builds
through the shared entry factory so the namespace is stated rather than left to
the model default (FR08), and then restores the peer's ``vector_clock``
verbatim — stamping a local clock over a peer's causality is the same corruption
FR08 exists to prevent, only inverted.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    from trw_memory.models.memory import MemoryEntry

    from trw_mcp.sync.pull import SyncPuller

logger = structlog.get_logger(__name__)

__all__ = ["team_learning_to_entry"]


def _local_node_id() -> str:
    """This installation's vector-clock node id.

    The factory requires one; it is immediately overwritten with the PEER's
    clock below, because a remote payload's causality is the only correct value
    here. So this identifies the row's local container, not who edited it.
    """
    from trw_memory.models.config import MemoryConfig
    from trw_memory.models.entry_factory import local_node_id_for

    return local_node_id_for(MemoryConfig().storage_path)


def team_learning_to_entry(
    puller: SyncPuller,
    raw_learning: dict[str, Any],
    *,
    local_id: str,
    namespace: str,
) -> MemoryEntry | None:
    """Build the entry for one pulled learning, addressed at *namespace*.

    Returns ``None`` for a payload that cannot be read: a malformed remote item
    must fail open for itself without aborting the whole merge.
    """
    from trw_memory.models.entry_factory import new_entry
    from trw_memory.models.memory import MemoryStatus, MemoryType

    from trw_mcp.sync.pull import _resolve_sync_source

    try:
        metadata = {str(key): str(value) for key, value in dict(raw_learning.get("metadata") or {}).items()}
        pull_seq = raw_learning.get("sync_seq")
        if pull_seq is not None:
            metadata["team_sync_pull_seq"] = str(pull_seq)

        # PRD-INFRA-139 FR06: the server tags company-tier learnings with
        # source=company_sync in metadata; team learnings carry no source tag.
        # Preserve the distinction locally while merging via the same path.
        sync_source = _resolve_sync_source(metadata)

        entry = new_entry(
            entry_id=local_id,
            content=str(raw_learning.get("summary", "")),
            namespace=namespace,
            local_node_id=_local_node_id(),
            fields={
                "remote_id": str(raw_learning.get("source_learning_id", "")).strip() or None,
                "detail": str(raw_learning.get("detail", "")),
                "tags": [str(tag) for tag in raw_learning.get("tags", []) if isinstance(tag, str)],
                "importance": puller._coerce_importance(raw_learning.get("impact")),
                "status": MemoryStatus(str(raw_learning.get("status", "active"))),
                "type": MemoryType(str(raw_learning.get("type", "pattern"))),
                "source": sync_source,
                "source_identity": sync_source,
                "client_profile": sync_source,
                "metadata": metadata,
            },
        )
        # The peer's clock, verbatim — see the module docstring.
        return entry.model_copy(update={"vector_clock": puller._coerce_vector_clock(raw_learning.get("vector_clock"))})
    except Exception:  # justified: boundary, a malformed remote payload fails open for that entry
        logger.warning(
            "sync_team_merge_invalid_entry",
            event_type="sync_team_merge",
            outcome="error",
            source_learning_id=str(raw_learning.get("source_learning_id", "")),
            exc_info=True,
        )
        return None
