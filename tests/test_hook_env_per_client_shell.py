"""PRD-FIX-118/R8 — a shell-level proof that lib-trw.sh reads its OWN client's
hook-env file, regardless of which other client synced most recently.

Before this split, every client's sync rewrote the ONE shared
``.trw/runtime/hook-env.sh``, so a hook installed for one client (e.g.
Claude Code) picked up whichever OTHER client (opencode, grok) synced last --
disarming its nudges and dropping its ``TRW_SESSION_ID`` export. The fix keys
the file lib-trw.sh sources off where the CALLING hook physically lives (its
own ``<config_dir>/hooks`` directory), never a client-id table, so two
real fake hook installs -- one under ``.claude/hooks/``, one under
``.opencode/hooks/`` -- must each see only their own settings no matter which
order the two clients were synced in.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from trw_mcp.bootstrap._file_ops import _write_hook_env_file
from trw_mcp.models.config._profiles import resolve_client_profile

pytestmark = pytest.mark.unit

_LIB_TRW = Path(__file__).resolve().parents[1] / "src" / "trw_mcp" / "data" / "hooks" / "lib-trw.sh"

_PROBE_BODY = """#!/bin/sh
_hook_dir="$(cd "$(dirname "$0")" && pwd)"
. "$_hook_dir/lib-trw.sh" 2>/dev/null || exit 0
printf 'NUDGE_ENABLED=%s\\n' "$NUDGE_ENABLED"
printf 'TRW_SESSION_ID=%s\\n' "${TRW_SESSION_ID:-<unset>}"
"""


def _install_fake_hook(root: Path, config_dir: str) -> Path:
    """A minimal real hook install: config_dir/hooks/{lib-trw.sh, probe.sh}."""
    hooks_dir = root / config_dir / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(_LIB_TRW, hooks_dir / "lib-trw.sh")
    probe = hooks_dir / "probe.sh"
    probe.write_text(_PROBE_BODY, encoding="utf-8")
    probe.chmod(0o755)
    return probe


def _run_probe(probe: Path, root: Path, **env: str) -> dict[str, str]:
    result = subprocess.run(
        ["sh", str(probe)],
        input="",
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin", "CLAUDE_PROJECT_DIR": str(root), **env},
        timeout=30,
        check=False,
    )
    parsed: dict[str, str] = {}
    for line in result.stdout.splitlines():
        if "=" in line:
            key, _, value = line.partition("=")
            parsed[key] = value
    return parsed


@pytest.mark.parametrize("sync_order", [("claude-code", "opencode"), ("opencode", "claude-code")])
def test_each_installed_client_reads_only_its_own_settings_regardless_of_sync_order(
    tmp_path: Path, sync_order: tuple[str, str]
) -> None:
    trw_dir = tmp_path / ".trw"
    for client_id in sync_order:
        _write_hook_env_file(trw_dir, resolve_client_profile(client_id))

    claude_probe = _install_fake_hook(tmp_path, ".claude")
    opencode_probe = _install_fake_hook(tmp_path, ".opencode")

    claude_result = _run_probe(claude_probe, tmp_path, CLAUDE_CODE_SESSION_ID="claude-session-xyz")
    opencode_result = _run_probe(opencode_probe, tmp_path, CLAUDE_CODE_SESSION_ID="claude-session-xyz")

    # claude-code: nudges on, and it DOES publish a session variable.
    assert claude_result["NUDGE_ENABLED"] == "true", sync_order
    assert claude_result["TRW_SESSION_ID"] == "claude-session-xyz", sync_order

    # opencode: nudges off (light profile), and it publishes NO session
    # variable of its own, so the ambient CLAUDE_CODE_SESSION_ID must never
    # leak into its TRW_SESSION_ID -- that would be exactly the
    # cross-contamination this split exists to prevent.
    assert opencode_result["NUDGE_ENABLED"] == "false", sync_order
    assert opencode_result["TRW_SESSION_ID"] == "<unset>", sync_order


def test_grok_then_claude_code_leaves_claude_codes_hook_pinned(tmp_path: Path) -> None:
    """The exact scenario in the defect report, with the third hookless client."""
    trw_dir = tmp_path / ".trw"
    _write_hook_env_file(trw_dir, resolve_client_profile("claude-code"))
    _write_hook_env_file(trw_dir, resolve_client_profile("grok"))

    claude_probe = _install_fake_hook(tmp_path, ".claude")
    result = _run_probe(claude_probe, tmp_path, CLAUDE_CODE_SESSION_ID="claude-session-abc")

    assert result["NUDGE_ENABLED"] == "true", "grok's later sync must not have disarmed claude-code's nudges"
    assert result["TRW_SESSION_ID"] == "claude-session-abc", "grok's later sync must not have dropped the pin key"


# --------------------------------------------------------------------------- #
# sol round 1 P1: codex and copilot execute the SAME physical .claude/hooks
# script as claude-code, so a path-derived key alone always resolves "claude"
# for them too. TRW_HOOK_CLIENT must let them read their OWN file instead.
# --------------------------------------------------------------------------- #


def test_trw_hook_client_env_var_outranks_the_path_derived_key(tmp_path: Path) -> None:
    """A hook physically under .claude/hooks/ but run with TRW_HOOK_CLIENT=codex
    must read hook-env.d/codex.sh, not hook-env.d/claude.sh -- the exact
    situation codex's adapter creates (bootstrap/_codex_hooks.py)."""
    trw_dir = tmp_path / ".trw"
    _write_hook_env_file(trw_dir, resolve_client_profile("claude-code"))
    _write_hook_env_file(trw_dir, resolve_client_profile("codex"))

    # The physical script lives under .claude/hooks/ (codex has no directory
    # of its own for these) -- dirname-derivation alone would resolve "claude".
    claude_dir_probe = _install_fake_hook(tmp_path, ".claude")

    codex_result = _run_probe(claude_dir_probe, tmp_path, TRW_HOOK_CLIENT="codex")
    claude_result = _run_probe(claude_dir_probe, tmp_path)  # no override -> path-derived "claude"

    assert codex_result["NUDGE_ENABLED"] == "true"  # codex's own profile default
    assert claude_result["NUDGE_ENABLED"] == "true"
    # Give the two profiles different display names so the probe distinguishes them.
    codex_env = (trw_dir / "runtime" / "hook-env.d" / "codex.sh").read_text(encoding="utf-8")
    claude_env = (trw_dir / "runtime" / "hook-env.d" / "claude.sh").read_text(encoding="utf-8")
    assert "Codex CLI" in codex_env
    assert "Claude Code" in claude_env
    assert codex_env != claude_env


