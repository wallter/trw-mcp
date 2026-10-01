"""The installer runs project commands through the trw-mcp it installed, never the first one on PATH (P0, 2026-10-01).

``find_trw_cmd`` preferred ``shutil.which("trw-mcp")``. On a machine holding two installs that is whichever copy PATH
lists first, so ``update-project`` ran from the stale 8.1.2 over a fresh 8.1.5 install and re-rendered the project's
hooks from the wrong code. The shadow warning only printed at the very end, after every command had already run.

Every case uses fake interpreters and binaries in a temp dir: no pip, no network, no real trw-mcp.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from tests._install_trw_pip_target_contract_support import _INSTALLER_PATHS, _load_installer_module

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="the stubs are POSIX shell scripts")

_BOTH = pytest.mark.parametrize("installer_path", _INSTALLER_PATHS, ids=["template", "artifact"])


def _executable(path: Path, body: str = "#!/bin/sh\nexit 0\n") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)
    return path


class _Machine:
    """A target interpreter whose RECORD names an installed ``trw-mcp``, and a stale one first on PATH."""

    def __init__(self, root: Path, monkeypatch: pytest.MonkeyPatch, *, record: bool = True) -> None:
        self.installed = _executable(root / "target-env" / "bin" / "trw-mcp")
        self.stale = _executable(root / "stale-bin" / "trw-mcp")
        # Answers the installer's probes for the target interpreter: the distribution RECORD (the installed
        # console script), the installed version, and "importable".
        record_line = f'echo "{self.installed}"' if record else ":"
        self.python = _executable(
            root / "target-env" / "bin" / "python",
            "#!/bin/sh\n"
            'case "$*" in\n'
            f"  *\"m.distribution('trw-mcp')\"*) {record_line} ;;\n"
            '  *"m.version("*) echo 99.0.0 ;;\n'
            "  *) exit 0 ;;\n"
            "esac\n",
        )
        monkeypatch.setenv("PATH", f"{self.stale.parent}{os.pathsep}{os.environ.get('PATH', '')}")


@_BOTH
def test_find_trw_cmd_runs_the_trw_mcp_its_target_interpreter_installed_not_the_first_on_path(
    installer_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_installer_module(installer_path)
    machine = _Machine(tmp_path, monkeypatch)

    assert module.find_trw_cmd(str(machine.python)) == [str(machine.installed)]


@_BOTH
def test_find_trw_cmd_without_a_recorded_script_runs_the_target_interpreter_never_the_path_copy(
    installer_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_installer_module(installer_path)
    machine = _Machine(tmp_path, monkeypatch, record=False)

    assert module.find_trw_cmd(str(machine.python)) == [str(machine.python), "-B", "-m", "trw_mcp.server"]


@_BOTH
def test_project_setup_runs_every_command_through_the_installed_trw_mcp_with_a_stale_one_first_on_path(
    installer_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_installer_module(installer_path)
    machine = _Machine(tmp_path, monkeypatch)
    target_dir = tmp_path / "project"
    (target_dir / ".git").mkdir(parents=True)
    run_calls: list[list[str]] = []
    monkeypatch.setattr(module, "_detect_installed_clis", list)
    monkeypatch.setattr(module, "_detect_project_ides", lambda _path: ["cursor-ide", "codex"])
    monkeypatch.setattr(module, "run_with_progress", lambda _ui, _label, cmd, **_k: run_calls.append(cmd) or True)

    module.phase_project_setup(
        MagicMock(), 3, 4, str(machine.python), target_dir, False, interactive=False, ide=["cursor-ide", "codex"]
    )

    assert run_calls, "project setup ran nothing"
    assert {call[0] for call in run_calls} == {str(machine.installed)}, run_calls


def _drive_install_that_keeps_a_newer_install(module, machine: _Machine, tmp_path: Path, ui: MagicMock) -> None:
    """``phase_install_packages`` on the branch where a newer trw-mcp is already resident: no pip, no network."""
    module.TRW_VERSION = "1.0.0"
    memory_whl = tmp_path / "wheels" / "trw_memory-1.0.0-py3-none-any.whl"
    mcp_whl = tmp_path / "wheels" / "trw_mcp-1.0.0-py3-none-any.whl"
    for wheel in (memory_whl, mcp_whl):
        wheel.parent.mkdir(parents=True, exist_ok=True)
        wheel.write_bytes(b"")
    returned = module.phase_install_packages(ui, 2, 4, str(machine.python), memory_whl, mcp_whl)
    assert returned == str(machine.python)


def _warnings(ui: MagicMock) -> str:
    return "\n".join(str(call.args[0]) for call in ui.step_warn.call_args_list)


@_BOTH
def test_a_different_trw_mcp_on_path_is_named_beside_the_installed_one_before_anything_runs(
    installer_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_installer_module(installer_path)
    machine = _Machine(tmp_path, monkeypatch)
    ui = MagicMock()

    _drive_install_that_keeps_a_newer_install(module, machine, tmp_path, ui)

    warned = _warnings(ui)
    assert str(machine.stale) in warned, warned
    assert str(machine.installed) in warned, warned
    assert "first on your PATH" in warned, warned


@_BOTH
def test_no_warning_when_path_resolves_to_the_installed_trw_mcp_through_a_symlink(
    installer_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_installer_module(installer_path)
    machine = _Machine(tmp_path, monkeypatch)
    link = tmp_path / "pipx-bin" / "trw-mcp"
    link.parent.mkdir()
    link.symlink_to(machine.installed)
    monkeypatch.setenv("PATH", f"{link.parent}{os.pathsep}{os.environ['PATH']}")
    ui = MagicMock()

    _drive_install_that_keeps_a_newer_install(module, machine, tmp_path, ui)

    assert "first on your PATH" not in _warnings(ui), _warnings(ui)


@_BOTH
def test_no_warning_when_path_holds_the_launcher_shim_this_installer_wrote_for_the_installed_trw_mcp(
    installer_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_installer_module(installer_path)
    machine = _Machine(tmp_path, monkeypatch)
    shim = _executable(
        tmp_path / "local-bin" / "trw-mcp",
        f'#!/usr/bin/env bash\n# TRW launcher shim -> managed install. Regenerated by the installer; do not edit.\nexec "{machine.installed}" "$@"\n',
    )
    monkeypatch.setenv("PATH", f"{shim.parent}{os.pathsep}{os.environ['PATH']}")
    ui = MagicMock()

    _drive_install_that_keeps_a_newer_install(module, machine, tmp_path, ui)

    assert "first on your PATH" not in _warnings(ui), _warnings(ui)


@_BOTH
def test_the_shadow_warning_prints_normalized_paths_not_dotdot_segments(
    installer_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """E2E-INC-139 b: a RECORD-derived binary path reads ``site-packages/../../../bin/trw-mcp``; print it collapsed."""
    module = _load_installer_module(installer_path)
    machine = _Machine(tmp_path, monkeypatch)
    roundabout = tmp_path / "target-env" / "lib" / "site-packages" / ".." / ".." / "bin" / "trw-mcp"
    (tmp_path / "target-env" / "lib" / "site-packages").mkdir(parents=True)
    machine.installed.parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / "target-env" / "lib").mkdir(exist_ok=True)
    monkeypatch.setattr(module, "_MCP_TARGET_BINARY", str(roundabout))
    ui = MagicMock()

    module._warn_if_another_trw_mcp_is_first_on_path(ui)

    warned = _warnings(ui)
    assert ".." not in warned, warned
    assert str(machine.installed) in warned, warned


@_BOTH
@pytest.mark.parametrize("quiet", [True, False], ids=["quiet", "plain"])
def test_the_banner_reports_the_resident_version_when_the_downgrade_guard_kept_a_newer_one(
    installer_path: Path, quiet: bool, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """E2E-INC-139 c: "TRW Framework v<bundle> installed" was printed over a kept, newer resident install."""
    module = _load_installer_module(installer_path)
    monkeypatch.setattr(module, "TRW_VERSION", "8.1.5")
    monkeypatch.setattr(module, "_MCP_EFFECTIVE_VERSION", "9.0.0")
    ui = MagicMock()
    ui.quiet, ui.interactive = quiet, False

    module.show_success_banner(ui, "offline", [])

    out = capsys.readouterr().out
    assert "v9.0.0" in out, out
    assert "8.1.5" not in out, out


@_BOTH
def test_the_banner_falls_back_to_the_bundle_version_when_no_resident_version_was_decided(
    installer_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_installer_module(installer_path)
    monkeypatch.setattr(module, "TRW_VERSION", "8.1.5")
    monkeypatch.setattr(module, "_MCP_EFFECTIVE_VERSION", None)
    ui = MagicMock()
    ui.quiet = True

    module.show_success_banner(ui, "offline", [])

    assert "v8.1.5" in capsys.readouterr().out
