"""Unit tests for cursor-cli bootstrap generators (PRD-CORE-137).

Covers Tasks 14-17b:
  Task 14: generate_cursor_cli_config — cli.json permissions
  Task 15: generate_cursor_cli_agents_md — AGENTS.md sentinel block
  Task 16: generate_cursor_cli_hooks — 5-event CLI subset
  Task 17b: _emit_cli_safety_reminder — TTY/tmux advisory

Additional hardening (Sprint 91):
  - TypedDict / Final type assertions
  - Parameterized smart-merge cases (PRD spec §FR03 acceptance: 5 cases)
  - Empty-file malformed JSON fallback
  - Non-object root fallback (JSON array instead of object)
  - _merge_agents_md pure-function unit tests
  - structlog capture for cursor_cli_tty_reminder event
  - Hook token presence in correct allow/deny list (not just key existence)
  - Bash stdin/stdout hook functional tests
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
import structlog.testing


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

    def test_empty_file_overwrites(self, tmp_path: Path) -> None:
        """Empty file (valid empty string, invalid JSON) triggers overwrite."""
        from trw_mcp.bootstrap._cursor_cli import generate_cursor_cli_config

        cursor_dir = tmp_path / ".cursor"
        cursor_dir.mkdir()
        (cursor_dir / "cli.json").write_text("")

        result = generate_cursor_cli_config(tmp_path)
        assert ".cursor/cli.json" in result["updated"]
        merged = _read_cli_json(tmp_path)
        assert "permissions" in merged

    def test_non_object_root_overwrites(self, tmp_path: Path) -> None:
        """A JSON array (not object) at root triggers overwrite with defaults."""
        from trw_mcp.bootstrap._cursor_cli import generate_cursor_cli_config

        cursor_dir = tmp_path / ".cursor"
        cursor_dir.mkdir()
        (cursor_dir / "cli.json").write_text("[1, 2, 3]")

        result = generate_cursor_cli_config(tmp_path)
        assert ".cursor/cli.json" in result["updated"]
        merged = _read_cli_json(tmp_path)
        assert "permissions" in merged

    def test_malformed_emits_warning_in_info(self, tmp_path: Path) -> None:
        """Malformed JSON overwrite appends a warning to result['info']."""
        from trw_mcp.bootstrap._cursor_cli import generate_cursor_cli_config

        cursor_dir = tmp_path / ".cursor"
        cursor_dir.mkdir()
        (cursor_dir / "cli.json").write_text("not_json")

        result = generate_cursor_cli_config(tmp_path)
        info = result.get("info", [])
        all_info = " ".join(info)
        assert "malformed" in all_info.lower() or "WARNING" in all_info

    def test_permissions_not_a_dict_overwrites(self, tmp_path: Path) -> None:
        """permissions key exists but is a non-dict (e.g. a string) → overwrite."""
        from trw_mcp.bootstrap._cursor_cli import generate_cursor_cli_config

        cursor_dir = tmp_path / ".cursor"
        cursor_dir.mkdir()
        (cursor_dir / "cli.json").write_text(json.dumps({"permissions": "should-be-dict"}))

        result = generate_cursor_cli_config(tmp_path)
        assert ".cursor/cli.json" in result["updated"]
        merged = _read_cli_json(tmp_path)
        assert isinstance(merged["permissions"], dict)


class TestCliConfigSmartMergeParameterized:
    """PRD-CORE-137-FR03 acceptance: 5 parameterized smart-merge cases.

    Case 1: Fresh-write (covered by TestCliConfigFresh)
    Case 2: User allow + user deny tokens preserved alongside TRW defaults
    Case 3: User Shell(rm) allow with TRW Shell(rm -rf) deny — both kept on own path
    Case 4: Extra top-level JSON keys preserved (covered by TestCliConfigSmartMerge)
    Case 5: Malformed JSON → overwrite (covered by TestCliConfigMalformed)
    """

    def test_case2_user_tokens_preserved_alongside_trw_defaults(self, tmp_path: Path) -> None:
        """Case 2: User Shell(my-custom-tool) allow + Write(secret.key) deny survive."""
        from trw_mcp.bootstrap._cursor_cli import generate_cursor_cli_config

        cursor_dir = tmp_path / ".cursor"
        cursor_dir.mkdir()
        user_config = {
            "permissions": {
                "allow": ["Shell(my-custom-tool)"],
                "deny": ["Write(secret.key)"],
            }
        }
        (cursor_dir / "cli.json").write_text(json.dumps(user_config))

        generate_cursor_cli_config(tmp_path)
        merged = _read_cli_json(tmp_path)

        # User tokens
        assert "Shell(my-custom-tool)" in merged["permissions"]["allow"]
        assert "Write(secret.key)" in merged["permissions"]["deny"]
        # TRW defaults also present
        assert "Shell(git)" in merged["permissions"]["allow"]
        assert "Read(.env*)" in merged["permissions"]["deny"]

    def test_case3_user_shell_rm_allow_coexists_with_trw_shell_rm_rf_deny(
        self, tmp_path: Path
    ) -> None:
        """Case 3: User's Shell(rm) allow + TRW's Shell(rm -rf) deny both kept.

        These are semantically different tokens; user allow wins at runtime per
        Cursor token grammar (exact match, not prefix). TRW deny is preserved on
        its own path (Shell(rm -rf)), so both coexist without collision.
        """
        from trw_mcp.bootstrap._cursor_cli import generate_cursor_cli_config

        cursor_dir = tmp_path / ".cursor"
        cursor_dir.mkdir()
        # User explicitly allows 'Shell(rm)' — shorter path than TRW's 'Shell(rm -rf)' deny
        user_config = {
            "permissions": {
                "allow": ["Shell(rm)"],
                "deny": [],
            }
        }
        (cursor_dir / "cli.json").write_text(json.dumps(user_config))

        generate_cursor_cli_config(tmp_path)
        merged = _read_cli_json(tmp_path)

        # User's Shell(rm) stays in allow
        assert "Shell(rm)" in merged["permissions"]["allow"]
        # TRW's Shell(rm -rf) should appear in deny (different token — not a duplicate)
        assert "Shell(rm -rf)" in merged["permissions"]["deny"]


class TestCliConfigTokenPlacement:
    """Verify tokens appear in the correct allow/deny list (not just key existence)."""

    @pytest.mark.parametrize(
        "token",
        [
            "Read(**/*)",
            "Shell(git)",
            "Shell(grep)",
            "Shell(find)",
            "Shell(rg)",
            "Shell(ls)",
            "Shell(cat)",
            "Shell(pytest)",
            "Shell(npm)",
            "Shell(python)",
            "Shell(trw-mcp)",
        ],
    )
    def test_each_allow_token_in_allow_list(self, tmp_path: Path, token: str) -> None:
        """Each _DEFAULT_ALLOW token appears in permissions.allow, not permissions.deny."""
        from trw_mcp.bootstrap._cursor_cli import generate_cursor_cli_config

        generate_cursor_cli_config(tmp_path)
        config = _read_cli_json(tmp_path)
        assert token in config["permissions"]["allow"], f"Missing in allow: {token}"
        assert token not in config["permissions"]["deny"], f"Incorrectly in deny: {token}"

    @pytest.mark.parametrize(
        "token",
        [
            "Shell(rm -rf)",
            "Shell(curl)",
            "Shell(wget)",
            "Read(.env*)",
            "Read(**/.env.local)",
            "Read(**/secrets.yaml)",
            "Write(.env*)",
            "Write(.git/**/*)",
            "Write(.trw/**/*)",
            "Write(node_modules/**/*)",
        ],
    )
    def test_each_deny_token_in_deny_list(self, tmp_path: Path, token: str) -> None:
        """Each _DEFAULT_DENY token appears in permissions.deny, not permissions.allow."""
        from trw_mcp.bootstrap._cursor_cli import generate_cursor_cli_config

        generate_cursor_cli_config(tmp_path)
        config = _read_cli_json(tmp_path)
        assert token in config["permissions"]["deny"], f"Missing in deny: {token}"
        assert token not in config["permissions"]["allow"], f"Incorrectly in allow: {token}"


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


class TestAgentsMdCursorCliContentGating:
    """P1-A audit fix: cursor-cli AGENTS.md must not contain claude-code-only
    surfaces (Agent Teams, subagent delegation content, FRAMEWORK.md framework-ref
    sections). The cursor-cli profile has include_framework_ref=False and
    include_agent_teams=False; the rendered AGENTS.md must respect those gates.
    """

    def test_cursor_cli_agents_md_omits_agent_teams_content(self, tmp_path: Path) -> None:
        """cursor-cli dispatcher output must not contain Agent Teams language."""
        from trw_mcp.bootstrap._ide_targets import _update_cursor_cli_artifacts

        (tmp_path / ".cursor").mkdir()
        result: dict[str, list[str]] = {"created": [], "updated": [], "preserved": []}
        _update_cursor_cli_artifacts(tmp_path, result)

        agents_md = (tmp_path / "AGENTS.md").read_text()

        # Explicit negative assertions — these are Claude-Code-specific surfaces
        # the cursor-cli profile deliberately excludes.
        assert "TeamCreate" not in agents_md, (
            "AGENTS.md must not mention TeamCreate — cursor-cli disables Agent Teams"
        )
        assert "Agent Teams" not in agents_md, (
            "AGENTS.md must not describe Agent Teams — cursor-cli has include_agent_teams=False"
        )
        assert "SendMessage" not in agents_md, (
            "AGENTS.md must not reference SendMessage (Agent Teams dispatch)"
        )
        assert "FRAMEWORK.md" not in agents_md, (
            "AGENTS.md must not reference FRAMEWORK.md — cursor-cli has include_framework_ref=False"
        )

    def test_cursor_cli_agents_md_contains_expected_surface(self, tmp_path: Path) -> None:
        """cursor-cli AGENTS.md DOES contain TRW MCP tool guidance + ceremony workflow."""
        from trw_mcp.bootstrap._ide_targets import _update_cursor_cli_artifacts

        (tmp_path / ".cursor").mkdir()
        result: dict[str, list[str]] = {"created": [], "updated": [], "preserved": []}
        _update_cursor_cli_artifacts(tmp_path, result)

        agents_md = (tmp_path / "AGENTS.md").read_text()

        # Positive assertions — these are the platform-generic surfaces cursor-cli needs.
        assert "trw_session_start" in agents_md
        assert "trw_deliver" in agents_md
        assert "<!-- TRW:BEGIN -->" in agents_md
        assert "<!-- TRW:END -->" in agents_md


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


# ===========================================================================
# Bash functional tests for hook scripts (stdin → stdout JSON contract)
# ===========================================================================


_HOOKS_DATA_DIR = (
    Path(__file__).parent.parent / "src" / "trw_mcp" / "data" / "hooks" / "cursor"
)


def _run_hook(script_name: str, stdin_payload: str) -> subprocess.CompletedProcess[str]:
    """Run a cursor hook script with a JSON stdin payload and return result."""
    script = _HOOKS_DATA_DIR / script_name
    return subprocess.run(
        ["bash", str(script)],
        input=stdin_payload,
        capture_output=True,
        text=True,
    )


class TestHookScriptFunctional:
    """Functional tests: hook scripts emit valid JSON on stdout for representative inputs."""

    def test_after_mcp_emits_valid_json(self) -> None:
        """trw-after-mcp.sh emits valid JSON on stdout for a normal MCP payload."""
        payload = json.dumps({"tool_name": "trw_session_start", "result": "ok"})
        result = _run_hook("trw-after-mcp.sh", payload)
        assert result.returncode == 0, f"Script exited non-zero:\n{result.stderr}"
        # stdout must be valid JSON
        output = json.loads(result.stdout.strip())
        assert isinstance(output, dict)

    def test_after_shell_emits_valid_json(self) -> None:
        """trw-after-shell.sh emits valid JSON on stdout for a normal shell payload."""
        payload = json.dumps({"command": "git status", "exit_code": 0, "duration_ms": 42})
        result = _run_hook("trw-after-shell.sh", payload)
        assert result.returncode == 0, f"Script exited non-zero:\n{result.stderr}"
        output = json.loads(result.stdout.strip())
        assert isinstance(output, dict)

    def test_before_shell_allows_safe_command(self) -> None:
        """trw-before-shell.sh emits permission=allow for a safe command."""
        payload = json.dumps({"command": "git status"})
        result = _run_hook("trw-before-shell.sh", payload)
        assert result.returncode == 0, f"Script exited non-zero:\n{result.stderr}"
        output = json.loads(result.stdout.strip())
        assert output.get("permission") == "allow"

    def test_before_shell_denies_secret_leak(self) -> None:
        """trw-before-shell.sh emits permission=deny when a secret token is found."""
        payload = json.dumps({"command": "echo API_KEY=sk-secret123"})
        result = _run_hook("trw-before-shell.sh", payload)
        assert result.returncode == 0, f"Script exited non-zero:\n{result.stderr}"
        output = json.loads(result.stdout.strip())
        assert output.get("permission") == "deny"
        assert "user_message" in output

    def test_before_shell_allows_env_var_reference(self) -> None:
        """trw-before-shell.sh does NOT deny $API_KEY variable references (not a leak)."""
        payload = json.dumps({"command": "curl -H $API_KEY https://example.com"})
        result = _run_hook("trw-before-shell.sh", payload)
        assert result.returncode == 0, f"Script exited non-zero:\n{result.stderr}"
        output = json.loads(result.stdout.strip())
        # $API_KEY is a reference, not an assignment — should be allowed
        assert output.get("permission") == "allow", (
            "Variable reference $API_KEY should not trigger deny (no value after =)"
        )

    def test_before_shell_allows_empty_command(self) -> None:
        """trw-before-shell.sh allows empty/missing command field without crashing."""
        payload = json.dumps({})
        result = _run_hook("trw-before-shell.sh", payload)
        assert result.returncode == 0, f"Script exited non-zero:\n{result.stderr}"
        output = json.loads(result.stdout.strip())
        assert output.get("permission") == "allow"


# ===========================================================================
# _merge_agents_md pure function unit tests
# ===========================================================================


class TestMergeAgentsMdPureFunction:
    """Unit tests for the _merge_agents_md pure helper."""

    def test_replaces_content_between_sentinels(self) -> None:
        from trw_mcp.bootstrap._cursor_cli import _merge_agents_md

        existing = "Before\n<!-- TRW:BEGIN -->\nOld content\n<!-- TRW:END -->\nAfter\n"
        trw_block = "<!-- TRW:BEGIN -->\nNew content\n<!-- TRW:END -->"
        result = _merge_agents_md(existing, trw_block, "<!-- TRW:BEGIN -->", "<!-- TRW:END -->")
        assert "New content" in result
        assert "Old content" not in result
        assert "Before\n" in result
        assert "After\n" in result

    def test_prepends_when_no_sentinels(self) -> None:
        from trw_mcp.bootstrap._cursor_cli import _merge_agents_md

        existing = "# Existing rules\nDo something.\n"
        trw_block = "<!-- TRW:BEGIN -->\nTRW stuff\n<!-- TRW:END -->"
        result = _merge_agents_md(existing, trw_block, "<!-- TRW:BEGIN -->", "<!-- TRW:END -->")
        begin_idx = result.index("<!-- TRW:BEGIN -->")
        existing_idx = result.index("# Existing rules")
        assert begin_idx < existing_idx
        assert "Do something." in result

    def test_preserves_content_outside_sentinels(self) -> None:
        from trw_mcp.bootstrap._cursor_cli import _merge_agents_md

        pre = "# My rules\n"
        post = "\n## Custom\nDo not break.\n"
        existing = pre + "<!-- TRW:BEGIN -->\nOld\n<!-- TRW:END -->" + post
        trw_block = "<!-- TRW:BEGIN -->\nNew\n<!-- TRW:END -->"
        result = _merge_agents_md(existing, trw_block, "<!-- TRW:BEGIN -->", "<!-- TRW:END -->")
        assert "# My rules" in result
        assert "Do not break." in result
        assert "New" in result
        assert "Old" not in result

    def test_empty_existing_prepends(self) -> None:
        from trw_mcp.bootstrap._cursor_cli import _merge_agents_md

        trw_block = "<!-- TRW:BEGIN -->\nContent\n<!-- TRW:END -->"
        result = _merge_agents_md("", trw_block, "<!-- TRW:BEGIN -->", "<!-- TRW:END -->")
        assert "<!-- TRW:BEGIN -->" in result
        assert "Content" in result


# ===========================================================================
# Structlog capture test for cursor_cli_tty_reminder event (FR08a)
# ===========================================================================


class TestTtyReminderStructlog:
    """Verify _emit_cli_safety_reminder emits cursor_cli_tty_reminder via structlog."""

    def test_structlog_event_emitted(self, tmp_path: Path) -> None:
        """cursor_cli_tty_reminder event captured by structlog.testing.capture_logs."""
        from trw_mcp.bootstrap._cursor_cli import _emit_cli_safety_reminder
        from trw_mcp.models.typed_dicts._bootstrap import BootstrapFileResult

        result: BootstrapFileResult = {"created": [], "updated": [], "preserved": []}
        with structlog.testing.capture_logs() as cap:
            _emit_cli_safety_reminder(result)

        events = [e["event"] for e in cap]
        assert "cursor_cli_tty_reminder" in events, (
            f"Expected cursor_cli_tty_reminder in structlog output; got: {events}"
        )

    def test_structlog_event_has_tty_required(self, tmp_path: Path) -> None:
        """cursor_cli_tty_reminder log entry includes tty_required=True."""
        from trw_mcp.bootstrap._cursor_cli import _emit_cli_safety_reminder
        from trw_mcp.models.typed_dicts._bootstrap import BootstrapFileResult

        result: BootstrapFileResult = {"created": [], "updated": [], "preserved": []}
        with structlog.testing.capture_logs() as cap:
            _emit_cli_safety_reminder(result)

        reminder_events = [e for e in cap if e.get("event") == "cursor_cli_tty_reminder"]
        assert len(reminder_events) >= 1
        assert reminder_events[0].get("tty_required") is True

    def test_generate_cli_config_emits_tty_reminder_via_structlog(
        self, tmp_path: Path
    ) -> None:
        """generate_cursor_cli_config call emits cursor_cli_tty_reminder via structlog."""
        from trw_mcp.bootstrap._cursor_cli import generate_cursor_cli_config

        with structlog.testing.capture_logs() as cap:
            generate_cursor_cli_config(tmp_path)

        events = [e["event"] for e in cap]
        assert "cursor_cli_tty_reminder" in events


# ===========================================================================
# TypedDict and Final type surface checks (static)
# ===========================================================================


class TestTypeAnnotations:
    """Verify that typed constants and TypedDicts are importable and correctly shaped."""

    def test_default_allow_is_tuple(self) -> None:
        from trw_mcp.bootstrap._cursor_cli import _DEFAULT_ALLOW

        assert isinstance(_DEFAULT_ALLOW, tuple)

    def test_default_deny_is_tuple(self) -> None:
        from trw_mcp.bootstrap._cursor_cli import _DEFAULT_DENY

        assert isinstance(_DEFAULT_DENY, tuple)

    def test_cli_hook_scripts_is_tuple(self) -> None:
        from trw_mcp.bootstrap._cursor_cli import _CLI_HOOK_SCRIPTS

        assert isinstance(_CLI_HOOK_SCRIPTS, tuple)

    def test_tty_reminder_lines_is_tuple(self) -> None:
        from trw_mcp.bootstrap._cursor_cli import _TTY_REMINDER_LINES

        assert isinstance(_TTY_REMINDER_LINES, tuple)

    def test_cli_hook_events_has_five_keys(self) -> None:
        from trw_mcp.bootstrap._cursor_cli import _CLI_HOOK_EVENTS

        assert len(_CLI_HOOK_EVENTS) == 5

    def test_cursor_cli_permissions_typeddict_importable(self) -> None:
        from trw_mcp.bootstrap._cursor_cli import CursorCliPermissions  # noqa: F401

    def test_cursor_cli_config_typeddict_importable(self) -> None:
        from trw_mcp.bootstrap._cursor_cli import CursorCliConfig  # noqa: F401


# ===========================================================================
# CLI detection: cursor-cli does NOT false-positive on .cursor/rules/ only
# ===========================================================================


class TestCursorCliDetectionNegative:
    """cursor-cli detection must not trigger from IDE-only signals."""

    def test_cursor_rules_dir_alone_not_cursor_cli(self, tmp_path: Path) -> None:
        """Presence of .cursor/rules/ (IDE artifact) must NOT trigger cursor-cli."""
        from unittest.mock import patch

        from trw_mcp.bootstrap._utils import detect_ide

        cursor_dir = tmp_path / ".cursor"
        rules_dir = cursor_dir / "rules"
        rules_dir.mkdir(parents=True)
        (rules_dir / "trw-ceremony.mdc").write_text("# rules")

        # No cli.json, no cursor-agent binary, no CURSOR_API_KEY env
        with patch("shutil.which", return_value=None), patch.dict(
            "os.environ", {}, clear=True
        ):
            result = detect_ide(tmp_path)

        # cursor-ide may be detected (has .cursor dir), cursor-cli must NOT be
        assert "cursor-cli" not in result, (
            ".cursor/rules/ alone should not trigger cursor-cli detection"
        )
