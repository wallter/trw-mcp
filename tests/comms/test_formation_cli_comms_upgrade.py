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
import subprocess
import sys
from pathlib import Path

import pytest

from tests._formation_test_support import FormationFixture, formation_env, pin_session  # noqa: F401
from tests.comms.test_storage_contract import _v3_mailbox
from trw_mcp.comms import _schema, _store
from trw_mcp.tools._formation_cli import add_formation_subcommands, run_formation

_SRC = Path(__file__).resolve().parents[2] / "src"


def _parse(*argv: str) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="trw-mcp")
    add_formation_subcommands(parser.add_subparsers(dest="command"))
    return parser.parse_args(["formation", *argv])


def _version(manifest: Path) -> str:
    import sqlite3

    conn = sqlite3.connect(_store.database_path(manifest))
    try:
        return str(conn.execute("SELECT value FROM schema_meta").fetchone()[0])
    finally:
        conn.close()


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
    env = {**os.environ, "PYTHONPATH": str(_SRC), "TRW_PROJECT_ROOT": str(formation_env.project_root)}

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
