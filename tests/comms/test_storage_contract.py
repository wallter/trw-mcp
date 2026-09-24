"""PRD-CORE-274-NFR02/NFR03 storage regression controls.

Uses the production module's actual sqlite DBAPI, including process-local
replacement. Never patches shared source or deletes a production database.
"""

from __future__ import annotations

import hashlib
import json
import multiprocessing as mp
import os
import queue
import re
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from tests._formation_test_support import FormationFixture, formation_env  # noqa: F401
from tests._layout import requires_local_timing
from tests._timing import assert_budget
from tests.comms.conftest import joined_member
from trw_mcp.comms import _schema, _store, _upgrade
from trw_mcp.formation import load


@pytest.fixture(autouse=True)
def report_dbapi() -> None:
    print(
        f"production_dbapi={_store.sqlite3.__name__} sqlite_version={_store.sqlite3.sqlite_version} "
        f"store_sha256={hashlib.sha256(Path(_store.__file__).read_bytes()).hexdigest()}"
    )


def test_existing_zero_byte_file_refuses_without_repair(tmp_path: Path) -> None:
    manifest = tmp_path / "formation.yaml"
    path = _store.database_path(manifest)
    path.touch()
    before = path.read_bytes()
    refused = False
    try:
        with _store.connect(manifest, busy_timeout_ms=20):
            pass
    except _store.StoreError:
        refused = True
    after = path.read_bytes()
    print(f"zero_byte_refused={refused} before_bytes={len(before)} after_bytes={len(after)}")
    assert refused, "existing empty evidence was accepted/initialized"
    assert after == before == b""


