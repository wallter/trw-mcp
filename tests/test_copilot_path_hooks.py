"""Copilot path instruction and hook tests."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from trw_mcp.bootstrap._copilot import (
    _COPILOT_ADAPTER_INSTALL_PATH,
    _COPILOT_ADAPTER_SCRIPT_NAME,
    _COPILOT_HOOK_MAP,
    _COPILOT_HOOKS_PATH,
    _COPILOT_INSTRUCTIONS_DIR,
    _PATH_SCOPED_TEMPLATES,
    _TRW_HOOK_DESCRIPTION_PREFIX,
    _build_hook_adapter_command,
    _bundled_adapter_script_path,
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

    def test_hooks_command_references_adapter_script(self, fake_git_repo: Path) -> None:
        """Hook commands must invoke the trw-copilot-adapter.sh script (no inline shell)."""
        generate_copilot_hooks(fake_git_repo)
        data = json.loads((fake_git_repo / _COPILOT_HOOKS_PATH).read_text())
        for event_name, groups in data["hooks"].items():
            for group in groups:
                for hook in group.get("hooks", []):
                    command = hook.get("command", "")
                    assert _COPILOT_ADAPTER_SCRIPT_NAME in command, (
                        f"Hook {event_name} command should invoke {_COPILOT_ADAPTER_SCRIPT_NAME}"
                    )

    def test_hooks_command_includes_event_name(self, fake_git_repo: Path) -> None:
        """Each hook command must pass the event name as an argument to the adapter."""
        generate_copilot_hooks(fake_git_repo)
        data = json.loads((fake_git_repo / _COPILOT_HOOKS_PATH).read_text())
        for event_name, groups in data["hooks"].items():
            if not event_name.startswith("TRW") and event_name in _COPILOT_HOOK_MAP:
                for group in groups:
                    for hook in group.get("hooks", []):
                        command = hook.get("command", "")
                        assert event_name in command, (
                            f"Hook command for {event_name} should include the event name as arg"
                        )

    def test_hooks_pre_tool_use_references_adapter(self, fake_git_repo: Path) -> None:
        """preToolUse hook command must invoke the adapter (permission logic is in the script)."""
        generate_copilot_hooks(fake_git_repo)
        data = json.loads((fake_git_repo / _COPILOT_HOOKS_PATH).read_text())
        pre_tool_groups = data["hooks"]["preToolUse"]
        command = pre_tool_groups[0]["hooks"][0]["command"]
        assert _COPILOT_ADAPTER_SCRIPT_NAME in command
        assert "preToolUse" in command

    def test_hooks_adapter_script_installed(self, fake_git_repo: Path) -> None:
        """generate_copilot_hooks installs trw-copilot-adapter.sh alongside hooks.json."""
        result = generate_copilot_hooks(fake_git_repo)
        assert not result["errors"]
        adapter_path = fake_git_repo / _COPILOT_ADAPTER_INSTALL_PATH
        assert adapter_path.is_file(), "trw-copilot-adapter.sh was not installed"
        # Must be executable
        assert adapter_path.stat().st_mode & 0o111, "adapter script is not executable"

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
            "old" in description and _TRW_HOOK_DESCRIPTION_PREFIX in description for description in descriptions
        )
        assert any(description.startswith(_TRW_HOOK_DESCRIPTION_PREFIX) for description in descriptions)

    def test_merge_empty_existing(self) -> None:
        merged = _merge_copilot_hooks({"version": 1, "hooks": {}})
        assert merged["version"] == 1
        for event in _COPILOT_HOOK_MAP:
            assert event in merged["hooks"]


@pytest.mark.unit
class TestCopilotHookCommandShellValidity:
    """Regression tests: generated hook commands must be bash-syntax-valid.

    PRD fix: the previous single-quote outer wrapper with inner single-quoted
    grep/sed patterns caused 'unexpected EOF while looking for matching' when
    Copilot ran the command via bash -c.  These tests are the permanent guard.
    """

    def test_all_events_pass_bash_n(self) -> None:
        """Every hook event's generated command passes bash -n (no syntax errors)."""
        for event_name in _COPILOT_HOOK_MAP:
            cmd = _build_hook_adapter_command(event_name, "/tmp/fake-hook.sh")
            result = subprocess.run(
                ["bash", "-n", "-c", cmd],
                capture_output=True,
                text=True,
            )
            assert result.returncode == 0, (
                f"bash -n failed for event '{event_name}': {result.stderr.strip()!r}\nCommand was: {cmd!r}"
            )

    def test_command_is_simple_invocation_no_inline_shell(self) -> None:
        """Generated command is a clean /bin/sh invocation — no nested quoting."""
        for event_name in _COPILOT_HOOK_MAP:
            cmd = _build_hook_adapter_command(event_name, "/tmp/fake-hook.sh", "/tmp/adapter.sh")
            # Command must be exactly: /bin/sh "<adapter>" "<hook>" "<event>"
            assert cmd == f'/bin/sh "/tmp/adapter.sh" "/tmp/fake-hook.sh" "{event_name}"', (
                f"Unexpected command shape for {event_name}: {cmd!r}"
            )

    def test_adapter_script_passes_bash_n(self) -> None:
        """The bundled trw-copilot-adapter.sh script is shell-syntax-valid."""
        adapter = _bundled_adapter_script_path()
        assert adapter.is_file(), f"Bundled adapter script missing: {adapter}"
        result = subprocess.run(["bash", "-n", str(adapter)], capture_output=True, text=True)
        assert result.returncode == 0, f"bash -n failed on adapter script: {result.stderr.strip()!r}"


