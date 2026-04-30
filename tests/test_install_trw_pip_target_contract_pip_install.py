"""Behavioral regression tests for install-trw.py --pip-target hardening."""

from __future__ import annotations

import sys
import zipfile
from collections.abc import Sequence
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from tests._install_trw_pip_target_contract_support import _INSTALLER_PATHS, _load_installer_module


@pytest.mark.parametrize("installer_path", _INSTALLER_PATHS, ids=["template", "artifact"])
def test_pip_install_disables_bytecode_writes(installer_path: Path, monkeypatch) -> None:
    module = _load_installer_module(installer_path)
    ui = MagicMock()
    calls: list[dict[str, object]] = []

    def fake_run(
        cmd,
        env=None,
        stdout=None,
        stderr=None,
        timeout=None,
        *,
        capture_output=False,
        text=False,
        check=False,
        **_kwargs,
    ):
        calls.append({"cmd": cmd, "env": dict(env or {}), "timeout": timeout})
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    assert module.pip_install(sys.executable, "trw-mcp[ai]", "trw-mcp", ui, target_dir="/tmp/trw-pip") is True
    assert [call["cmd"] for call in calls] == [
        [
            sys.executable,
            "-B",
            "-m",
            "pip",
            "install",
            "--upgrade",
            "--quiet",
            "--target",
            "/tmp/trw-pip",
            "trw-mcp[ai]",
        ]
    ]
    assert all(call["env"]["PYTHONDONTWRITEBYTECODE"] == "1" for call in calls)
    assert all(call["env"]["PIP_NO_CACHE_DIR"] == "1" for call in calls)
    assert all(call["env"]["PIP_CACHE_DIR"] == "/tmp/trw-pip/.cache/pip" for call in calls)
    assert all(call["env"]["XDG_CACHE_HOME"] == "/tmp/trw-pip/.cache" for call in calls)
    assert all(call["env"]["TMPDIR"] == "/tmp/trw-pip/.tmp" for call in calls)


@pytest.mark.parametrize("installer_path", _INSTALLER_PATHS, ids=["template", "artifact"])
def test_pip_install_adds_no_deps_for_target_wheels_when_runtime_deps_are_already_present(
    installer_path: Path, monkeypatch
) -> None:
    module = _load_installer_module(installer_path)
    ui = MagicMock()
    calls: list[list[str]] = []

    monkeypatch.setattr(module, "_wheel_runtime_dependencies_satisfied", lambda wheel_path: True)
    monkeypatch.setattr(
        module.subprocess,
        "run",
        lambda cmd, env=None, stdout=None, stderr=None, timeout=None: (
            calls.append(cmd) or SimpleNamespace(returncode=0)
        ),
    )

    assert module.pip_install(
        sys.executable, "/tmp/trw_mcp-0.41.1-py3-none-any.whl", "trw-mcp", ui, target_dir="/tmp/trw-pip"
    )
    assert calls == [
        [
            sys.executable,
            "-B",
            "-m",
            "pip",
            "install",
            "--upgrade",
            "--quiet",
            "--no-deps",
            "--target",
            "/tmp/trw-pip",
            "/tmp/trw_mcp-0.41.1-py3-none-any.whl",
        ]
    ]


@pytest.mark.parametrize("installer_path", _INSTALLER_PATHS, ids=["template", "artifact"])
def test_pip_install_keeps_dependency_resolution_for_target_packages_when_runtime_deps_are_missing(
    installer_path: Path, monkeypatch
) -> None:
    module = _load_installer_module(installer_path)
    ui = MagicMock()
    calls: list[list[str]] = []

    monkeypatch.setattr(module, "_wheel_runtime_dependencies_satisfied", lambda wheel_path: False)
    monkeypatch.setattr(
        module.subprocess,
        "run",
        lambda cmd, env=None, stdout=None, stderr=None, timeout=None: (
            calls.append(cmd) or SimpleNamespace(returncode=0)
        ),
    )

    assert module.pip_install(
        sys.executable, "/tmp/trw_mcp-0.41.1-py3-none-any.whl", "trw-mcp", ui, target_dir="/tmp/trw-pip"
    )
    assert calls == [
        [
            sys.executable,
            "-B",
            "-m",
            "pip",
            "install",
            "--upgrade",
            "--quiet",
            "--target",
            "/tmp/trw-pip",
            "/tmp/trw_mcp-0.41.1-py3-none-any.whl",
        ]
    ]