def test_interrupted_schema_initialization_rolls_back_all_ddl(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    dbapi = _store.sqlite3
    original_connect = dbapi.connect
    reached: list[str] = []

    class Interrupted(dbapi.Connection):
        def execute(self, sql: str, parameters: Any = ()) -> Any:
            if sql.lstrip().upper().startswith("CREATE TABLE"):
                reached.append("execute_create")
                if reached.count("execute_create") == 2:
                    raise dbapi.OperationalError("injected second DDL interruption")
            return super().execute(sql, parameters)

        def executescript(self, script: str) -> Any:
            # Preserve executescript's actual implicit-commit semantics, then
            # interrupt after the first real CREATE. If production moves to
            # individual execute calls, the hook above remains reachable.
            first = script.split(";", 1)[0] + ";"
            super().executescript(first)
            reached.append("executescript_first_create")
            raise dbapi.OperationalError("injected second DDL interruption")

    def injecting_connect(*args: Any, **kwargs: Any) -> Any:
        kwargs["factory"] = Interrupted
        return original_connect(*args, **kwargs)

    manifest = tmp_path / "formation.yaml"
    monkeypatch.setattr(dbapi, "connect", injecting_connect)
    with pytest.raises(_store.StoreError, match="injected"):
        with _store.connect(manifest, busy_timeout_ms=20):
            pytest.fail("DDL interruption hook did not stop initialization")
    assert reached, "control unreachable: cannot credit an unexecuted interruption"
    print(f"interruption_hooks={reached}")
    with original_connect(_store.database_path(manifest)) as connection:
        tables = connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        assert tables == [], f"partial DDL survived rollback: {tables}"
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def _hold_exclusive(path: str, ready: Any, release: Any) -> None:
    connection = _store.sqlite3.connect(path, isolation_level=None)
    try:
        connection.execute("BEGIN EXCLUSIVE")
        ready.set()  # Only signals after the real lock is acquired.
        if not release.wait(5):
            raise RuntimeError("parent never released lock holder")
        connection.rollback()
    finally:
        connection.close()


def _stop(process: Any) -> None:
    process.join(timeout=3)
    if process.is_alive():
        process.terminate()
        process.join(timeout=2)
    assert not process.is_alive(), "child survived bounded termination"


def _contended_connect_attempt(tmp_path: Path) -> dict[str, Any]:
    """Hold an exclusive lock in a child process, then attempt a contended
    connect from this process. Returns every fact the two twins below assert
    on, without asserting anything itself."""
    manifest = tmp_path / "formation.yaml"
    with _store.connect(manifest, busy_timeout_ms=100):
        pass
    context = mp.get_context("fork")
    ready, release = context.Event(), context.Event()
    child = context.Process(target=_hold_exclusive, args=(str(_store.database_path(manifest)), ready, release))
    child.start()
    ready_reached = ready.wait(2)
    opened = False
    refusal = None
    retryable = None
    try:
        started = time.monotonic()
        try:
            with _store.connect(manifest, busy_timeout_ms=20):
                opened = True
        except _store.StoreError as caught:
            refusal = caught.refusal
            retryable = caught.retryable
        elapsed = time.monotonic() - started
        print(f"configured_busy_timeout_ms=20 observed_refusal_seconds={elapsed:.6f}")
    finally:
        release.set()
        _stop(child)
    return {
        "ready_reached": ready_reached,
        "opened": opened,
        "refusal": refusal,
        "retryable": retryable,
        "elapsed": elapsed,
        "exitcode": child.exitcode,
    }


def test_separate_process_exclusive_lock_respects_20ms(tmp_path: Path) -> None:
    result = _contended_connect_attempt(tmp_path)
    assert result["ready_reached"], "lock holder never reached BEGIN EXCLUSIVE"
    assert not result["opened"], "read/write mailbox opened despite exclusive lock"
    assert result["refusal"] is _store.StoreRefusal.CONTENDED
    assert result["retryable"]
    assert result["exitcode"] == 0


@requires_local_timing
def test_separate_process_exclusive_lock_respects_20ms_budget(tmp_path: Path) -> None:
    result = _contended_connect_attempt(tmp_path)
    # Scheduling allowance, explicitly not a 20ms real-time guarantee. This
    # detects the observed stdlib-default 5s regression without flaky 20ms timing.
    assert_budget("contended_connect_refusal", result["elapsed"], 0.5, "s")


def _first_open(manifest: str, barrier: Any, results: Any) -> None:
    try:
        barrier.wait(timeout=3)
        with _store.connect(Path(manifest), busy_timeout_ms=2000) as connection:
            rows = connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            results.put(
                (
                    "ok",
                    sorted(row[0] for row in rows),
                    connection.execute("SELECT value FROM schema_meta WHERE key='schema_version'").fetchone()[0],
                )
            )
    except Exception as exc:
        results.put(("error", type(exc).__name__, str(exc)))


def test_two_process_first_open_yields_one_complete_schema(tmp_path: Path) -> None:
    manifest = tmp_path / "formation.yaml"
    context = mp.get_context("fork")
    barrier, results = context.Barrier(2), context.Queue()
    children = [context.Process(target=_first_open, args=(str(manifest), barrier, results)) for _ in range(2)]
    for child in children:
        child.start()
    observed = []
    try:
        for _ in children:
            try:
                observed.append(results.get(timeout=5))
            except queue.Empty:
                pytest.fail("first-open child produced no result")
    finally:
        for child in children:
            _stop(child)
        results.close()
        results.join_thread()
    print(f"first_open_outcomes={observed}")
    assert all(child.exitcode == 0 for child in children)
    expected = sorted(re.findall(r"CREATE TABLE\s+(\w+)", _schema.V3_SCHEMA, re.IGNORECASE))
    assert observed == [("ok", expected, str(_store.SCHEMA_VERSION))] * 2
    with _store.sqlite3.connect(_store.database_path(manifest)) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert connection.execute("SELECT count(*) FROM schema_meta WHERE key='schema_version'").fetchone()[0] == 1


# ---------------------------------------------------------------------------
# PRD-CORE-274 FR16 (Amendment 02): explicit v3 -> v4 upgrade and guarded rollback.
# ---------------------------------------------------------------------------

_T0 = 1000.0
_INC = "b" * 32
_ROWS = (  # message id, key, state, admitted offset, extra milestones, recipient incarnation
    ("1" * 32, "k1", "pending", 1, (), _INC),
    ("2" * 32, "k2", "pending", 2, (("fetch_prepared", 3),), _INC),
    ("3" * 32, "k3", "acked", 4, (("fetch_prepared", 5), ("acked", 6)), _INC),
    ("4" * 32, "k4", "expired", 7, (("expired", 8),), "c" * 32),  # expired by an old replacement
)


def _v3_mailbox(env: FormationFixture) -> Path:
    """A consistent v3 mailbox with every row kind an upgrade must carry, beside a real manifest."""
    joined_member(env, "impl-1", "pin-1")
    run2 = joined_member(env, "impl-2", "pin-2")  # both pins record pid 999999: exited
    loaded = load(env.orchestrator_run, trw_dir=env.trw_dir)
    assert loaded is not None
    manifest = loaded.manifest_path
    conn = sqlite3.connect(_store.database_path(manifest))
    for statement in _schema.ddl_statements(3):
        conn.execute(statement)
    conn.execute("INSERT INTO schema_meta VALUES ('schema_version','3')")
    conn.execute(
        "INSERT INTO groups VALUES (?,?,?,?,?,0,256,8192,64,32,?)",
        ("a" * 32, loaded.manifest.formation_id, str(manifest), _T0, _T0 + 100, len(_ROWS)),
    )
    conn.execute(
        "INSERT INTO endpoints VALUES (?,?,?,?,?,?,?,?)",
        ("a" * 32, "impl-2", _INC, "pin-2", str(run2), _T0, _T0 + 50, _T0 + 170),
    )
    for message_id, key, state, offset, facts, incarnation in _ROWS:
        conn.execute(
            "INSERT INTO admissions VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                "a" * 32,
                "impl-1",
                key,
                "impl-2",
                "request",
                "on_demand",
                f"body-{key}",
                message_id,
                incarnation,
                _T0 + offset,
                state,
            ),
        )
        for fact, at in (("admitted", offset), *facts):
            conn.execute("INSERT INTO milestones VALUES (?,?,?)", (message_id, fact, _T0 + at))
    conn.commit()
    conn.close()
    return manifest


