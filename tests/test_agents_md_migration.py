"""Tests for AGENTS.md migration behavior."""

from __future__ import annotations

from pathlib import Path

from trw_mcp.models.config import TRWConfig
from trw_mcp.state.claude_md import TRW_MARKER_END, TRW_MARKER_START
from trw_mcp.state.claude_md._agents_md import _migrate_trw_content_from_agents_md


class TestMigrateTrwContentFromAgentsMd:
    """Tests for AGENTS.md TRW content migration — FR08."""

    def test_strips_trw_markers_from_agents_md(self, tmp_project: Path) -> None:
        """TRW markers are removed from AGENTS.md after migration."""
        agents_path = tmp_project / "AGENTS.md"
        agents_path.write_text(
            f"# User Section\n\n{TRW_MARKER_START}\n## TRW\n- gotcha\n{TRW_MARKER_END}\n",
            encoding="utf-8",
        )

        migrated, _ = _migrate_trw_content_from_agents_md(tmp_project, TRWConfig())

        assert migrated is True
        content = agents_path.read_text(encoding="utf-8")
        assert TRW_MARKER_START not in content
        assert TRW_MARKER_END not in content

    def test_preserves_user_content_after_stripping(self, tmp_project: Path) -> None:
        """User-authored content before and after TRW block is preserved."""
        agents_path = tmp_project / "AGENTS.md"
        agents_path.write_text(
            f"# My Custom Config\n\nUser note.\n\n"
            f"{TRW_MARKER_START}\n## TRW\n- item\n{TRW_MARKER_END}\n\n"
            "Post-TRW user content.\n",
            encoding="utf-8",
        )

        _migrate_trw_content_from_agents_md(tmp_project, TRWConfig())

        content = agents_path.read_text(encoding="utf-8")
        assert "# My Custom Config" in content
        assert "User note." in content
        assert "Post-TRW user content." in content

    def test_strips_trw_auto_comment_along_with_markers(self, tmp_project: Path) -> None:
        """TRW_AUTO_COMMENT that precedes TRW block is removed alongside the markers."""
        from trw_mcp.state.claude_md._parser import TRW_AUTO_COMMENT

        agents_path = tmp_project / "AGENTS.md"
        agents_path.write_text(
            f"# User Section\n\n{TRW_AUTO_COMMENT}\n{TRW_MARKER_START}\n## TRW\n- item\n{TRW_MARKER_END}\n",
            encoding="utf-8",
        )

        migrated, _ = _migrate_trw_content_from_agents_md(tmp_project, TRWConfig())

        assert migrated is True
        content = agents_path.read_text(encoding="utf-8")
        assert TRW_AUTO_COMMENT not in content
        assert TRW_MARKER_START not in content
        assert TRW_MARKER_END not in content
        assert "# User Section" in content

    def test_idempotent_when_no_ide_detected(self, tmp_project: Path) -> None:
        """Running migration twice on a project with no IDE produces same result."""
        agents_path = tmp_project / "AGENTS.md"
        agents_path.write_text(
            f"# User Content\n\n{TRW_MARKER_START}\n## TRW\n- item\n{TRW_MARKER_END}\n",
            encoding="utf-8",
        )

        migrated1, _ = _migrate_trw_content_from_agents_md(tmp_project, TRWConfig())
        content_after_first = agents_path.read_text(encoding="utf-8")

        migrated2, _ = _migrate_trw_content_from_agents_md(tmp_project, TRWConfig())
        content_after_second = agents_path.read_text(encoding="utf-8")

        assert migrated1 is True
        assert migrated2 is False
        assert content_after_first == content_after_second

    def test_returns_false_when_agents_md_missing(self, tmp_project: Path) -> None:
        """No AGENTS.md → (False, '') returned immediately."""
        migrated, path = _migrate_trw_content_from_agents_md(tmp_project, TRWConfig())

        assert migrated is False
        assert path == ""

    def test_strips_markers_even_when_no_ide_detected(self, tmp_project: Path) -> None:
        """TRW markers are stripped from AGENTS.md even when no IDE config dir is present."""
        agents_path = tmp_project / "AGENTS.md"
        agents_path.write_text(
            f"# User Content\n\n{TRW_MARKER_START}\n## TRW\n- item\n{TRW_MARKER_END}\n",
            encoding="utf-8",
        )

        migrated, path = _migrate_trw_content_from_agents_md(tmp_project, TRWConfig())

        assert migrated is True
        assert path == ""
        content = agents_path.read_text(encoding="utf-8")
        assert TRW_MARKER_START not in content
        assert "# User Content" in content
