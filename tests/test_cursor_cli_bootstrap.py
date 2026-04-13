"""Unit tests for cursor-cli bootstrap generators (PRD-CORE-137).

Covers Tasks 14-17b:
  Task 14: generate_cursor_cli_config — cli.json permissions
  Task 15: generate_cursor_cli_agents_md — AGENTS.md sentinel block
  Task 16: generate_cursor_cli_hooks — 5-event CLI subset
  Task 17b: _emit_cli_safety_reminder — TTY/tmux advisory
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _read_cli_json(tmp_path: Path) -> dict:
    return json.loads((tmp_path / ".cursor" / "cli.json").read_text())


def _read_hooks_json(tmp_path: Path) -> dict:
    return json.loads((tmp_path / ".cursor" / "hooks.json").read_text())


# ===========================================================================
# Task 14: generate_cursor_cli_config
# ===========================================================================


class TestCliConfigFresh:
    """test_cli_config_fresh: fresh write creates all baseline tokens."""

    def test_file_created(self, tmp_path: Path) -> None:
        from trw_mcp.bootstrap._cursor_cli import generate_cursor_cli_config

        result = generate_cursor_cli_config(tmp_path)
        assert ".cursor/cli.json" in result["created"]
        assert (tmp_path / ".cursor" / "cli.json").is_file()

    def test_baseline_allow_tokens(self, tmp_path: Path) -> None:
        from trw_mcp.bootstrap._cursor_cli import (
            _DEFAULT_ALLOW,
            generate_cursor_cli_config,
        )

        generate_cursor_cli_config(tmp_path)
        config = _read_cli_json(tmp_path)
        for token in _DEFAULT_ALLOW:
            assert token in config["permissions"]["allow"], f"Missing allow token: {token}"

    def test_baseline_deny_tokens(self, tmp_path: Path) -> None:
        from trw_mcp.bootstrap._cursor_cli import (
            _DEFAULT_DENY,
            generate_cursor_cli_config,
        )

        generate_cursor_cli_config(tmp_path)
        config = _read_cli_json(tmp_path)
        for token in _DEFAULT_DENY:
            assert token in config["permissions"]["deny"], f"Missing deny token: {token}"

    def test_has_note_key(self, tmp_path: Path) -> None:
        from trw_mcp.bootstrap._cursor_cli import generate_cursor_cli_config

        generate_cursor_cli_config(tmp_path)
        config = _read_cli_json(tmp_path)
        assert "_note" in config

    def test_tty_reminder_in_info(self, tmp_path: Path) -> None:
        from trw_mcp.bootstrap._cursor_cli import generate_cursor_cli_config

        result = generate_cursor_cli_config(tmp_path)
        info = result.get("info", [])
        all_info = " ".join(info)
        assert "TTY" in all_info
        assert "tmux" in all_info


class TestCliConfigSmartMerge:
    """test_cli_config_smart_merge_preserves_user_allow: user tokens survive merge."""

    def test_preserves_user_allow_token(self, tmp_path: Path) -> None:
        from trw_mcp.bootstrap._cursor_cli import generate_cursor_cli_config

        cursor_dir = tmp_path / ".cursor"
        cursor_dir.mkdir()
        user_config = {
            "permissions": {
                "allow": ["Shell(my-tool)"],
                "deny": [],
            }
        }
        (cursor_dir / "cli.json").write_text(json.dumps(user_config))

        result = generate_cursor_cli_config(tmp_path)
        assert ".cursor/cli.json" in result["updated"]
        merged = _read_cli_json(tmp_path)
        assert "Shell(my-tool)" in merged["permissions"]["allow"]
        # TRW defaults should also be present
        assert "Shell(git)" in merged["permissions"]["allow"]

    def test_preserves_user_deny_token(self, tmp_path: Path) -> None:
        from trw_mcp.bootstrap._cursor_cli import generate_cursor_cli_config

        cursor_dir = tmp_path / ".cursor"
        cursor_dir.mkdir()
        user_config = {
            "permissions": {
                "allow": [],
                "deny": ["Write(secret.key)"],
            }
        }
        (cursor_dir / "cli.json").write_text(json.dumps(user_config))

        generate_cursor_cli_config(tmp_path)
        merged = _read_cli_json(tmp_path)
        assert "Write(secret.key)" in merged["permissions"]["deny"]
        # TRW deny defaults present too
        assert "Read(.env*)" in merged["permissions"]["deny"]

    def test_preserves_extra_top_level_keys(self, tmp_path: Path) -> None:
        """test_cli_config_preserves_extra_keys: model_defaults survives merge."""
        from trw_mcp.bootstrap._cursor_cli import generate_cursor_cli_config

        cursor_dir = tmp_path / ".cursor"
        cursor_dir.mkdir()
        user_config = {
            "permissions": {"allow": [], "deny": []},
            "model_defaults": {"temperature": 0.7},
        }
        (cursor_dir / "cli.json").write_text(json.dumps(user_config))

        generate_cursor_cli_config(tmp_path)
        merged = _read_cli_json(tmp_path)
        assert "model_defaults" in merged
        assert merged["model_defaults"]["temperature"] == 0.7


class TestCliConfigNoDuplicates:
    """test_cli_config_no_duplicate_tokens: no duplicate entries when user has TRW token."""

    def test_no_duplicate_allow_tokens(self, tmp_path: Path) -> None:
        from trw_mcp.bootstrap._cursor_cli import generate_cursor_cli_config

        cursor_dir = tmp_path / ".cursor"
        cursor_dir.mkdir()
        # Pre-seed with a TRW default — should NOT be duplicated
        user_config = {
            "permissions": {
                "allow": ["Shell(git)"],  # already in _DEFAULT_ALLOW
                "deny": [],
            }
        }
        (cursor_dir / "cli.json").write_text(json.dumps(user_config))

        generate_cursor_cli_config(tmp_path)
        merged = _read_cli_json(tmp_path)
        allow = merged["permissions"]["allow"]
        assert allow.count("Shell(git)") == 1, "Shell(git) should appear exactly once"

    def test_no_duplicate_deny_tokens(self, tmp_path: Path) -> None:
        from trw_mcp.bootstrap._cursor_cli import generate_cursor_cli_config

        cursor_dir = tmp_path / ".cursor"
        cursor_dir.mkdir()
        user_config = {
            "permissions": {
                "allow": [],
                "deny": ["Read(.env*)"],  # already in _DEFAULT_DENY
            }
        }
        (cursor_dir / "cli.json").write_text(json.dumps(user_config))

        generate_cursor_cli_config(tmp_path)
        merged = _read_cli_json(tmp_path)
        deny = merged["permissions"]["deny"]
        assert deny.count("Read(.env*)") == 1, "Read(.env*) should appear exactly once"


class TestCliConfigMalformed:
    """test_cli_config_malformed_fallback: malformed JSON triggers overwrite + warning."""

    def test_malformed_json_overwrites(self, tmp_path: Path) -> None:
        from trw_mcp.bootstrap._cursor_cli import (
            _DEFAULT_ALLOW,
            _DEFAULT_DENY,
            generate_cursor_cli_config,
        )

        cursor_dir = tmp_path / ".cursor"
        cursor_dir.mkdir()
        (cursor_dir / "cli.json").write_text("{ not valid json {{{{")

        result = generate_cursor_cli_config(tmp_path)
        assert ".cursor/cli.json" in result["updated"]
        # Should now contain the defaults
        merged = _read_cli_json(tmp_path)
        for token in _DEFAULT_ALLOW:
            assert token in merged["permissions"]["allow"]
        for token in _DEFAULT_DENY:
            assert token in merged["permissions"]["deny"]


# ===========================================================================
# Task 15: generate_cursor_cli_agents_md
# ===========================================================================


class TestAgentsMdFresh:
    """test_agents_md_fresh_creates_sentinel_block."""

    def test_creates_file(self, tmp_path: Path) -> None:
        from trw_mcp.bootstrap._cursor_cli import generate_cursor_cli_agents_md

        result = generate_cursor_cli_agents_md(tmp_path, "Test ceremony content")
        assert "AGENTS.md" in result["created"]
        assert (tmp_path / "AGENTS.md").is_file()

    def test_sentinels_present(self, tmp_path: Path) -> None:
        from trw_mcp.bootstrap._cursor_cli import generate_cursor_cli_agents_md

        generate_cursor_cli_agents_md(tmp_path, "Test ceremony content")
        content = (tmp_path / "AGENTS.md").read_text()
        assert "<!-- TRW:BEGIN -->" in content
        assert "<!-- TRW:END -->" in content

    def test_trw_section_inside_block(self, tmp_path: Path) -> None:
        from trw_mcp.bootstrap._cursor_cli import generate_cursor_cli_agents_md

        generate_cursor_cli_agents_md(tmp_path, "Ceremony content here")
        content = (tmp_path / "AGENTS.md").read_text()
        begin_idx = content.index("<!-- TRW:BEGIN -->")
        end_idx = content.index("<!-- TRW:END -->")
        block = content[begin_idx:end_idx]
        assert "Ceremony content here" in block

    def test_cursor_cli_header(self, tmp_path: Path) -> None:
        from trw_mcp.bootstrap._cursor_cli import generate_cursor_cli_agents_md

        generate_cursor_cli_agents_md(tmp_path, "Content")
        content = (tmp_path / "AGENTS.md").read_text()
        assert "cursor-cli" in content


class TestAgentsMdSentinelMerge:
    """test_agents_md_sentinel_merge_preserves_user_content."""

    def test_preserves_pre_content(self, tmp_path: Path) -> None:
        from trw_mcp.bootstrap._cursor_cli import generate_cursor_cli_agents_md

        agents_file = tmp_path / "AGENTS.md"
        pre_content = "# My Project Rules\nBe concise.\n\n"
        post_content = "\n## Custom Stuff\nDon't break things.\n"
        agents_file.write_text(
            pre_content
            + "<!-- TRW:BEGIN -->\nOld TRW content\n<!-- TRW:END -->"
            + post_content
        )

        generate_cursor_cli_agents_md(tmp_path, "New TRW content")
        content = agents_file.read_text()
        assert "Be concise." in content
        assert "Don't break things." in content
        assert "New TRW content" in content
        assert "Old TRW content" not in content

    def test_updated_in_result(self, tmp_path: Path) -> None:
        from trw_mcp.bootstrap._cursor_cli import generate_cursor_cli_agents_md

        agents_file = tmp_path / "AGENTS.md"
        agents_file.write_text(
            "<!-- TRW:BEGIN -->\nOld content\n<!-- TRW:END -->\n"
        )
        result = generate_cursor_cli_agents_md(tmp_path, "New content")
        assert "AGENTS.md" in result["updated"]


class TestAgentsMdNoSentinels:
    """test_agents_md_no_sentinels_prepends_block."""

    def test_no_sentinels_prepends(self, tmp_path: Path) -> None:
        from trw_mcp.bootstrap._cursor_cli import generate_cursor_cli_agents_md

        agents_file = tmp_path / "AGENTS.md"
        original = "# My existing rules\nBe careful.\n"
        agents_file.write_text(original)

        generate_cursor_cli_agents_md(tmp_path, "TRW content")
        content = agents_file.read_text()
        # TRW block should come first
        begin_idx = content.index("<!-- TRW:BEGIN -->")
        original_idx = content.index("Be careful.")
        assert begin_idx < original_idx
        # Original content preserved
        assert "Be careful." in content


# ===========================================================================
# Task 16: generate_cursor_cli_hooks
# ===========================================================================


class TestCliHooksFiveEvents:
    """test_cli_hooks_subset_five_events."""

    def test_exactly_five_events(self, tmp_path: Path) -> None:
        from trw_mcp.bootstrap._cursor_cli import generate_cursor_cli_hooks

        generate_cursor_cli_hooks(tmp_path)
        hooks = _read_hooks_json(tmp_path)
        assert len(hooks["hooks"]) == 5

    def test_all_five_expected_events(self, tmp_path: Path) -> None:
        from trw_mcp.bootstrap._cursor_cli import generate_cursor_cli_hooks

        generate_cursor_cli_hooks(tmp_path)
        hooks = _read_hooks_json(tmp_path)
        expected = {
            "beforeShellExecution",
            "afterShellExecution",
            "beforeMCPExecution",
            "afterMCPExecution",
            "stop",
        }
        assert set(hooks["hooks"].keys()) == expected


class TestCliHooksFailClosed:
    """test_cli_hooks_fail_closed_security."""

    def test_before_shell_fail_closed_true(self, tmp_path: Path) -> None:
        from trw_mcp.bootstrap._cursor_cli import generate_cursor_cli_hooks

        generate_cursor_cli_hooks(tmp_path)
        hooks = _read_hooks_json(tmp_path)
        handlers = hooks["hooks"]["beforeShellExecution"]
        assert any(h.get("failClosed") is True for h in handlers)

    def test_before_mcp_fail_closed_true(self, tmp_path: Path) -> None:
        from trw_mcp.bootstrap._cursor_cli import generate_cursor_cli_hooks

        generate_cursor_cli_hooks(tmp_path)
        hooks = _read_hooks_json(tmp_path)
        handlers = hooks["hooks"]["beforeMCPExecution"]
        assert any(h.get("failClosed") is True for h in handlers)

    def test_after_shell_fail_closed_false(self, tmp_path: Path) -> None:
        from trw_mcp.bootstrap._cursor_cli import generate_cursor_cli_hooks

        generate_cursor_cli_hooks(tmp_path)
        hooks = _read_hooks_json(tmp_path)
        handlers = hooks["hooks"]["afterShellExecution"]
        assert all(h.get("failClosed") is False for h in handlers)

    def test_after_mcp_fail_closed_false(self, tmp_path: Path) -> None:
        from trw_mcp.bootstrap._cursor_cli import generate_cursor_cli_hooks

        generate_cursor_cli_hooks(tmp_path)
        hooks = _read_hooks_json(tmp_path)
        handlers = hooks["hooks"]["afterMCPExecution"]
        assert all(h.get("failClosed") is False for h in handlers)

    def test_stop_fail_closed_false(self, tmp_path: Path) -> None:
        from trw_mcp.bootstrap._cursor_cli import generate_cursor_cli_hooks

        generate_cursor_cli_hooks(tmp_path)
        hooks = _read_hooks_json(tmp_path)
        handlers = hooks["hooks"]["stop"]
        assert all(h.get("failClosed") is False for h in handlers)


class TestCliHooksNoIdeEvents:
    """test_cli_hooks_no_ide_events: IDE-only events absent from CLI hooks."""

    @pytest.mark.parametrize(
        "ide_event",
        [
            "beforeTabFileRead",
            "afterTabFileEdit",
            "subagentStart",
            "beforeSubmitPrompt",
        ],
    )
    def test_ide_event_absent(self, tmp_path: Path, ide_event: str) -> None:
        from trw_mcp.bootstrap._cursor_cli import generate_cursor_cli_hooks

        generate_cursor_cli_hooks(tmp_path)
        hooks = _read_hooks_json(tmp_path)
        assert ide_event not in hooks["hooks"], f"IDE-only event should not be present: {ide_event}"


class TestCliHooksPreservesIdeEvents:
    """test_cli_hooks_preserves_ide_events_when_dual."""

    def test_preserves_seeded_ide_events(self, tmp_path: Path) -> None:
        """If IDE already wrote 8 events, CLI pass preserves all of them."""
        from trw_mcp.bootstrap._cursor_cli import generate_cursor_cli_hooks

        cursor_dir = tmp_path / ".cursor"
        cursor_dir.mkdir()
        # Seed hooks.json with 8 IDE events (using non-TRW-prefix commands)
        ide_hooks: dict = {
            "version": 1,
            "hooks": {
                "beforeTabFileRead": [{"command": "user-before-tab.sh", "type": "command"}],
                "afterTabFileEdit": [{"command": "user-after-tab.sh", "type": "command"}],
                "subagentStart": [{"command": "user-subagent.sh", "type": "command"}],
                "beforeSubmitPrompt": [{"command": "user-before-submit.sh", "type": "command"}],
                "afterAgentResponse": [{"command": "user-after-response.sh", "type": "command"}],
                "afterAgentThought": [{"command": "user-after-thought.sh", "type": "command"}],
                "preToolUse": [{"command": "user-pre-tool.sh", "type": "command"}],
                "postToolUse": [{"command": "user-post-tool.sh", "type": "command"}],
            },
        }
        (cursor_dir / "hooks.json").write_text(json.dumps(ide_hooks))

        generate_cursor_cli_hooks(tmp_path)
        merged = _read_hooks_json(tmp_path)

        # CLI events added
        assert "beforeShellExecution" in merged["hooks"]
        assert "afterMCPExecution" in merged["hooks"]
        assert "stop" in merged["hooks"]
        # IDE events preserved
        assert "beforeTabFileRead" in merged["hooks"]
        assert "afterTabFileEdit" in merged["hooks"]
        assert "subagentStart" in merged["hooks"]


# ===========================================================================
# Task 17b: TTY reminder
# ===========================================================================


class TestTtyReminder:
    """test_tty_reminder_emitted: result["info"] contains TTY and tmux."""

    def test_tty_reminder_on_fresh_init(self, tmp_path: Path) -> None:
        from trw_mcp.bootstrap._cursor_cli import generate_cursor_cli_config

        result = generate_cursor_cli_config(tmp_path)
        info = result.get("info", [])
        all_text = " ".join(info)
        assert "TTY" in all_text
        assert "tmux" in all_text

    def test_github_actions_mentioned(self, tmp_path: Path) -> None:
        from trw_mcp.bootstrap._cursor_cli import generate_cursor_cli_config

        result = generate_cursor_cli_config(tmp_path)
        info = result.get("info", [])
        all_text = " ".join(info)
        assert "GitHub Actions" in all_text

    def test_emit_helper_idempotent(self, tmp_path: Path) -> None:
        """Calling emit twice does not duplicate lines."""
        from trw_mcp.bootstrap._cursor_cli import _emit_cli_safety_reminder
        from trw_mcp.models.typed_dicts._bootstrap import BootstrapFileResult

        result: BootstrapFileResult = {"created": [], "updated": [], "preserved": []}
        _emit_cli_safety_reminder(result)
        count_after_first = len(result.get("info", []))
        _emit_cli_safety_reminder(result)
        count_after_second = len(result.get("info", []))
        assert count_after_second == count_after_first, (
            "Calling _emit_cli_safety_reminder twice should not grow the info list"
        )


# ===========================================================================
# Bash syntax checks on hook scripts (Task 17)
# ===========================================================================


class TestHookScriptSyntax:
    """Verify CLI hook scripts pass bash -n syntax check."""

    @pytest.mark.parametrize(
        "script_name",
        ["trw-after-mcp.sh", "trw-before-shell.sh", "trw-after-shell.sh"],
    )
    def test_bash_syntax(self, script_name: str) -> None:
        from pathlib import Path as _Path

        hooks_dir = (
            _Path(__file__).parent.parent
            / "src"
            / "trw_mcp"
            / "data"
            / "hooks"
            / "cursor"
        )
        script = hooks_dir / script_name
        assert script.is_file(), f"Hook script missing: {script}"
        result = subprocess.run(
            ["bash", "-n", str(script)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, (
            f"bash -n failed on {script_name}:\n{result.stderr}"
        )
