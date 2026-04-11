"""Behavioral regression tests for install-trw.py --pip-target hardening."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

_INSTALLER_TEMPLATE = Path(__file__).resolve().parent.parent / "scripts" / "install-trw.template.py"
_INSTALLER_ARTIFACT = Path(__file__).resolve().parent.parent / "dist" / "install-trw.py"
_INSTALLER_PATHS = [_INSTALLER_TEMPLATE, _INSTALLER_ARTIFACT]


def _load_installer_module(installer_path: Path):
    spec = importlib.util.spec_from_file_location(f"install_trw_test_{installer_path.stem}", installer_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("installer_path", _INSTALLER_PATHS, ids=["template", "artifact"])
def test_phase_install_packages_writes_wrapper_and_verifies_imports_from_pip_target(
    installer_path: Path, tmp_path: Path, monkeypatch
) -> None:
    module = _load_installer_module(installer_path)
    ui = MagicMock()
    pip_target = str(tmp_path / "trw-pip")
    wrapper_path = Path(pip_target) / "bin" / "trw-mcp"
    memory_whl = tmp_path / "trw-memory.whl"
    mcp_whl = tmp_path / "trw-mcp.whl"
    pip_calls: list[tuple[str, str]] = []
    run_calls: list[dict[str, object]] = []

    monkeypatch.setattr(
        module,
        "pip_install",
        lambda python, package, label, ui, target_dir="": pip_calls.append((package, target_dir)) or True,
    )

    def fake_run(cmd, env=None, stdout=None, stderr=None):
        run_calls.append({"cmd": cmd, "env": dict(env or {})})
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(module.subprocess, "run", fake_run)

    module.phase_install_packages(
        ui,
        2,
        4,
        sys.executable,
        memory_whl,
        mcp_whl,
        pip_target=pip_target,
    )

    assert pip_calls == [
        (str(memory_whl), pip_target),
        (str(mcp_whl), pip_target),
    ]
    assert wrapper_path.read_text(encoding="utf-8") == (
        "#!/bin/bash\n"
        f"export PYTHONPATH={pip_target}:$PYTHONPATH\n"
        f'exec {sys.executable} -B -c "from trw_mcp.server import main; main()" "$@"\n'
    )
    assert [call["cmd"] for call in run_calls] == [
        [sys.executable, "-B", "-c", "import trw_memory"],
        [sys.executable, "-B", "-c", "import trw_mcp"],
    ]
    assert all(call["env"]["PYTHONPATH"] == pip_target for call in run_calls)
    assert all(call["env"]["PYTHONDONTWRITEBYTECODE"] == "1" for call in run_calls)
    assert all(call["env"]["PIP_NO_CACHE_DIR"] == "1" for call in run_calls)
    assert all(call["env"]["PIP_CACHE_DIR"] == f"{pip_target}/.cache/pip" for call in run_calls)
    assert all(call["env"]["XDG_CACHE_HOME"] == f"{pip_target}/.cache" for call in run_calls)
    assert all(call["env"]["TMPDIR"] == f"{pip_target}/.tmp" for call in run_calls)


@pytest.mark.parametrize("installer_path", _INSTALLER_PATHS, ids=["template", "artifact"])
def test_phase_install_packages_keeps_default_install_behavior_without_pip_target(
    installer_path: Path, tmp_path: Path, monkeypatch
) -> None:
    module = _load_installer_module(installer_path)
    ui = MagicMock()
    wrapper_path = tmp_path / "trw-pip" / "bin" / "trw-mcp"
    memory_whl = tmp_path / "trw-memory.whl"
    mcp_whl = tmp_path / "trw-mcp.whl"
    pip_calls: list[tuple[str, str]] = []
    run_calls: list[dict[str, object]] = []

    monkeypatch.setattr(
        module,
        "pip_install",
        lambda python, package, label, ui, target_dir="": pip_calls.append((package, target_dir)) or True,
    )
    monkeypatch.setenv("PYTHONPATH", "preexisting-path")

    def fake_run(cmd, env=None, stdout=None, stderr=None):
        run_calls.append({"cmd": cmd, "env": dict(env or {})})
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(module.subprocess, "run", fake_run)

    module.phase_install_packages(
        ui,
        2,
        4,
        sys.executable,
        memory_whl,
        mcp_whl,
    )

    assert pip_calls == [
        (str(memory_whl), ""),
        (str(mcp_whl), ""),
    ]
    assert not wrapper_path.exists()
    assert [call["cmd"] for call in run_calls] == [
        [sys.executable, "-B", "-c", "import trw_memory"],
        [sys.executable, "-B", "-c", "import trw_mcp"],
    ]
    assert all(call["env"]["PYTHONPATH"] == "preexisting-path" for call in run_calls)
    assert all(call["env"]["PYTHONDONTWRITEBYTECODE"] == "1" for call in run_calls)


@pytest.mark.parametrize("installer_path", _INSTALLER_PATHS, ids=["template", "artifact"])
def test_pip_install_disables_bytecode_writes(installer_path: Path, monkeypatch) -> None:
    module = _load_installer_module(installer_path)
    ui = MagicMock()
    calls: list[dict[str, object]] = []

    def fake_run(cmd, env=None, stdout=None, stderr=None, timeout=None):
        calls.append({"cmd": cmd, "env": dict(env or {}), "timeout": timeout})
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    assert module.pip_install(sys.executable, "trw-mcp[ai]", "trw-mcp", ui, target_dir="/tmp/trw-pip") is True
    assert [call["cmd"] for call in calls] == [
        [sys.executable, "-B", "-m", "pip", "install", "--upgrade", "--quiet", "--target", "/tmp/trw-pip", "trw-mcp[ai]"]
    ]
    assert all(call["env"]["PYTHONDONTWRITEBYTECODE"] == "1" for call in calls)
    assert all(call["env"]["PIP_NO_CACHE_DIR"] == "1" for call in calls)
    assert all(call["env"]["PIP_CACHE_DIR"] == "/tmp/trw-pip/.cache/pip" for call in calls)
    assert all(call["env"]["XDG_CACHE_HOME"] == "/tmp/trw-pip/.cache" for call in calls)
    assert all(call["env"]["TMPDIR"] == "/tmp/trw-pip/.tmp" for call in calls)


@pytest.mark.parametrize("installer_path", _INSTALLER_PATHS, ids=["template", "artifact"])
def test_phase_install_extras_passes_pip_target_to_all_optional_installs(
    installer_path: Path, monkeypatch
) -> None:
    module = _load_installer_module(installer_path)
    ui = MagicMock()
    pip_calls: list[tuple[str, str]] = []

    monkeypatch.setattr(
        module,
        "pip_install",
        lambda python, package, label, ui, target_dir="": pip_calls.append((package, target_dir)) or True,
    )

    features = module.phase_install_extras(
        ui,
        3,
        5,
        sys.executable,
        install_ai=True,
        install_sqlitevec=True,
        pip_target="/tmp/trw-pip",
    )

    assert pip_calls == [
        ("trw-mcp[ai]", "/tmp/trw-pip"),
        ("sentence-transformers>=2.0.0", "/tmp/trw-pip"),
        ("sqlite-vec", "/tmp/trw-pip"),
    ]
    assert features == ["AI/LLM", "embeddings", "sqlite-vec"]


@pytest.mark.parametrize("installer_path", _INSTALLER_PATHS, ids=["template", "artifact"])
def test_validate_pip_target_rejects_shell_metacharacters(installer_path: Path) -> None:
    module = _load_installer_module(installer_path)

    assert module.validate_pip_target("/tmp/trw-pip/subdir") == "/tmp/trw-pip/subdir"

    for invalid in ("", "/tmp/trw-pip;rm -rf /", "$(pwd)"):
        if invalid == "":
            assert module.validate_pip_target(invalid) == ""
        else:
            try:
                module.validate_pip_target(invalid)
            except ValueError as exc:
                assert "Invalid --pip-target" in str(exc)
            else:
                raise AssertionError(f"{invalid!r} should be rejected")


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
    monkeypatch.setattr(module, "_load_prior_config", lambda target_dir: {})
    monkeypatch.setattr(module, "check_python_version", lambda ui: sys.executable)
    monkeypatch.setattr(module, "phase_extract_wheels", lambda ui, step, total, tmpdir: (tmp_path / "m.whl", tmp_path / "c.whl"))
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
