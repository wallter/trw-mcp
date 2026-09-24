"""``trw-mcp doctor`` row for the resolved memory store (PRD-CORE-280 FR05).

Kept out of ``_subcommands_doctor.py`` as a sibling, the same shape
``_doctor_memory_wal`` and ``_doctor_memory_daemon`` use. The row resolves
through ``selected_store``, the one place trw-mcp reaches memory, so it reports
the checkout's daemon namespace; an unpinned checkout fails with the command that
fixes it.

The project ``memory.db`` itself is read by :func:`probe_project_db` alone, over a
``mode=ro`` connection, for the split-store WARN's count of rows left behind after
migration. Doctor never opens it through ``SQLiteBackend``, which writes.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path


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


# Real learning rows of one namespace: a canary decoy (``metadata.system_canary``) is not one.
_REAL_ROWS_SQL = (
    "SELECT COUNT(*) FROM memories WHERE namespace = ? "
    "AND NOT (json_valid(metadata) AND COALESCE(json_extract(metadata, '$.system_canary'), '') = 'true')"
)


class ProjectDbSchemaError(Exception):
    """The file opened but holds no ``memories`` table; *uninitialized* when it holds no tables at all."""

    def __init__(self, message: str, *, uninitialized: bool) -> None:
        super().__init__(message)
        self.uninitialized = uninitialized


def probe_project_db(db_path: Path, namespace: str) -> int:
    """Real rows in *namespace* of an existing ``memory.db``, read-only.

    A ``mode=ro`` URI connection, never ``SQLiteBackend``: its constructor opens a
    writable connection, sets WAL and creates schema, so it would write during a doctor run.
    With no ``-wal`` beside it the file is at rest, so it is also opened
    ``immutable=1``: ``mode=ro`` alone still creates ``-wal``/``-shm`` on a WAL
    database. With a live writer's ``-wal`` present, plain ``mode=ro`` reads it,
    so the count includes rows not yet checkpointed. Raises
    ``sqlite3.DatabaseError`` on a file that is not a readable database, and
    :class:`ProjectDbSchemaError` when it has no ``memories`` table.
    """
    at_rest = not db_path.with_name(db_path.name + "-wal").exists()
    conn = sqlite3.connect(f"{db_path.resolve().as_uri()}?mode=ro{'&immutable=1' if at_rest else ''}", uri=True)
    try:
        tables = sorted(row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'"))
        if not tables:
            raise ProjectDbSchemaError(f"{db_path} is uninitialized (no tables)", uninitialized=True)
        if "memories" not in tables:
            raise ProjectDbSchemaError(
                f"{db_path} is not a TRW memory database (no memories table; tables: {', '.join(tables[:5])})",
                uninitialized=False,
            )
        return int(conn.execute(_REAL_ROWS_SQL, (namespace,)).fetchone()[0])
    finally:
        conn.close()


def _stray_rows(db_path: Path) -> int:
    """Rows left in the project ``memory.db`` after migration; an absent or never-initialized file holds none."""
    from trw_mcp.state._constants import DEFAULT_NAMESPACE

    if not db_path.exists():
        return 0
    try:
        return probe_project_db(db_path, DEFAULT_NAMESPACE)
    except ProjectDbSchemaError as exc:
        if exc.uninitialized:
            return 0
        raise


def memory_backend_row(target: Path) -> tuple[str, str]:
    """``(status, message)`` for the ``memory_backend`` doctor row."""
    from trw_mcp.state._store_selection import StoreUnavailableError, selected_store

    trw_dir = target / ".trw"
    try:
        store, namespace = selected_store(trw_dir)
        count = store.count(namespace)
        stray = _stray_rows(trw_dir / "memory" / "memory.db")
    except ProjectDbSchemaError as exc:
        return ("WARN" if exc.uninitialized else "FAIL"), str(exc)
    except (StoreUnavailableError, sqlite3.DatabaseError) as exc:
        return "FAIL", str(exc)

    replay, readable = _replay_state(trw_dir)
    message = f"store=daemon namespace={namespace} ({count} entries){replay}"
    if stray:
        return "WARN", (
            f"{message}; split store: {stray} row(s) remain in the project memory.db — "
            "run `trw-mcp memory migrate --to user` to merge them."
        )
    return ("PASS" if readable else "WARN"), message
