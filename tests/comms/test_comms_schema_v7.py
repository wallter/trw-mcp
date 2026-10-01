"""PRD-CORE-349 FR01: mailbox v7 (the append-only ``ahr_events`` log) and the gated upgrade chain to it."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from tests._formation_test_support import FormationFixture, formation_env  # noqa: F401
from tests.comms.test_comms_schema_v5 import _v5_handoff
from tests.comms.test_comms_schema_v6 import _v5_mailbox
from tests.comms.test_formation_cli_comms_upgrade import _dump, _execute, _open_refusal, _source, _verdict, _version
from trw_mcp.comms import _schema, _store, _upgrade


def _v6_mailbox(env: FormationFixture) -> Path:
    """A real v6 file: the current build's store with the v7 step undone and stamped 6."""
    manifest = _v5_handoff(env)
    _execute(manifest, ("DROP TABLE ahr_events", ()), ("UPDATE schema_meta SET value='6'", ()))
    assert _verdict(manifest, version=6) is None
    return manifest


def _ddl(manifest: Path) -> set[str]:
    conn = sqlite3.connect(_store.database_path(manifest))
    try:
        rows = conn.execute("SELECT sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'").fetchall()
    finally:
        conn.close()
    return {" ".join(str(row[0]).split()).lower() for row in rows}


def test_ddl_statements_v7_is_v6_plus_the_ahr_step() -> None:
    assert _schema.SCHEMA_VERSION == 7
    assert _schema.ddl_statements(7) == [*_schema.ddl_statements(6), *_schema.V7_STEPS]
    assert any("create table ahr_events" in step.lower() for step in _schema.V7_STEPS)
    assert "6" in _schema.UPGRADABLE_FROM


def test_a_v6_mailbox_is_refused_until_upgraded(formation_env: FormationFixture) -> None:
    manifest = _v6_mailbox(formation_env)
    assert _open_refusal(manifest) is _store.StoreRefusal.UPGRADE_REQUIRED
    before = _dump(manifest)
    result = _upgrade.upgrade(manifest, ttl_seconds=86400)
    assert (result["status"], result["schema_version"], result["from_version"]) == ("upgraded", 7, 6)
    assert _version(manifest) == "7" and _verdict(manifest) is None and _open_refusal(manifest) is None
    assert _dump(manifest) == before, "v6 -> v7 is additive: every row is retained"
    assert _upgrade.upgrade(manifest, ttl_seconds=86400)["status"] == "already_current"


@pytest.mark.parametrize("source", ["3", "4", "5", "6"])
def test_every_supported_version_upgrades_to_the_fresh_v7_ddl(formation_env: FormationFixture, source: str) -> None:
    if source in ("3", "4"):
        manifest = _source(formation_env, source)
    elif source == "5":
        manifest = _v5_mailbox(formation_env)
    else:
        manifest = _v6_mailbox(formation_env)
    _upgrade.upgrade(manifest, ttl_seconds=86400)
    memory = sqlite3.connect(":memory:")
    try:
        for statement in _schema.ddl_statements(_schema.SCHEMA_VERSION):
            memory.execute(statement)
        fresh = {
            " ".join(str(row[0]).split()).lower()
            for row in memory.execute("SELECT sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'")
        }
    finally:
        memory.close()
    assert _ddl(manifest) == fresh
    assert _verdict(manifest) is None


def test_v7_rollback_restores_the_verified_v6_file(formation_env: FormationFixture) -> None:
    manifest = _v6_mailbox(formation_env)
    before = _dump(manifest)
    _upgrade.upgrade(manifest, ttl_seconds=86400)
    assert _upgrade.rollback(manifest)["schema_version"] == 6
    assert _version(manifest) == "6" and _verdict(manifest, version=6) is None
    assert _dump(manifest) == before
