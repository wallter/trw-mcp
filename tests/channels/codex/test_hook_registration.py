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
    assert ".codex/hooks.json" in result["updated"], f"hooks.json not in result['updated']: {result}"
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
    commands = [cmd for cmd in _distill_group_commands(groups) if "trw_post_edit_telemetry" in cmd]
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
                    "hooks": [
                        {
                            "type": "command",
                            "command": '/bin/sh "$(git rev-parse --show-toplevel)/.claude/hooks/pre-tool-deliver-gate.sh"',
                        }
                    ],
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
    assert any("post-tool-event.sh" in cmd for cmd in commands), f"Ceremony hook lost after merge: {commands}"
    assert any("trw_post_edit_telemetry" in cmd for cmd in commands), f"Distill hook not added: {commands}"

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


# ---------------------------------------------------------------------------
# Coverage: error paths in merge_distill_hook_into_hooks_json
# ---------------------------------------------------------------------------


def test_merge_distill_hook_handles_malformed_json(tmp_path: Path) -> None:
    """merge_distill_hook handles corrupt hooks.json gracefully (recovers with fresh)."""
    from trw_mcp.bootstrap._codex_distill_channels import merge_distill_hook_into_hooks_json

    # Write malformed JSON so the parse path triggers
    codex_dir = tmp_path / ".codex"
    codex_dir.mkdir(parents=True, exist_ok=True)
    (codex_dir / "hooks.json").write_text("not valid json{{{", encoding="utf-8")

    # Should recover gracefully and write a fresh hooks.json
    result = merge_distill_hook_into_hooks_json(tmp_path)
    assert result["written"] is True
    assert result["error"] is None

    # Verify the new file is valid JSON with the distill entry
    data = json.loads((codex_dir / "hooks.json").read_text(encoding="utf-8"))
    groups = _get_post_tool_groups(data)
    commands = _distill_group_commands(groups)
    assert any("trw_post_edit_telemetry" in c for c in commands)


def test_merge_distill_hook_handles_non_dict_json(tmp_path: Path) -> None:
    """merge_distill_hook handles hooks.json that contains a JSON list (not dict)."""
    from trw_mcp.bootstrap._codex_distill_channels import merge_distill_hook_into_hooks_json

    codex_dir = tmp_path / ".codex"
    codex_dir.mkdir(parents=True, exist_ok=True)
    # A JSON list instead of a dict — branch line 99 not taken
    (codex_dir / "hooks.json").write_text(json.dumps([1, 2, 3]) + "\n", encoding="utf-8")

    result = merge_distill_hook_into_hooks_json(tmp_path)
    # Should succeed because it starts fresh (non-dict JSON → existing={})
    assert result["written"] is True


def test_merge_distill_hook_handles_non_utf8_json(tmp_path: Path) -> None:
    """merge_distill_hook recovers from a non-UTF-8 hooks.json without crashing.

    ``read_text(encoding="utf-8")`` raises ``UnicodeDecodeError`` (a
    ``ValueError``, not an ``OSError``), so the prior reader let it escape
    uncaught and crash bootstrap. The hardened reader routes through
    ``read_json_object`` and starts fresh.
    """
    from trw_mcp.bootstrap._codex_distill_channels import merge_distill_hook_into_hooks_json

    codex_dir = tmp_path / ".codex"
    codex_dir.mkdir(parents=True, exist_ok=True)
    (codex_dir / "hooks.json").write_bytes(b"\xff\xfe not valid utf-8 \x80\x81")

    result = merge_distill_hook_into_hooks_json(tmp_path)

    assert result["written"] is True
    assert result["error"] is None
    data = json.loads((codex_dir / "hooks.json").read_text(encoding="utf-8"))
    commands = _distill_group_commands(_get_post_tool_groups(data))
    assert any("trw_post_edit_telemetry" in c for c in commands)


# ---------------------------------------------------------------------------
# Coverage: install_codex_distill_channels error paths
# ---------------------------------------------------------------------------


