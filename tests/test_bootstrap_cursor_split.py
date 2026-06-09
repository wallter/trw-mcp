"""Split bootstrap Cursor IDE tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from trw_mcp.bootstrap import init_project, update_project

from ._bootstrap_test_support import patch_update_project_internals


@pytest.mark.integration
class TestCursorBootstrap:
    """FR05+FR06+FR07: Cursor IDE bootstrap — hooks, rules, mcp config."""

    def test_fr05_cursor_hooks_created(self, tmp_path: Path) -> None:
        """FR05: generate_cursor_hooks creates .cursor/hooks.json with TRW hooks."""
        from trw_mcp.bootstrap._cursor import generate_cursor_hooks

        result = generate_cursor_hooks(tmp_path)

        assert ".cursor/hooks.json" in result["created"]
        config = json.loads((tmp_path / ".cursor" / "hooks.json").read_text())
        assert len(config["hooks"]) == 4
        events = {h["event"] for h in config["hooks"]}
        assert "beforeMCPExecution" in events
        assert "beforeSubmitPrompt" in events
        assert "afterFileEdit" in events
        assert "stop" in events

    def test_fr05_cursor_hooks_all_have_trw_descriptions(self, tmp_path: Path) -> None:
        """FR05: All generated hooks have descriptions starting with 'TRW'."""
        from trw_mcp.bootstrap._cursor import generate_cursor_hooks

        generate_cursor_hooks(tmp_path)
        config = json.loads((tmp_path / ".cursor" / "hooks.json").read_text())
        for hook in config["hooks"]:
            assert hook["description"].startswith("TRW"), (
                f"Hook {hook['event']} description does not start with 'TRW': {hook['description']}"
            )

    def test_fr05_cursor_hooks_smart_merge_preserves_user_hooks(self, tmp_path: Path) -> None:
        """FR05: Smart merge preserves existing user hooks when file already exists."""
        from trw_mcp.bootstrap._cursor import generate_cursor_hooks

        cursor_dir = tmp_path / ".cursor"
        cursor_dir.mkdir()
        existing = {"hooks": [{"event": "custom", "command": "echo hi", "description": "User hook"}]}
        (cursor_dir / "hooks.json").write_text(json.dumps(existing))

        result = generate_cursor_hooks(tmp_path)

        assert ".cursor/hooks.json" in result["updated"]
        config = json.loads((tmp_path / ".cursor" / "hooks.json").read_text())
        # User hook preserved + 4 TRW hooks = 5 total
        assert len(config["hooks"]) == 5
        descriptions = [h["description"] for h in config["hooks"]]
        assert "User hook" in descriptions

    def test_fr05_cursor_hooks_smart_merge_replaces_trw_hooks(self, tmp_path: Path) -> None:
        """FR05: Smart merge replaces stale TRW hooks without duplicating them."""
        from trw_mcp.bootstrap._cursor import generate_cursor_hooks

        cursor_dir = tmp_path / ".cursor"
        cursor_dir.mkdir()
        existing = {
            "hooks": [
                {"event": "old", "command": "echo old", "description": "TRW old hook"},
                {"event": "custom", "command": "echo hi", "description": "User hook"},
            ]
        }
        (cursor_dir / "hooks.json").write_text(json.dumps(existing))

        generate_cursor_hooks(tmp_path)
        config = json.loads((tmp_path / ".cursor" / "hooks.json").read_text())
        # Old TRW hook removed, 4 new TRW hooks + user hook = 5
        assert len(config["hooks"]) == 5
        # Stale TRW hook gone
        old_events = [h["event"] for h in config["hooks"]]
        assert "old" not in old_events

    def test_fr05_cursor_hooks_force_overwrites(self, tmp_path: Path) -> None:
        """FR05: force=True overwrites existing hooks without merging."""
        from trw_mcp.bootstrap._cursor import generate_cursor_hooks

        cursor_dir = tmp_path / ".cursor"
        cursor_dir.mkdir()
        existing = {"hooks": [{"event": "custom", "command": "echo hi", "description": "User hook"}]}
        (cursor_dir / "hooks.json").write_text(json.dumps(existing))

        result = generate_cursor_hooks(tmp_path, force=True)

        assert ".cursor/hooks.json" in result["created"]
        config = json.loads((tmp_path / ".cursor" / "hooks.json").read_text())
        # Only TRW hooks — user hook not preserved
        assert len(config["hooks"]) == 4

    def test_fr05_cursor_hooks_malformed_json_fallback(self, tmp_path: Path) -> None:
        """FR05: Malformed existing JSON is gracefully overwritten."""
        from trw_mcp.bootstrap._cursor import generate_cursor_hooks

        cursor_dir = tmp_path / ".cursor"
        cursor_dir.mkdir()
        (cursor_dir / "hooks.json").write_text("not valid json {{")

        result = generate_cursor_hooks(tmp_path)

        assert ".cursor/hooks.json" in result["updated"]
        config = json.loads((tmp_path / ".cursor" / "hooks.json").read_text())
        assert len(config["hooks"]) == 4

    def test_fr06_cursor_rules_created(self, tmp_path: Path) -> None:
        """FR06: generate_cursor_rules creates .cursor/rules/trw-ceremony.mdc."""
        from trw_mcp.bootstrap._cursor import generate_cursor_rules

        result = generate_cursor_rules(tmp_path, "## TRW Protocol\nContent here")

        assert ".cursor/rules/trw-ceremony.mdc" in result["created"]
        rules_file = tmp_path / ".cursor" / "rules" / "trw-ceremony.mdc"
        assert rules_file.exists()
        content = rules_file.read_text()
        assert "alwaysApply: true" in content
        assert "TRW Protocol" in content
        assert "Content here" in content

    def test_fr06_cursor_rules_frontmatter_valid(self, tmp_path: Path) -> None:
        """FR06: Generated rules file has valid MDC frontmatter."""
        from trw_mcp.bootstrap._cursor import generate_cursor_rules

        generate_cursor_rules(tmp_path, "## TRW\nBody")
        content = (tmp_path / ".cursor" / "rules" / "trw-ceremony.mdc").read_text()
        assert content.startswith("---\n")
        assert "alwaysApply: true" in content
        assert "globs: []" in content
        assert "description:" in content

    def test_fr06_cursor_rules_under_500_lines(self, tmp_path: Path) -> None:
        """FR06: Generated rules file stays under 500 lines."""
        from trw_mcp.bootstrap._cursor import generate_cursor_rules

        generate_cursor_rules(tmp_path, "Short content")
        content = (tmp_path / ".cursor" / "rules" / "trw-ceremony.mdc").read_text()
        assert len(content.splitlines()) < 500

    def test_fr06_cursor_rules_update_on_existing(self, tmp_path: Path) -> None:
        """FR06: Calling generate_cursor_rules on an existing file reports 'updated'."""
        from trw_mcp.bootstrap._cursor import generate_cursor_rules

        generate_cursor_rules(tmp_path, "First content")
        result = generate_cursor_rules(tmp_path, "Updated content")

        assert ".cursor/rules/trw-ceremony.mdc" in result["updated"]
        content = (tmp_path / ".cursor" / "rules" / "trw-ceremony.mdc").read_text()
        assert "Updated content" in content

    def test_fr07_cursor_mcp_created(self, tmp_path: Path) -> None:
        """FR07: generate_cursor_mcp_config creates .cursor/mcp.json with TRW entry."""
        from trw_mcp.bootstrap._cursor import generate_cursor_mcp_config

        result = generate_cursor_mcp_config(tmp_path)

        assert ".cursor/mcp.json" in result["created"]
        mcp_file = tmp_path / ".cursor" / "mcp.json"
        assert mcp_file.exists()
        config = json.loads(mcp_file.read_text())
        assert "mcpServers" in config
        assert "trw" in config["mcpServers"]

    def test_fr07_cursor_mcp_entry_has_command(self, tmp_path: Path) -> None:
        """FR07: TRW MCP entry has a command field."""
        from trw_mcp.bootstrap._cursor import generate_cursor_mcp_config

        generate_cursor_mcp_config(tmp_path)
        config = json.loads((tmp_path / ".cursor" / "mcp.json").read_text())
        trw_entry = config["mcpServers"]["trw"]
        assert "command" in trw_entry

    def test_fr07_cursor_mcp_smart_merge_preserves_user_servers(self, tmp_path: Path) -> None:
        """FR07: Smart merge preserves existing MCP servers in .cursor/mcp.json."""
        from trw_mcp.bootstrap._cursor import generate_cursor_mcp_config

        cursor_dir = tmp_path / ".cursor"
        cursor_dir.mkdir()
        existing = {"mcpServers": {"other-server": {"command": "other-mcp"}}}
        (cursor_dir / "mcp.json").write_text(json.dumps(existing))

        result = generate_cursor_mcp_config(tmp_path)

        assert ".cursor/mcp.json" in result["updated"]
        config = json.loads((tmp_path / ".cursor" / "mcp.json").read_text())
        assert "other-server" in config["mcpServers"]
        assert "trw" in config["mcpServers"]

    def test_fr07_cursor_mcp_smart_merge_updates_trw_entry(self, tmp_path: Path) -> None:
        """FR07: Smart merge updates the trw entry even if it already exists."""
        from trw_mcp.bootstrap._cursor import generate_cursor_mcp_config

        cursor_dir = tmp_path / ".cursor"
        cursor_dir.mkdir()
        existing = {
            "mcpServers": {
                "trw": {"command": "old-command"},
                "other": {"command": "other-mcp"},
            }
        }
        (cursor_dir / "mcp.json").write_text(json.dumps(existing))

        generate_cursor_mcp_config(tmp_path)
        config = json.loads((tmp_path / ".cursor" / "mcp.json").read_text())
        # other server preserved
        assert "other" in config["mcpServers"]
        # trw entry refreshed
        assert config["mcpServers"]["trw"]["command"] != "old-command"

    def test_fr07_cursor_mcp_force_overwrites(self, tmp_path: Path) -> None:
        """FR07: force=True writes a fresh mcp.json without merging."""
        from trw_mcp.bootstrap._cursor import generate_cursor_mcp_config

        cursor_dir = tmp_path / ".cursor"
        cursor_dir.mkdir()
        existing = {"mcpServers": {"other": {"command": "other-mcp"}}}
        (cursor_dir / "mcp.json").write_text(json.dumps(existing))

        result = generate_cursor_mcp_config(tmp_path, force=True)

        assert ".cursor/mcp.json" in result["created"]
        config = json.loads((tmp_path / ".cursor" / "mcp.json").read_text())
        # Only TRW — user server removed
        assert "other" not in config["mcpServers"]
        assert "trw" in config["mcpServers"]

    def test_fr07_cursor_mcp_malformed_json_fallback(self, tmp_path: Path) -> None:
        """FR07: Malformed existing JSON is gracefully overwritten."""
        from trw_mcp.bootstrap._cursor import generate_cursor_mcp_config

        cursor_dir = tmp_path / ".cursor"
        cursor_dir.mkdir()
        (cursor_dir / "mcp.json").write_text("{{not valid json")

        result = generate_cursor_mcp_config(tmp_path)

        assert ".cursor/mcp.json" in result["updated"]
        config = json.loads((tmp_path / ".cursor" / "mcp.json").read_text())
        assert "trw" in config["mcpServers"]

    def test_fr05_fr06_fr07_cursor_dir_auto_created(self, tmp_path: Path) -> None:
        """FR05+FR06+FR07: .cursor/ directory and subdirs are created automatically."""
        import shutil as _shutil

        from trw_mcp.bootstrap._cursor import (
            generate_cursor_hooks,
            generate_cursor_mcp_config,
            generate_cursor_rules,
        )

        # FR05: .cursor/ created by generate_cursor_hooks
        assert not (tmp_path / ".cursor").exists()
        generate_cursor_hooks(tmp_path)
        assert (tmp_path / ".cursor").is_dir()
        assert (tmp_path / ".cursor" / "hooks.json").exists()

        # FR07: .cursor/ created (or reused) by generate_cursor_mcp_config
        _shutil.rmtree(tmp_path / ".cursor")
        assert not (tmp_path / ".cursor").exists()
        generate_cursor_mcp_config(tmp_path)
        assert (tmp_path / ".cursor").is_dir()
        assert (tmp_path / ".cursor" / "mcp.json").exists()

        # FR06: .cursor/rules/ subdir auto-created by generate_cursor_rules
        _shutil.rmtree(tmp_path / ".cursor")
        generate_cursor_rules(tmp_path, "content")
        assert (tmp_path / ".cursor" / "rules").is_dir()
        assert (tmp_path / ".cursor" / "rules" / "trw-ceremony.mdc").exists()

    def test_fr05_fr07_init_project_cursor_ide(self, tmp_path: Path) -> None:
        """FR05+FR07: init_project(ide='cursor-ide') creates .cursor/ artifacts."""
        (tmp_path / ".git").mkdir()

        result = init_project(tmp_path, ide="cursor-ide")

        assert not result["errors"], result["errors"]
        assert (tmp_path / ".cursor" / "hooks.json").exists()
        assert (tmp_path / ".cursor" / "mcp.json").exists()
        assert (tmp_path / ".cursor" / "rules" / "trw-ceremony.mdc").exists()

    def test_fr05_fr07_init_project_ide_all_includes_cursor(self, tmp_path: Path) -> None:
        """FR05+FR07: init_project(ide='all') creates Cursor artifacts alongside others."""
        (tmp_path / ".git").mkdir()

        result = init_project(tmp_path, ide="all")

        assert not result["errors"], result["errors"]
        # Cursor artifacts
        assert (tmp_path / ".cursor" / "hooks.json").exists()
        assert (tmp_path / ".cursor" / "mcp.json").exists()
        # Claude Code artifacts still present
        assert (tmp_path / ".claude").is_dir()
        assert (tmp_path / "CLAUDE.md").exists()

    def test_fr05_fr07_update_project_cursor_ide(self, tmp_path: Path) -> None:
        """FR05+FR07: update_project with cursor detected updates .cursor/ artifacts."""
        (tmp_path / ".git").mkdir()  # update_project now requires a real git repo
        (tmp_path / ".trw").mkdir()
        (tmp_path / ".trw" / "config.yaml").write_text("task_root: docs\n")
        (tmp_path / ".cursor").mkdir()  # Presence triggers cursor detection

        with patch_update_project_internals():
            result = update_project(tmp_path)

        assert (tmp_path / ".cursor" / "hooks.json").exists()
        assert (tmp_path / ".cursor" / "mcp.json").exists()
        assert (tmp_path / ".cursor" / "rules" / "trw-ceremony.mdc").exists()
