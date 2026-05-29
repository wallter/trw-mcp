"""Tests for install_codex_distill_channels hooks.json registration.

Verifies:
- install_codex_distill_channels writes .codex/hooks.json with a
  PostToolUse group referencing trw_post_edit_telemetry (the wiring fix).
- Running install twice is idempotent — no double-add.
- Existing ceremony entries in hooks.json are preserved on merge.

PRD-DIST-2402 FR41-FR43.
"""

from __future__ import annotations

import json
from pathlib import Path

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _read_hooks_json(project: Path) -> dict:
    """Read and parse .codex/hooks.json from a project root."""
    hooks_path = project / ".codex" / "hooks.json"
    assert hooks_path.exists(), f".codex/hooks.json not found at {hooks_path}"
    return json.loads(hooks_path.read_text(encoding="utf-8"))


def _get_post_tool_groups(hooks_data: dict) -> list:
    """Extract PostToolUse groups list from hooks.json dict."""
    return hooks_data.get("hooks", {}).get("PostToolUse", [])


def _distill_group_commands(groups: list) -> list[str]:
    """Collect all command strings from PostToolUse groups."""
    commands = []
    for group in groups:
        if isinstance(group, dict):
            for hook in group.get("hooks") or []:
                if isinstance(hook, dict):
                    cmd = hook.get("command", "")
                    commands.append(cmd)
    return commands


# ---------------------------------------------------------------------------
# Core registration tests
# ---------------------------------------------------------------------------


def test_install_creates_hooks_json_with_distill_entry(tmp_path: Path) -> None:
    """install_codex_distill_channels writes .codex/hooks.json referencing trw_post_edit_telemetry."""
    # git init so bootstrap_codex_channel_manifest can write manifest
    import subprocess
    subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)

    from trw_mcp.bootstrap._codex_distill_channels import install_codex_distill_channels

    result = install_codex_distill_channels(tmp_path)

    # hooks.json must exist
    hooks_path = tmp_path / ".codex" / "hooks.json"
    assert hooks_path.exists(), ".codex/hooks.json was not created"

    # Must contain a PostToolUse group referencing trw_post_edit_telemetry
    data = _read_hooks_json(tmp_path)
    groups = _get_post_tool_groups(data)
    commands = _distill_group_commands(groups)
    assert any("trw_post_edit_telemetry" in cmd for cmd in commands), (
        f"No trw_post_edit_telemetry command found in PostToolUse groups: {commands}"
    )

    # Result should list hooks.json as updated (not in errors)
    assert ".codex/hooks.json" in result["updated"], (
        f"hooks.json not in result['updated']: {result}"
    )
    assert not result["errors"], f"Unexpected errors: {result['errors']}"


def test_install_idempotent_no_double_add(tmp_path: Path) -> None:
    """Running install_codex_distill_channels twice does not duplicate the distill hook."""
    import subprocess
    subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)

    from trw_mcp.bootstrap._codex_distill_channels import install_codex_distill_channels

    install_codex_distill_channels(tmp_path)
    install_codex_distill_channels(tmp_path)

    data = _read_hooks_json(tmp_path)
    groups = _get_post_tool_groups(data)
    commands = [
        cmd for cmd in _distill_group_commands(groups)
        if "trw_post_edit_telemetry" in cmd
    ]
    assert len(commands) == 1, (
        f"Expected exactly 1 trw_post_edit_telemetry command after 2 installs, got {len(commands)}: {commands}"
    )


def test_install_preserves_existing_ceremony_entries(tmp_path: Path) -> None:
    """install_codex_distill_channels preserves pre-existing ceremony hooks.json entries."""
    import subprocess
    subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)

    # Write a pre-existing hooks.json with a ceremony PostToolUse entry
    codex_dir = tmp_path / ".codex"
    codex_dir.mkdir(parents=True, exist_ok=True)
    ceremony_entry = {
        "description": "TRW managed: PostToolUse",
        "hooks": [
            {
                "type": "command",
                "command": '/bin/sh "$(git rev-parse --show-toplevel)/.claude/hooks/post-tool-event.sh"',
                "statusMessage": "Logging TRW tool effects",
            }
        ],
    }
    pre_existing = {
        "hooks": {
            "PostToolUse": [ceremony_entry],
            "PreToolUse": [
                {
                    "description": "TRW managed: PreToolUse",
                    "hooks": [{"type": "command", "command": '/bin/sh "$(git rev-parse --show-toplevel)/.claude/hooks/pre-tool-deliver-gate.sh"'}],
                }
            ],
        }
    }
    (codex_dir / "hooks.json").write_text(json.dumps(pre_existing, indent=2) + "\n", encoding="utf-8")

    from trw_mcp.bootstrap._codex_distill_channels import install_codex_distill_channels

    install_codex_distill_channels(tmp_path)

    data = _read_hooks_json(tmp_path)
    groups = _get_post_tool_groups(data)
    commands = _distill_group_commands(groups)

    # Both ceremony and distill entries must be present
    assert any("post-tool-event.sh" in cmd for cmd in commands), (
        f"Ceremony hook lost after merge: {commands}"
    )
    assert any("trw_post_edit_telemetry" in cmd for cmd in commands), (
        f"Distill hook not added: {commands}"
    )

    # PreToolUse entry must also be preserved
    pre_groups = data.get("hooks", {}).get("PreToolUse", [])
    pre_commands = _distill_group_commands(pre_groups)
    assert any("pre-tool-deliver-gate.sh" in cmd for cmd in pre_commands), (
        f"PreToolUse ceremony hook lost: {pre_commands}"
    )


