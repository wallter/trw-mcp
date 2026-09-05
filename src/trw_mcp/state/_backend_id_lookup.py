"""Resolve a learning id against a backend whose namespace the caller does not know.

PRD-CORE-245 FR03 made ``StorageBackend.get`` require a namespace: under schema
5 a memory row is identified by ``(namespace, id)``, so a bare id addresses
nothing in particular. Most trw-mcp call sites do know their namespace and pass
it directly.

A handful genuinely do not, and they are all the same shape: a learning id
arrives from outside — a recall result, a ``trw_learn`` update, an ownership
probe across the project and user tiers — and the question being asked is "does
THIS STORE hold this id at all". This module answers that question explicitly,
by trying each namespace the store actually contains, rather than by
reintroducing an unscoped read that would answer it for whichever namespace
happened to sort first.

The distinction matters: an unscoped ``WHERE id = ?`` silently picks a row; this
enumerates and returns the first match with its namespace attached, so the
caller can qualify the write that follows.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog

from trw_mcp.exceptions import NamespaceEnumerationError
from trw_mcp.state._constants import DEFAULT_NAMESPACE

if TYPE_CHECKING:
    from trw_memory.models.memory import MemoryEntry
    from trw_memory.storage.interface import StorageBackend

logger = structlog.get_logger(__name__)

__all__ = ["resolve_entry_in_backend"]


def resolve_entry_in_backend(
    backend: StorageBackend,
    entry_id: str,
    *,
    namespace: str | None = None,
) -> MemoryEntry | None:
    """Return the entry *entry_id* names in *backend*, or ``None``.

    When *namespace* is given this is exactly ``backend.get(entry_id,
    namespace=namespace)``. When it is ``None`` — the federated case, where the
    caller is asking which store owns an id rather than which namespace — every
    namespace present in the store is tried in a stable order and the first
    match is returned.

    Never raises for a missing namespace table or an empty store: ``None`` means
    the store was enumerated and does not hold *entry_id*.

    Raises:
        NamespaceEnumerationError: when the backend could not list its
            namespaces AND the unnamed namespace does not hold the id. The
            enumeration failure is not fatal on its own — the unnamed namespace
            is still probed, because one definite attempt beats a give-up — but
            a MISS across an incomplete enumeration is not absence. Returning
            ``None`` there reported rows in the project or user namespace as
            ``not_found``, a confident denial the store never supported.
    """
    if namespace is not None:
        return backend.get(entry_id, namespace=namespace)
    try:
        namespaces = sorted(backend.list_namespaces())
    except Exception as exc:  # justified: one definite attempt, then a typed failure
        logger.warning("namespace_enumeration_failed", entry_id=entry_id, exc_info=True)
        entry = backend.get(entry_id, namespace=DEFAULT_NAMESPACE)
        if entry is not None:
            return entry
        raise NamespaceEnumerationError(
            f"could not enumerate namespaces ({type(exc).__name__}); "
            f"{entry_id!r} is absent from {DEFAULT_NAMESPACE!r} but may exist elsewhere"
        ) from exc
    for candidate in namespaces:
        entry = backend.get(entry_id, namespace=candidate)
        if entry is not None:
            return entry
    return None
