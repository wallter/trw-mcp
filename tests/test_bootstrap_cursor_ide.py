"""Integration tests for the cursor-ide full bootstrap (Task 13).

Tests the end-to-end artifact production when target_platforms=["cursor-ide"]
is configured via init_project / _update_cursor_artifacts.
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
    """Initialize a minimal git repo so init_project succeeds."""
    subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "commit", "--allow-empty", "-m", "init"],
        check=True,
        capture_output=True,
        env={"GIT_AUTHOR_NAME": "test", "GIT_AUTHOR_EMAIL": "t@t.com",
             "GIT_COMMITTER_NAME": "test", "GIT_COMMITTER_EMAIL": "t@t.com",
             "HOME": str(tmp_path)},
    )
    return tmp_path


# ---------------------------------------------------------------------------
# Task 13 integration: full IDE bootstrap via _update_cursor_artifacts
# ---------------------------------------------------------------------------


class TestUpdateCursorArtifactsCursorIde:
    """Tests for _update_cursor_artifacts with cursor-ide target."""

    def _call_update(self, tmp_path: Path, ide_override: str = "cursor-ide") -> dict:
        from trw_mcp.bootstrap._ide_targets import _update_cursor_artifacts

        result: dict = {"created": [], "updated": [], "preserved": [], "errors": []}
        _update_cursor_artifacts(tmp_path, result, ide_override=ide_override)
        return result

    def test_init_project_cursor_ide_full_bootstrap(self, tmp_path: Path) -> None:
        """End-to-end: cursor-ide target produces all expected IDE artifacts."""
        from trw_mcp.bootstrap._init_project import init_project

        repo = _make_git_repo(tmp_path)
        result = init_project(repo, ide="cursor-ide")

        # Core cursor IDE artifacts
        assert (repo / ".cursor" / "mcp.json").is_file(), ".cursor/mcp.json missing"
        assert (repo / ".cursor" / "rules" / "trw-ceremony.mdc").is_file(), "rules MDC missing"
        assert (repo / ".cursor" / "hooks.json").is_file(), "hooks.json missing"

        # Subagents
        assert (repo / ".cursor" / "agents" / "trw-explorer.md").is_file()
        assert (repo / ".cursor" / "agents" / "trw-implementer.md").is_file()
        assert (repo / ".cursor" / "agents" / "trw-reviewer.md").is_file()
        assert (repo / ".cursor" / "agents" / "trw-researcher.md").is_file()

        # Commands
        assert (repo / ".cursor" / "commands" / "trw-deliver.md").is_file()
        assert (repo / ".cursor" / "commands" / "trw-audit.md").is_file()

        # Skills (at least one curated skill present in bundled data)
        skills_dir = repo / ".cursor" / "skills"
        assert skills_dir.is_dir()
        skill_dirs = list(skills_dir.iterdir())
        assert len(skill_dirs) > 0, "No skills were mirrored"

        # At least 20 cursor files total
        cursor_files = list((repo / ".cursor").rglob("*"))
        cursor_file_count = sum(1 for f in cursor_files if f.is_file())
        assert cursor_file_count >= 20, (
            f"Expected >= 20 cursor files, got {cursor_file_count}"
        )

    def test_ide_idempotent_no_drift(self, tmp_path: Path) -> None:
        """Running twice: second run reports updated-only, not created."""
        result1 = self._call_update(tmp_path)
        created_count_1 = len(result1["created"])
        assert created_count_1 > 0, "First run should create files"

        result2 = self._call_update(tmp_path)
        # Second run: no new creates, some updates
        assert len(result2["created"]) == 0, (
            f"Second run unexpectedly created: {result2['created']}"
        )
        assert len(result2["updated"]) > 0 or len(result2["preserved"]) > 0

    def test_ide_only_when_not_cli(self, tmp_path: Path) -> None:
        """When target=cursor-ide only, .cursor/cli.json is NOT created."""
        self._call_update(tmp_path, ide_override="cursor-ide")

        cli_json = tmp_path / ".cursor" / "cli.json"
        assert not cli_json.exists(), ".cursor/cli.json should not exist for cursor-ide"

    def test_rules_mdc_ide_only(self, tmp_path: Path) -> None:
        """target_platforms=[cursor-ide] writes .cursor/rules/trw-ceremony.mdc."""
        self._call_update(tmp_path, ide_override="cursor-ide")

        mdc = tmp_path / ".cursor" / "rules" / "trw-ceremony.mdc"
        assert mdc.is_file(), "rules MDC not written for cursor-ide target"

    def test_rules_mdc_not_written_for_cli_only(self, tmp_path: Path) -> None:
        """When only cursor-cli is active, rules MDC is NOT written."""
        from trw_mcp.bootstrap._ide_targets import _update_cursor_artifacts

        result: dict = {"created": [], "updated": [], "preserved": [], "errors": []}
        _update_cursor_artifacts(tmp_path, result, ide_override="cursor-cli")

        mdc = tmp_path / ".cursor" / "rules" / "trw-ceremony.mdc"
        assert not mdc.exists(), "rules MDC should not be written for cursor-cli-only target"

    def test_mcp_json_written_for_ide(self, tmp_path: Path) -> None:
        """mcp.json is written for cursor-ide target."""
        self._call_update(tmp_path, ide_override="cursor-ide")

        mcp_file = tmp_path / ".cursor" / "mcp.json"
        assert mcp_file.is_file()
        data = json.loads(mcp_file.read_text(encoding="utf-8"))
        assert "mcpServers" in data
        assert "trw" in data["mcpServers"]

    def test_hooks_json_has_all_ide_events(self, tmp_path: Path) -> None:
        """hooks.json has all 8 IDE events after cursor-ide bootstrap."""
        from trw_mcp.bootstrap._cursor_ide import _IDE_HOOK_EVENTS

        self._call_update(tmp_path, ide_override="cursor-ide")

        hooks_file = tmp_path / ".cursor" / "hooks.json"
        assert hooks_file.is_file()
        data = json.loads(hooks_file.read_text(encoding="utf-8"))
        registered = set(data["hooks"].keys())
        for event in _IDE_HOOK_EVENTS:
            assert event in registered, f"Missing event in hooks.json: {event}"

    def test_subagents_have_correct_readonly_flags(self, tmp_path: Path) -> None:
        """Subagent frontmatter readonly flags are correct after full bootstrap."""
        import yaml

        self._call_update(tmp_path, ide_override="cursor-ide")
        agents_dir = tmp_path / ".cursor" / "agents"

        def get_frontmatter(path: Path) -> dict:
            content = path.read_text(encoding="utf-8")
            parts = content.split("---\n", 2)
            return yaml.safe_load(parts[1])

        assert get_frontmatter(agents_dir / "trw-implementer.md")["readonly"] is False
        assert get_frontmatter(agents_dir / "trw-explorer.md")["readonly"] is True
        assert get_frontmatter(agents_dir / "trw-reviewer.md")["readonly"] is True
        assert get_frontmatter(agents_dir / "trw-researcher.md")["readonly"] is True

    def test_no_cursor_artifacts_when_not_cursor_target(self, tmp_path: Path) -> None:
        """When target is opencode, no .cursor/ directory is created."""
        from trw_mcp.bootstrap._ide_targets import _update_cursor_artifacts

        result: dict = {"created": [], "updated": [], "preserved": [], "errors": []}
        _update_cursor_artifacts(tmp_path, result, ide_override="opencode")

        assert not (tmp_path / ".cursor").exists(), (
            ".cursor/ should not be created for opencode target"
        )

    def test_bootstrap_emits_tool_ceiling_advisory(self, tmp_path: Path) -> None:
        """cursor-ide bootstrap includes tool-ceiling advisory in result['info']."""
        result = self._call_update(tmp_path, ide_override="cursor-ide")

        info = result.get("info", [])
        assert any("24 MCP tools" in msg for msg in info), (
            f"Expected tool-ceiling advisory in result['info'], got: {info}"
        )
        assert any("cursor-ide" in msg for msg in info), (
            "Tool-ceiling advisory should mention cursor-ide"
        )

    def test_no_tool_ceiling_advisory_for_cli_only(self, tmp_path: Path) -> None:
        """cursor-cli-only bootstrap does NOT emit the IDE tool-ceiling advisory."""
        result = self._call_update(tmp_path, ide_override="cursor-cli")

        info = result.get("info", [])
        # Advisory is cursor-ide specific; CLI should not include it
        assert not any("24 MCP tools" in msg for msg in info), (
            f"Tool-ceiling advisory should not appear for cursor-cli-only: {info}"
        )
