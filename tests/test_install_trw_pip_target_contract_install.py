"""Behavioral regression tests for install-trw.py --pip-target hardening."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from tests._install_trw_pip_target_contract_support import _INSTALLER_PATHS, _load_installer_module


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

    def fake_run(
        cmd,
        env=None,
        stdout=None,
        stderr=None,
        *,
        capture_output=False,
        text=False,
        timeout=None,
        check=False,
        input=None,
        **_kwargs,
    ):
        run_calls.append({"cmd": cmd, "env": dict(env or {})})
        return SimpleNamespace(
            returncode=0,
            stdout='{"jsonrpc":"2.0","id":2,"result":{"tools":[{"name":"trw_session_start"}]}}\n',
            stderr="",
        )

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    wrapper_path.parent.mkdir(parents=True, exist_ok=True)

    module.phase_install_packages(
        ui,
        2,
        4,
        sys.executable,
        memory_whl,
        mcp_whl,
        pip_target=pip_target,
    )

    installed_wheels: list[tuple[str, str]] = list(pip_calls)
    for call in run_calls:
        cmd = call["cmd"]
        if isinstance(cmd, list) and "pip" in cmd and "install" in cmd:
            for arg in cmd:
                if str(arg).endswith(".whl"):
                    installed_wheels.append((str(arg), pip_target))
    assert (str(memory_whl), pip_target) in installed_wheels
    assert (str(mcp_whl), pip_target) in installed_wheels

    assert wrapper_path.read_text(encoding="utf-8") == (
        "#!/bin/bash\n"
        f"export PYTHONPATH={pip_target}:$PYTHONPATH\n"
        f'exec {sys.executable} -B -c "from trw_mcp.server import main; main()" "$@"\n'
    )
    verify_calls = [call for call in run_calls if "-c" in call["cmd"]]
    assert [call["cmd"] for call in verify_calls] == [
        [sys.executable, "-B", "-c", "import trw_memory"],
        [sys.executable, "-B", "-c", "import trw_mcp"],
    ]
    assert all(call["env"]["PYTHONPATH"] == pip_target for call in verify_calls)
    assert all(call["env"]["PYTHONDONTWRITEBYTECODE"] == "1" for call in verify_calls)
    assert all(call["env"]["PIP_NO_CACHE_DIR"] == "1" for call in verify_calls)
    assert all(call["env"]["PIP_CACHE_DIR"] == f"{pip_target}/.cache/pip" for call in verify_calls)
    assert all(call["env"]["XDG_CACHE_HOME"] == f"{pip_target}/.cache" for call in verify_calls)
    assert all(call["env"]["TMPDIR"] == f"{pip_target}/.tmp" for call in verify_calls)


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

    def fake_run(
        cmd,
        env=None,
        stdout=None,
        stderr=None,
        *,
        capture_output=False,
        text=False,
        timeout=None,
        check=False,
        **_kwargs,
    ):
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

    installed_wheels: list[str] = [p for p, _ in pip_calls]
    for call in run_calls:
        cmd = call["cmd"]
        if isinstance(cmd, list) and "pip" in cmd and "install" in cmd:
            for arg in cmd:
                if str(arg).endswith(".whl"):
                    installed_wheels.append(str(arg))
    assert str(memory_whl) in installed_wheels, f"expected memory wheel install; got {installed_wheels}"
    assert str(mcp_whl) in installed_wheels, f"expected mcp wheel install; got {installed_wheels}"
    assert not wrapper_path.exists()
    verify_calls = [call for call in run_calls if "-c" in call["cmd"]]
    assert [call["cmd"] for call in verify_calls] == [
        [sys.executable, "-B", "-c", "import trw_memory"],
        [sys.executable, "-B", "-c", "import trw_mcp"],
    ]
    assert all(call["env"]["PYTHONPATH"] == "preexisting-path" for call in verify_calls)
    assert all(call["env"]["PYTHONDONTWRITEBYTECODE"] == "1" for call in verify_calls)


@pytest.mark.parametrize("installer_path", _INSTALLER_PATHS, ids=["template", "artifact"])
def test_phase_install_extras_passes_pip_target_to_all_optional_installs(installer_path: Path, monkeypatch) -> None:
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
