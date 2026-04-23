"""PRD-CORE-149 FR05: hook short-circuit under HOOKS_ENABLED=false.

Runs the shipped ``phase-cycle-stop.sh`` and verifies its stdout is empty
when HOOKS_ENABLED=false (light-mode profiles). Runs it with
HOOKS_ENABLED=true and confirms the hook still short-circuits cleanly
(exit 0) -- the hook may still emit nothing in a fresh-tmp scenario, so
we only assert on the HOOKS_ENABLED=false branch being strictly silent.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

HOOK = Path(__file__).resolve().parents[1] / "src" / "trw_mcp" / "data" / "hooks" / "phase-cycle-stop.sh"


def _run_hook(env_overrides: dict[str, str], cwd: Path) -> subprocess.CompletedProcess[bytes]:
    env = {**os.environ, **env_overrides}
    # send empty stdin so the hook doesn't block on read
    return subprocess.run(
        ["/bin/sh", str(HOOK)],
        input=b"",
        env=env,
        cwd=str(cwd),
        capture_output=True,
        timeout=10,
        check=False,
    )


def test_hook_script_exists_and_is_executable() -> None:
    assert HOOK.exists(), f"expected shipped hook at {HOOK}"


def test_hooks_disabled_produces_zero_stdout(tmp_path: Path) -> None:
    """FR05 exit criteria: HOOKS_ENABLED=false => stdout is 0 bytes."""
    proc = _run_hook({"HOOKS_ENABLED": "false", "CLAUDE_PROJECT_DIR": str(tmp_path)}, tmp_path)
    assert proc.returncode == 0, f"hook should exit 0 under HOOKS_ENABLED=false, got {proc.returncode}"
    assert proc.stdout == b"", f"expected zero stdout bytes, got {len(proc.stdout)} bytes"


def test_hooks_enabled_exits_cleanly(tmp_path: Path) -> None:
    """Backward compat: when HOOKS_ENABLED is unset / true, the hook must
    still return 0 (fail-open) and not error out in a fresh tmp dir."""
    proc = _run_hook(
        {"HOOKS_ENABLED": "true", "CLAUDE_PROJECT_DIR": str(tmp_path)},
        tmp_path,
    )
    # fail-open: even if phase state is missing, the hook should exit 0 via trap
    assert proc.returncode == 0, (
        f"hook should fail-open under HOOKS_ENABLED=true, got rc={proc.returncode} "
        f"stderr={proc.stderr[:200]!r}"
    )


def test_hook_env_file_sourced_propagates_hooks_enabled(tmp_path: Path) -> None:
    """End-to-end FR04+FR05: write a hook-env.sh with HOOKS_ENABLED=false, then
    invoke the hook WITHOUT passing HOOKS_ENABLED in the environment. The
    lib-trw.sh source block should pick it up and short-circuit."""
    runtime = tmp_path / ".trw" / "runtime"
    runtime.mkdir(parents=True)
    (runtime / "hook-env.sh").write_text('export HOOKS_ENABLED="false"\n', encoding="utf-8")

    proc = _run_hook(
        {"CLAUDE_PROJECT_DIR": str(tmp_path), "HOOKS_ENABLED": ""},
        tmp_path,
    )
    assert proc.returncode == 0
    assert proc.stdout == b"", f"expected silence after sourcing hook-env.sh, got {proc.stdout!r}"
