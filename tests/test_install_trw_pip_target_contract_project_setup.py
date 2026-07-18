"""Behavioral regression tests for install-trw.py --pip-target hardening."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from tests._install_trw_pip_target_contract_support import _INSTALLER_PATHS, _load_installer_module


@pytest.mark.parametrize("installer_path", _INSTALLER_PATHS, ids=["template", "artifact"])
def test_find_trw_cmd_prefers_pip_target_wrapper(installer_path: Path, tmp_path: Path, monkeypatch) -> None:
    module = _load_installer_module(installer_path)
    wrapper = tmp_path / "trw-pip" / "bin" / "trw-mcp"
    wrapper.parent.mkdir(parents=True)
    wrapper.write_text("#!/bin/bash\n", encoding="utf-8")
    wrapper.chmod(0o755)

    monkeypatch.setattr(module.shutil, "which", lambda name: "/usr/local/bin/trw-mcp")

    assert module.find_trw_cmd(sys.executable, pip_target=str(tmp_path / "trw-pip")) == [str(wrapper)]


@pytest.mark.parametrize("installer_path", _INSTALLER_PATHS, ids=["template", "artifact"])
def test_parse_ide_argument_supports_comma_separated_multi_select(installer_path: Path) -> None:
    module = _load_installer_module(installer_path)

    assert module._parse_ide_argument("cursor-ide,codex,copilot") == ["cursor-ide", "codex", "copilot"]
    assert module._parse_ide_argument("all") == module._SUPPORTED_IDES


@pytest.mark.parametrize("installer_path", _INSTALLER_PATHS, ids=["template", "artifact"])
def test_phase_project_setup_prefers_pip_target_wrapper_for_multi_client_setup(
    installer_path: Path, tmp_path: Path, monkeypatch
) -> None:
    module = _load_installer_module(installer_path)
    ui = MagicMock()
    target_dir = tmp_path / "project"
    wrapper = tmp_path / "trw-pip" / "bin" / "trw-mcp"
    target_dir.mkdir()
    (target_dir / ".git").mkdir()
    wrapper.parent.mkdir(parents=True)
    wrapper.write_text("#!/bin/bash\n", encoding="utf-8")
    wrapper.chmod(0o755)

    run_calls: list[list[str]] = []

    monkeypatch.setattr(module, "_detect_installed_clis", list)
    monkeypatch.setattr(module, "_detect_project_ides", lambda _path: ["cursor-ide", "codex"])
    monkeypatch.setattr(module, "run_with_progress", lambda _ui, _label, cmd: run_calls.append(cmd) or True)

    selected = module.phase_project_setup(
        ui,
        3,
        4,
        sys.executable,
        target_dir,
        False,
        interactive=False,
        ide=["cursor-ide", "codex"],
        pip_target=str(tmp_path / "trw-pip"),
    )

    assert selected == ["cursor-ide", "codex"]
    assert run_calls == [
        [str(wrapper), "init-project", str(target_dir), "--ide", "cursor-ide"],
        [str(wrapper), "update-project", str(target_dir), "--ide", "codex"],
    ]