@pytest.mark.parametrize("bad_value", ["../claude", "codex; rm -rf /", "codex$(id)", "codex file", ""])
def test_invalid_trw_hook_client_falls_back_to_path_derivation_not_injection(tmp_path: Path, bad_value: str) -> None:
    """An unsanitary TRW_HOOK_CLIENT must never be used as a filename component."""
    trw_dir = tmp_path / ".trw"
    _write_hook_env_file(trw_dir, resolve_client_profile("claude-code"))
    claude_probe = _install_fake_hook(tmp_path, ".claude")

    result = _run_probe(claude_probe, tmp_path, TRW_HOOK_CLIENT=bad_value)

    # Falls through to the path-derived "claude" key -- claude-code's own file
    # -- rather than treating the raw value as a path component.
    assert result["NUDGE_ENABLED"] == "true"
    assert not (trw_dir / "runtime" / "hook-env.d" / f"{bad_value}.sh").exists()


def test_codex_registered_hook_command_reads_codexs_own_file(tmp_path: Path) -> None:
    """The ACTUAL command codex's installer writes to .codex/hooks.json, run for real.

    sol round 2 P1: codex's profile default (``nudge_enabled=True``) is ALSO
    lib-trw.sh's missing-file default, so a broken key (the command's
    ``TRW_HOOK_CLIENT`` value not matching the key the writer actually used)
    would pass this assertion vacuously by falling through to the same
    default. Flipping codex's written value to ``false`` makes the two
    outcomes diverge, so this only passes when the command's key and the
    writer's key are provably the same.
    """
    from trw_mcp.bootstrap._codex_hooks import _trw_hook_group
    from trw_mcp.bootstrap._hook_env import _hook_env_key

    trw_dir = tmp_path / ".trw"
    _write_hook_env_file(trw_dir, resolve_client_profile("claude-code"))
    codex_profile = resolve_client_profile("codex").model_copy(update={"nudge_enabled": False})
    _write_hook_env_file(trw_dir, codex_profile)
    _install_fake_hook(tmp_path, ".claude")
    # The registered command uses `git rev-parse --show-toplevel`; a real git
    # root is required for that substitution to resolve to *tmp_path*.
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)

    command = _trw_hook_group(event="SessionStart", script_name="probe.sh")["hooks"][0]["command"]
    # The key in the command must be the SAME RESOLVER the writer used, not a
    # literal that happens to match today.
    expected_key = _hook_env_key(resolve_client_profile("codex"))
    assert command.startswith(f"TRW_HOOK_CLIENT={expected_key} ")

    result = subprocess.run(
        ["sh", "-c", command],
        input="",
        capture_output=True,
        text=True,
        env={"PATH": os.environ.get("PATH", "/usr/bin:/bin")},
        cwd=tmp_path,
        timeout=30,
        check=False,
    )
    codex_env = (trw_dir / "runtime" / "hook-env.d" / f"{expected_key}.sh").read_text(encoding="utf-8")
    assert "Codex CLI" in codex_env
    assert "NUDGE_ENABLED=false" in result.stdout, (
        "codex must read its OWN file (nudge off) -- reading true here means it "
        f"fell back to the shared default instead of hook-env.d/{expected_key}.sh"
    )