# ---------------------------------------------------------------------------
# merge_distill_hook_into_hooks_json unit tests
# ---------------------------------------------------------------------------


def test_merge_distill_hook_creates_file_when_missing(tmp_path: Path) -> None:
    """merge_distill_hook_into_hooks_json creates hooks.json when it does not exist."""
    from trw_mcp.bootstrap._codex_distill_channels import merge_distill_hook_into_hooks_json

    result = merge_distill_hook_into_hooks_json(tmp_path)

    assert result["written"] is True
    assert result["error"] is None
    assert result["skipped"] is False

    hooks_path = tmp_path / ".codex" / "hooks.json"
    assert hooks_path.exists()

    data = json.loads(hooks_path.read_text(encoding="utf-8"))
    groups = _get_post_tool_groups(data)
    commands = _distill_group_commands(groups)
    assert any("trw_post_edit_telemetry" in cmd for cmd in commands)


def test_merge_distill_hook_idempotent(tmp_path: Path) -> None:
    """merge_distill_hook_into_hooks_json returns skipped=True on second call."""
    from trw_mcp.bootstrap._codex_distill_channels import merge_distill_hook_into_hooks_json

    r1 = merge_distill_hook_into_hooks_json(tmp_path)
    r2 = merge_distill_hook_into_hooks_json(tmp_path)

    assert r1["written"] is True
    assert r2["written"] is False
    assert r2["skipped"] is True

    # Only one entry in the file
    data = json.loads((tmp_path / ".codex" / "hooks.json").read_text(encoding="utf-8"))
    groups = _get_post_tool_groups(data)
    commands = [c for c in _distill_group_commands(groups) if "trw_post_edit_telemetry" in c]
    assert len(commands) == 1


def test_merge_distill_hook_appends_not_clobbers(tmp_path: Path) -> None:
    """merge_distill_hook_into_hooks_json appends to existing PostToolUse groups."""
    codex_dir = tmp_path / ".codex"
    codex_dir.mkdir(parents=True, exist_ok=True)

    existing_group = {
        "description": "user hook",
        "hooks": [{"type": "command", "command": "echo hello"}],
    }
    (codex_dir / "hooks.json").write_text(
        json.dumps({"hooks": {"PostToolUse": [existing_group]}}, indent=2) + "\n",
        encoding="utf-8",
    )

    from trw_mcp.bootstrap._codex_distill_channels import merge_distill_hook_into_hooks_json

    merge_distill_hook_into_hooks_json(tmp_path)

    data = json.loads((codex_dir / "hooks.json").read_text(encoding="utf-8"))
    groups = _get_post_tool_groups(data)
    assert len(groups) == 2, f"Expected 2 groups (user + distill), got: {len(groups)}"

    commands = _distill_group_commands(groups)
    assert any("trw_post_edit_telemetry" in c for c in commands), "Distill hook missing"
    assert any("echo hello" in c for c in commands), "Existing user hook clobbered"


def test_merge_distill_hook_description_is_correct(tmp_path: Path) -> None:
    """The distill hook group has the expected description string."""
    from trw_mcp.bootstrap._codex_distill_channels import merge_distill_hook_into_hooks_json

    merge_distill_hook_into_hooks_json(tmp_path)

    data = json.loads((tmp_path / ".codex" / "hooks.json").read_text(encoding="utf-8"))
    groups = _get_post_tool_groups(data)
    descriptions = [g.get("description", "") for g in groups if isinstance(g, dict)]
    assert any("trw-distill PostToolUse telemetry" in d for d in descriptions), (
        f"Expected trw-distill description, got: {descriptions}"
    )
