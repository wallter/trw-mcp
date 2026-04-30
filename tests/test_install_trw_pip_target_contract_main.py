"""Behavioral regression tests for install-trw.py --pip-target hardening."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from tests._install_trw_pip_target_contract_support import _INSTALLER_PATHS, _load_installer_module


@pytest.mark.parametrize("installer_path", _INSTALLER_PATHS, ids=["template", "artifact"])
def test_main_threads_pip_target_into_extras_phase_when_enabled(
    installer_path: Path, tmp_path: Path, monkeypatch
) -> None:
    module = _load_installer_module(installer_path)
    project_dir = tmp_path / "project"
    scratch_dir = tmp_path / "scratch"
    project_dir.mkdir()
    scratch_dir.mkdir()
    observed: dict[str, object] = {}

    monkeypatch.setattr(module, "show_banner", lambda ui: None)
    monkeypatch.setattr(module, "show_success_banner", lambda *args, **kwargs: None)
    monkeypatch.setattr(module, "_load_prior_config", lambda target_dir, ui=None: {})
    monkeypatch.setattr(module, "check_python_version", lambda ui: sys.executable)
    monkeypatch.setattr(
        module, "phase_extract_wheels", lambda ui, step, total, tmpdir: (tmp_path / "m.whl", tmp_path / "c.whl")
    )
    monkeypatch.setattr(module, "phase_install_packages", lambda *args, **kwargs: None)
    monkeypatch.setattr(module, "phase_project_setup", lambda *args, **kwargs: None)
    monkeypatch.setattr(module, "_restart_mcp_servers", lambda target_dir, ui: None)
    monkeypatch.setattr(module.tempfile, "mkdtemp", lambda prefix="": str(scratch_dir))
    monkeypatch.setattr(module.shutil, "rmtree", lambda path: None)

    def fake_phase_install_extras(ui, step, total, python, install_ai, install_sqlitevec, pip_target=""):
        observed.update(
            {
                "step": step,
                "total": total,
                "python": python,
                "install_ai": install_ai,
                "install_sqlitevec": install_sqlitevec,
                "pip_target": pip_target,
            }
        )
        return ["AI/LLM", "embeddings", "sqlite-vec"]

    monkeypatch.setattr(module, "phase_install_extras", fake_phase_install_extras)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "install-trw.py",
            "--script",
            "--ai",
            "--sqlite-vec",
            "--pip-target",
            "/tmp/trw-pip",
            str(project_dir),
        ],
    )

    module.main()

    assert observed == {
        "step": 3,
        "total": 4,
        "python": sys.executable,
        "install_ai": True,
        "install_sqlitevec": True,
        "pip_target": "/tmp/trw-pip",
    }


@pytest.mark.parametrize("installer_path", _INSTALLER_PATHS, ids=["template", "artifact"])
def test_main_threads_pip_target_into_project_setup(installer_path: Path, tmp_path: Path, monkeypatch) -> None:
    module = _load_installer_module(installer_path)
    project_dir = tmp_path / "project"
    scratch_dir = tmp_path / "scratch"
    project_dir.mkdir()
    scratch_dir.mkdir()
    observed: dict[str, object] = {}

    monkeypatch.setattr(module, "show_banner", lambda ui: None)
    monkeypatch.setattr(module, "show_success_banner", lambda *args, **kwargs: None)
    monkeypatch.setattr(module, "_load_prior_config", lambda target_dir, ui=None: {})
    monkeypatch.setattr(module, "check_python_version", lambda ui: sys.executable)
    monkeypatch.setattr(
        module, "phase_extract_wheels", lambda ui, step, total, tmpdir: (tmp_path / "m.whl", tmp_path / "c.whl")
    )
    monkeypatch.setattr(module, "phase_install_packages", lambda *args, **kwargs: None)
    monkeypatch.setattr(module, "phase_install_extras", lambda *args, **kwargs: [])
    monkeypatch.setattr(module, "_restart_mcp_servers", lambda target_dir, ui: None)
    monkeypatch.setattr(module.tempfile, "mkdtemp", lambda prefix="": str(scratch_dir))
    monkeypatch.setattr(module.shutil, "rmtree", lambda path: None)
    monkeypatch.setattr(
        module,
        "phase_project_setup",
        lambda ui, step, total, python, target_dir, upgrade_only, interactive=False, ide=None, pip_target="": (
            observed.update(
                {
                    "step": step,
                    "total": total,
                    "python": python,
                    "target_dir": target_dir,
                    "upgrade_only": upgrade_only,
                    "interactive": interactive,
                    "ide": ide,
                    "pip_target": pip_target,
                }
            )
        ),
    )

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "install-trw.py",
            "--script",
            "--pip-target",
            "/tmp/trw-pip",
            str(project_dir),
        ],
    )

    module.main()

    assert observed == {
        "step": 3,
        "total": 3,
        "python": sys.executable,
        "target_dir": project_dir,
        "upgrade_only": False,
        "interactive": False,
        "ide": None,
        "pip_target": "/tmp/trw-pip",
    }


@pytest.mark.parametrize("installer_path", _INSTALLER_PATHS, ids=["template", "artifact"])
def test_main_parses_multi_client_ide_argument_for_project_setup(
    installer_path: Path, tmp_path: Path, monkeypatch
) -> None:
    module = _load_installer_module(installer_path)
    project_dir = tmp_path / "project"
    scratch_dir = tmp_path / "scratch"
    project_dir.mkdir()
    scratch_dir.mkdir()
    observed: dict[str, object] = {}

    monkeypatch.setattr(module, "show_banner", lambda ui: None)
    monkeypatch.setattr(module, "show_success_banner", lambda *args, **kwargs: None)
    monkeypatch.setattr(module, "_load_prior_config", lambda target_dir, ui=None: {})
    monkeypatch.setattr(module, "check_python_version", lambda ui: sys.executable)
    monkeypatch.setattr(
        module, "phase_extract_wheels", lambda ui, step, total, tmpdir: (tmp_path / "m.whl", tmp_path / "c.whl")
    )
    monkeypatch.setattr(module, "phase_install_packages", lambda *args, **kwargs: None)
    monkeypatch.setattr(module, "phase_install_extras", lambda *args, **kwargs: [])
    monkeypatch.setattr(module, "_restart_mcp_servers", lambda target_dir, ui: None)
    monkeypatch.setattr(module.tempfile, "mkdtemp", lambda prefix="": str(scratch_dir))
    monkeypatch.setattr(module.shutil, "rmtree", lambda path: None)
    monkeypatch.setattr(
        module,
        "phase_project_setup",
        lambda ui, step, total, python, target_dir, upgrade_only, interactive=False, ide=None, pip_target="": (
            observed.update(
                {
                    "step": step,
                    "total": total,
                    "python": python,
                    "target_dir": target_dir,
                    "upgrade_only": upgrade_only,
                    "interactive": interactive,
                    "ide": ide,
                    "pip_target": pip_target,
                }
            )
            or ["cursor-ide", "codex", "gemini"]
        ),
    )

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "install-trw.py",
            "--script",
            "--ide",
            "cursor-ide,codex,gemini",
            str(project_dir),
        ],
    )

    module.main()

    assert observed["ide"] == ["cursor-ide", "codex", "gemini"]
