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

    def fake_phase_install_extras(ui, step, total, python, install_ai, install_sqlitevec, pip_target="", offline=False):
        observed.update(
            {
                "step": step,
                "total": total,
                "python": python,
                "install_ai": install_ai,
                "install_sqlitevec": install_sqlitevec,
                "pip_target": pip_target,
                "offline": offline,
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
        "offline": False,
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
        lambda ui, step, total, python, target_dir, upgrade_only, interactive=False, ide=None, pip_target="", **_kwargs: (
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
        lambda ui, step, total, python, target_dir, upgrade_only, interactive=False, ide=None, pip_target="", **_kwargs: (
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
            or ["cursor-ide", "codex", "copilot"]
        ),
    )

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "install-trw.py",
            "--script",
            "--ide",
            "cursor-ide,codex,copilot",
            str(project_dir),
        ],
    )

    module.main()

    assert observed["ide"] == ["cursor-ide", "codex", "copilot"]


@pytest.mark.parametrize("installer_path", _INSTALLER_PATHS, ids=["template", "artifact"])
def test_upgrade_preserves_prior_identity_platform_urls_and_clients(
    installer_path: Path, tmp_path: Path, monkeypatch
) -> None:
    """Upgrade-only script reinstalls must not clobber custom config when project setup is skipped."""
    module = _load_installer_module(installer_path)
    project_dir = tmp_path / "project"
    scratch_dir = tmp_path / "scratch"
    project_dir.mkdir()
    scratch_dir.mkdir()
    observed: dict[str, object] = {}

    prior_config = {
        "project_name": "trw-framework-dev",
        "api_key": "trw_key_123",
        "platform_urls": ["http://localhost:5002"],
        "target_platforms": ["claude-code", "codex"],
    }

    monkeypatch.setattr(module, "show_banner", lambda ui: None)
    monkeypatch.setattr(module, "show_success_banner", lambda *args, **kwargs: None)
    monkeypatch.setattr(module, "_load_prior_config", lambda target_dir, ui=None: prior_config)
    monkeypatch.setattr(module, "check_python_version", lambda ui: sys.executable)
    monkeypatch.setattr(
        module, "phase_extract_wheels", lambda ui, step, total, tmpdir: (tmp_path / "m.whl", tmp_path / "c.whl")
    )
    monkeypatch.setattr(module, "phase_install_packages", lambda *args, **kwargs: None)
    monkeypatch.setattr(module, "phase_install_extras", lambda *args, **kwargs: [])
    monkeypatch.setattr(module, "phase_project_setup", lambda *args, **kwargs: [])
    monkeypatch.setattr(module, "_restart_mcp_servers", lambda target_dir, ui: None)
    monkeypatch.setattr(module, "_check_all_backends", lambda target_dir: [])
    monkeypatch.setattr(module.tempfile, "mkdtemp", lambda prefix="": str(scratch_dir))
    monkeypatch.setattr(module.shutil, "rmtree", lambda path: None)

    def fake_update_config(config_path, project_name, api_key, telemetry_enabled, **kwargs):
        observed.update(
            {
                "project_name": project_name,
                "api_key": api_key,
                "telemetry_enabled": telemetry_enabled,
                "target_platforms": kwargs.get("target_platforms"),
                "rewrite_platform_urls": kwargs.get("rewrite_platform_urls"),
            }
        )
        return True

    monkeypatch.setattr(module, "update_config", fake_update_config)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "install-trw.py",
            "--script",
            "--upgrade",
            "--skip-auth",
            str(project_dir),
        ],
    )

    module.main()

    assert observed == {
        "project_name": "trw-framework-dev",
        "api_key": "trw_key_123",
        # Telemetry consent is independent from API key presence; a mocked
        # prior config with no recorded telemetry choice must remain off.
        "telemetry_enabled": False,
        "target_platforms": None,
        "rewrite_platform_urls": False,
    }


def test_upgrade_version_metadata_preserves_stamp_without_deployed_authority(tmp_path: Path) -> None:
    """Missing deployed bodies cannot rewrite even a syntactically valid prior stamp."""
    module = _load_installer_module(_INSTALLER_PATHS[0])
    version_path = tmp_path / ".trw" / "frameworks" / "VERSION.yaml"
    version_path.parent.mkdir(parents=True)
    version_path.write_text(
        "framework_version: v25_TRW\n"
        "aaref_version: v2.0.0\n"
        "trw_mcp_version: 0.1.0\n"
        "deployed_at: '2026-01-01T00:00:00+00:00'\n",
        encoding="utf-8",
    )

    module._write_version_yaml_metadata(tmp_path)

    assert version_path.read_text(encoding="utf-8") == (
        "framework_version: v25_TRW\n"
        "aaref_version: v2.0.0\n"
        "trw_mcp_version: 0.1.0\n"
        "deployed_at: '2026-01-01T00:00:00+00:00'\n"
    )


def test_upgrade_version_metadata_parses_full_framework_version(tmp_path: Path) -> None:
    """A patch-capable body supersedes an older stamp without collapsing its version."""
    # Test the tracked source of truth. The ignored release artifact is covered by
    # the separate deterministic build + installer-drift gate, never trusted here.
    module = _load_installer_module(_INSTALLER_PATHS[0])
    frameworks = tmp_path / ".trw" / "frameworks"
    frameworks.mkdir(parents=True)
    (frameworks / "VERSION.yaml").write_text(
        "framework_version: v25_TRW\naaref_version: v2.0.0\ntrw_mcp_version: 0.1.0\n",
        encoding="utf-8",
    )
    (frameworks / "FRAMEWORK.md").write_text(
        "v26.1.1_TRW — MODEL-AGNOSTIC ENGINEERING MEMORY FRAMEWORK\n",
        encoding="utf-8",
    )
    (frameworks / "AARE-F-FRAMEWORK.md").write_text(
        "# AARE-F\n\n**Version**: 3.2.0\n",
        encoding="utf-8",
    )

    module._write_version_yaml_metadata(tmp_path)

    content = (frameworks / "VERSION.yaml").read_text(encoding="utf-8")
    assert "framework_version: v26.1.1_TRW" in content
    assert "aaref_version: v3.2.0" in content
    assert f"trw_mcp_version: {module.TRW_VERSION}" in content


def test_upgrade_version_metadata_fails_closed_when_authorities_are_missing(tmp_path: Path) -> None:
    module = _load_installer_module(_INSTALLER_PATHS[0])
    version_path = tmp_path / ".trw" / "frameworks" / "VERSION.yaml"

    module._write_version_yaml_metadata(tmp_path)

    assert not version_path.exists()


def test_upgrade_version_metadata_preserves_malformed_existing_stamp(tmp_path: Path) -> None:
    module = _load_installer_module(_INSTALLER_PATHS[0])
    version_path = tmp_path / ".trw" / "frameworks" / "VERSION.yaml"
    version_path.parent.mkdir(parents=True)
    original = "framework_version: bogus\naaref_version: nope\n"
    version_path.write_text(original, encoding="utf-8")

    module._write_version_yaml_metadata(tmp_path)

    assert version_path.read_text(encoding="utf-8") == original