def test_copilot_registered_hook_command_reads_copilots_own_file(tmp_path: Path) -> None:
    """The ACTUAL command copilot's installer writes to hooks.json, run for real
    through the real bundled adapter script.

    sol round 2 P1: copilot's profile default (``nudge_enabled=True``) is ALSO
    lib-trw.sh's missing-file default -- the exact reason this test passed
    vacuously before while the command exported ``TRW_HOOK_CLIENT=copilot``
    but the writer's key (``_hook_env_key``) was actually ``github``. Flipping
    the written value to ``false`` makes a key mismatch observable.
    """
    from trw_mcp.bootstrap._copilot import _build_hook_adapter_command, _bundled_adapter_script_path
    from trw_mcp.bootstrap._hook_env import _hook_env_key

    trw_dir = tmp_path / ".trw"
    _write_hook_env_file(trw_dir, resolve_client_profile("claude-code"))
    copilot_profile = resolve_client_profile("copilot").model_copy(update={"nudge_enabled": False})
    _write_hook_env_file(trw_dir, copilot_profile)
    claude_probe = _install_fake_hook(tmp_path, ".claude")

    adapter = _bundled_adapter_script_path()
    command = _build_hook_adapter_command("sessionStart", str(claude_probe), str(adapter))
    expected_key = _hook_env_key(resolve_client_profile("copilot"))
    assert command.startswith(f"TRW_HOOK_CLIENT={expected_key} ")

    result = subprocess.run(
        ["sh", "-c", command],
        input="{}",
        capture_output=True,
        text=True,
        env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "CLAUDE_PROJECT_DIR": str(tmp_path)},
        timeout=30,
        check=False,
    )
    copilot_env = (trw_dir / "runtime" / "hook-env.d" / f"{expected_key}.sh").read_text(encoding="utf-8")
    assert "GitHub Copilot CLI" in copilot_env
    assert "NUDGE_ENABLED=false" in result.stdout, (
        "copilot must read its OWN file (nudge off) -- reading true here means it "
        f"fell back to the shared default instead of hook-env.d/{expected_key}.sh"
    )
