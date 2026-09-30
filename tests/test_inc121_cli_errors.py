"""E2E-INC-121 (swarm-e2e S13-A4): CLI verbs answer a known refusal in one line, never a traceback.

(a) ``formation status`` with no pinned run raised ``StateError`` straight out of the handler; (b)
``maintain-verify`` with the memory daemon unreachable printed a 44-line ``StoreUnavailableError`` traceback.
Each now exits 1 with a named reason and a remedy. An unexpected exception still traces: only the named
refusal classes are caught.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest


def _exit_code(fn: object, args: argparse.Namespace) -> int:
    with pytest.raises(SystemExit) as done:
        fn(args)  # type: ignore[operator]
    return int(done.value.code or 0)


def test_formation_status_with_no_pinned_run_is_one_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from trw_mcp.exceptions import StateError
    from trw_mcp.tools import _formation_cli

    def no_pin(*_a: object, **_k: object) -> Path:
        raise StateError("No active run found for this session")

    monkeypatch.setattr("trw_mcp.state._paths.resolve_run_path", no_pin)
    code = _exit_code(_formation_cli.run_formation, argparse.Namespace(formation_command="status", run_path=None))
    err = capsys.readouterr().err
    assert code == 1
    assert "Traceback" not in err and "No active run" in err and "--run" in err


def test_an_unexpected_error_in_formation_status_still_traces(monkeypatch: pytest.MonkeyPatch) -> None:
    from trw_mcp.tools import _formation_cli

    def boom(*_a: object, **_k: object) -> Path:
        raise ZeroDivisionError("a bug, not a refusal")

    monkeypatch.setattr("trw_mcp.state._paths.resolve_run_path", boom)
    with pytest.raises(ZeroDivisionError):
        _formation_cli.run_formation(argparse.Namespace(formation_command="status", run_path=None))


def test_maintain_verify_with_the_store_down_is_one_line(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from trw_mcp.server._subcommands_maintain import _run_maintain_verify
    from trw_mcp.state._store_selection import StoreUnavailableError

    def down(**_k: object) -> object:
        raise StoreUnavailableError("the memory daemon is not reachable; start it with `trw-mcp memory start`")

    monkeypatch.setattr("trw_mcp.tools._maintain_verify.run_maintain_verify_for_project", down)
    code = _exit_code(_run_maintain_verify, argparse.Namespace(namespace=None, as_json=False))
    err = capsys.readouterr().err
    assert code == 1 and "Traceback" not in err and err.strip().startswith("Error: the memory daemon is not reachable")


def test_prd_validate_outside_the_project_names_the_root_and_the_fix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """INC-121 (f): `prd validate --prd-path /nonexistent.md` said only 'escapes project root'."""
    from trw_mcp.exceptions import StateError
    from trw_mcp.tools import _prd_validate_tool

    project = tmp_path / "proj"
    project.mkdir()
    monkeypatch.setattr("trw_mcp.tools.requirements.resolve_project_root", lambda: project)
    with pytest.raises(StateError) as refused:
        _prd_validate_tool.run_prd_validate(prd_path=str(tmp_path / "nonexistent.md"))
    assert f"outside the project root {project}" in str(refused.value) and "inside the project" in str(refused.value)


def test_audit_reports_for_itself_without_raw_log_lines() -> None:
    """INC-121 (d): a daemon-off audit ended with a raw sqlite_fallback_to_yaml JSON line and escaped traceback."""
    from trw_mcp.server._cli import _SELF_REPORTING_COMMANDS

    assert "audit" in _SELF_REPORTING_COMMANDS


def test_an_unknown_sub_verb_flag_prints_that_verbs_usage_not_the_root(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """INC-121 (g): an unknown flag on a sub-verb printed the ~1 KB ROOT usage."""
    from trw_mcp.server import _cli

    monkeypatch.setattr("sys.argv", ["trw-mcp", "feedback", "list", "--bogus"])
    with pytest.raises(SystemExit) as done:
        _cli.main()
    err = capsys.readouterr().err
    assert done.value.code == 2
    assert "feedback list" in err.splitlines()[0] and "unrecognized arguments: --bogus" in err
    assert len(err) < 400


def test_a_failed_audit_says_so_on_stderr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """codex r1 KI: audit is self-reporting (its WARNING/ERROR logs are dropped), so its failure must be printed."""
    from trw_mcp.server import _subcommands

    monkeypatch.setattr(
        "trw_mcp.audit.run_audit", lambda *_a, **_k: {"status": "failed", "error": "runs root unreadable"}
    )
    with pytest.raises(SystemExit) as done:
        _subcommands._run_audit(argparse.Namespace(target_dir=str(tmp_path), format="json", output=None, fix=False))
    # sys.exit(<str>): the interpreter prints it to stderr and exits 1
    assert done.value.code == "Error: audit failed: runs root unreadable"


def test_a_failed_audits_reason_cannot_drive_the_terminal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from trw_mcp.server import _subcommands

    monkeypatch.setattr("trw_mcp.audit.run_audit", lambda *_a, **_k: {"status": "failed", "error": "bad\x1b[31m path"})
    with pytest.raises(SystemExit) as done:
        _subcommands._run_audit(argparse.Namespace(target_dir=str(tmp_path), format="json", output=None, fix=False))
    message = str(done.value.code)
    assert "\x1b" not in message and "bad\\x1b[31m path" in message
