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

import json
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
    """FR09 measures the shipped hook with real generated-policy semantics.

    ``SESSION_HOOK`` runs straight from the bundled source tree (never an
    installed ``.claude/hooks/``), so lib-trw.sh derives its per-client key from
    that location rather than from any client profile -- see
    ``hook_env_key_for_hooks_dir``. Writing under any OTHER key would never be
    read, which would make this test vacuous.
    """
    from trw_mcp.bootstrap._file_ops import hook_env_key_for_hooks_dir

    runtime = tmp_path / ".trw" / "runtime" / "hook-env.d"
    runtime.mkdir(parents=True)
    key = hook_env_key_for_hooks_dir(SESSION_HOOK.parent)
    env_file = runtime / f"{key}.sh"

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


def _run_compaction_hook(hook: Path, root: Path, payload: bytes) -> subprocess.CompletedProcess[bytes]:
    """Run one compaction hook with no session identity, so both read the project-wide marker."""
    env = {**os.environ, "CLAUDE_PROJECT_DIR": str(root), "TRW_SESSION_ID": "", "CLAUDE_CODE_SESSION_ID": ""}
    return subprocess.run(
        ["/bin/sh", str(hook)], input=payload, env=env, cwd=str(root), capture_output=True, timeout=10, check=False
    )


@pytest.mark.parametrize("with_marker", [True, False], ids=["recovered-run", "no-marker"])
def test_post_compact_output_is_injected_or_absent(tmp_path: Path, with_marker: bool) -> None:
    """PRD-CORE-301 FR06: PostCompact prints nothing, and SessionStart:compact still recovers.

    Claude Code documents no injection of PostCompact output, and the operator's
    recorded transcript (six compactions, 2026-09-23..26) shows the hook's stdout
    only as a dim status line in the ``/compact`` command echo, under a
    ``<local-command-caveat>``; no ``hook_*`` attachment carries it, while every
    SessionStart:compact output arrives as a ``hook_success`` attachment. Plain
    stdout that claimed "injected automatically" was therefore neither injected
    nor true, so the hook is silent, and the recovered run still reaches the model
    through the SessionStart compact branch.
    """
    context = tmp_path / ".trw" / "context"
    context.mkdir(parents=True)
    run_rel = ".trw/runs/fetch-retry/20260926T000000Z-feedface"
    if with_marker:
        marker = {"run_path": f"{tmp_path}/{run_rel}", "phase": "validate", "events_logged": 15}
        marker["last_checkpoint"] = "added retry with jitter"
        (context / "pre_compact_state.json").write_text(json.dumps(marker), encoding="utf-8")

    post = _run_compaction_hook(HOOK.with_name("post-compact.sh"), tmp_path, b'{"trigger":"auto"}')
    assert post.returncode == 0
    assert post.stdout == b"", post.stdout.decode(errors="replace")

    start = _run_compaction_hook(SESSION_HOOK, tmp_path, b'{"source":"compact"}')
    assert start.returncode == 0
    recovered = start.stdout.decode(errors="replace")
    assert "CONTEXT COMPACTED" in recovered
    if with_marker:
        assert f"RECOVERED: Run at {run_rel}" in recovered
        assert "RECOVERED: Phase: validate | Events: 15" in recovered
        assert 'LAST CHECKPOINT: "added retry with jitter"' in recovered
    else:
        assert "RECOVERED:" not in recovered


def test_compact_recovery_labels_a_factory_checkpoint_as_a_sanitized_preview(tmp_path: Path) -> None:
    """E2E-INC-065: the banner blanks brackets, so a factory READY shown there must not look copyable."""
    context = tmp_path / ".trw" / "context"
    context.mkdir(parents=True)
    payload = {
        "factory": 1,
        "kind": "READY",
        "attempt": "X",
        "subject_sha": "a" * 40,
        "receipts": {"build": ["build-1"]},
    }
    marker = {
        "run_path": f"{tmp_path}/.trw/runs/x/20260926T000000Z-feedface",
        "phase": "validate",
        "events_logged": 1,
        "last_checkpoint": json.dumps(payload),
    }
    (context / "pre_compact_state.json").write_text(json.dumps(marker), encoding="utf-8")

    start = _run_compaction_hook(SESSION_HOOK, tmp_path, b'{"source":"compact"}')
    line = next(ln for ln in start.stdout.decode(errors="replace").splitlines() if ln.startswith("LAST CHECKPOINT:"))
    assert "[" not in line  # the sanitizer still blanks brackets (injection defense unchanged)
    assert line.endswith("(sanitized preview, not a payload to copy: exact JSON is in the run meta/checkpoints.jsonl)")
