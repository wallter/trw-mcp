"""Regression test for 2026-04-21 iter-18 replication-v2 apparatus breakdown.

Final root cause (after correction): trw_mcp 0.46.1 declares
``Requires-Dist: trw-memory>=0.7.0,<1.0.0``. Installing wheels sequentially
with one pip invocation per wheel caused one of two failure modes:

1. WITHOUT --no-deps (original behavior, iter-18-replication v1): pip's
   resolver for the trw-mcp install reached PyPI for trw-memory but only
   0.6.11 was there, failing the install silently.

2. WITH --no-deps forced (iter-18-replication v2 attempt): pip skipped
   ALL deps including the external transitive deps (structlog, pydantic,
   ruamel-yaml). trw-mcp installed but crashed on import in container
   Python that lacked the dev env's pre-installed deps. Same end-result
   as v1: zero trw_* tool registration in eval containers.

Correct fix: install BOTH bundled wheels in ONE pip invocation with
``--find-links <wheel_dir>``. pip's resolver then satisfies the internal
trw-mcp → trw-memory dependency from the bundled wheel WHILE still
fetching external transitive deps (structlog, pydantic, etc.) from PyPI
normally. This test pins the command-shape of that combined invocation.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

_TEMPLATE = Path(__file__).resolve().parent.parent / "scripts" / "install-trw.template.py"
_ARTIFACT = Path(__file__).resolve().parent.parent / "dist" / "install-trw.py"
_PATHS = [_TEMPLATE, _ARTIFACT]


def _load(installer_path: Path):
    spec = importlib.util.spec_from_file_location(
        f"install_trw_combined_{installer_path.stem}", installer_path
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("installer_path", _PATHS, ids=["template", "artifact"])
def test_phase_install_packages_uses_combined_find_links(
    installer_path: Path, tmp_path: Path, monkeypatch
) -> None:
    """phase_install_packages must install both wheels in ONE pip call with
    --find-links pointing at the wheel directory. Pins the combined-invocation
    contract that fixes the iter-18-replication-v2 structlog-missing regression.
    """
    module = _load(installer_path)
    observed_runs: list[list[str]] = []

    def fake_run(cmd, env=None, stdout=None, stderr=None, *, capture_output=False, text=False, timeout=None, check=False, input=None, **_kwargs):
        observed_runs.append(list(cmd))
        return SimpleNamespace(
            returncode=0,
            stdout='{"jsonrpc":"2.0","id":2,"result":{"tools":[{"name":"trw_session_start"}]}}\n',
            stderr="",
        )

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    # Pre-create the wrapper so the post-install preflight probe sees it.
    pip_target = tmp_path / "trw-pip"
    (pip_target / "bin").mkdir(parents=True, exist_ok=True)
    wrapper = pip_target / "bin" / "trw-mcp"
    wrapper.write_text("#!/bin/bash\nstub\n")
    wrapper.chmod(0o755)

    memory_whl = tmp_path / "trw_memory-0.7.0-py3-none-any.whl"
    mcp_whl = tmp_path / "trw_mcp-0.46.1-py3-none-any.whl"
    memory_whl.write_bytes(b"stub")
    mcp_whl.write_bytes(b"stub")

    module.phase_install_packages(
        MagicMock(), 2, 4, sys.executable,
        memory_whl, mcp_whl,
        pip_target=str(pip_target),
    )

    # Find the combined invocation: a pip install with --find-links and BOTH wheels.
    combined_runs = [
        cmd for cmd in observed_runs
        if "pip" in cmd and "install" in cmd
        and "--find-links" in cmd
        and str(memory_whl) in cmd
        and str(mcp_whl) in cmd
    ]
    assert combined_runs, (
        "expected ONE pip install call with --find-links pointing at the "
        f"wheel dir AND both wheels on the command line. observed runs: "
        f"{[c for c in observed_runs if 'pip' in c][:3]}"
    )
    # Canonical shape check on the combined call
    combined = combined_runs[0]
    assert "--upgrade" in combined
    assert "--quiet" in combined
    assert "--find-links" in combined
    find_links_idx = combined.index("--find-links")
    assert combined[find_links_idx + 1] == str(memory_whl.parent), (
        "--find-links must point at the wheel dir so pip finds both locally"
    )
    # Importantly, --no-deps should NOT be in this combined call (so pip can
    # fetch transitive external deps like structlog from PyPI)
    assert "--no-deps" not in combined, (
        "combined install must NOT use --no-deps — structlog and other "
        "transitive deps must come from PyPI"
    )


@pytest.mark.parametrize("installer_path", _PATHS, ids=["template", "artifact"])
def test_combined_install_passes_target_dir(
    installer_path: Path, tmp_path: Path, monkeypatch
) -> None:
    """The combined install must preserve --target so wheels land in the
    pip_target directory, not in site-packages."""
    module = _load(installer_path)
    observed_runs: list[list[str]] = []

    def fake_run(cmd, env=None, stdout=None, stderr=None, *, capture_output=False, text=False, timeout=None, check=False, input=None, **_kwargs):
        observed_runs.append(list(cmd))
        return SimpleNamespace(
            returncode=0,
            stdout='{"jsonrpc":"2.0","id":2,"result":{"tools":[{"name":"trw_session_start"}]}}\n',
            stderr="",
        )

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    pip_target = tmp_path / "trw-pip"
    (pip_target / "bin").mkdir(parents=True, exist_ok=True)
    (pip_target / "bin" / "trw-mcp").write_text("#!/bin/bash\nstub\n")
    (pip_target / "bin" / "trw-mcp").chmod(0o755)

    memory_whl = tmp_path / "trw_memory-0.7.0-py3-none-any.whl"
    mcp_whl = tmp_path / "trw_mcp-0.46.1-py3-none-any.whl"
    memory_whl.write_bytes(b"stub")
    mcp_whl.write_bytes(b"stub")

    module.phase_install_packages(
        MagicMock(), 2, 4, sys.executable,
        memory_whl, mcp_whl,
        pip_target=str(pip_target),
    )

    combined_runs = [
        cmd for cmd in observed_runs
        if "pip" in cmd and "install" in cmd and "--find-links" in cmd
    ]
    assert combined_runs
    combined = combined_runs[0]
    target_idx = combined.index("--target")
    assert combined[target_idx + 1] == str(pip_target)


@pytest.mark.parametrize("installer_path", _PATHS, ids=["template", "artifact"])
def test_combined_install_falls_back_to_sequential_on_failure(
    installer_path: Path, tmp_path: Path, monkeypatch
) -> None:
    """If the combined invocation fails (e.g. PEP 668 managed env), the
    installer falls back to sequential pip_install calls which have their
    own --user / --break-system-packages escalation."""
    module = _load(installer_path)
    observed_runs: list[list[str]] = []
    pip_install_calls: list[tuple[str, str]] = []

    def fake_run(cmd, env=None, stdout=None, stderr=None, *, capture_output=False, text=False, timeout=None, check=False, input=None, **_kwargs):
        observed_runs.append(list(cmd))
        # Fail the combined call (has --find-links), succeed others
        if "--find-links" in list(cmd):
            return SimpleNamespace(returncode=1, stdout="", stderr="mock failure")
        return SimpleNamespace(
            returncode=0,
            stdout='{"jsonrpc":"2.0","id":2,"result":{"tools":[{"name":"trw_session_start"}]}}\n',
            stderr="",
        )

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    # pip_install is the fallback path — stub it to succeed so we can observe it gets called
    monkeypatch.setattr(
        module,
        "pip_install",
        lambda python, package, label, ui, target_dir="": (
            pip_install_calls.append((package, target_dir)) or True
        ),
    )

    pip_target = tmp_path / "trw-pip"
    (pip_target / "bin").mkdir(parents=True, exist_ok=True)
    (pip_target / "bin" / "trw-mcp").write_text("#!/bin/bash\nstub\n")
    (pip_target / "bin" / "trw-mcp").chmod(0o755)

    memory_whl = tmp_path / "trw_memory-0.7.0-py3-none-any.whl"
    mcp_whl = tmp_path / "trw_mcp-0.46.1-py3-none-any.whl"
    memory_whl.write_bytes(b"stub")
    mcp_whl.write_bytes(b"stub")

    module.phase_install_packages(
        MagicMock(), 2, 4, sys.executable,
        memory_whl, mcp_whl,
        pip_target=str(pip_target),
    )

    # Verify the fallback fired (pip_install was called for each wheel)
    assert len(pip_install_calls) >= 2, (
        f"expected pip_install fallback for both wheels; got {pip_install_calls}"
    )
    packages_installed = [call[0] for call in pip_install_calls]
    assert str(memory_whl) in packages_installed
    assert str(mcp_whl) in packages_installed