def _rows(path: Path, sql: str) -> list[tuple[Any, ...]]:
    conn = sqlite3.connect(path)
    try:
        return [tuple(row) for row in conn.execute(sql)]
    finally:
        conn.close()


def _v3_verifies(path: Path) -> bool:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("BEGIN")
        _schema.verify(conn, version=3)
        return True
    except (ValueError, _schema.SchemaVersionError):
        # trw-fail-silent-allow: the verdict IS the return value; callers assert on it
        return False
    finally:
        conn.close()


def test_v4_build_refuses_a_v3_mailbox_and_leaves_it_unchanged(formation_env: FormationFixture) -> None:
    manifest = _v3_mailbox(formation_env)
    path = _store.database_path(manifest)
    before = path.read_bytes()
    with pytest.raises(_store.StoreError) as refused:
        with _store.connect(manifest, busy_timeout_ms=20):
            pass
    assert refused.value.refusal is _store.StoreRefusal.UPGRADE_REQUIRED
    assert "comms-upgrade" in str(refused.value)
    assert path.read_bytes() == before


def test_upgrade_is_lossless_and_rollback_without_traffic_restores_v3(formation_env: FormationFixture) -> None:
    manifest = _v3_mailbox(formation_env)
    path = _store.database_path(manifest)
    original = _rows(path, "SELECT message_id,request_key,state,admitted_at FROM admissions ORDER BY rowid")
    milestones = _rows(path, "SELECT * FROM milestones ORDER BY message_id,fact")

    assert _upgrade.upgrade(manifest, ttl_seconds=86400)["status"] == "upgraded"
    with _store.connect(manifest, busy_timeout_ms=20):
        pass  # the v4 verifier accepts the upgraded file
    upgraded = _rows(
        path, "SELECT message_id,request_key,state,expires_at,delivery_count FROM admissions ORDER BY rowid"
    )
    assert [row[:3] for row in upgraded] == [row[:3] for row in original], "every row and dedup key retained"
    assert [row[3] for row in upgraded] == [row[3] + 86400 for row in original]
    assert [row[4] for row in upgraded] == [0, 1, 1, 0], "fetch_prepared rows carry one delivery"
    assert _rows(path, "SELECT generation,protocol FROM endpoints") == [(1, 3)], "v3-enrolled endpoint labelled"
    assert not _v3_verifies(path), "a v3 reader refuses the upgraded file"

    assert _upgrade.rollback(manifest)["status"] == "rolled_back"
    assert _v3_verifies(path)
    assert _rows(path, "SELECT message_id,request_key,state,admitted_at FROM admissions ORDER BY rowid") == original
    assert _rows(path, "SELECT * FROM milestones ORDER BY message_id,fact") == milestones


