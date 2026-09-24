"""``trw-mcp doctor`` reads the memory store, not a raw backend (PRD-CORE-280 FR05).

Exercises ``memory_backend_row`` directly (the function ``_check_memory_backend``
delegates to) rather than the full ``_doctor_core`` catalogue: a daemon may not
be available in tests, so the "migrated" branch is built by monkeypatching
``selected_store`` to hand back a :class:`FakeMemoryStore` under a pinned
namespace, the same fixture ``tests._memory_store_fake`` and
``tests/test_store_contract.py`` already use for the daemon-free cases.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from trw_memory.models.memory import MemoryEntry
from trw_memory.storage.sqlite_backend import SQLiteBackend

from tests._memory_store_fake import FakeMemoryStore
from trw_mcp.server._doctor_memory_store import memory_backend_row
from trw_mcp.state._tier_routing import USER_NAMESPACE


def _pin(target: Path, store: FakeMemoryStore, namespace: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """Make ``selected_store(target / ".trw")`` resolve to *store* under *namespace*."""
    monkeypatch.setattr(
        "trw_mcp.state._store_selection.selected_store",
        lambda trw_dir: (store, namespace),
    )


def _write_stray_project_row(target: Path, *more: str) -> SQLiteBackend:
    """A real row (plus *more*, and one canary decoy) left in the project's own memory.db.

    The writer is returned open, so its rows sit in ``-wal`` until the caller closes it.
    """
    (target / ".trw" / "memory").mkdir(parents=True, exist_ok=True)
    backend = SQLiteBackend(target / ".trw" / "memory" / "memory.db")
    backend.store(MemoryEntry(id="C-decoy", content="decoy", namespace="default", metadata={"system_canary": "true"}))
    for entry_id in ("L-stray1", *more):
        backend.store(MemoryEntry(id=entry_id, content=f"Stray row {entry_id}", namespace="default"))
    return backend


def test_a_migrated_checkout_reports_store_namespace_and_count(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / ".trw").mkdir()
    store = FakeMemoryStore()
    store.put("Migrated learning", "project:demo", {"entry_id": "L-m1"})
    store.put("Migrated learning two", "project:demo", {"entry_id": "L-m2"})
    _pin(tmp_path, store, "project:demo", monkeypatch)

    status, message = memory_backend_row(tmp_path)

    assert status == "PASS"
    assert "store=daemon" in message
    assert "namespace=project:demo" in message
    assert "2 entries" in message
    assert "split store" not in message


def test_replay_in_progress_is_named_in_the_message(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    trw_dir = tmp_path / ".trw"
    trw_dir.mkdir()
    (trw_dir / "sync-state.json").write_text(json.dumps({"replay": {"next_seq": 12}}), encoding="utf-8")
    store = FakeMemoryStore()
    _pin(tmp_path, store, "project:demo", monkeypatch)

    status, message = memory_backend_row(tmp_path)

    assert status == "PASS"
    assert "a sync replay is in progress" in message


def test_a_pinned_checkout_with_stray_project_rows_warns_split_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / ".trw").mkdir()
    _write_stray_project_row(tmp_path).close()
    store = FakeMemoryStore()
    store.put("Migrated learning", "project:demo", {"entry_id": "L-m3"})
    _pin(tmp_path, store, "project:demo", monkeypatch)

    status, message = memory_backend_row(tmp_path)

    assert status == "WARN"
    assert "split store" in message
    assert "trw-mcp memory migrate --to user" in message


def test_the_split_store_count_excludes_canary_decoys(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A canary decoy in ``default`` is not counted; only the one real stray row is."""
    (tmp_path / ".trw").mkdir()
    _write_stray_project_row(tmp_path).close()
    _pin(tmp_path, FakeMemoryStore(), "project:demo", monkeypatch)

    _status, message = memory_backend_row(tmp_path)

    assert "split store: 1 row(s)" in message


def test_a_project_db_holding_only_canary_decoys_never_warns_split_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:

    (tmp_path / ".trw").mkdir()
    backend = _write_stray_project_row(tmp_path)
    try:
        assert backend.delete("L-stray1", namespace="default")
        assert backend.count(namespace="default") > 0, "the canary decoys are still there"
    finally:
        backend.close()
    _pin(tmp_path, FakeMemoryStore(), "project:demo", monkeypatch)

    status, message = memory_backend_row(tmp_path)

    assert (status, "split store" in message) == ("PASS", False)


def test_an_unreadable_sync_state_warns_instead_of_passing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    trw_dir = tmp_path / ".trw"
    trw_dir.mkdir()
    (trw_dir / "sync-state.json").write_text("{not json", encoding="utf-8")
    _pin(tmp_path, FakeMemoryStore(), "project:demo", monkeypatch)

    status, message = memory_backend_row(tmp_path)

    assert status == "WARN"
    assert "sync-state.json is unreadable" in message


def test_a_pinned_checkout_with_no_project_db_never_warns_split_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / ".trw").mkdir()
    store = FakeMemoryStore()
    store.put("Migrated learning", "project:demo", {"entry_id": "L-m4"})
    _pin(tmp_path, store, "project:demo", monkeypatch)

    status, message = memory_backend_row(tmp_path)

    assert status == "PASS"
    assert "split store" not in message