def test_install_codex_distill_channels_manifest_error(tmp_path: Path) -> None:
    """install_codex_distill_channels captures manifest bootstrap errors."""
    from unittest.mock import patch

    from trw_mcp.bootstrap._codex_distill_channels import install_codex_distill_channels
    from trw_mcp.channels._manifest_loader import ManifestValidationError

    with patch(
        "trw_mcp.bootstrap._codex_distill_channels.bootstrap_codex_channel_manifest",
        side_effect=ManifestValidationError("test validation failure"),
    ):
        result = install_codex_distill_channels(tmp_path)

    assert any("manifest" in e.lower() for e in result["errors"]), (
        f"Expected manifest error in errors: {result['errors']}"
    )


def test_install_codex_distill_channels_generic_manifest_error(tmp_path: Path) -> None:
    """install_codex_distill_channels captures generic manifest bootstrap errors."""
    from unittest.mock import patch

    from trw_mcp.bootstrap._codex_distill_channels import install_codex_distill_channels

    with patch(
        "trw_mcp.bootstrap._codex_distill_channels.bootstrap_codex_channel_manifest",
        side_effect=RuntimeError("unexpected manifest failure"),
    ):
        result = install_codex_distill_channels(tmp_path)

    assert any("manifest" in e.lower() for e in result["errors"]), (
        f"Expected manifest error in errors: {result['errors']}"
    )


def test_install_codex_distill_channels_hook_install_error(tmp_path: Path) -> None:
    """install_codex_distill_channels captures hook install errors."""
    import subprocess
    import unittest.mock as mock

    subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)

    from trw_mcp.bootstrap._codex_distill_channels import install_codex_distill_channels

    with mock.patch(
        "trw_mcp.channels.codex._post_tool_use_telemetry.install_hook_script",
        side_effect=PermissionError("cannot write hook"),
    ):
        result = install_codex_distill_channels(tmp_path)

    assert any("hook" in e.lower() for e in result["errors"]), f"Expected hook error in errors: {result['errors']}"


def test_install_codex_distill_channels_hooks_json_error_from_merge(tmp_path: Path) -> None:
    """install_codex_distill_channels records error when merge_distill_hook returns error."""
    import subprocess
    from unittest.mock import patch

    subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)

    from trw_mcp.bootstrap._codex_distill_channels import install_codex_distill_channels

    with patch(
        "trw_mcp.bootstrap._codex_distill_channels.merge_distill_hook_into_hooks_json",
        return_value={
            "written": False,
            "path": str(tmp_path / ".codex/hooks.json"),
            "skipped": False,
            "error": "disk full",
        },
    ):
        result = install_codex_distill_channels(tmp_path)

    assert any("hooks.json" in e.lower() or "disk full" in e for e in result["errors"]), (
        f"Expected hooks.json error in result errors: {result['errors']}"
    )


def test_install_codex_distill_channels_hooks_json_exception(tmp_path: Path) -> None:
    """install_codex_distill_channels captures exception thrown by merge_distill_hook."""
    import subprocess
    from unittest.mock import patch

    subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)

    from trw_mcp.bootstrap._codex_distill_channels import install_codex_distill_channels

    with patch(
        "trw_mcp.bootstrap._codex_distill_channels.merge_distill_hook_into_hooks_json",
        side_effect=RuntimeError("unexpected merge failure"),
    ):
        result = install_codex_distill_channels(tmp_path)

    assert any("hooks.json" in e.lower() or "merge" in e.lower() for e in result["errors"]), (
        f"Expected hooks.json merge error: {result['errors']}"
    )


