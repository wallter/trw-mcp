"""Tests for channels/claude_code/_memory_path.py (PRD-DIST-2405 FR10)."""

from __future__ import annotations

from pathlib import Path

from trw_mcp.channels.claude_code._memory_path import (
    derive_claude_project_id,
    resolve_memory_dir,
    resolve_memory_index_path,
)


class TestDeriveClaudeProjectId:
    def test_derive_project_id_encoding(self) -> None:
        """FR10: /home/dev/projects/my-app → -home-dev-projects-my-app."""
        project_root = Path("/home/dev/projects/my-app")
        result = derive_claude_project_id(project_root)
        assert result == "-home-dev-projects-my-app"

    def test_derive_project_id_leading_dash(self) -> None:
        """Result must start with '-'."""
        result = derive_claude_project_id(Path("/tmp/myproject"))
        assert result.startswith("-")

    def test_derive_project_id_no_double_leading_dash(self) -> None:
        """Leading '-' should not be duplicated."""
        result = derive_claude_project_id(Path("/a/b/c"))
        assert not result.startswith("--")

    def test_derive_project_id_slashes_replaced(self) -> None:
        """All '/' characters are replaced with '-'."""
        result = derive_claude_project_id(Path("/a/b/c"))
        assert "/" not in result

    def test_derive_project_id_deep_path(self) -> None:
        """Deep path encodes correctly."""
        result = derive_claude_project_id(Path("/home/user/work/projects/foo-bar"))
        assert result == "-home-user-work-projects-foo-bar"

    def test_derive_project_id_matches_observed_directory(self) -> None:
        """Matches the Anthropic-documented encoding for a concrete project dir."""
        observed = "-home-dev-projects-my-app"
        computed = derive_claude_project_id(Path("/home/dev/projects/my-app"))
        assert computed == observed


class TestResolveMemoryDir:
    def test_resolve_memory_dir_path_structure(self, tmp_path: Path) -> None:
        """Memory dir is <claude_projects_dir>/<project_id>/memory/."""
        claude_dir = tmp_path / "claude_projects"
        project_root = Path("/home/user/myproject")
        result = resolve_memory_dir(project_root, claude_projects_dir=claude_dir)
        project_id = derive_claude_project_id(project_root)
        assert result == claude_dir / project_id / "memory"

    def test_resolve_memory_dir_default_base(self) -> None:
        """Without override, uses ~/.claude/projects/."""
        project_root = Path("/tmp/proj")
        result = resolve_memory_dir(project_root)
        assert "claude" in str(result)
        assert "memory" in str(result).lower()

    def test_resolve_memory_index_path(self, tmp_path: Path) -> None:
        """Index path is memory_dir/MEMORY.md."""
        claude_dir = tmp_path / "claude"
        project_root = Path("/home/user/proj")
        result = resolve_memory_index_path(project_root, claude_projects_dir=claude_dir)
        assert result.name == "MEMORY.md"
        assert result.parent == resolve_memory_dir(project_root, claude_projects_dir=claude_dir)