def test_upgrade_refuses_while_a_member_process_is_alive_even_with_an_expired_lease(
    formation_env: FormationFixture,
) -> None:
    manifest = _v3_mailbox(formation_env)  # impl-2's endpoint lease expired at _T0 + 170, long ago
    pins_path = formation_env.trw_dir / "runtime" / "pins.json"
    pins = json.loads(pins_path.read_text(encoding="utf-8"))
    pins["pin-2"]["pid"] = os.getpid()  # a genuinely live process
    pins_path.write_text(json.dumps(pins), encoding="utf-8")
    path = _store.database_path(manifest)
    before = path.read_bytes()

    with pytest.raises(_store.StoreError) as refused:
        _upgrade.upgrade(manifest, ttl_seconds=86400)
    assert refused.value.refusal is _store.StoreRefusal.UPGRADE_NOT_QUIESCENT
    assert "impl-2" in str(refused.value) and "impl-1" not in str(refused.value)
    assert path.read_bytes() == before
    assert _upgrade.upgrade(manifest, acknowledged=["impl-2"], ttl_seconds=86400)["status"] == "upgraded"


def test_a_held_write_lock_makes_the_upgrade_refuse_contended_with_the_file_unchanged(
    formation_env: FormationFixture,
) -> None:
    manifest = _v3_mailbox(formation_env)
    path = _store.database_path(manifest)
    before = path.read_bytes()
    holder = sqlite3.connect(path, isolation_level=None)
    holder.execute("BEGIN IMMEDIATE")
    try:
        with pytest.raises(_store.StoreError) as refused:
            _upgrade.upgrade(manifest, ttl_seconds=86400, busy_timeout_ms=50)
        assert refused.value.refusal is _store.StoreRefusal.CONTENDED
    finally:
        holder.execute("ROLLBACK")
        holder.close()
    assert path.read_bytes() == before


@pytest.mark.parametrize("failing_step", range(len(_schema.V4_STEPS) + 1))
def test_failure_after_each_upgrade_step_leaves_a_verifiable_v3_file(
    formation_env: FormationFixture, monkeypatch: pytest.MonkeyPatch, failing_step: int
) -> None:
    manifest = _v3_mailbox(formation_env)
    path = _store.database_path(manifest)
    original = _rows(path, "SELECT * FROM admissions ORDER BY rowid")
    steps = (*_schema.V4_STEPS[:failing_step], "ALTER TABLE no_such_table ADD COLUMN injected INTEGER")
    monkeypatch.setattr(_upgrade, "V4_STEPS", steps)
    with pytest.raises(sqlite3.OperationalError):
        _upgrade.upgrade(manifest, ttl_seconds=86400)
    assert _v3_verifies(path)
    assert _rows(path, "SELECT * FROM admissions ORDER BY rowid") == original
    assert not path.with_name(_upgrade.RECORD_FILENAME).exists(), "no record means no rollback of a non-upgrade"
    assert not list(path.parent.glob("comms.sqlite3.v3-*.bak")), "a failed attempt leaves no orphan backup"


@pytest.mark.parametrize(
    "write",
    [
        "UPDATE endpoints SET last_seen_at = last_seen_at + 1",  # a heartbeat alone
        "UPDATE admissions SET state='acked' WHERE message_id='" + "1" * 32 + "'",  # an ACK-shaped change
        "UPDATE groups SET group_time = group_time + 1",  # a clock touch
    ],
)
def test_rollback_refuses_after_any_post_upgrade_write_and_keeps_it(
    formation_env: FormationFixture, write: str
) -> None:
    manifest = _v3_mailbox(formation_env)
    path = _store.database_path(manifest)
    _upgrade.upgrade(manifest, ttl_seconds=86400)
    conn = sqlite3.connect(path)
    conn.execute(write)
    conn.commit()
    conn.close()
    after_write = _rows(path, "SELECT * FROM admissions ORDER BY rowid")
    with pytest.raises(_store.StoreError) as refused:
        _upgrade.rollback(manifest)
    assert refused.value.refusal is _store.StoreRefusal.ROLLBACK_WOULD_DROP_TRAFFIC
    assert _rows(path, "SELECT * FROM admissions ORDER BY rowid") == after_write, "the post-upgrade write survives"


