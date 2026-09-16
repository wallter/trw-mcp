"""PRD-CORE-274-NFR02/NFR03 storage regression controls.

Uses the production module's actual sqlite DBAPI, including process-local
replacement. Never patches shared source or deletes a production database.
"""

from __future__ import annotations

import hashlib
import multiprocessing as mp
import queue
import re
import time
from pathlib import Path
from typing import Any

import pytest

from trw_mcp.comms import _store


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


def test_separate_process_exclusive_lock_respects_20ms(tmp_path: Path) -> None:
    manifest = tmp_path / "formation.yaml"
    with _store.connect(manifest, busy_timeout_ms=100):
        pass
    context = mp.get_context("fork")
    ready, release = context.Event(), context.Event()
    child = context.Process(target=_hold_exclusive, args=(str(_store.database_path(manifest)), ready, release))
    child.start()
    try:
        assert ready.wait(2), "lock holder never reached BEGIN EXCLUSIVE"
        started = time.monotonic()
        with pytest.raises(_store.StoreError) as caught:
            with _store.connect(manifest, busy_timeout_ms=20):
                pytest.fail("read/write mailbox opened despite exclusive lock")
        elapsed = time.monotonic() - started
        print(f"configured_busy_timeout_ms=20 observed_refusal_seconds={elapsed:.6f}")
        assert caught.value.refusal is _store.StoreRefusal.CONTENDED
        assert caught.value.retryable
        # Scheduling allowance, explicitly not a 20ms real-time guarantee. This
        # detects the observed stdlib-default 5s regression without flaky 20ms timing.
        assert elapsed < 0.5
    finally:
        release.set()
        _stop(child)
    assert child.exitcode == 0


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
    expected = sorted(re.findall(r"CREATE TABLE\s+(\w+)", _store._SCHEMA, re.IGNORECASE))
    assert observed == [("ok", expected, str(_store.SCHEMA_VERSION))] * 2
    with _store.sqlite3.connect(_store.database_path(manifest)) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert connection.execute("SELECT count(*) FROM schema_meta WHERE key='schema_version'").fetchone()[0] == 1
