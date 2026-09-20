"""PRD-CORE-274-NFR02/NFR03 initialization, publication, and corruption controls."""

from __future__ import annotations

import multiprocessing as mp
import os
import stat
from pathlib import Path
from typing import Any

import pytest

from trw_mcp.comms import _store


@pytest.mark.parametrize("damage", ["garbage", "missing_table", "extra_trigger", "group_clock", "orphan"])
def test_corrupt_existing_bytes_preserved_before_pragma_writes(tmp_path: Path, damage: str) -> None:
    manifest = tmp_path / "formation.yaml"
    with _store.connect(manifest, busy_timeout_ms=20):
        pass
    path = _store.database_path(manifest)
    if damage == "garbage":
        path.write_bytes(b"damaged preexisting mailbox\x00\xff")
    else:
        with _store.sqlite3.connect(path) as connection:
            if damage == "missing_table":
                connection.execute("DROP TABLE endpoints")
            elif damage == "extra_trigger":
                connection.execute("CREATE TRIGGER strange AFTER INSERT ON groups BEGIN SELECT 1; END")
            elif damage == "group_clock":
                connection.execute(
                    "INSERT INTO groups VALUES (?, 'f', ?, 100, 1, 0, 256, 8192, 64, 32, 0, 16777216)",
                    ("a" * 32, str(manifest)),
                )
            else:
                connection.execute(
                    "INSERT INTO endpoints VALUES (?, 'm', ?, 's', ?, 1, 2, 3, 1, 4)",
                    ("b" * 32, "c" * 32, str(tmp_path / "run")),
                )
    before = path.read_bytes()
    with pytest.raises(_store.StoreError) as caught:
        with _store.connect(manifest, busy_timeout_ms=20):
            pytest.fail("damaged evidence was accepted")
    assert caught.value.refusal is _store.StoreRefusal.CORRUPT
    assert path.read_bytes() == before


def test_unavailable_hardlink_refuses_without_canonical_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manifest = tmp_path / "formation.yaml"
    reached = []

    def unavailable(*args: Any, **kwargs: Any) -> None:
        reached.append(True)
        raise OSError("filesystem does not support links")

    monkeypatch.setattr(_store.os, "link", unavailable)
    with pytest.raises(_store.StoreError) as caught:
        with _store.connect(manifest, busy_timeout_ms=20):
            pass
    assert reached == [True]
    assert caught.value.refusal is _store.StoreRefusal.UNAVAILABLE
    assert not _store.database_path(manifest).exists()
    assert not list(tmp_path.glob(".comms-initializing-*"))


@pytest.mark.parametrize("fail_cleanup", [False, True])
def test_failed_directory_sync_keeps_published_canonical(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fail_cleanup: bool
) -> None:
    manifest = tmp_path / "formation.yaml"
    real_fsync = _store.os.fsync
    reached = []

    def failed_directory_sync(fd: int) -> None:
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            reached.append(True)
            raise OSError("directory sync failed")
        real_fsync(fd)

    monkeypatch.setattr(_store.os, "fsync", failed_directory_sync)
    real_unlink = Path.unlink
    cleanup_reached = []

    def failed_cleanup(path: Path, missing_ok: bool = False) -> None:
        if path.name.startswith(".comms-initializing-"):
            cleanup_reached.append(True)
            raise OSError("staging cleanup failed after publication")
        real_unlink(path, missing_ok=missing_ok)

    if fail_cleanup:
        monkeypatch.setattr(Path, "unlink", failed_cleanup)
    with pytest.raises(_store.StoreError) as caught:
        with _store.connect(manifest, busy_timeout_ms=20):
            pass
    assert reached == [True]
    assert caught.value.refusal is _store.StoreRefusal.PUBLISH_UNCERTAIN
    path = _store.database_path(manifest)
    before = path.read_bytes()
    assert before
    if fail_cleanup:
        assert cleanup_reached == [True], "combined-fault control did not reach cleanup"
        assert isinstance(caught.value.__cause__, OSError)
        assert len(list(tmp_path.glob(".comms-initializing-*"))) == 1
    else:
        assert not list(tmp_path.glob(".comms-initializing-*"))
    # Subsequent lookup reconciles the already-published DB; it does not recreate it.
    with _store.connect(manifest, busy_timeout_ms=20) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert path.read_bytes() == before


