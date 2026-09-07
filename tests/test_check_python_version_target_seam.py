"""C06 — ``check_python_version`` must honor ``TRW_TARGET_PYTHON``.

install.sh may install the trw-mcp CONSOLE SCRIPT into an isolated venv (a
pipx venv, the managed ``${XDG_DATA_HOME}/trw/venv``, or a ``uv tool`` venv)
that is NOT ``sys.executable`` — the bootstrap always invokes
``"$PYTHON" install-trw.py``, where ``$PYTHON`` is the interpreter that ran
the *bootstrap script itself*, not necessarily the one that owns the
freshly-installed trw-mcp binary. Before this fix, ``check_python_version``
unconditionally returned ``sys.executable``, so the bundled wheels
(``phase_install_packages``) always installed into the bootstrap interpreter
even when the PEP 668 fallback ladder put trw-mcp somewhere else.

The template is loaded as a module by file path (``scripts/`` is not a
package) — same pattern as ``test_phase_install_downgrade_guard.py``.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

_TEMPLATE = Path(__file__).resolve().parent.parent / "scripts" / "install-trw.template.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("install_trw_check_python_version", _TEMPLATE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def installer() -> ModuleType:
    return _load()


def test_no_env_var_returns_sys_executable(installer: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    """Absent TRW_TARGET_PYTHON preserves the prior (only) behavior."""
    monkeypatch.delenv("TRW_TARGET_PYTHON", raising=False)
    assert installer.check_python_version(installer.UI()) == sys.executable


def test_valid_target_python_is_honored(
    installer: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A real, executable, sufficiently-new interpreter wins over sys.executable.

    Exercises the REAL subprocess probe (no mocking) against a fake python
    shim so this proves the seam actually runs the target, not just that it
    trusts the env var's string value.
    """
    fake_python = tmp_path / "fake-python"
    fake_python.write_text(
        "#!/usr/bin/env bash\n"
        'if [ "$1" = "-c" ]; then echo "3.12"; fi\n',
        encoding="utf-8",
    )
    fake_python.chmod(0o755)
    monkeypatch.setenv("TRW_TARGET_PYTHON", str(fake_python))
    assert installer.check_python_version(installer.UI()) == str(fake_python)


def test_nonexecutable_target_falls_back(
    installer: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A TRW_TARGET_PYTHON pointing at a non-executable file must not be trusted."""
    bogus = tmp_path / "not-a-python"
    bogus.write_text("not an interpreter", encoding="utf-8")
    monkeypatch.setenv("TRW_TARGET_PYTHON", str(bogus))
    assert installer.check_python_version(installer.UI()) == sys.executable


def test_target_that_fails_to_run_falls_back(
    installer: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A TRW_TARGET_PYTHON that exists+is executable but errors out is rejected."""
    broken = tmp_path / "broken-python"
    broken.write_text("#!/usr/bin/env bash\nexit 1\n", encoding="utf-8")
    broken.chmod(0o755)
    monkeypatch.setenv("TRW_TARGET_PYTHON", str(broken))
    assert installer.check_python_version(installer.UI()) == sys.executable


def test_target_below_min_version_falls_back(
    installer: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A TRW_TARGET_PYTHON reporting an unsupported version is rejected, not blindly trusted."""
    old_python = tmp_path / "old-python"
    old_python.write_text(
        "#!/usr/bin/env bash\n"
        'if [ "$1" = "-c" ]; then echo "3.8"; fi\n',
        encoding="utf-8",
    )
    old_python.chmod(0o755)
    monkeypatch.setenv("TRW_TARGET_PYTHON", str(old_python))
    assert installer.check_python_version(installer.UI()) == sys.executable


def test_target_equal_to_sys_executable_takes_the_fast_path(
    installer: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When install.sh's TRW_TARGET_PYTHON == sys.executable (plain pip/--user
    rungs), no subprocess probe is needed — it's a no-op match."""
    monkeypatch.setenv("TRW_TARGET_PYTHON", sys.executable)
    calls: list[list[str]] = []
    real_run = subprocess.run

    def _tracking_run(cmd, *args, **kwargs):  # type: ignore[no-untyped-def]
        calls.append(cmd)
        return real_run(cmd, *args, **kwargs)

    monkeypatch.setattr(installer.subprocess, "run", _tracking_run)
    assert installer.check_python_version(installer.UI()) == sys.executable
    assert calls == []