@pytest.mark.unit
class TestCopilotAdapterScriptBehavior:
    """Behavioral tests for trw-copilot-adapter.sh.

    Tests run the real shell script with synthetic input to verify:
    - toolName extraction from Copilot JSON payload
    - preToolUse allow/deny JSON decision output
    - fail-open when hook script is missing
    """

    @pytest.fixture()
    def adapter(self) -> Path:
        """Return the bundled adapter script path."""
        p = _bundled_adapter_script_path()
        assert p.is_file()
        return p

    @pytest.fixture()
    def allow_hook(self, tmp_path: Path) -> Path:
        """A fake TRW hook that exits 0 (allow)."""
        hook = tmp_path / "allow-hook.sh"
        hook.write_text("#!/bin/sh\nexit 0\n")
        hook.chmod(0o755)
        return hook

    @pytest.fixture()
    def deny_hook(self, tmp_path: Path) -> Path:
        """A fake TRW hook that exits 2 (deny)."""
        hook = tmp_path / "deny-hook.sh"
        hook.write_text("#!/bin/sh\nexit 2\n")
        hook.chmod(0o755)
        return hook

    @pytest.fixture()
    def echo_tool_name_hook(self, tmp_path: Path) -> Path:
        """A fake TRW hook that echoes $TOOL_NAME."""
        hook = tmp_path / "echo-hook.sh"
        hook.write_text('#!/bin/sh\nprintf "TOOL_NAME=%s\\n" "$TOOL_NAME"\nexit 0\n')
        hook.chmod(0o755)
        return hook

    def _run_adapter(self, adapter: Path, hook: Path, event: str, payload: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["/bin/sh", str(adapter), str(hook), event],
            input=payload,
            capture_output=True,
            text=True,
        )

    def test_tool_name_extracted_via_grep_fallback(self, adapter: Path, echo_tool_name_hook: Path) -> None:
        """toolName is correctly extracted from the Copilot JSON payload."""
        payload = '{"toolName":"str_replace_editor","tool_input":{"path":"foo.py"}}'
        result = self._run_adapter(adapter, echo_tool_name_hook, "postToolUse", payload)
        assert result.returncode == 0
        assert "str_replace_editor" in result.stdout

    def test_pre_tool_use_allow_on_exit_0(self, adapter: Path, allow_hook: Path) -> None:
        """preToolUse emits allow JSON when hook exits 0."""
        payload = '{"toolName":"trw_learn","tool_input":{}}'
        result = self._run_adapter(adapter, allow_hook, "preToolUse", payload)
        assert result.returncode == 0
        assert result.stdout == '{"permissionDecision":"allow"}'

    def test_pre_tool_use_deny_on_exit_2(self, adapter: Path, deny_hook: Path) -> None:
        """preToolUse emits deny JSON when hook exits 2."""
        payload = '{"toolName":"trw_deliver","tool_input":{}}'
        result = self._run_adapter(adapter, deny_hook, "preToolUse", payload)
        assert result.returncode == 0
        assert result.stdout == '{"permissionDecision":"deny"}'

    def test_fail_open_missing_hook(self, adapter: Path, tmp_path: Path) -> None:
        """preToolUse fails open (allow) when the hook script does not exist."""
        missing = tmp_path / "nonexistent-hook.sh"
        payload = '{"toolName":"anything","tool_input":{}}'
        result = self._run_adapter(adapter, missing, "preToolUse", payload)
        assert result.returncode == 0
        assert result.stdout == '{"permissionDecision":"allow"}'

    def test_non_permission_hook_fail_open_on_error(self, adapter: Path, tmp_path: Path) -> None:
        """Non-permission hooks fail open (exit 0) when hook script is missing."""
        missing = tmp_path / "nonexistent-hook.sh"
        payload = '{"toolName":"anything","tool_input":{}}'
        result = self._run_adapter(adapter, missing, "postToolUse", payload)
        assert result.returncode == 0