def test_two_ready_publishers_cannot_overwrite_each_other(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    context = mp.get_context("fork")
    barrier, outcomes = context.Barrier(2), context.Queue()
    original_link = os.link
    manifest = tmp_path / "formation.yaml"

    def gated_link(source: Any, destination: Any, **kwargs: Any) -> None:
        # Both staging DBs are complete and closed before either publication.
        with _store.sqlite3.connect(source) as connection:
            assert connection.execute("SELECT value FROM schema_meta").fetchone()[0] == str(_store.SCHEMA_VERSION)
        barrier.wait(timeout=3)
        try:
            original_link(source, destination, **kwargs)
        except FileExistsError:
            outcomes.put("lost_publication")
            raise
        outcomes.put("won_publication")

    monkeypatch.setattr(_store.os, "link", gated_link)

    def worker() -> None:
        try:
            with _store.connect(manifest, busy_timeout_ms=1000) as connection:
                assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            outcomes.put("opened")
        except Exception as exc:
            outcomes.put(type(exc).__name__ + ":" + str(exc))

    children = [context.Process(target=worker) for _ in range(2)]
    for child in children:
        child.start()
    try:
        observed = [outcomes.get(timeout=5) for _ in range(4)]
        print(f"gated_publication_outcomes={observed}")
        assert sorted(observed) == ["lost_publication", "opened", "opened", "won_publication"]
    finally:
        for child in children:
            child.join(timeout=3)
            if child.is_alive():
                child.terminate()
                child.join(timeout=2)
        outcomes.close()
        outcomes.join_thread()
    assert all(child.exitcode == 0 for child in children)
    assert not list(tmp_path.glob(".comms-initializing-*"))


def test_mid_ddl_rollback_verified_inside_staging_before_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dbapi, real_connect = _store.sqlite3, _store.sqlite3.connect
    creates, rollback_tables = [], []

    class Interrupted(dbapi.Connection):
        def execute(self, sql: str, parameters: Any = ()) -> Any:
            if sql.lstrip().upper().startswith("CREATE TABLE"):
                creates.append(sql)
                if len(creates) == 2:
                    assert self.in_transaction, "DDL is not inside a real transaction"
                    raise dbapi.OperationalError("diagnostic second-DDL interruption")
            return super().execute(sql, parameters)

        def rollback(self) -> None:
            super().rollback()
            rollback_tables.append(super().execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall())

    def wrapped_connect(*args: Any, **kwargs: Any) -> Any:
        kwargs["factory"] = Interrupted
        return real_connect(*args, **kwargs)

    monkeypatch.setattr(dbapi, "connect", wrapped_connect)
    manifest = tmp_path / "formation.yaml"
    with pytest.raises(_store.StoreError, match="second-DDL"):
        with _store.connect(manifest, busy_timeout_ms=20):
            pytest.fail("fault not reached")
    assert len(creates) == 2
    assert rollback_tables == [[]], "rollback proof must inspect staging before deletion"
    assert not _store.database_path(manifest).exists()
    assert not list(tmp_path.glob(".comms-initializing-*"))


def test_all_digit_incarnation_remains_exact_text(tmp_path: Path) -> None:
    manifest = tmp_path / "formation.yaml"
    token = "01234567890123456789012345678901"
    with _store.connect(manifest, busy_timeout_ms=20) as connection:
        with _store.immediate(connection):
            connection.execute(
                "INSERT INTO groups VALUES (?, 'f', ?, 1, 1, 0, 256, 8192, 64, 32, 0, 16777216)",
                ("a" * 32, str(manifest)),
            )
            connection.execute(
                "INSERT INTO endpoints VALUES (?, 'm', ?, 's', ?, 1, 1, 121, 1, 4)",
                ("a" * 32, token, str(tmp_path / "run")),
            )
    with _store.connect(manifest, busy_timeout_ms=20) as connection:
        row = connection.execute("SELECT incarnation,typeof(incarnation) FROM endpoints").fetchone()
        assert tuple(row) == (token, "text")


@pytest.mark.parametrize("version,affinity", [(1, "INTEGER"), (2, "TEXT")])
def test_old_schema_refuses_without_migration(tmp_path: Path, version: int, affinity: str) -> None:
    manifest = tmp_path / "formation.yaml"
    path = _store.database_path(manifest)
    # Actual prototype layout: no snapshotted policy or admission records.
    # Do not derive a purported old schema from the new production schema.
    old_schema = """
    CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
    CREATE TABLE groups (group_id TEXT PRIMARY KEY, formation_id TEXT NOT NULL,
        manifest_path TEXT NOT NULL, created_at REAL NOT NULL,
        group_time REAL NOT NULL, closed INTEGER NOT NULL DEFAULT 0);
    CREATE TABLE endpoints (group_id TEXT NOT NULL, member_id TEXT NOT NULL,
        incarnation AFFINITY NOT NULL, session_id TEXT NOT NULL, run_path TEXT NOT NULL,
        enrolled_at REAL NOT NULL, last_seen_at REAL NOT NULL, lease_expires_at REAL NOT NULL,
        PRIMARY KEY (group_id, member_id));
    """.replace("AFFINITY", affinity)
    with _store.sqlite3.connect(path) as connection:
        connection.executescript(old_schema)
        connection.execute("INSERT INTO schema_meta VALUES ('schema_version',?)", (str(version),))
    before = path.read_bytes()
    with pytest.raises(_store.StoreError) as caught:
        with _store.connect(manifest, busy_timeout_ms=20):
            pass
    assert caught.value.refusal is _store.StoreRefusal.SCHEMA_MISMATCH
    assert path.read_bytes() == before
