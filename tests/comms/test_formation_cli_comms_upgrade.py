"""PRD-CORE-274 FR16: ``trw-mcp formation comms-upgrade|comms-rollback`` wiring (board seq 663).

The library behaviour is proven in ``test_storage_contract.py``; this module proves the
parser routes to it, that it is orchestrator-only by this session's PIN (a forged ``--run``
is refused, T29), that every refusal exits non-zero with its reason, and that the real
console entry point (``trw_mcp.server:main``) reaches the actual migration.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from tests._formation_test_support import FormationFixture, formation_env, pin_session  # noqa: F401
from tests.comms.test_storage_contract import _v3_mailbox
from trw_mcp.comms import _policy, _schema, _store, _upgrade
from trw_mcp.tools._formation_cli import add_formation_subcommands, run_formation

_SRC = Path(__file__).resolve().parents[2] / "src"


def _parse(*argv: str) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="trw-mcp")
    add_formation_subcommands(parser.add_subparsers(dest="command"))
    return parser.parse_args(["formation", *argv])


def _version(manifest: Path) -> str:
    conn = sqlite3.connect(_store.database_path(manifest))
    try:
        return str(conn.execute("SELECT value FROM schema_meta").fetchone()[0])
    finally:
        conn.close()


def _v4_mailbox(env: FormationFixture) -> Path:
    """The v3 fixture as a v4 build left it: the V4 columns filled, stamped 4, verified as v4."""
    manifest = _v3_mailbox(env)
    conn = sqlite3.connect(_store.database_path(manifest))
    conn.row_factory = sqlite3.Row
    for step in _schema.V4_STEPS:
        conn.execute(step)
    for row in conn.execute("SELECT * FROM admissions").fetchall():
        prepared = conn.execute(
            "SELECT 1 FROM milestones WHERE message_id=? AND fact='fetch_prepared'", (row["message_id"],)
        ).fetchone()
        conn.execute(
            "UPDATE admissions SET expires_at=?, delivery_count=?, canonical_sha256=? WHERE message_id=?",
            (row["admitted_at"] + 86400, 1 if prepared else 0, _upgrade._canonical_sha256(row), row["message_id"]),
        )
    conn.execute("UPDATE schema_meta SET value='4'")
    conn.commit()
    conn.execute("BEGIN")
    _schema.verify(conn, version=4)
    conn.close()
    return manifest


def _open_refusal(manifest: Path) -> _store.StoreRefusal | None:
    """The refusal ``connect`` (what every comms call runs) raises for this mailbox, or None if it opens."""
    try:
        with _store.connect(manifest, busy_timeout_ms=20):
            return None
    except _store.StoreError as exc:
        # trw-fail-silent-allow: the refusal IS the return value; callers assert on it
        return exc.refusal


def test_v4_mailbox_refuses_until_upgraded_to_v5(
    formation_env: FormationFixture, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """PRD-CORE-322 FR05 (S1 failing-first): a v4 file is refused, unchanged, until the explicit upgrade."""
    manifest = _v4_mailbox(formation_env)
    path = _store.database_path(manifest)
    before = path.read_bytes()
    assert _open_refusal(manifest) is _store.StoreRefusal.UPGRADE_REQUIRED
    with pytest.raises(_store.StoreError) as refused:
        with _store.connect(manifest, busy_timeout_ms=20):
            pass
    assert "trw-mcp formation comms-upgrade" in str(refused.value)
    assert path.read_bytes() == before, "the refusal changed the v4 file"

    pin_session(monkeypatch, formation_env.orchestrator_run)
    with pytest.raises(SystemExit) as done:
        run_formation(_parse("comms-upgrade", "--run", str(formation_env.orchestrator_run)))
    assert done.value.code == 0
    result = json.loads(capsys.readouterr().out)
    assert (result["status"], result["schema_version"], result["from_version"]) == ("upgraded", 5, 4)
    assert _version(manifest) == "5"
    assert _open_refusal(manifest) is None


def test_parser_routes_both_verbs_with_repeatable_ack() -> None:
    upgrade = _parse("comms-upgrade", "--run", "/r", "--ack", "a", "--ack", "b")
    assert (upgrade.formation_command, upgrade.run_path, upgrade.acknowledged) == ("comms-upgrade", "/r", ["a", "b"])
    rollback = _parse("comms-rollback", "--run", "/r")
    assert (rollback.formation_command, rollback.run_path) == ("comms-rollback", "/r")


def test_upgrade_then_rollback_through_the_cli(
    formation_env: FormationFixture, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = _v3_mailbox(formation_env)
    pin_session(monkeypatch, formation_env.orchestrator_run)
    run = str(formation_env.orchestrator_run)
    with pytest.raises(SystemExit) as done:
        run_formation(_parse("comms-upgrade", "--run", run))
    assert done.value.code == 0
    assert json.loads(capsys.readouterr().out)["status"] == "upgraded"
    assert _version(manifest) == str(_schema.SCHEMA_VERSION)
    with pytest.raises(SystemExit) as rolled:
        run_formation(_parse("comms-rollback", "--run", run))
    assert rolled.value.code == 0
    assert _version(manifest) == "3"


def test_refusals_exit_nonzero_with_their_reason(
    formation_env: FormationFixture, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = _v3_mailbox(formation_env)
    pin_session(monkeypatch, formation_env.member_runs["impl-1"], key="member-session")
    with pytest.raises(SystemExit) as not_orchestrator:
        run_formation(_parse("comms-upgrade"))
    assert not_orchestrator.value.code == 1
    assert "orchestrator-only" in capsys.readouterr().err

    pin_session(monkeypatch, formation_env.orchestrator_run)
    with pytest.raises(SystemExit) as nothing_to_undo:
        run_formation(_parse("comms-rollback", "--run", str(formation_env.orchestrator_run)))
    assert nothing_to_undo.value.code == 1
    assert "no upgrade record" in capsys.readouterr().err
    assert _version(manifest) == "3", "a refused command changes nothing"


def test_the_console_entry_point_reaches_the_real_migration_and_refusal_exits_nonzero(
    formation_env: FormationFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = _v3_mailbox(formation_env)
    pin_session(monkeypatch, formation_env.orchestrator_run)
    env = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join([str(_SRC), str(_SRC.parents[1] / "trw-memory" / "src")]),
        "TRW_PROJECT_ROOT": str(formation_env.project_root),
    }

    def cli(*argv: str) -> subprocess.CompletedProcess[str]:
        code = "import sys; from trw_mcp.server import main; sys.argv = ['trw-mcp', *sys.argv[1:]]; main()"
        return subprocess.run(
            [sys.executable, "-c", code, "formation", *argv],
            cwd=formation_env.project_root,
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
        )

    refused = cli("comms-rollback", "--run", str(formation_env.orchestrator_run))
    assert refused.returncode != 0 and "no upgrade record" in refused.stderr
    upgraded = cli("comms-upgrade", "--run", str(formation_env.orchestrator_run))
    assert upgraded.returncode == 0, upgraded.stderr
    assert json.loads(upgraded.stdout.strip().splitlines()[-1])["status"] == "upgraded"
    assert _version(manifest) == str(_schema.SCHEMA_VERSION)


def test_a_storage_error_from_a_step_is_a_named_refusal_not_a_traceback(
    formation_env: FormationFixture, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from trw_mcp.comms import _upgrade

    manifest = _v3_mailbox(formation_env)
    pin_session(monkeypatch, formation_env.orchestrator_run)
    monkeypatch.setattr(_upgrade, "V4_STEPS", ("ALTER TABLE no_such_table ADD COLUMN x INTEGER",))
    with pytest.raises(SystemExit) as refused:
        run_formation(_parse("comms-upgrade", "--run", str(formation_env.orchestrator_run)))
    assert refused.value.code == 1
    assert "storage_unavailable: OperationalError" in capsys.readouterr().err
    assert _version(manifest) == "3"


@pytest.mark.parametrize("verb", ["comms-upgrade", "comms-rollback"])
def test_a_member_session_naming_the_orchestrator_run_is_refused(
    formation_env: FormationFixture, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, verb: str
) -> None:
    """T29 (codex-a P1): ``--run`` is a path any shell can type, so it grants nothing."""
    manifest = _v3_mailbox(formation_env)
    pin_session(monkeypatch, formation_env.member_runs["impl-1"], key="member-session")

    with pytest.raises(SystemExit) as refused:
        run_formation(_parse(verb, "--run", str(formation_env.orchestrator_run)))

    assert refused.value.code == 1
    assert "run_not_pinned" in capsys.readouterr().err
    assert _version(manifest) == "3", "a forged --run changed the mailbox schema"


# ---------------------------------------------------------------------------
# PRD-CORE-322 FR05 helpers and the v4 CLI round trip (library arms: test_comms_schema_v5.py).
# ---------------------------------------------------------------------------

_TABLES = ("groups", "endpoints", "admissions", "milestones", "refusal_counts")


def _execute(manifest: Path, *statements: tuple[str, tuple[Any, ...]]) -> None:
    conn = sqlite3.connect(_store.database_path(manifest))
    try:
        for sql, params in statements:
            conn.execute(sql, params)
        conn.commit()
    finally:
        conn.close()


def _dump(manifest: Path) -> dict[str, list[tuple[Any, ...]]]:
    conn = sqlite3.connect(_store.database_path(manifest))
    try:
        return {table: sorted(tuple(row) for row in conn.execute(f"SELECT * FROM {table}")) for table in _TABLES}
    finally:
        conn.close()


def _verdict(manifest: Path, *, version: int = _schema.SCHEMA_VERSION) -> str | None:
    """None when the verifier accepts the file as *version*, else its rejection text."""
    conn = sqlite3.connect(_store.database_path(manifest))
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("BEGIN")
        _schema.verify(conn, version=version)
        return None
    except ValueError as exc:
        # trw-fail-silent-allow: the rejection text IS the return value; callers assert on it
        return str(exc)
    finally:
        conn.close()


def _source(env: FormationFixture, version: str) -> Path:
    manifest = _v4_mailbox(env) if version == "4" else _v3_mailbox(env)
    # A refusal counter row, so "every table's rows survive" covers all five tables.
    _execute(manifest, ("INSERT INTO refusal_counts VALUES (?,?,?)", ("a" * 32, sorted(_policy.REFUSALS)[0], 2)))
    return manifest


def test_v4_upgrade_then_rollback_round_trips_through_the_cli(
    formation_env: FormationFixture, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = _source(formation_env, "4")
    before = _dump(manifest)
    pin_session(monkeypatch, formation_env.orchestrator_run)
    run = str(formation_env.orchestrator_run)
    for verb in ("comms-upgrade", "comms-rollback"):
        with pytest.raises(SystemExit) as done:
            run_formation(_parse(verb, "--run", run))
        assert done.value.code == 0, capsys.readouterr().err
    assert json.loads(capsys.readouterr().out.strip().splitlines()[-1]) == {
        "status": "rolled_back",
        "schema_version": 4,
        "backup": _record_backup(manifest),
    }
    assert _version(manifest) == "4" and _verdict(manifest, version=4) is None
    assert _dump(manifest) == before
    assert _open_refusal(manifest) is _store.StoreRefusal.UPGRADE_REQUIRED


def _record_backup(manifest: Path) -> str:
    rolled = _store.database_path(manifest).with_name(_upgrade.RECORD_FILENAME).with_suffix(".rolled-back.json")
    return str(json.loads(rolled.read_text(encoding="utf-8"))["backup"])
