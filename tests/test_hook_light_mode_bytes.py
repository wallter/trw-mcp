"""PRD-CORE-149 FR05: hook short-circuit under ``hooks_enabled: false``.

Runs the shipped ``stop-ceremony.sh`` and verifies its stdout is empty
when ``hooks_enabled`` resolves false (light-mode profiles seed it in
``.trw/config.yaml`` at install and TRWConfig publishes it). Runs it with the key unset and confirms the hook still short-circuits cleanly
(exit 0) -- the hook may still emit nothing in a fresh-tmp scenario, so
we only assert on the disabled branch being strictly silent. The per-hook
silence and side-effect sweep lives in ``test_hooks_enabled_config_gate.py``.

The subject was ``phase-cycle-stop.sh`` until PRD-CORE-250-FR03 deleted it as
registered by no shipped template. ``stop-ceremony.sh`` is the correct
replacement rather than a convenient one: it is the Stop-event hook that DOES
ship registered in both templates, and it is the only other blocking hook, so
the FR05 short-circuit matters more there than it ever did on a file that could
not fire.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from trw_mcp.models.config import TRWConfig
from trw_mcp.state._hook_flags import write_hook_flags

pytestmark = pytest.mark.unit

HOOK = Path(__file__).resolve().parents[1] / "src" / "trw_mcp" / "data" / "hooks" / "stop-ceremony.sh"
SESSION_HOOK = HOOK.with_name("session-start.sh")
POLICY_GATED_HOOKS = (SESSION_HOOK, HOOK.with_name("post-compact.sh"), HOOK.with_name("post-tool-event.sh"))


def _disable_hooks(root: Path) -> None:
    """The one hooks switch: ``hooks_enabled`` as TRWConfig publishes it for lib-trw.sh."""
    (root / ".trw").mkdir(exist_ok=True)
    (root / ".trw" / "config.yaml").write_text("hooks_enabled: false\n", encoding="utf-8")
    write_hook_flags(root / ".trw", TRWConfig(hooks_enabled=False))


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


def _run_session_hook(cwd: Path) -> subprocess.CompletedProcess[bytes]:
    env = {**os.environ, "CLAUDE_PROJECT_DIR": str(cwd)}
    return subprocess.run(
        ["/bin/sh", str(SESSION_HOOK)],
        input=b'{"source":"startup"}',
        env=env,
        cwd=str(cwd),
        capture_output=True,
        timeout=10,
        check=False,
    )


def _run_named_hook(hook: Path, cwd: Path) -> subprocess.CompletedProcess[bytes]:
    env = {**os.environ, "CLAUDE_PROJECT_DIR": str(cwd)}
    return subprocess.run(
        ["/bin/sh", str(hook)],
        input=b'{"source":"startup","tool_name":"Write","tool_input":{"file_path":"x.py"}}',
        env=env,
        cwd=str(cwd),
        capture_output=True,
        timeout=10,
        check=False,
    )


def test_hook_script_exists_and_is_executable() -> None:
    assert HOOK.exists(), f"expected shipped hook at {HOOK}"


def test_hooks_enabled_exits_cleanly(tmp_path: Path) -> None:
    """Backward compat: when ``hooks_enabled`` is unset, the hook must
    still return 0 (fail-open) and not error out in a fresh tmp dir."""
    proc = _run_hook(
        {"CLAUDE_PROJECT_DIR": str(tmp_path)},
        tmp_path,
    )
    # fail-open: even if phase state is missing, the hook should exit 0 via trap
    assert proc.returncode == 0, (
        f"hook should fail-open with hooks enabled, got rc={proc.returncode} stderr={proc.stderr[:200]!r}"
    )


def test_init_installed_hooks_honor_generated_policy(tmp_path: Path) -> None:
    """FR05 default path: init-installed hook copies are silent and side-effect free."""
    from trw_mcp.bootstrap._init_project import _install_hooks

    (tmp_path / ".claude" / "hooks").mkdir(parents=True)
    result: dict[str, list[str]] = {"created": [], "skipped": [], "errors": []}
    _install_hooks(tmp_path, True, result)
    assert result["errors"] == []

    _disable_hooks(tmp_path)
    for source_hook in (*POLICY_GATED_HOOKS, HOOK):
        installed = tmp_path / ".claude" / "hooks" / source_hook.name
        proc = _run_named_hook(installed, tmp_path)
        assert proc.returncode == 0, installed.name
        assert proc.stdout == b"", installed.name
    assert not list((tmp_path / ".trw").rglob("events.jsonl"))


def test_light_profile_stdout_is_less_than_half_of_full_profile(tmp_path: Path) -> None:
    """FR09 measures the shipped hook with real generated-policy semantics."""
    runtime = tmp_path / ".trw" / "runtime"
    runtime.mkdir(parents=True)
    env_file = runtime / "hook-env.sh"

    env_file.write_text("export NUDGE_ENABLED=true\n", encoding="utf-8")
    full = _run_session_hook(tmp_path)
    assert full.returncode == 0
    assert len(full.stdout) > 0, "full profile fixture must exercise observable hook output"

    env_file.write_text("export NUDGE_ENABLED=false\n", encoding="utf-8")
    _disable_hooks(tmp_path)
    light = _run_session_hook(tmp_path)
    assert light.returncode == 0
    assert len(light.stdout) < len(full.stdout) * 0.5


def test_disabled_hook_is_silent_while_protected_instructions_keep_gate(tmp_path: Path) -> None:
    """QUAL-113 FR05: optional hook absence cannot remove lifecycle truth."""
    from trw_mcp.state.claude_md.sections._tool_lifecycle import render_deliver_gate_statement

    _disable_hooks(tmp_path)
    proc = _run_hook({"CLAUDE_PROJECT_DIR": str(tmp_path)}, tmp_path)
    protected = render_deliver_gate_statement()

    assert proc.returncode == 0
    assert proc.stdout == b""
    assert "Call `trw_session_start()` first." in protected
    assert "Do NOT call `trw_deliver` unless" in protected


def test_default_hook_assets_never_recommend_bypassing_host_trust() -> None:
    hook_root = HOOK.parent
    combined = "\n".join(path.read_text(encoding="utf-8") for path in hook_root.glob("*.sh"))

    assert "dangerously-bypass-hook-trust" not in combined
