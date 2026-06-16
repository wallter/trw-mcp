"""Tests for install_antigravity_distill_channels hooks.json registration.

Mirrors the pattern of tests/channels/codex/test_hook_registration.py.

The Codex channel had a bug where the hook SCRIPT was dropped but hooks.json was
never REGISTERED, so Codex never invoked the hook (only caught by live turn, 2026-05-28).
These tests ensure the analogous gap cannot exist for AG-03.

Verifies:
- install_antigravity_distill_channels writes .antigravitycli/hooks.json with a
  PreToolUse entry referencing trw_before_edit_telemetry (the registration check).
- hooks.json has the correct agy format: {"PreToolUse": [{"matcher": ..., "command": ...}]}
- Running install twice is idempotent — no duplicate entries.
- Existing entries in hooks.json (other clients, other events) are preserved.
- The hook script is also installed (.antigravitycli/hooks/trw_before_edit_telemetry.py).

Live verification results (2026-05-29 — agy v1.0.2 / v1.0.3):
- hooks.json IS written with correct format (registration confirmed).
- Hook SCRIPT IS installed and works when invoked directly.
- PRODUCT LIMITATION: agy v1.0.2-1.0.3 uses Step_CodeAction (not PreToolUse jsonhook)
  for file edits — PreToolUse hooks do not fire for write_file in --print mode.
  This is a product limitation in agy's current internal execution path, not a
  registration gap. The hooks.json format and content are correct.
  Evidence: agy log "Auto-approving tool confirmation: 'Edit' (type=Step_CodeAction)"
  — the file write path bypasses jsonhook entirely. The hook system (ParseHooksFile)
  exists in the binary but is not invoked for the CodeAction tool path in v1.0.2-1.0.3.

PRD-DIST-2404 FR07-FR10, AG-03.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _read_hooks_json(project: Path) -> dict[str, object]:
    """Read and parse .antigravitycli/hooks.json from a project root."""
    hooks_path = project / ".antigravitycli" / "hooks.json"
    assert hooks_path.exists(), f".antigravitycli/hooks.json not found at {hooks_path}"
    result: dict[str, object] = json.loads(hooks_path.read_text(encoding="utf-8"))
    assert isinstance(result, dict)
    return result


def _get_pre_tool_hooks(hooks_data: dict[str, object]) -> list[dict[str, str]]:
    """Extract PreToolUse hooks list from hooks.json dict."""
    raw = hooks_data.get("PreToolUse", [])
    if not isinstance(raw, list):
        return []
    return [h for h in raw if isinstance(h, dict)]


def _distill_hook_commands(hooks: list[dict[str, str]]) -> list[str]:
    """Collect all command strings from PreToolUse hooks."""
    return [h.get("command", "") for h in hooks]


# ---------------------------------------------------------------------------
# Core registration tests
# ---------------------------------------------------------------------------


def test_install_creates_hooks_json_with_distill_entry(tmp_path: Path) -> None:
    """install_antigravity_distill_channels writes .antigravitycli/hooks.json with trw_before_edit_telemetry."""
    subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)

    from trw_mcp.bootstrap._antigravity_distill_channels import install_antigravity_distill_channels

    result = install_antigravity_distill_channels(tmp_path)

    # hooks.json must exist
    hooks_path = tmp_path / ".antigravitycli" / "hooks.json"
    assert hooks_path.exists(), ".antigravitycli/hooks.json was not created by installer"

    # Must contain a PreToolUse entry referencing trw_before_edit_telemetry
    data = _read_hooks_json(tmp_path)
    hooks = _get_pre_tool_hooks(data)
    commands = _distill_hook_commands(hooks)
    assert any("trw_before_edit_telemetry" in cmd for cmd in commands), (
        f"No trw_before_edit_telemetry command found in PreToolUse hooks: {commands}"
    )

    # Result should list hooks.json as created (not in errors)
    assert ".antigravitycli/hooks.json" in result["created"], f"hooks.json not in result['created']: {result}"
    assert not result["errors"], f"Unexpected errors: {result['errors']}"


def test_install_creates_hook_script(tmp_path: Path) -> None:
    """install_antigravity_distill_channels creates the hook script file."""
    subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)

    from trw_mcp.bootstrap._antigravity_distill_channels import install_antigravity_distill_channels

    install_antigravity_distill_channels(tmp_path)

    hook_script = tmp_path / ".antigravitycli" / "hooks" / "trw_before_edit_telemetry.py"
    assert hook_script.exists(), f"Hook script not found at {hook_script}"
    assert hook_script.stat().st_size > 0, "Hook script is empty"

    # Script should reference the channel ID
    content = hook_script.read_text(encoding="utf-8")
    assert "ag-03-before-edit-hook" in content or "ag-03" in content.lower(), (
        "Hook script missing AG-03 channel ID reference"
    )


def test_install_idempotent_no_double_add(tmp_path: Path) -> None:
    """Running install_antigravity_distill_channels twice does not duplicate the distill hook."""
    subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)

    from trw_mcp.bootstrap._antigravity_distill_channels import install_antigravity_distill_channels

    install_antigravity_distill_channels(tmp_path)
    install_antigravity_distill_channels(tmp_path)

    data = _read_hooks_json(tmp_path)
    hooks = _get_pre_tool_hooks(data)
    commands = [cmd for cmd in _distill_hook_commands(hooks) if "trw_before_edit_telemetry" in cmd]
    assert len(commands) == 1, (
        f"Expected exactly 1 trw_before_edit_telemetry command after 2 installs, got {len(commands)}: {commands}"
    )


def test_install_preserves_existing_post_tool_use_entries(tmp_path: Path) -> None:
    """install_antigravity_distill_channels preserves pre-existing PostToolUse hooks.json entries."""
    subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)

    # Write a pre-existing hooks.json with a PostToolUse entry
    antigravity_dir = tmp_path / ".antigravitycli"
    antigravity_dir.mkdir(parents=True, exist_ok=True)
    pre_existing = {
        "PostToolUse": [{"matcher": "read_file", "command": "echo POST_HOOK_EXISTING"}],
    }
    (antigravity_dir / "hooks.json").write_text(json.dumps(pre_existing, indent=2) + "\n", encoding="utf-8")

    from trw_mcp.bootstrap._antigravity_distill_channels import install_antigravity_distill_channels

    install_antigravity_distill_channels(tmp_path)

    data = _read_hooks_json(tmp_path)

    # Original PostToolUse entry preserved
    assert "PostToolUse" in data, "PostToolUse section was removed by installer"
    raw_post = data["PostToolUse"]
    post_hooks: list[dict[str, str]] = (
        [h for h in raw_post if isinstance(h, dict)] if isinstance(raw_post, list) else []
    )
    post_commands = _distill_hook_commands(post_hooks)
    assert any("echo POST_HOOK_EXISTING" in cmd for cmd in post_commands), (
        f"Pre-existing PostToolUse hook was lost: {post_commands}"
    )

    # New PreToolUse entry added
    assert "PreToolUse" in data, "PreToolUse section missing after install"
    pre_commands = _distill_hook_commands(_get_pre_tool_hooks(data))
    assert any("trw_before_edit_telemetry" in cmd for cmd in pre_commands), (
        f"trw_before_edit_telemetry hook was not added: {pre_commands}"
    )


def test_install_preserves_existing_pre_tool_use_non_distill_entries(tmp_path: Path) -> None:
    """install_antigravity_distill_channels preserves existing user PreToolUse entries."""
    subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)

    antigravity_dir = tmp_path / ".antigravitycli"
    antigravity_dir.mkdir(parents=True, exist_ok=True)
    pre_existing = {
        "PreToolUse": [{"matcher": "glob", "command": "echo USER_PRE_HOOK"}],
    }
    (antigravity_dir / "hooks.json").write_text(json.dumps(pre_existing, indent=2) + "\n", encoding="utf-8")

    from trw_mcp.bootstrap._antigravity_distill_channels import install_antigravity_distill_channels

    install_antigravity_distill_channels(tmp_path)

    data = _read_hooks_json(tmp_path)
    pre_hooks = _get_pre_tool_hooks(data)
    commands = _distill_hook_commands(pre_hooks)

    # User hook preserved
    assert any("echo USER_PRE_HOOK" in cmd for cmd in commands), (
        f"Pre-existing user PreToolUse hook was lost: {commands}"
    )
    # Distill hook added
    assert any("trw_before_edit_telemetry" in cmd for cmd in commands), (
        f"trw_before_edit_telemetry hook was not added alongside user hook: {commands}"
    )


def test_hooks_json_format_is_valid_agy_format(tmp_path: Path) -> None:
    """hooks.json written by installer uses the correct agy format (not Codex format)."""
    subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)

    from trw_mcp.bootstrap._antigravity_distill_channels import install_antigravity_distill_channels

    install_antigravity_distill_channels(tmp_path)

    data = _read_hooks_json(tmp_path)

    # Agy format: top-level "PreToolUse" key (NOT nested under "hooks" like Codex)
    assert "PreToolUse" in data, (
        "hooks.json missing top-level 'PreToolUse' key — wrong format (Codex uses {'hooks': {'PreToolUse': ...}})"
    )
    assert "hooks" not in data, "hooks.json has 'hooks' wrapper key — this is Codex format, not agy format"

    # Each entry must have matcher and command
    pre_hooks = _get_pre_tool_hooks(data)
    assert len(pre_hooks) >= 1, "No PreToolUse hooks registered"
    for hook in pre_hooks:
        assert "matcher" in hook, f"Hook entry missing 'matcher': {hook}"
        assert "command" in hook, f"Hook entry missing 'command': {hook}"


def test_install_hook_script_referenced_in_hooks_json(tmp_path: Path) -> None:
    """The hook script path referenced in hooks.json matches the installed script location."""
    subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)

    from trw_mcp.bootstrap._antigravity_distill_channels import install_antigravity_distill_channels

    install_antigravity_distill_channels(tmp_path)

    data = _read_hooks_json(tmp_path)
    pre_hooks = _get_pre_tool_hooks(data)
    commands = _distill_hook_commands(pre_hooks)

    # The command must reference the installed script path
    distill_cmd = next((cmd for cmd in commands if "trw_before_edit_telemetry" in cmd), None)
    assert distill_cmd is not None, "trw_before_edit_telemetry command not found"

    # The command must reference the installed script path (relative or absolute)
    expected_rel = ".antigravitycli/hooks/trw_before_edit_telemetry.py"
    assert expected_rel in distill_cmd, (
        f"Hook command does not reference expected script path '{expected_rel}': '{distill_cmd}'"
    )

    # The actual script file must exist
    hook_script = tmp_path / ".antigravitycli" / "hooks" / "trw_before_edit_telemetry.py"
    assert hook_script.exists(), f"Hook script referenced in hooks.json not found at {hook_script}"
