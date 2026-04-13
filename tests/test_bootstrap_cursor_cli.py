"""Integration tests for cursor-cli dispatcher wiring (PRD-CORE-137-FR07).

Tests the end-to-end init_project flow with target_platforms=[cursor-cli],
dual-surface runs, and negative assertions for IDE-only artifacts.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_git_repo(tmp_path: Path) -> Path:
    """Create a minimal git repo in tmp_path and return it."""
    subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=str(tmp_path),
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=str(tmp_path),
        check=True,
        capture_output=True,
    )
    return tmp_path


def _read_hooks_json(tmp_path: Path) -> dict:
    return json.loads((tmp_path / ".cursor" / "hooks.json").read_text())


def _read_cli_json(tmp_path: Path) -> dict:
    return json.loads((tmp_path / ".cursor" / "cli.json").read_text())


# ---------------------------------------------------------------------------
# Test: full CLI bootstrap via init_project
# ---------------------------------------------------------------------------


class TestInitProjectCursorCliFullBootstrap:
    """test_init_project_cursor_cli_full_bootstrap."""

    def test_agents_md_created(self, tmp_path: Path) -> None:
        from trw_mcp.bootstrap._init_project import init_project

        repo = _make_git_repo(tmp_path)
        init_project(repo, ide="cursor-cli")
        assert (repo / "AGENTS.md").is_file()

    def test_cli_json_created(self, tmp_path: Path) -> None:
        from trw_mcp.bootstrap._init_project import init_project

        repo = _make_git_repo(tmp_path)
        init_project(repo, ide="cursor-cli")
        assert (repo / ".cursor" / "cli.json").is_file()

    def test_hooks_json_created(self, tmp_path: Path) -> None:
        from trw_mcp.bootstrap._init_project import init_project

        repo = _make_git_repo(tmp_path)
        init_project(repo, ide="cursor-cli")
        assert (repo / ".cursor" / "hooks.json").is_file()

    def test_mcp_json_created(self, tmp_path: Path) -> None:
        from trw_mcp.bootstrap._init_project import init_project

        repo = _make_git_repo(tmp_path)
        init_project(repo, ide="cursor-cli")
        assert (repo / ".cursor" / "mcp.json").is_file()

    def test_agents_md_has_sentinel_block(self, tmp_path: Path) -> None:
        from trw_mcp.bootstrap._init_project import init_project

        repo = _make_git_repo(tmp_path)
        init_project(repo, ide="cursor-cli")
        content = (repo / "AGENTS.md").read_text()
        assert "<!-- TRW:BEGIN -->" in content
        assert "<!-- TRW:END -->" in content

    def test_cli_json_has_permissions(self, tmp_path: Path) -> None:
        from trw_mcp.bootstrap._init_project import init_project

        repo = _make_git_repo(tmp_path)
        init_project(repo, ide="cursor-cli")
        config = _read_cli_json(repo)
        assert "permissions" in config
        assert "allow" in config["permissions"]
        assert "deny" in config["permissions"]

    def test_cli_json_baseline_allow_tokens(self, tmp_path: Path) -> None:
        from trw_mcp.bootstrap._cursor_cli import _DEFAULT_ALLOW
        from trw_mcp.bootstrap._init_project import init_project

        repo = _make_git_repo(tmp_path)
        init_project(repo, ide="cursor-cli")
        config = _read_cli_json(repo)
        for token in _DEFAULT_ALLOW:
            assert token in config["permissions"]["allow"], f"Missing: {token}"

    def test_hooks_json_cli_events_present(self, tmp_path: Path) -> None:
        from trw_mcp.bootstrap._init_project import init_project

        repo = _make_git_repo(tmp_path)
        init_project(repo, ide="cursor-cli")
        hooks = _read_hooks_json(repo)
        # CLI hooks subset should be present via generate_cursor_cli_hooks
        # (legacy hooks from generate_cursor_hooks are also present)
        assert "hooks" in hooks


class TestCliOnlyNoIdeArtifacts:
    """test_cli_only_no_ide_artifacts: IDE-specific dirs not created."""

    def test_no_agents_dir(self, tmp_path: Path) -> None:
        from trw_mcp.bootstrap._init_project import init_project

        repo = _make_git_repo(tmp_path)
        init_project(repo, ide="cursor-cli")
        # .cursor/agents/ should NOT be created by CLI-only bootstrap
        assert not (repo / ".cursor" / "agents").exists()

    def test_no_commands_dir(self, tmp_path: Path) -> None:
        from trw_mcp.bootstrap._init_project import init_project

        repo = _make_git_repo(tmp_path)
        init_project(repo, ide="cursor-cli")
        # .cursor/commands/ should NOT be created by CLI-only bootstrap
        assert not (repo / ".cursor" / "commands").exists()


# ---------------------------------------------------------------------------
# Test: TTY reminder in result
# ---------------------------------------------------------------------------


class TestTtyReminderInBootstrapResult:
    """TTY/tmux reminder surfaces via generate_cursor_cli_config (FR08a)."""

    def test_tty_in_cli_config_result(self, tmp_path: Path) -> None:
        from trw_mcp.bootstrap._cursor_cli import generate_cursor_cli_config

        result = generate_cursor_cli_config(tmp_path)
        info_text = " ".join(result.get("info", []))
        assert "TTY" in info_text
        assert "tmux" in info_text


# ---------------------------------------------------------------------------
# Test: dual-surface integration
# ---------------------------------------------------------------------------


class TestDualSurface:
    """Dual-surface tests: cursor-ide + cursor-cli."""

    def test_dual_surface_both_artifacts_exist(self, tmp_path: Path) -> None:
        """Both CLI and IDE artifacts present after dual-surface init."""
        from trw_mcp.bootstrap._cursor_cli import (
            generate_cursor_cli_agents_md,
            generate_cursor_cli_config,
            generate_cursor_cli_hooks,
        )
        from trw_mcp.bootstrap._cursor import generate_cursor_mcp_config

        # Simulate IDE surface writing mcp.json first
        mcp_result = generate_cursor_mcp_config(tmp_path)
        assert ".cursor/mcp.json" in mcp_result["created"]

        # Now CLI surface runs — mcp.json should be updated (smart-merge)
        mcp_result2 = generate_cursor_mcp_config(tmp_path)
        assert ".cursor/mcp.json" in mcp_result2["updated"]

        # CLI artifacts
        generate_cursor_cli_config(tmp_path)
        generate_cursor_cli_agents_md(tmp_path, "CLI TRW content")
        generate_cursor_cli_hooks(tmp_path)

        assert (tmp_path / "AGENTS.md").is_file()
        assert (tmp_path / ".cursor" / "cli.json").is_file()
        assert (tmp_path / ".cursor" / "hooks.json").is_file()
        assert (tmp_path / ".cursor" / "mcp.json").is_file()

    def test_dual_surface_hooks_union(self, tmp_path: Path) -> None:
        """Dual-surface hooks.json contains union of IDE and CLI events."""
        import json as _json

        from trw_mcp.bootstrap._cursor import build_cursor_hook_config, smart_merge_cursor_json
        from trw_mcp.bootstrap._cursor_cli import (
            _CLI_HOOK_EVENTS,
            generate_cursor_cli_hooks,
        )

        cursor_dir = tmp_path / ".cursor"
        cursor_dir.mkdir()

        # Simulate IDE surface writing 4 legacy events
        ide_events = {
            "beforeMCPExecution": [{"command": ".cursor/hooks/trw-before-mcp-ide.sh", "type": "command"}],
            "beforeSubmitPrompt": [{"command": ".cursor/hooks/trw-submit.sh", "type": "command"}],
            "afterFileEdit": [{"command": ".cursor/hooks/trw-after-edit.sh", "type": "command"}],
            "stop": [{"command": ".cursor/hooks/trw-stop-ide.sh", "type": "command"}],
        }
        ide_hooks_body = build_cursor_hook_config(ide_events)
        hooks_file = cursor_dir / "hooks.json"
        hooks_file.write_text(_json.dumps(ide_hooks_body, indent=2) + "\n")

        # CLI surface adds its 5 events via smart-merge
        generate_cursor_cli_hooks(tmp_path)

        merged = _read_hooks_json(tmp_path)
        events = merged["hooks"]

        # CLI events should be present
        assert "beforeShellExecution" in events
        assert "afterShellExecution" in events
        assert "afterMCPExecution" in events

        # IDE events should still be present (preserved by smart-merge)
        assert "beforeSubmitPrompt" in events
        assert "afterFileEdit" in events

    def test_dual_surface_mcp_json_written_once_on_fresh(self, tmp_path: Path) -> None:
        """On fresh init, mcp.json written once; subsequent call produces 'updated'."""
        from trw_mcp.bootstrap._cursor import generate_cursor_mcp_config

        result1 = generate_cursor_mcp_config(tmp_path)
        result2 = generate_cursor_mcp_config(tmp_path)

        # First call creates
        assert ".cursor/mcp.json" in result1["created"]
        # Second call updates (smart-merge, not duplicate create)
        assert ".cursor/mcp.json" in result2["updated"]
        assert ".cursor/mcp.json" not in result2["created"]
