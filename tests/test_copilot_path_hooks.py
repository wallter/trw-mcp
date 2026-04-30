"""Copilot path instruction and hook tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from trw_mcp.bootstrap._copilot import (
    _COPILOT_HOOK_MAP,
    _COPILOT_HOOKS_PATH,
    _COPILOT_INSTRUCTIONS_DIR,
    _PATH_SCOPED_TEMPLATES,
    _TRW_HOOK_DESCRIPTION_PREFIX,
    _copilot_hooks_payload,
    _is_trw_hook_group,
    _merge_copilot_hooks,
    generate_copilot_hooks,
    generate_copilot_path_instructions,
)

from ._copilot_test_support import fake_git_repo  # noqa: F401


@pytest.mark.unit
class TestCopilotPathInstructions:
    """Test generate_copilot_path_instructions."""

    def test_path_instructions_dir_created(self, fake_git_repo: Path) -> None:
        result = generate_copilot_path_instructions(fake_git_repo)
        assert not result["errors"]
        assert (fake_git_repo / _COPILOT_INSTRUCTIONS_DIR).is_dir()

    def test_path_instructions_files_created(self, fake_git_repo: Path) -> None:
        result = generate_copilot_path_instructions(fake_git_repo)
        assert not result["errors"]
        instructions_dir = fake_git_repo / _COPILOT_INSTRUCTIONS_DIR
        md_files = list(instructions_dir.glob("*.instructions.md"))
        assert len(md_files) >= 1
        assert len(md_files) == len(_PATH_SCOPED_TEMPLATES)

    def test_path_instructions_yaml_frontmatter(self, fake_git_repo: Path) -> None:
        generate_copilot_path_instructions(fake_git_repo)
        instructions_dir = fake_git_repo / _COPILOT_INSTRUCTIONS_DIR
        for md_file in instructions_dir.glob("*.instructions.md"):
            content = md_file.read_text()
            assert content.startswith("---"), f"{md_file.name} missing YAML frontmatter"
            assert "applyTo:" in content, f"{md_file.name} missing applyTo field"

    def test_path_instructions_no_overwrite(self, fake_git_repo: Path) -> None:
        """Existing files are preserved without force."""
        generate_copilot_path_instructions(fake_git_repo)

        first_template_name = next(iter(_PATH_SCOPED_TEMPLATES))
        custom_path = fake_git_repo / _COPILOT_INSTRUCTIONS_DIR / first_template_name
        custom_path.write_text("# My custom instructions\n")

        result = generate_copilot_path_instructions(fake_git_repo)
        assert not result["errors"]

        rel_path = f"{_COPILOT_INSTRUCTIONS_DIR}/{first_template_name}"
        assert rel_path in result["preserved"]
        assert custom_path.read_text() == "# My custom instructions\n"

    def test_path_instructions_force_overwrites(self, fake_git_repo: Path) -> None:
        """force=True overwrites existing files."""
        generate_copilot_path_instructions(fake_git_repo)

        first_template_name = next(iter(_PATH_SCOPED_TEMPLATES))
        custom_path = fake_git_repo / _COPILOT_INSTRUCTIONS_DIR / first_template_name
        custom_path.write_text("# Custom\n")

        result = generate_copilot_path_instructions(fake_git_repo, force=True)
        assert not result["errors"]

        content = custom_path.read_text()
        assert "applyTo:" in content

    def test_path_instructions_created_list(self, fake_git_repo: Path) -> None:
        result = generate_copilot_path_instructions(fake_git_repo)
        assert len(result["created"]) == len(_PATH_SCOPED_TEMPLATES)
        for name in _PATH_SCOPED_TEMPLATES:
            assert f"{_COPILOT_INSTRUCTIONS_DIR}/{name}" in result["created"]


@pytest.mark.unit
class TestCopilotHooks:
    """Test generate_copilot_hooks and merge logic."""

    def test_hooks_json_created(self, fake_git_repo: Path) -> None:
        result = generate_copilot_hooks(fake_git_repo)
        assert not result["errors"]
        assert (fake_git_repo / _COPILOT_HOOKS_PATH).is_file()

    def test_hooks_json_valid_structure(self, fake_git_repo: Path) -> None:
        generate_copilot_hooks(fake_git_repo)
        data = json.loads((fake_git_repo / _COPILOT_HOOKS_PATH).read_text())
        assert data["version"] == 1
        assert "hooks" in data
        assert isinstance(data["hooks"], dict)

    def test_hooks_json_has_session_start(self, fake_git_repo: Path) -> None:
        generate_copilot_hooks(fake_git_repo)
        data = json.loads((fake_git_repo / _COPILOT_HOOKS_PATH).read_text())
        assert "sessionStart" in data["hooks"]

    def test_hooks_json_has_all_expected_events(self, fake_git_repo: Path) -> None:
        generate_copilot_hooks(fake_git_repo)
        data = json.loads((fake_git_repo / _COPILOT_HOOKS_PATH).read_text())
        for event_name in _COPILOT_HOOK_MAP:
            assert event_name in data["hooks"], f"Missing hook event: {event_name}"

    def test_hooks_json_merge_preserves_user_hooks(self, fake_git_repo: Path) -> None:
        """Write existing hooks.json with user hooks, verify user hooks preserved after merge."""
        hooks_dir = fake_git_repo / ".github" / "hooks"
        hooks_dir.mkdir(parents=True)
        hooks_path = fake_git_repo / _COPILOT_HOOKS_PATH

        user_hooks = {
            "version": 1,
            "hooks": {
                "sessionStart": [
                    {
                        "description": "My custom startup hook",
                        "hooks": [{"type": "command", "command": "echo hello"}],
                    }
                ],
                "myCustomEvent": [
                    {
                        "description": "Totally custom event",
                        "hooks": [{"type": "command", "command": "echo custom"}],
                    }
                ],
            },
        }
        hooks_path.write_text(json.dumps(user_hooks))

        result = generate_copilot_hooks(fake_git_repo)
        assert not result["errors"]

        data = json.loads(hooks_path.read_text())
        assert "myCustomEvent" in data["hooks"]
        session_groups = data["hooks"]["sessionStart"]
        descriptions = [group.get("description", "") for group in session_groups]
        assert any("My custom startup hook" in description for description in descriptions)
        assert any(description.startswith(_TRW_HOOK_DESCRIPTION_PREFIX) for description in descriptions)

    def test_hooks_stdin_adapter_in_command(self, fake_git_repo: Path) -> None:
        """Verify hook commands reference stdin reading (cat pattern)."""
        generate_copilot_hooks(fake_git_repo)
        data = json.loads((fake_git_repo / _COPILOT_HOOKS_PATH).read_text())
        for event_name, groups in data["hooks"].items():
            for group in groups:
                for hook in group.get("hooks", []):
                    command = hook.get("command", "")
                    assert "_input=$(cat)" in command, f"Hook {event_name} missing stdin adapter"

    def test_hooks_pre_tool_use_has_permission_decision(self, fake_git_repo: Path) -> None:
        """preToolUse hook must output JSON with permissionDecision."""
        generate_copilot_hooks(fake_git_repo)
        data = json.loads((fake_git_repo / _COPILOT_HOOKS_PATH).read_text())
        pre_tool_groups = data["hooks"]["preToolUse"]
        command = pre_tool_groups[0]["hooks"][0]["command"]
        assert "permissionDecision" in command

    def test_hooks_force_overwrites_existing(self, fake_git_repo: Path) -> None:
        """force=True ignores existing hooks.json entirely."""
        hooks_dir = fake_git_repo / ".github" / "hooks"
        hooks_dir.mkdir(parents=True)
        hooks_path = fake_git_repo / _COPILOT_HOOKS_PATH
        hooks_path.write_text(json.dumps({"version": 1, "hooks": {"myEvent": []}}))

        result = generate_copilot_hooks(fake_git_repo, force=True)
        assert not result["errors"]

        data = json.loads(hooks_path.read_text())
        assert "myEvent" not in data["hooks"]

    def test_hooks_trw_description_prefix(self, fake_git_repo: Path) -> None:
        """All TRW hook groups have the description prefix for identification."""
        generate_copilot_hooks(fake_git_repo)
        data = json.loads((fake_git_repo / _COPILOT_HOOKS_PATH).read_text())
        for event_name, groups in data["hooks"].items():
            for group in groups:
                description = group.get("description", "")
                assert description.startswith(_TRW_HOOK_DESCRIPTION_PREFIX), (
                    f"Hook {event_name} missing TRW description prefix"
                )


@pytest.mark.unit
class TestCopilotHooksMerge:
    """Unit tests for hooks payload and merge helpers."""

    def test_copilot_hooks_payload_version(self) -> None:
        payload = _copilot_hooks_payload()
        assert payload["version"] == 1

    def test_copilot_hooks_payload_events(self) -> None:
        payload = _copilot_hooks_payload()
        hooks = payload["hooks"]
        assert isinstance(hooks, dict)
        for event in _COPILOT_HOOK_MAP:
            assert event in hooks

    def test_is_trw_hook_group_positive(self) -> None:
        group = {"description": f"{_TRW_HOOK_DESCRIPTION_PREFIX} some description"}
        assert _is_trw_hook_group(group) is True

    def test_is_trw_hook_group_negative(self) -> None:
        group = {"description": "My custom hook"}
        assert _is_trw_hook_group(group) is False

    def test_is_trw_hook_group_missing_description(self) -> None:
        assert _is_trw_hook_group({}) is False

    def test_merge_replaces_trw_keeps_user(self) -> None:
        existing = {
            "version": 1,
            "hooks": {
                "sessionStart": [
                    {"description": "User hook", "hooks": []},
                    {"description": f"{_TRW_HOOK_DESCRIPTION_PREFIX} old", "hooks": []},
                ],
            },
        }
        merged = _merge_copilot_hooks(existing)
        session_groups = merged["hooks"]["sessionStart"]
        descriptions = [group["description"] for group in session_groups]
        assert any("User hook" in description for description in descriptions)
        assert not any(
            "old" in description and _TRW_HOOK_DESCRIPTION_PREFIX in description
            for description in descriptions
        )
        assert any(description.startswith(_TRW_HOOK_DESCRIPTION_PREFIX) for description in descriptions)

    def test_merge_empty_existing(self) -> None:
        merged = _merge_copilot_hooks({"version": 1, "hooks": {}})
        assert merged["version"] == 1
        for event in _COPILOT_HOOK_MAP:
            assert event in merged["hooks"]
