"""Regression test for 2026-04-21 iter-18 replication apparatus breakdown.

Root cause (confirmed): trw_mcp 0.46.1 wheel declares ``Requires-Dist:
trw-memory>=0.7.0,<1.0.0``. At installer time, trw-memory 0.7.0 is installed
first to ``--pip-target /tmp/trw-pip``. pip_install() for trw-mcp then checks
``_wheel_runtime_dependencies_satisfied`` which uses
``importlib_metadata.version("trw-memory")`` — but that query inspects the
container's site-packages, not the --target directory. trw-memory 0.7.0 is
invisible there (it was installed to /tmp/trw-pip), so the check returns
False, ``--no-deps`` is NOT added, and pip tries to resolve trw-memory from
PyPI. PyPI only has 0.6.11, so ``No matching distribution found for
trw-memory<1.0.0,>=0.7.0`` — the trw-mcp install fails silently. Result:
``/tmp/trw-pip/bin/trw-mcp`` is never created, opencode fails to spawn the
MCP server, trw_* tools never register, and every iter-18-replication run
fell back to bash-only execution with 0% TRW tool engagement.

This test pins the fix: when ``--pip-target`` is set, pip_install MUST pass
``--no-deps`` unconditionally for .whl packages, because the installer
bundles the complete dependency set and the runtime dependency check cannot
inspect the target directory reliably.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_TEMPLATE = Path(__file__).resolve().parent.parent / "scripts" / "install-trw.template.py"
_ARTIFACT = Path(__file__).resolve().parent.parent / "dist" / "install-trw.py"
_PATHS = [_TEMPLATE, _ARTIFACT]


def _load(installer_path: Path):
    spec = importlib.util.spec_from_file_location(
        f"install_trw_nodeps_{installer_path.stem}", installer_path
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("installer_path", _PATHS, ids=["template", "artifact"])
def test_pip_install_uses_no_deps_when_target_dir_set(
    installer_path: Path, tmp_path: Path, monkeypatch
) -> None:
    """When --pip-target is set, --no-deps must be in the pip command.

    The fix (2026-04-21): because the installer bundles both trw-memory
    and trw-mcp into the same --target, and importlib_metadata.version
    cannot see target_dir packages, we must pass --no-deps so pip does
    NOT try to resolve deps from PyPI (where the bundled version may not
    exist yet).
    """
    module = _load(installer_path)
    observed_cmds: list[list[str]] = []

    def fake_run_quiet(cmd: list[str]) -> bool:
        observed_cmds.append(list(cmd))
        return True  # Simulate success so pip_install returns True

    monkeypatch.setattr(module, "_run_quiet", fake_run_quiet)

    # Create a dummy wheel file (content doesn't matter since _run_quiet is mocked)
    fake_wheel = tmp_path / "trw_mcp-0.46.1-py3-none-any.whl"
    fake_wheel.write_text("stub")

    ui = MagicMock()
    ok = module.pip_install(
        sys.executable,
        str(fake_wheel),
        "trw-mcp",
        ui,
        target_dir=str(tmp_path / "trw-pip"),
    )

    assert ok is True, "pip_install should return True when subprocess succeeds"
    assert observed_cmds, "pip_install should have invoked _run_quiet"
    first_cmd = observed_cmds[0]
    assert "--no-deps" in first_cmd, (
        "L-fovv-regression: --no-deps MUST be present when target_dir is set "
        "for .whl packages. Without it, pip tries to resolve bundled "
        "dependencies from PyPI and fails for versions not yet published. "
        f"Got command: {first_cmd}"
    )
    assert "--target" in first_cmd
    target_idx = first_cmd.index("--target")
    assert first_cmd[target_idx + 1] == str(tmp_path / "trw-pip")


@pytest.mark.parametrize("installer_path", _PATHS, ids=["template", "artifact"])
def test_pip_install_skips_no_deps_when_target_dir_empty(
    installer_path: Path, tmp_path: Path, monkeypatch
) -> None:
    """Backward-compat: when no target_dir, pip_install behaves as before.

    The --no-deps-unconditional rule applies ONLY when --target is set.
    For ordinary virtualenv installs, pip's normal dep resolver works
    correctly against the installed environment.
    """
    module = _load(installer_path)
    observed_cmds: list[list[str]] = []

    def fake_run_quiet(cmd: list[str]) -> bool:
        observed_cmds.append(list(cmd))
        return True

    monkeypatch.setattr(module, "_run_quiet", fake_run_quiet)

    fake_wheel = tmp_path / "trw_mcp-0.46.1-py3-none-any.whl"
    fake_wheel.write_text("stub")

    ui = MagicMock()
    module.pip_install(
        sys.executable,
        str(fake_wheel),
        "trw-mcp",
        ui,
        target_dir="",  # no --target
    )

    assert observed_cmds
    first_cmd = observed_cmds[0]
    assert "--target" not in first_cmd, "no --target should appear when target_dir is empty"
    # --no-deps should NOT be forced in the ordinary install path


@pytest.mark.parametrize("installer_path", _PATHS, ids=["template", "artifact"])
def test_pip_install_non_wheel_package_does_not_get_no_deps(
    installer_path: Path, tmp_path: Path, monkeypatch
) -> None:
    """Non-.whl packages (e.g. PyPI names) use normal dep resolution.

    The --no-deps-unconditional rule applies only to .whl packages because
    those are the ones bundled by the installer. A PyPI package (if ever
    installed via pip_install in the future) should still have its deps
    resolved normally.
    """
    module = _load(installer_path)
    observed_cmds: list[list[str]] = []

    def fake_run_quiet(cmd: list[str]) -> bool:
        observed_cmds.append(list(cmd))
        return True

    monkeypatch.setattr(module, "_run_quiet", fake_run_quiet)

    ui = MagicMock()
    module.pip_install(
        sys.executable,
        "some-pypi-package",  # Not a .whl
        "some-pypi-package",
        ui,
        target_dir=str(tmp_path / "trw-pip"),
    )

    assert observed_cmds
    first_cmd = observed_cmds[0]
    assert "--no-deps" not in first_cmd, (
        "--no-deps should only be forced for .whl packages, not pypi names"
    )
