"""The mailbox upgrade and rollback must keep their exclusive lock for the whole hold (C15 class).

``_exclusive()`` holds ``locking_mode=EXCLUSIVE`` so no write can fall between the
backup, the schema steps, the commit and the recorded change counter. POSIX fcntl
locks belong to a (process, inode) pair, so reading the counter or copying the
backup through a raw open/close of the live file released that lock mid-hold.
Each probe runs in a separate process, the only place the lock is visible.
"""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from tests._formation_test_support import FormationFixture, formation_env  # noqa: F401
from tests.comms.test_storage_contract import _v3_mailbox
from trw_mcp.comms import _store, _upgrade

pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX advisory-lock semantics")

_WRITE_PROBE = """
import sqlite3, sys
conn = sqlite3.connect(sys.argv[1], timeout=0, isolation_level=None)
try:
    conn.execute("BEGIN IMMEDIATE")
    conn.execute("CREATE TABLE IF NOT EXISTS intruder(x)")
    conn.execute("COMMIT")
    print("granted")
except sqlite3.OperationalError:
    print("blocked")
"""


def _other_process_can_write(db: Path) -> bool:
    out = subprocess.run(
        [sys.executable, "-c", _WRITE_PROBE, str(db)], capture_output=True, text=True, timeout=30, check=True
    )
    return out.stdout.strip() == "granted"


def test_upgrade_holds_its_lock_across_the_backup_and_the_counter(
    formation_env: FormationFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = _v3_mailbox(formation_env)
    db = _store.database_path(manifest)
    probes: list[tuple[str, bool]] = []
    real_apply, real_record = _upgrade._apply_v4, _upgrade._write_record

    def _apply(conn: sqlite3.Connection, ttl_seconds: int) -> None:
        probes.append(("after counter + backup", _other_process_can_write(db)))
        real_apply(conn, ttl_seconds)

    def _record(path: Path, record: dict[str, Any]) -> None:
        probes.append(("after commit counter", _other_process_can_write(db)))
        real_record(path, record)

    monkeypatch.setattr(_upgrade, "_apply_v4", _apply)
    monkeypatch.setattr(_upgrade, "_write_record", _record)

    assert _upgrade.upgrade(manifest, ttl_seconds=86400)["status"] == "upgraded"
    assert probes == [("after counter + backup", False), ("after commit counter", False)]
    assert _other_process_can_write(db) is True  # released when the hold ends


def test_rollback_holds_its_lock_across_the_counter_check(
    formation_env: FormationFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = _v3_mailbox(formation_env)
    db = _store.database_path(manifest)
    assert _upgrade.upgrade(manifest, ttl_seconds=86400)["status"] == "upgraded"
    probes: list[bool] = []
    real_connect = sqlite3.connect

    def _connect(database: Any, *args: Any, **kwargs: Any) -> sqlite3.Connection:
        if ".bak" in str(database):  # the backup source opens after the counter check
            probes.append(_other_process_can_write(db))
        return real_connect(database, *args, **kwargs)

    monkeypatch.setattr(_upgrade.sqlite3, "connect", _connect)

    assert _upgrade.rollback(manifest)["status"] == "rolled_back"
    assert probes == [False]
