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

from tests._layout import PACKAGE_ROOT
from tests._memory_fixtures import DaemonCheckout
from tests._memory_store_fake import FakeMemoryStore
from trw_mcp.models.config import TRWConfig
from trw_mcp.server._doctor_memory_store import memory_backend_row
from trw_mcp.state._store_migration import holds_rows
from trw_mcp.state._tier_routing import USER_NAMESPACE

pytestmark = pytest.mark.usefixtures("stub_cli_version_probes")


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


def _legacy_schema_project_db(target: Path) -> Path:
    """A project ``memory.db`` predating the ``verification_checked_at``/``protection_tier`` migrations,
    holding one seeded canary and one ordinary row (``probe_fixtures.legacy_schema``, PRD-QUAL-147)."""
    from trw_memory.security._runtime_canary import _seeded_canary
    from trw_memory.storage.probe_fixtures import legacy_schema

    db = target / ".trw" / "memory" / "memory.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    backend = SQLiteBackend(db)
    try:
        backend.store(_seeded_canary("canary-001"))
        backend.store(MemoryEntry(id="L-stray1", content="Stray row L-stray1", namespace="default"))
    finally:
        backend.close()
    return legacy_schema(db)


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


def test_a_flag_only_decoy_that_is_not_the_pinned_canary_is_counted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A row merely carrying the ``system_canary`` FLAG, without the pinned id/content, is real data
    (PRD-CORE-309): only a pinned canary identity is excluded, never the flag alone."""
    (tmp_path / ".trw").mkdir()
    _write_stray_project_row(tmp_path).close()
    _pin(tmp_path, FakeMemoryStore(), "project:demo", monkeypatch)

    _status, message = memory_backend_row(tmp_path)

    assert "split store: 2 row(s)" in message


def test_a_real_seeded_canary_is_still_not_counted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The pinned-identity predicate still excludes a genuine seeded canary, flag or no flag."""
    from trw_memory.security._runtime_canary import _seeded_canary

    (tmp_path / ".trw").mkdir()
    backend = SQLiteBackend(tmp_path / ".trw" / "memory" / "memory.db")
    backend.store(_seeded_canary("canary-001"))
    backend.close()
    _pin(tmp_path, FakeMemoryStore(), "project:demo", monkeypatch)

    status, message = memory_backend_row(tmp_path)

    assert (status, "split store" in message) == ("PASS", False)


