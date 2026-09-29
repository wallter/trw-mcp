"""``trw-mcp doctor`` row for the resolved memory store (PRD-CORE-280 FR05).

Kept out of ``_subcommands_doctor.py`` as a sibling, the same shape
``_doctor_memory_wal`` and ``_doctor_memory_daemon`` use. The row resolves
through ``selected_store``, the one place trw-mcp reaches memory, so it reports
the checkout's daemon namespace; an unpinned checkout fails with the command that
fixes it.

The project ``memory.db`` is a file a checkout can commit, so its count of rows
left behind after migration comes from trw-memory's ``probe_store`` alone
(PRD-QUAL-147): bounded, read-only, never ``SQLiteBackend``, which writes.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from trw_mcp.models.config import TRWConfig

#: The ``probe_store`` states (``StoreState`` values) that fail the row, and each one's message.
_FAILS = {
    "not_trw": "{db} is not a TRW memory database (no memories table; tables: {tables})",
    "refused": "{db} was refused unread: its schema object(s) {detail} could run unbounded work on "
    "every read (a generated column, view, trigger or expression index); inspect the file, then move it aside",
    "unreadable": "{db} could not be read: {detail}",
}


def _replay_state(trw_dir: Path) -> tuple[str, bool]:
    """``(message suffix, readable)`` for ``sync-state.json``'s full-pull replay state."""
    state = trw_dir / "sync-state.json"
    if not state.exists():
        return "", True
    try:
        data = json.loads(state.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:  # trw-fail-silent-allow: reported as a WARN in the doctor row
        return f"; sync-state.json is unreadable ({exc})", False
    return ("; a sync replay is in progress" if isinstance(data, dict) and data.get("replay") else ""), True


def memory_backend_row(target: Path) -> tuple[str, str]:
    """``(status, message)`` for the ``memory_backend`` doctor row."""
    from trw_memory.storage import probe_store

    from trw_mcp.state._constants import DEFAULT_NAMESPACE
    from trw_mcp.state._store_selection import StoreUnavailableError, selected_store

    trw_dir = target / ".trw"
    try:
        store, namespace = selected_store(trw_dir)
        count = store.count(namespace)
    except StoreUnavailableError as exc:
        return "FAIL", str(exc)
    db = trw_dir / "memory" / "memory.db"
    probe = probe_store(db, DEFAULT_NAMESPACE)  # ABSENT and UNINITIALIZED hold no strays
    if probe.state in _FAILS:
        return "FAIL", _FAILS[probe.state].format(db=db, tables=", ".join(probe.tables), detail=probe.detail)

    replay, readable = _replay_state(trw_dir)
    message = f"store=daemon namespace={namespace} ({count} entries){replay}"
    if probe.real_rows:
        return "WARN", (
            f"{message}; split store: {probe.real_rows} row(s) remain in the project memory.db — "
            "run `trw-mcp memory migrate --to user` to merge them."
        )
    return ("PASS" if readable else "WARN"), message


def memory_ledger_row(target: Path, config: TRWConfig) -> tuple[str, str]:
    """``(status, message)`` for ``memory-ledger-sample`` (PRD-CORE-334 FR04): the namespace's exact decision count.

    Advisory: PASS, or SKIP when the store cannot be read. With decisions and a sync target it adds the
    rollout reminder, because a client older than 8.0 cannot take a decision row.
    """
    from trw_mcp.state._store_selection import StoreUnavailableError, selected_store

    try:
        store, namespace = selected_store(target / ".trw")
        decisions = store.health(namespace).get("types", {}).get("decision", 0)
    except StoreUnavailableError as exc:
        return "SKIP", f"decision ledger not read: {exc}"
    message = (
        f"{decisions} decision row(s) in namespace={namespace}; list them with "
        "trw_recall(query='*', options={'record_type': 'decision'})"
    )
    if decisions and config.resolved_sync_targets:
        message += "; sync is configured: every client sharing this team/sync space must run trw-mcp 8.0 or later"
    return "PASS", message
