"""The quarantine check the learning publisher makes before each row leaves the host (PRD-CORE-333).

Belongs to ``telemetry/publisher.py``. The publisher reads the YAML mirror directly, not through a backend read,
so the read-layer filter never saw its rows (CORE-333-PUBLISHER-BYPASS). Each row is checked by trw-memory's
send-time gate with the full identity the ledger keys on: its id in this checkout's pinned namespace, its source
and remote ids, and its content hash. The ledger is daemon-wide (trw-memory's default security paths, as
``state/_daemon_store.py`` compares them).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path

#: Called with a mirror entry's id and its YAML mapping, right before its POST; True when it must not be sent.
QuarantineCheck = Callable[[str, Mapping[str, object]], bool]


def quarantine_check(trw_dir: Path) -> QuarantineCheck | None:
    """The per-row check for this project's mirror, or ``None`` when no row can be proven clean (publish nothing).

    The id key is namespace-qualified and resolved as the store resolves it (``_store_selection``): an unreadable
    pin publishes nothing; an unpinned linked worktree uses its main checkout's pin; any other unpinned checkout has
    no store, so no ledger decision can name its rows by id, and only the namespace-free keys are checked.
    """
    from trw_memory.security.egress_gate import egress_refused
    from trw_memory.security.provenance import entry_content_hash

    from trw_mcp.exceptions import StateError
    from trw_mcp.state._namespace_pin_read import PinUnreadableError, pinned_namespace
    from trw_mcp.state._store_selection import StoreUnavailableError, _main_checkout_pin

    try:
        namespace = pinned_namespace(trw_dir)
    except (PinUnreadableError, StateError):  # trw-fail-silent-allow: fail closed; the publisher logs and sends nothing
        return None
    if not namespace:
        try:
            _grant, namespace = _main_checkout_pin(trw_dir)
        except StoreUnavailableError:
            namespace = ""

    def blocks(entry_id: str, data: Mapping[str, object]) -> bool:
        # The keys ``entry_identity_keys`` gives the stored row: id (namespace-qualified), source and remote ids,
        # content hash -- built directly, so no MemoryEntry is constructed outside trw-memory's entry factory.
        keys: list[tuple[str, ...]] = [("id", namespace, entry_id)] if namespace else []
        keys += [("src", value) for field in ("source_learning_id", "remote_id") if (value := _text(data, field))]
        keys.append(("hash", entry_content_hash(str(data.get("summary", "")), str(data.get("detail", "")))))
        return egress_refused(keys) is not None  # blocked, or the ledger is unreadable: never sent

    return blocks


def _text(data: Mapping[str, object], field: str) -> str:
    value = data.get(field)
    return value if isinstance(value, str) else ""
