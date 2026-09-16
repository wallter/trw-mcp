"""CORE274-NFR03 actual SQLite durability readback and load-bearing refusal.

The runtime base/connect come from the store's DBAPI (which can be pysqlite3),
never a stale independently imported sqlite3 implementation. Faults alter real
connection settings; query results and the store's verification are not faked.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from trw_mcp.comms import _store

if TYPE_CHECKING:
    from sqlite3 import Connection, Cursor
else:
    Connection = getattr(_store, "sqlite3").Connection


def readback(conn: Connection) -> dict[str, Any]:
    return {
        name: conn.execute(f"PRAGMA {name}").fetchone()[0] for name in ("journal_mode", "synchronous", "busy_timeout")
    }


@pytest.mark.parametrize("timeout", [1, 20, 1234])
def test_fresh_and_reopened_store_read_back_real_durability(tmp_path: Path, timeout: int) -> None:
    manifest = tmp_path / "formation.yaml"
    with _store.connect(manifest, busy_timeout_ms=timeout) as connection:
        assert readback(connection) == {"journal_mode": "delete", "synchronous": 2, "busy_timeout": timeout}
        assert not connection.in_transaction
    assert _store.database_path(manifest).is_file()
    with _store.connect(manifest, busy_timeout_ms=timeout + 17) as reopened:
        assert readback(reopened) == {"journal_mode": "delete", "synchronous": 2, "busy_timeout": timeout + 17}
        assert reopened.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert not reopened.in_transaction


def install_wrong_setting(monkeypatch: pytest.MonkeyPatch, pragma: str) -> list[Any]:
    dbapi = getattr(_store, "sqlite3")
    original = dbapi.connect
    observations: list[Any] = []
    replacement = {"journal_mode": "MEMORY", "synchronous": "OFF", "busy_timeout": "0"}[pragma]

    class WrongSetting(Connection):
        def execute(self, sql: str, parameters: Any = ()) -> Cursor:
            if sql.upper().startswith(f"PRAGMA {pragma.upper()}="):
                result = super().execute(f"PRAGMA {pragma}={replacement}")
                actual = super().execute(f"PRAGMA {pragma}").fetchone()[0]
                observations.append(actual)
                return result
            return super().execute(sql, parameters)

    def connect(*args: Any, **kwargs: Any) -> Connection:
        connection: Connection = original(*args, **kwargs, factory=WrongSetting)
        assert type(connection).__base__ is dbapi.Connection
        return connection

    monkeypatch.setattr(dbapi, "connect", connect)
    return observations


def assert_unapplied_refused(manifest: Path, accepted_readbacks: list[dict[str, Any]]) -> None:
    """The identical refusal oracle is used with and without the production guard."""
    try:
        with _store.connect(manifest, busy_timeout_ms=37) as connection:
            accepted_readbacks.append(readback(connection))
    except _store.StoreError as exc:
        assert exc.refusal is _store.StoreRefusal.PRAGMA_UNAPPLIED
        assert exc.retryable is False
    else:
        raise AssertionError("store accepted an unapplied pragma")


@pytest.mark.parametrize("existing", [False, True])
@pytest.mark.parametrize("pragma,wrong", [("journal_mode", "memory"), ("synchronous", 0), ("busy_timeout", 0)])
def test_actual_unapplied_setting_refuses_fresh_and_existing_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, existing: bool, pragma: str, wrong: Any
) -> None:
    manifest = tmp_path / "formation.yaml"
    path = _store.database_path(manifest)
    if existing:
        with _store.connect(manifest, busy_timeout_ms=37):
            pass
    baseline = path.read_bytes() if existing else None
    observations = install_wrong_setting(monkeypatch, pragma)
    accepted: list[dict[str, Any]] = []
    assert_unapplied_refused(manifest, accepted)
    assert observations and all(value == wrong for value in observations), "real setting mutation was not reached"
    assert accepted == [], "store yielded an unsafe connection"
    if existing:
        assert path.read_bytes() == baseline
    else:
        assert not path.exists(), "failed initialization published a canonical store"
    assert not list(tmp_path.glob(".comms-initializing-*"))


@pytest.mark.parametrize("existing", [False, True])
def test_removing_only_synchronous_guard_breaks_unchanged_refusal_oracle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, existing: bool
) -> None:
    manifest = tmp_path / "formation.yaml"
    if existing:
        with _store.connect(manifest, busy_timeout_ms=37):
            pass
    observations = install_wrong_setting(monkeypatch, "synchronous")
    monkeypatch.setattr(_store, "_REQUIRED_PRAGMAS", {"journal_mode": "delete"})
    accepted: list[dict[str, Any]] = []
    with pytest.raises(AssertionError, match="store accepted an unapplied pragma"):
        assert_unapplied_refused(manifest, accepted)
    assert observations and all(value == 0 for value in observations)
    assert accepted == [{"journal_mode": "delete", "synchronous": 0, "busy_timeout": 37}]