def test_writer_blocked_during_the_upgrade_lands_after_release_and_then_blocks_rollback(
    formation_env: FormationFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Lane-C R6-1: the counter is recorded before the lock is released, so a writer that was
    blocked during the upgrade commits AFTER the recorded value and must make rollback refuse."""
    manifest = _v3_mailbox(formation_env)
    path = _store.database_path(manifest)
    started = threading.Event()
    outcome: list[str] = []

    def blocked_writer() -> None:
        conn = sqlite3.connect(path, timeout=10, isolation_level=None)
        started.set()
        try:
            conn.execute("BEGIN IMMEDIATE")  # blocks until the upgrade releases its exclusive lock
            conn.execute("UPDATE groups SET group_time = group_time + 1")
            conn.execute("COMMIT")
            outcome.append("committed")
        finally:
            conn.close()

    real_apply = _upgrade._apply_v4
    writer = threading.Thread(target=blocked_writer)

    def apply_with_contender(conn: sqlite3.Connection, ttl_seconds: int) -> None:
        writer.start()
        assert started.wait(5)
        time.sleep(0.2)  # the writer is now waiting on the upgrade's lock
        real_apply(conn, ttl_seconds)

    monkeypatch.setattr(_upgrade, "_apply_v4", apply_with_contender)
    assert _upgrade.upgrade(manifest, ttl_seconds=86400)["status"] == "upgraded"
    writer.join(10)
    assert outcome == ["committed"], "the blocked writer committed once the lock was released"
    record = json.loads(path.with_name(_upgrade.RECORD_FILENAME).read_text(encoding="utf-8"))
    assert _upgrade.change_counter(path) > record["change_counter"], "its commit came after the recorded counter"
    with pytest.raises(_store.StoreError) as refused:
        _upgrade.rollback(manifest)
    assert refused.value.refusal is _store.StoreRefusal.ROLLBACK_WOULD_DROP_TRAFFIC


def test_process_exited_is_fail_closed_for_unknown_or_invalid_pids() -> None:
    assert _upgrade.process_exited(os.getpid()) is False
    for unknown in (None, 0, -1, "123", True):
        assert _upgrade.process_exited(unknown) is False


def _to_wal(path: Path) -> None:
    conn = sqlite3.connect(path)
    assert conn.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
    conn.close()


def test_a_wal_mode_mailbox_makes_the_upgrade_refuse_without_converting_it(formation_env: FormationFixture) -> None:
    """Lane-C A1 B-1: in WAL mode the header counter does not move per commit, so it proves nothing."""
    manifest = _v3_mailbox(formation_env)
    path = _store.database_path(manifest)
    _to_wal(path)
    before = path.read_bytes()
    with pytest.raises(_store.StoreError) as refused:
        _upgrade.upgrade(manifest, ttl_seconds=86400)
    assert refused.value.refusal is _store.StoreRefusal.PRAGMA_UNAPPLIED
    assert path.read_bytes() == before, "the file is neither converted nor migrated"


def test_a_mailbox_switched_to_wal_after_the_upgrade_blocks_rollback(formation_env: FormationFixture) -> None:
    manifest = _v3_mailbox(formation_env)
    path = _store.database_path(manifest)
    _upgrade.upgrade(manifest, ttl_seconds=86400)
    _to_wal(path)
    conn = sqlite3.connect(path)
    conn.execute("UPDATE groups SET group_time = group_time + 1")
    conn.commit()
    conn.close()
    with pytest.raises(_store.StoreError) as refused:
        _upgrade.rollback(manifest)
    assert refused.value.refusal in (
        _store.StoreRefusal.PRAGMA_UNAPPLIED,
        _store.StoreRefusal.ROLLBACK_WOULD_DROP_TRAFFIC,
    )
    assert _rows(path, "SELECT value FROM schema_meta") == [("4",)], "nothing restored over the WAL-era write"


def test_a_restore_that_crashed_before_the_record_rename_is_finished_idempotently(
    formation_env: FormationFixture,
) -> None:
    manifest = _v3_mailbox(formation_env)
    path = _store.database_path(manifest)
    _upgrade.upgrade(manifest, ttl_seconds=86400)
    record = path.with_name(_upgrade.RECORD_FILENAME)
    saved = record.read_bytes()
    assert _upgrade.rollback(manifest)["status"] == "rolled_back"
    record.write_bytes(saved)  # as if the process died between the restore and the rename
    assert _upgrade.rollback(manifest)["status"] == "already_rolled_back"
    assert _v3_verifies(path)


def test_a_missing_record_refusal_names_the_backups(formation_env: FormationFixture) -> None:
    manifest = _v3_mailbox(formation_env)
    path = _store.database_path(manifest)
    backup = _upgrade.upgrade(manifest, ttl_seconds=86400)["backup"]
    path.with_name(_upgrade.RECORD_FILENAME).unlink()  # as if the process died before writing it
    with pytest.raises(_store.StoreError) as refused:
        _upgrade.rollback(manifest)
    assert refused.value.refusal is _store.StoreRefusal.UNAVAILABLE
    assert backup in str(refused.value) and "manual operator step" in str(refused.value)


def test_a_counter_that_moved_by_more_than_one_writes_no_rollback_record(
    formation_env: FormationFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Lane-C seam (REVIEW-A1-FR16 delta): weak evidence is worse than none, so an
    unexpected counter jump leaves the upgrade done but rollback unavailable."""
    manifest = _v3_mailbox(formation_env)
    path = _store.database_path(manifest)
    real = _upgrade.change_counter
    reads: list[int] = []

    def jumping(target: Path) -> int:
        value = real(target)
        reads.append(value)
        return value + 1 if len(reads) == 2 else value  # the post-commit read sees before + 2

    monkeypatch.setattr(_upgrade, "change_counter", jumping)
    result = _upgrade.upgrade(manifest, ttl_seconds=86400)
    assert result["status"] == "upgraded" and result["rollback"] == "unavailable"
    assert not path.with_name(_upgrade.RECORD_FILENAME).exists()
    with pytest.raises(_store.StoreError) as refused:
        _upgrade.rollback(manifest)
    assert refused.value.refusal is _store.StoreRefusal.UNAVAILABLE


def _fresh_mailbox(tmp_path: Path) -> Path:
    manifest = tmp_path / "formation.yaml"
    manifest.write_text("x")
    with _store.connect(manifest, busy_timeout_ms=200):
        pass
    return manifest


def test_a_foreign_commit_between_open_and_lock_forces_full_reverification(tmp_path: Path) -> None:
    """NFR08 skips the second verification only if nothing else committed; a corrupting
    commit by another connection after open is still caught under the write lock (NFR03)."""
    manifest = _fresh_mailbox(tmp_path)
    with _store.connect(manifest, busy_timeout_ms=200) as conn:
        foreign = sqlite3.connect(_store.database_path(manifest))
        foreign.execute("INSERT INTO refusal_counts VALUES ('not-a-group', 'group_closed', 1)")  # orphan row
        foreign.commit()
        foreign.close()
        with pytest.raises(_store.StoreError) as refused, _store.immediate(conn):
            _store.validate_operation(conn)
    assert refused.value.refusal is _store.StoreRefusal.CORRUPT


def test_without_a_foreign_commit_the_locked_reverification_is_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = _fresh_mailbox(tmp_path)
    calls: list[int] = []
    real = _store._verify_schema
    with _store.connect(manifest, busy_timeout_ms=200) as conn:
        monkeypatch.setattr(_store, "_verify_schema", lambda c: calls.append(1) or real(c))
        with _store.immediate(conn):
            _store.validate_operation(conn)
    assert calls == [], "the bytes connect() verified were re-verified for nothing"
