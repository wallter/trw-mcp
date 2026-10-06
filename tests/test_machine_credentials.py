"""Computer-wide sign-in: ``~/.trw/credentials.yaml`` reused by every project on the machine.

A project install on a computer that is already signed in must not run the device-code
login again. Covers the runtime resolver (env > project > machine), the owner-only read
rule, the 0600 writer, ``auth login --machine/--no-machine``, ``auth promote``,
``auth logout --machine``, ``auth status`` naming the layer, and that the key never appears
in output. HOME is isolated per test by the conftest floor.
"""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from trw_mcp.cli._auth_machine import run_auth_promote, run_machine_logout
from trw_mcp.cli.auth import device_auth_status, run_auth_login, run_auth_status
from trw_mcp.models.config._credentials import (
    SOURCE_ENV,
    SOURCE_MACHINE,
    SOURCE_PROJECT,
    credentials_path_for,
    machine_credentials_path,
    machine_store_problem,
    read_key_from_file,
    read_machine_key,
    resolve_platform_api_key,
    resolve_platform_api_key_with_source,
    write_credentials_key,
    write_machine_key,
)

_MACHINE_KEY = "trw_dk_machine_wide_secret_value"
_PROJECT_KEY = "trw_dk_project_only_secret_value"
_posix_only = pytest.mark.skipif(sys.platform == "win32", reason="POSIX owner/mode bits")


@pytest.fixture(autouse=True)
def _no_env_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TRW_PLATFORM_API_KEY", raising=False)
    monkeypatch.delenv("TRW_API_KEY", raising=False)


@pytest.fixture
def config_path(tmp_path: Path) -> Path:
    cfg = tmp_path / "proj" / ".trw" / "config.yaml"
    cfg.parent.mkdir(parents=True)
    cfg.write_text('installation_id: "proj"\n', encoding="utf-8")
    return cfg


def test_home_is_isolated() -> None:
    assert machine_credentials_path().parent.parent == Path.home()
    assert not machine_credentials_path().exists()


def test_machine_key_used_when_project_has_none(config_path: Path) -> None:
    write_machine_key(_MACHINE_KEY)
    assert resolve_platform_api_key_with_source(config_path) == (_MACHINE_KEY, SOURCE_MACHINE)
    assert resolve_platform_api_key(config_path) == _MACHINE_KEY


def test_project_key_beats_machine_key(config_path: Path) -> None:
    write_machine_key(_MACHINE_KEY)
    write_credentials_key(credentials_path_for(config_path), _PROJECT_KEY)
    assert resolve_platform_api_key_with_source(config_path) == (_PROJECT_KEY, SOURCE_PROJECT)