def test_a_pinned_checkout_with_an_empty_project_db_never_warns_split_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / ".trw").mkdir()
    monkeypatch.setenv("HOME", str(tmp_path / ".home"))
    for key in ("TRW_DEDUP_ENABLED", "TRW_EMBEDDINGS_ENABLED"):
        monkeypatch.setenv(key, "false")
    # Present but empty: a 0-byte memory.db, no rows.
    memory_dir = tmp_path / ".trw" / "memory"
    memory_dir.mkdir(parents=True)
    (memory_dir / "memory.db").write_bytes(b"")

    store = FakeMemoryStore()
    store.put("Migrated learning", "project:demo", {"entry_id": "L-m5"})
    _pin(tmp_path, store, "project:demo", monkeypatch)

    status, message = memory_backend_row(tmp_path)

    assert status == "PASS"
    assert "split store" not in message


def test_a_daemon_store_error_fails_the_row_naming_doctor(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / ".trw").mkdir()
    from trw_mcp.state._store_selection import StoreUnavailableError

    def _raise(_trw_dir: Path) -> tuple[FakeMemoryStore, str]:
        raise StoreUnavailableError("daemon unreachable; run trw-mcp doctor")

    monkeypatch.setattr("trw_mcp.state._store_selection.selected_store", _raise)

    status, message = memory_backend_row(tmp_path)

    assert status == "FAIL"
    assert "trw-mcp doctor" in message


def test_user_namespace_rows_do_not_appear_in_the_project_namespace_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / ".trw").mkdir()
    store = FakeMemoryStore()
    store.put("Project row", "project:demo", {"entry_id": "L-m6"})
    store.put("User row", USER_NAMESPACE, {"entry_id": "L-m7"})
    _pin(tmp_path, store, "project:demo", monkeypatch)

    status, message = memory_backend_row(tmp_path)

    assert status == "PASS"
    assert "1 entries" in message


def _files(db: Path) -> dict[str, tuple[bytes, int] | None]:
    """Bytes and mtime of the db and its -wal/-shm siblings (None when absent)."""
    out: dict[str, tuple[bytes, int] | None] = {}
    for suffix in ("", "-wal", "-shm"):
        path = db.with_name(db.name + suffix)
        out[suffix] = (path.read_bytes(), path.stat().st_mtime_ns) if path.exists() else None
    return out


def test_doctor_leaves_the_project_db_and_its_wal_files_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Round 1 P0: doctor reads the project memory.db read-only -- no WAL switch, schema, recovery or migration."""
    (tmp_path / ".trw").mkdir()
    _write_stray_project_row(tmp_path).close()  # the files are at rest
    db = tmp_path / ".trw" / "memory" / "memory.db"
    before = _files(db)
    _pin(tmp_path, FakeMemoryStore(), "project:demo", monkeypatch)

    memory_backend_row(tmp_path)

    assert _files(db) == before


def test_the_split_store_count_is_not_capped_by_the_list_limit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Round 1 P1: a scoped SQL count, so rows older than any list window still count."""
    (tmp_path / ".trw").mkdir()
    _write_stray_project_row(tmp_path, "L-old0", "L-old1").close()
    monkeypatch.setattr("trw_mcp.state._constants.DEFAULT_LIST_LIMIT", 1)
    _pin(tmp_path, FakeMemoryStore(), "project:demo", monkeypatch)

    _status, message = memory_backend_row(tmp_path)

    assert "split store: 3 row(s)" in message


def test_with_a_live_writer_doctor_reads_the_wal_and_writes_neither_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A writer still holds rows in -wal: the count sees them, and db and -wal bytes are untouched.

    ``-shm`` is the writer's lock index and is excluded: the live writer owns it.
    """
    (tmp_path / ".trw").mkdir()
    writer = _write_stray_project_row(tmp_path)  # the writer stays open: rows sit in -wal
    db = tmp_path / ".trw" / "memory" / "memory.db"
    assert db.with_name("memory.db-wal").exists()
    before = {k: v for k, v in _files(db).items() if k != "-shm"}
    _pin(tmp_path, FakeMemoryStore(), "project:demo", monkeypatch)
    try:
        _status, message = memory_backend_row(tmp_path)

        assert "split store: 1 row(s)" in message
        assert {k: v for k, v in _files(db).items() if k != "-shm"} == before
    finally:
        writer.close()


def test_doctor_never_initialises_a_project_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A 0-byte memory.db stays 0 bytes: opening it through ``SQLiteBackend`` would write the schema and WAL."""
    db = tmp_path / ".trw" / "memory" / "memory.db"
    db.parent.mkdir(parents=True)
    db.write_bytes(b"")
    before = _files(db)
    _pin(tmp_path, FakeMemoryStore(), "project:demo", monkeypatch)

    status, message = memory_backend_row(tmp_path)

    assert _files(db) == before
    # The old project db of a pinned checkout, holding nothing stray.
    assert (status, "uninitialized" in message) == ("PASS", False)


def test_a_project_db_without_a_memories_table_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import sqlite3

    db = tmp_path / ".trw" / "memory" / "memory.db"
    db.parent.mkdir(parents=True)
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE something_else (x INTEGER)")
    conn.commit()
    conn.close()
    _pin(tmp_path, FakeMemoryStore(), "project:demo", monkeypatch)

    status, message = memory_backend_row(tmp_path)

    assert status == "FAIL"
    assert "not a TRW memory database" in message