def test_doctor_handles_a_legacy_schema_missing_a_migrated_column(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """sol r1 P2: a pre-migration project db must not fail the row; the seeded canary is still
    excluded and the one ordinary row is still counted."""
    _legacy_schema_project_db(tmp_path)
    _pin(tmp_path, FakeMemoryStore(), "project:demo", monkeypatch)

    status, message = memory_backend_row(tmp_path)

    assert (status, "split store: 1 row(s)" in message) == ("WARN", True)


def test_holds_rows_handles_a_legacy_schema_missing_a_migrated_column(tmp_path: Path) -> None:
    """sol r1 P2: the preflight/installer probe shares the same tolerant column selection."""
    db = _legacy_schema_project_db(tmp_path)

    assert holds_rows(db)


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

    assert "split store: 4 row(s)" in message


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

        assert "split store: 2 row(s)" in message
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


def _served_doctor_row(target: Path) -> tuple[str, str]:
    """The ``memory_backend`` row as the ``trw-mcp doctor`` handler prints it (``--format json``)."""
    import argparse
    import contextlib
    import io

    from trw_mcp.server import _subcommands_doctor as doctor

    out = io.StringIO()
    with pytest.MonkeyPatch.context() as mp, contextlib.redirect_stdout(out), contextlib.suppress(SystemExit):
        mp.setattr(doctor, "_CHECKS", (("memory_backend", "_check_memory_backend"),))
        doctor._run_doctor(argparse.Namespace(target_dir=str(target), format="json"))
    (row,) = json.loads(out.getvalue())["checks"]
    return row["status"], row["message"]


#: Building the file evaluates the generated column once (~400 MB), so a separate child builds it.
_BOMB_BUILD = (
    "import sys; from pathlib import Path; from trw_memory.storage import probe_fixtures as f; "
    "f.generated_column_bomb(Path(sys.argv[1]))"
)
#: The served callers, in a child: update-project's pin decision, then the doctor handler.
_BOMB_CHILD = """
import json, sys
from pathlib import Path
import trw_mcp.state._store_selection as selection
from trw_mcp.bootstrap._namespace_pin import pin_empty_checkout
from tests.test_doctor_memory_store import _served_doctor_row
root = Path(sys.argv[1])
pin = {}
pin_empty_checkout(root, pin)
class Store:
    def count(self, namespace): return 0
selection.selected_store = lambda trw_dir: (Store(), "project:demo")
print(json.dumps({"doctor": _served_doctor_row(root), "pin": pin, "pinned": (root / ".trw" / "config.yaml").exists()}))
"""
_RSS_CAP_MB = 256


def test_the_generated_column_bomb_is_refused_by_doctor_and_update_project_in_bounded_memory(tmp_path: Path) -> None:
    """PRD-QUAL-147 FR06: the 8 KB file an unbounded read turned into 6 GB, through the served doctor
    handler and update-project's ``holds_rows`` check. In a child killed past ``_RSS_CAP_MB`` (macOS
    has no working RLIMIT_AS), so a regression cannot take the test worker down."""
    import os
    import subprocess
    import sys
    import time

    env = {**os.environ, "PYTHONPATH": os.pathsep.join(sys.path)}
    db = tmp_path / ".trw" / "memory" / "memory.db"
    db.parent.mkdir(parents=True)
    subprocess.run([sys.executable, "-c", _BOMB_BUILD, str(db)], env=env, check=True, timeout=120)
    assert db.stat().st_size <= 8192
    # The child imports ``tests.test_doctor_memory_store``. ``python -c`` puts its cwd first on sys.path, so a
    # child started from the repo root found the repo-root ``tests/`` package instead and died with
    # ModuleNotFoundError (rc 1, 65 MB): it runs from the package root, where ``tests`` is this suite.
    child = subprocess.Popen(
        [sys.executable, "-c", _BOMB_CHILD, str(tmp_path)],
        env=env,
        stdout=subprocess.PIPE,
        text=True,
        cwd=PACKAGE_ROOT,
    )
    peak_mb, deadline = 0, time.monotonic() + 60
    while child.poll() is None and peak_mb <= _RSS_CAP_MB and time.monotonic() < deadline:
        rss = subprocess.run(["ps", "-o", "rss=", "-p", str(child.pid)], capture_output=True, text=True).stdout
        peak_mb = max(peak_mb, int(rss.strip() or 0) // 1024)
        time.sleep(0.02)
    child.kill()
    out, _ = child.communicate()

    assert peak_mb <= _RSS_CAP_MB and child.returncode == 0, f"child killed at {peak_mb} MB or failed: {out}"
    report = json.loads(out.strip().splitlines()[-1])
    status, message = report["doctor"]
    assert status == "FAIL" and "refused unread" in message and "memories" in message
    assert "memory migrate --to user --apply" in report["pin"]["warnings"][0], "a refused store may hold rows"
    assert not report["pinned"]


def test_a_file_that_is_not_a_database_fails_the_row_and_may_hold_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from trw_memory.storage.probe_fixtures import not_a_database

    db = tmp_path / ".trw" / "memory" / "memory.db"
    db.parent.mkdir(parents=True)
    not_a_database(db)
    _pin(tmp_path, FakeMemoryStore(), "project:demo", monkeypatch)

    status, message = _served_doctor_row(tmp_path)

    assert status == "FAIL" and "could not be read" in message
    assert holds_rows(db)


# -- PRD-CORE-334 FR04: the ``memory-ledger-sample`` row ------------------------------------------------


def test_reports_decision_type_count(daemon_checkout: DaemonCheckout, config: TRWConfig) -> None:
    """The count is exact: more decisions than one recall returns, and no row of another type."""
    from trw_mcp.server._doctor_memory_store import memory_ledger_row
    from trw_mcp.state.memory_adapter import store_learning

    over_cap = config.recall_max_results + 5
    for index in range(over_cap):
        store_learning(daemon_checkout.trw_dir, f"L-d{index:02d}", f"decision {index}", f"why {index}", type="decision")
    store_learning(daemon_checkout.trw_dir, "L-inc", "an incident", "what broke", type="incident")

    status, message = memory_ledger_row(daemon_checkout.trw_dir.parent, config)

    assert status == "PASS"
    assert f"{over_cap} decision row(s)" in message
    assert f"namespace={daemon_checkout.namespace}" in message
    assert "8.0" not in message  # no sync target configured: no rollout reminder


@pytest.mark.parametrize(
    ("decisions", "synced", "reminded"),
    [(2, True, True), (0, True, False), (2, False, False)],
)
def test_the_rollout_reminder_needs_decisions_and_a_sync_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, config: TRWConfig, decisions: int, synced: bool, reminded: bool
) -> None:
    from pydantic import SecretStr

    from trw_mcp.server._doctor_memory_store import memory_ledger_row

    store = FakeMemoryStore()
    for index in range(decisions):
        store._write(("project:demo", f"L-d{index}"), MemoryEntry(id=f"L-d{index}", content="d", type="decision"))
    store._write(("project:demo", "L-p"), MemoryEntry(id="L-p", content="p"))
    _pin(tmp_path, store, "project:demo", monkeypatch)
    if synced:
        config = config.model_copy(
            update={"platform_urls": ["https://sync.example"], "platform_api_key": SecretStr("k")}
        )

    status, message = memory_ledger_row(tmp_path, config)

    assert status == "PASS"
    assert f"{decisions} decision row(s)" in message
    assert ("8.0" in message) is reminded


def test_an_unavailable_store_skips_the_ledger_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, config: TRWConfig
) -> None:
    from trw_mcp.server._doctor_memory_store import memory_ledger_row
    from trw_mcp.state._store_selection import StoreUnavailableError

    def unavailable(_trw_dir: Path) -> object:
        raise StoreUnavailableError("no daemon")

    monkeypatch.setattr("trw_mcp.state._store_selection.selected_store", unavailable)

    assert memory_ledger_row(tmp_path, config) == ("SKIP", "decision ledger not read: no daemon")


def test_doctor_runs_the_ledger_row() -> None:
    from trw_mcp.server._doctor_checks_registry import CHECKS

    assert ("memory-ledger-sample", "_check_memory_ledger_sample") in CHECKS