def test_env_beats_project_and_machine(config_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    write_machine_key(_MACHINE_KEY)
    write_credentials_key(credentials_path_for(config_path), _PROJECT_KEY)
    monkeypatch.setenv("TRW_API_KEY", "trw_dk_env_value")
    assert resolve_platform_api_key_with_source(config_path) == ("trw_dk_env_value", SOURCE_ENV)


def test_no_layer_resolves_empty(config_path: Path) -> None:
    assert resolve_platform_api_key_with_source(config_path) == ("", "")


@_posix_only
def test_writer_creates_exactly_0600_under_home() -> None:
    old = os.umask(0)  # exact_mode: a permissive umask must not widen it
    try:
        path = write_machine_key(_MACHINE_KEY)
    finally:
        os.umask(old)
    assert path == machine_credentials_path()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert read_key_from_file(path) == _MACHINE_KEY


@pytest.mark.parametrize("bad", ["", "a\nb", 'a"b'])
def test_writer_refuses_unstorable_values(bad: str) -> None:
    with pytest.raises(ValueError):
        write_machine_key(bad)


@_posix_only
def test_group_readable_store_is_refused(config_path: Path) -> None:
    path = write_machine_key(_MACHINE_KEY)
    path.chmod(0o640)
    assert "looser than 0600" in machine_store_problem()
    assert read_machine_key() == ""
    assert resolve_platform_api_key(config_path) == ""


@_posix_only
def test_symlinked_store_is_refused(tmp_path: Path, config_path: Path) -> None:
    real = tmp_path / "elsewhere.yaml"
    real.write_text(f'platform_api_key: "{_MACHINE_KEY}"\n', encoding="utf-8")
    real.chmod(0o600)
    machine_credentials_path().parent.mkdir(parents=True, exist_ok=True)
    machine_credentials_path().symlink_to(real)
    assert machine_store_problem() == "is a symlink"
    assert resolve_platform_api_key(config_path) == ""


_LOGIN = {"api_key": _MACHINE_KEY, "org_name": "acme", "user_email": "dev@acme.test"}


def test_login_machine_saves_computer_wide_not_project(config_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    with patch("trw_mcp.cli.auth.device_auth_login", return_value=_LOGIN):
        assert run_auth_login("https://api.example.com", config_path, machine=True) == 0
    assert read_key_from_file(machine_credentials_path()) == _MACHINE_KEY
    assert not credentials_path_for(config_path).exists()
    assert _MACHINE_KEY not in config_path.read_text(encoding="utf-8")
    out = capsys.readouterr().out
    assert str(machine_credentials_path()) in out  # says where it went
    assert _MACHINE_KEY not in out


def test_login_no_machine_keeps_project_only(config_path: Path) -> None:
    with patch("trw_mcp.cli.auth.device_auth_login", return_value=_LOGIN):
        assert run_auth_login("https://api.example.com", config_path, machine=False) == 0
    assert read_key_from_file(credentials_path_for(config_path)) == _MACHINE_KEY
    assert not machine_credentials_path().exists()


def test_login_without_a_terminal_never_saves_computer_wide(config_path: Path) -> None:
    """Consent: no answer (no tty) means project-only."""
    with (
        patch("trw_mcp.cli.auth.device_auth_login", return_value=_LOGIN),
        patch("sys.stdin.isatty", return_value=False),
    ):
        assert run_auth_login("https://api.example.com", config_path) == 0
    assert not machine_credentials_path().exists()
    assert credentials_path_for(config_path).exists()


@pytest.mark.parametrize(("answer", "saved"), [("", True), ("y", True), ("n", False)])
def test_login_asks_on_a_terminal(config_path: Path, answer: str, saved: bool) -> None:
    with (
        patch("trw_mcp.cli.auth.device_auth_login", return_value=_LOGIN),
        patch("trw_mcp.cli._auth_machine.sys.stdin") as stdin,
        patch("builtins.input", return_value=answer),
    ):
        stdin.isatty.return_value = True
        assert run_auth_login("https://api.example.com", config_path) == 0
    assert machine_credentials_path().exists() is saved
    assert credentials_path_for(config_path).exists() is not saved


def test_promote_copies_project_key_up(config_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    write_credentials_key(credentials_path_for(config_path), _PROJECT_KEY)
    assert run_auth_promote(config_path) == 0
    assert read_machine_key() == _PROJECT_KEY
    assert _PROJECT_KEY not in capsys.readouterr().out


def test_promote_without_project_key_fails(config_path: Path) -> None:
    assert run_auth_promote(config_path) == 1
    assert not machine_credentials_path().exists()


def test_machine_logout_removes_only_the_machine_store(config_path: Path) -> None:
    write_machine_key(_MACHINE_KEY)
    write_credentials_key(credentials_path_for(config_path), _PROJECT_KEY)
    assert run_machine_logout() == 0
    assert not machine_credentials_path().exists()
    assert read_key_from_file(credentials_path_for(config_path)) == _PROJECT_KEY


def test_status_names_the_machine_layer_and_hides_the_key(
    config_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write_machine_key(_MACHINE_KEY)
    status = device_auth_status(config_path, "https://api.example.com")
    assert status["authenticated"] is True
    assert status["key_source"] == SOURCE_MACHINE
    run_auth_status(config_path, "https://api.example.com")
    out = capsys.readouterr().out
    assert SOURCE_MACHINE in out
    assert _MACHINE_KEY not in out