@pytest.mark.parametrize("installer_path", _INSTALLER_PATHS, ids=["template", "artifact"])
def test_wheel_runtime_dependencies_satisfied_parses_requires_dist(
    installer_path: Path, tmp_path: Path, monkeypatch
) -> None:
    module = _load_installer_module(installer_path)
    wheel_path = tmp_path / "demo-0.1.0-py3-none-any.whl"
    with zipfile.ZipFile(wheel_path, "w") as wheel:
        wheel.writestr(
            "demo-0.1.0.dist-info/METADATA",
            (
                "Metadata-Version: 2.1\n"
                "Name: demo\n"
                "Version: 0.1.0\n"
                "Requires-Dist: fastmcp>=3.0.0\n"
                "Requires-Dist: platformdirs>=4.0.0; python_version >= '3.10'\n"
            ),
        )

    versions = {"fastmcp": "3.2.3", "platformdirs": "4.9.6"}
    monkeypatch.setattr(module.importlib_metadata, "version", lambda name: versions[name])

    assert module._wheel_runtime_dependencies_satisfied(wheel_path) is True


@pytest.mark.parametrize("installer_path", _INSTALLER_PATHS, ids=["template", "artifact"])
def test_wheel_runtime_dependencies_satisfied_returns_false_when_a_dependency_is_missing(
    installer_path: Path, tmp_path: Path, monkeypatch
) -> None:
    module = _load_installer_module(installer_path)
    wheel_path = tmp_path / "demo-0.1.0-py3-none-any.whl"
    with zipfile.ZipFile(wheel_path, "w") as wheel:
        wheel.writestr(
            "demo-0.1.0.dist-info/METADATA",
            ("Metadata-Version: 2.1\nName: demo\nVersion: 0.1.0\nRequires-Dist: missing-dep>=1.0.0\n"),
        )

    def fake_version(name: str) -> str:
        raise module.importlib_metadata.PackageNotFoundError

    monkeypatch.setattr(module.importlib_metadata, "version", fake_version)

    assert module._wheel_runtime_dependencies_satisfied(wheel_path) is False


@pytest.mark.parametrize("installer_path", _INSTALLER_PATHS, ids=["template", "artifact"])
def test_wheel_runtime_dependencies_satisfied_returns_false_when_packaging_is_unavailable(
    installer_path: Path, tmp_path: Path
) -> None:
    module = _load_installer_module(installer_path)
    wheel_path = tmp_path / "demo-0.1.0-py3-none-any.whl"
    with zipfile.ZipFile(wheel_path, "w") as wheel:
        wheel.writestr(
            "demo-0.1.0.dist-info/METADATA",
            ("Metadata-Version: 2.1\nName: demo\nVersion: 0.1.0\nRequires-Dist: fastmcp>=3.0.0\n"),
        )

    real_import = __import__

    def fake_import(
        name: str,
        globals: dict[str, Any] | None = None,
        locals: dict[str, Any] | None = None,
        fromlist: Sequence[str] = (),
        level: int = 0,
    ) -> Any:
        if name == "packaging.requirements":
            raise ModuleNotFoundError("No module named 'packaging'")
        return real_import(name, globals, locals, fromlist, level)

    with patch("builtins.__import__", side_effect=fake_import):
        assert module._wheel_runtime_dependencies_satisfied(wheel_path) is False


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
