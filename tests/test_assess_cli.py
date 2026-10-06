"""``trw-mcp assess configure|status|install-check``: the jev key configured once per computer."""

from __future__ import annotations

import io
import stat
from pathlib import Path

import pytest

_KEY = "sk-or-cli-test-0000000000000000wxyz"
_PKG = Path(__file__).resolve().parents[1]
_REPO = _PKG.parent


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    for name in (
        "TRW_JEV_ENABLED",
        "OPENROUTER_API_KEY",
        "TRW_JEV_BASE_URL",
        "TRW_JEV_MODEL",
        "TRW_HEADLESS",
        "TRW_JSON",
    ):
        monkeypatch.delenv(name, raising=False)
    return home


def _dispatch(argv: list[str]) -> int:
    from trw_mcp.server._cli_argparse import _build_arg_parser
    from trw_mcp.server._subcommands import SUBCOMMAND_HANDLERS

    with pytest.raises(SystemExit) as exc_info:
        args = _build_arg_parser().parse_args(argv)
        SUBCOMMAND_HANDLERS[args.command](args)
    code = exc_info.value.code
    return code if isinstance(code, int) else 1


def _store_text(home: Path) -> str:
    return (home / ".trw" / "jev.env").read_text(encoding="utf-8")


def test_configure_reads_the_key_from_stdin_writes_0600_and_enables_the_machine(
    home: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    (home / ".trw").mkdir()
    (home / ".trw" / "config.yaml").write_text("# operator notes\ndebug: false\n", encoding="utf-8")
    monkeypatch.setattr("sys.stdin", io.StringIO(f"{_KEY}\n"))

    assert _dispatch(["assess", "configure"]) == 0

    out = capsys.readouterr()
    assert _KEY not in out.out + out.err and "...wxyz" in out.out
    store = home / ".trw" / "jev.env"
    assert stat.S_IMODE(store.stat().st_mode) == 0o600 and f"OPENROUTER_API_KEY={_KEY}" in _store_text(home)
    config = (home / ".trw" / "config.yaml").read_text(encoding="utf-8")
    assert "assess_enabled: true" in config and "# operator notes" in config and "debug: false" in config


def test_configure_takes_no_key_on_argv(home: Path) -> None:
    from trw_mcp.server._cli_argparse import _build_arg_parser

    with pytest.raises(SystemExit):
        _build_arg_parser().parse_args(["assess", "configure", _KEY])
    assert not (home / ".trw" / "jev.env").exists()


def test_configure_with_empty_stdin_writes_nothing(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    assert _dispatch(["assess", "configure"]) == 1
    assert not (home / ".trw").exists()


def test_configure_from_env_file_promotes_key_and_settings_only(
    home: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    dotenv = tmp_path / "project.env"
    dotenv.write_text(
        f"OPENROUTER_API_KEY={_KEY}\nTRW_JEV_MODEL=~typesafe/jev-latest\nTRW_JEV_ENABLED=true\nOTHER_SECRET=x\n",
        encoding="utf-8",
    )

    assert _dispatch(["assess", "configure", "--from-env-file", str(dotenv), "--no-enable"]) == 0

    text = _store_text(home)
    assert "TRW_JEV_MODEL=~typesafe/jev-latest" in text and "OTHER_SECRET" not in text and "TRW_JEV_ENABLED" not in text
    assert not (home / ".trw" / "config.yaml").exists()  # --no-enable
    assert _KEY not in capsys.readouterr().out


def test_status_names_sources_and_never_prints_the_key(
    home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO(f"{_KEY}\n"))
    _dispatch(["assess", "configure"])
    capsys.readouterr()

    assert _dispatch(["assess", "status", str(tmp_path)]) == 0

    out = capsys.readouterr().out
    assert _KEY not in out
    assert "backend: on (~/.trw/config.yaml)" in out and "key: present (...wxyz) from ~/.trw/jev.env" in out


def test_mask_key_shows_at_most_the_last_four() -> None:
    from trw_mcp.tools._assess_cli import mask_key

    assert mask_key(None) == "absent" and mask_key("short-key") == "present"
    assert mask_key(_KEY) == "present (...wxyz)"


def test_cli_label_matches_the_store_module() -> None:
    from trw_memory.decisions._machine_store import MACHINE_STORE_LABEL

    from trw_mcp.tools._assess_cli import MACHINE_STORE_LABEL as CLI_LABEL

    assert CLI_LABEL == MACHINE_STORE_LABEL


def test_install_check_inherits_a_machine_key_and_switches_the_machine_on(home: Path, tmp_path: Path) -> None:
    from trw_memory.decisions._machine_store import write_machine_store

    from trw_mcp.tools._assess_cli import install_check

    write_machine_store({"OPENROUTER_API_KEY": _KEY})
    project = tmp_path / "project"
    project.mkdir()

    line = install_check(project, offer=False)

    assert line.startswith("trw_assess: on for this project (enabled by ~/.trw/config.yaml")
    assert list(project.iterdir()) == []  # nothing written into the project
    assert _KEY not in line


def test_install_check_respects_a_project_that_switched_itself_off(home: Path, tmp_path: Path) -> None:
    from trw_memory.decisions._machine_store import write_machine_store

    from trw_mcp.tools._assess_cli import install_check

    write_machine_store({"OPENROUTER_API_KEY": _KEY})
    (tmp_path / ".trw").mkdir()
    (tmp_path / ".trw" / "config.yaml").write_text("assess_enabled: false\n", encoding="utf-8")

    line = install_check(tmp_path, offer=False)

    assert "switched off by project .trw/config.yaml" in line
    assert not (home / ".trw" / "config.yaml").exists()


def test_install_check_headless_reports_a_project_only_key_and_never_prompts(
    home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from trw_mcp.tools import _assess_cli

    (tmp_path / ".env").write_text(f"OPENROUTER_API_KEY={_KEY}\n", encoding="utf-8")
    monkeypatch.setenv("TRW_HEADLESS", "1")

    def _no_tty(*_a: object, **_k: object) -> object:
        raise AssertionError("prompted in a headless run")

    monkeypatch.setattr(_assess_cli, "open", _no_tty, raising=False)  # the /dev/tty open would fail the test

    result = _assess_cli.install_check(tmp_path, offer=True)

    assert "assess configure --from-env-file" in result and _KEY not in result
    assert not (home / ".trw" / "jev.env").exists()


def test_install_check_offer_accepted_promotes_to_the_machine(
    home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from trw_mcp.tools import _assess_cli

    project_env = f"OPENROUTER_API_KEY={_KEY}\n"
    (tmp_path / ".env").write_text(project_env, encoding="utf-8")
    monkeypatch.setattr(_assess_cli, "_ask_on_tty", lambda _q: True)

    line = _assess_cli.install_check(tmp_path, offer=True)

    assert line.startswith("trw_assess: key promoted to ~/.trw/jev.env (0600) and enabled")
    assert f"OPENROUTER_API_KEY={_KEY}" in _store_text(home)
    assert (tmp_path / ".env").read_text(encoding="utf-8") == project_env  # the project file is left as it was
    assert sorted(p.name for p in tmp_path.iterdir()) == [".env", "home"]


def test_install_check_with_nothing_configured_is_silent(home: Path, tmp_path: Path) -> None:
    from trw_mcp.tools._assess_cli import install_check

    assert install_check(tmp_path / "missing-is-fine", offer=True) == ""


def test_the_installer_template_runs_install_check_and_offers_only_when_interactive() -> None:
    template = (_PKG / "scripts" / "install-trw.template.py").read_text(encoding="utf-8")

    template_line = next(line for line in template.splitlines() if '"assess", "install-check"' in line)
    assert '["--offer"] * ui.interactive' in template_line


def test_the_bootstrap_runs_install_check_and_offers_only_when_interactive() -> None:
    shell_path = _REPO / "scripts" / "install.sh"
    if not shell_path.is_file():  # the public package tree ships the template, not the monorepo bootstrap
        pytest.skip("scripts/install.sh lives in the monorepo only")  # skip-category: layout
    shell_line = next(
        line for line in shell_path.read_text(encoding="utf-8").splitlines() if "assess install-check" in line
    )
    assert '"$HEADLESS" = true' in shell_line and '"$JSON_OUT" = true' in shell_line and "--offer" in shell_line