def test_install_codex_distill_channels_hook_skipped_path(tmp_path: Path) -> None:
    """install_codex_distill_channels adds to preserved when hook already exists."""
    import subprocess
    from unittest.mock import patch

    subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)

    from trw_mcp.bootstrap._codex_distill_channels import install_codex_distill_channels

    # First install so the hook exists
    install_codex_distill_channels(tmp_path)

    # The hook now already exists; with force=False it gets skipped
    # But install_codex_distill_channels always uses overwrite=force or True
    # so skipped can only happen if we patch install_hook_script to return skipped
    with patch(
        "trw_mcp.channels.codex._post_tool_use_telemetry.install_hook_script",
        return_value={"installed": False, "path": str(tmp_path), "skipped": True},
    ):
        result = install_codex_distill_channels(tmp_path)

    assert ".codex/hooks/trw_post_edit_telemetry.py" in result["preserved"], (
        f"Expected hook in preserved, got: {result}"
    )


def test_merge_distill_hook_with_non_dict_hooks_section(tmp_path: Path) -> None:
    """merge_distill_hook handles existing hooks.json where 'hooks' is not a dict."""
    from trw_mcp.bootstrap._codex_distill_channels import merge_distill_hook_into_hooks_json

    codex_dir = tmp_path / ".codex"
    codex_dir.mkdir(parents=True, exist_ok=True)
    # 'hooks' is a list instead of a dict — edge case
    (codex_dir / "hooks.json").write_text(
        json.dumps({"hooks": [{"weird": "structure"}]}) + "\n",
        encoding="utf-8",
    )

    result = merge_distill_hook_into_hooks_json(tmp_path)
    # Should still write (the non-dict hooks section gets replaced)
    assert result["written"] is True
    assert result["error"] is None


# ---------------------------------------------------------------------------
# FR13 — global home directory guard
# ---------------------------------------------------------------------------


def test_install_rejects_home_directory() -> None:
    """FR13: install_codex_distill_channels raises ValueError for Path.home()."""
    from pathlib import Path

    import pytest

    from trw_mcp.bootstrap._codex_distill_channels import install_codex_distill_channels

    with pytest.raises(ValueError, match="home directory"):
        install_codex_distill_channels(Path.home())


def test_install_accepts_non_home_directory(tmp_path: Path) -> None:
    """FR13: install_codex_distill_channels proceeds normally for a non-home target_dir."""
    import subprocess

    subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)

    from trw_mcp.bootstrap._codex_distill_channels import install_codex_distill_channels

    # Should not raise — tmp_path is not home
    result = install_codex_distill_channels(tmp_path)
    assert isinstance(result, dict)


# ---------------------------------------------------------------------------
# FR19 — hooks approval notice in result warnings
# ---------------------------------------------------------------------------


def test_install_result_contains_hooks_approval_notice(tmp_path: Path) -> None:
    """FR19: install_codex_distill_channels result includes hook approval notice in warnings."""
    import subprocess

    subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)

    from trw_mcp.bootstrap._codex_distill_channels import install_codex_distill_channels

    result = install_codex_distill_channels(tmp_path)

    assert "warnings" in result, "Result missing 'warnings' key"
    warnings = result["warnings"]
    assert len(warnings) >= 1, "Expected at least one warning (hook approval notice)"
    full_warning = " ".join(warnings)
    # The notice should mention hooks review — reuse codex_hooks_review_warning content
    assert "hooks" in full_warning.lower(), f"Hook approval notice not found in warnings: {warnings}"


# ---------------------------------------------------------------------------
# FR18 — gitignore entries for runtime state files
# ---------------------------------------------------------------------------


def test_install_adds_gitignore_entries_for_state_files(tmp_path: Path) -> None:
    """FR18: install_codex_distill_channels adds gitignore entries for state/lock/telemetry."""
    import subprocess

    subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)

    from trw_mcp.bootstrap._codex_distill_channels import install_codex_distill_channels
    from trw_mcp.channels._gitignore import list_gitignore_entries

    install_codex_distill_channels(tmp_path)

    entries = list_gitignore_entries(tmp_path)
    assert any("codex-*.state.json" in e for e in entries), f"codex-*.state.json not in gitignore entries: {entries}"
    assert any("codex-*.lock" in e for e in entries), f"codex-*.lock not in gitignore entries: {entries}"
    assert any("channel-events.jsonl" in e for e in entries), (
        f"channel-events.jsonl not in gitignore entries: {entries}"
    )
