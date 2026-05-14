"""Tests for CLAUDE.md sync behavior, LLM flags, and atomic writes."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from tests._tools_learning_shared import _get_tools
from trw_mcp.models.config import get_config
from trw_mcp.state.persistence import FileStateWriter


class TestTrwClaudeMdSync:
    """Tests for trw_claude_md_sync tool."""

    def test_generates_claude_md(self, tmp_path: Path) -> None:
        tools = _get_tools()

        # Record a high-impact learning
        tools["trw_learn"].fn(
            summary="Critical pattern discovered",
            detail="Always use context managers for DB connections",
            tags=["database"],
            impact=0.9,
        )

        # Sync
        result = tools["trw_claude_md_sync"].fn(scope="root")
        assert result["status"] == "synced"
        # CORE-093: learning promotion removed — learnings_promoted always 0
        assert result["learnings_promoted"] == 0

        # Verify CLAUDE.md was created with static behavioral protocol
        claude_md = tmp_path / "CLAUDE.md"
        assert claude_md.exists()
        content = claude_md.read_text(encoding="utf-8")
        assert "trw:start" in content
        assert "trw:end" in content

    def test_preserves_existing_content(self, tmp_path: Path) -> None:
        # Create existing CLAUDE.md
        claude_md = tmp_path / "CLAUDE.md"
        claude_md.write_text("# My Project\n\nExisting content.\n", encoding="utf-8")

        tools = _get_tools()
        tools["trw_learn"].fn(
            summary="New high impact learning",
            detail="Detail here",
            impact=0.9,
        )
        tools["trw_claude_md_sync"].fn(scope="root")

        content = claude_md.read_text(encoding="utf-8")
        assert "My Project" in content  # Preserved
        assert "Existing content" in content  # Preserved
        assert "trw:start" in content  # Added

    def test_replaces_existing_trw_section(self, tmp_path: Path) -> None:
        claude_md = tmp_path / "CLAUDE.md"
        claude_md.write_text(
            "# Project\n\n<!-- trw:start -->\nOld content\n<!-- trw:end -->\n\n# Other section\n",
            encoding="utf-8",
        )

        tools = _get_tools()
        tools["trw_learn"].fn(
            summary="Replacement learning",
            detail="Replaces old content",
            impact=0.9,
        )
        tools["trw_claude_md_sync"].fn(scope="root")

        content = claude_md.read_text(encoding="utf-8")
        assert "Old content" not in content
        # CORE-093: static behavioral protocol, no individual learnings
        assert "trw:start" in content
        assert "Other section" in content  # Preserved

    def test_sub_scope_creates_sub_claude_md(self, tmp_path: Path) -> None:
        """Sub-scope sync writes to target_dir/CLAUDE.md."""
        tools = _get_tools()

        tools["trw_learn"].fn(
            summary="Sub scope learning test",
            detail="For sub-scope CLAUDE.md",
            impact=0.9,
        )

        sub_dir = tmp_path / "src" / "module"
        sub_dir.mkdir(parents=True)

        result = tools["trw_claude_md_sync"].fn(
            scope="sub",
            target_dir=str(sub_dir),
        )
        assert result["scope"] == "sub"

        sub_claude_md = sub_dir / "CLAUDE.md"
        assert sub_claude_md.exists()
        content = sub_claude_md.read_text(encoding="utf-8")
        assert "trw:start" in content

    def test_enforces_line_limit(self, tmp_path: Path) -> None:
        """CLAUDE.md content is truncated when exceeding line limit."""

        tools = _get_tools()

        # Create existing CLAUDE.md with many lines
        claude_md = tmp_path / "CLAUDE.md"
        long_content = "\n".join(f"Line {i}" for i in range(300))
        claude_md.write_text(long_content, encoding="utf-8")

        tools["trw_learn"].fn(
            summary="Line limit test learning",
            detail="Trigger sync",
            impact=0.9,
        )
        tools["trw_claude_md_sync"].fn(scope="root")

        content = claude_md.read_text(encoding="utf-8")
        line_count = len(content.split("\n"))
        # Should be at or below the configured max (200) + 1 for truncation comment
        assert line_count <= get_config().claude_md_max_lines + 1

    def test_wildcard_returns_all_learnings(self, tmp_path: Path) -> None:
        """Query '*' or empty returns all learnings (filtered by other params)."""
        tools = _get_tools()

        tools["trw_learn"].fn(summary="Alpha learning", detail="First", impact=0.8)
        tools["trw_learn"].fn(summary="Beta learning", detail="Second", impact=0.3)
        tools["trw_learn"].fn(summary="Gamma learning", detail="Third", impact=0.9)

        # Wildcard query should return all
        result = tools["trw_recall"].fn(query="*")
        assert len(result["learnings"]) == 3

        # Wildcard with min_impact filter
        result = tools["trw_recall"].fn(query="*", min_impact=0.5)
        assert len(result["learnings"]) == 2

    def test_empty_query_returns_all_learnings(self, tmp_path: Path) -> None:
        """Empty string query returns all learnings."""
        tools = _get_tools()

        tools["trw_learn"].fn(summary="One", detail="Detail", impact=0.5)
        tools["trw_learn"].fn(summary="Two", detail="Detail", impact=0.5)

        result = tools["trw_recall"].fn(query="")
        assert len(result["learnings"]) == 2


class TestTrwClaudeMdSyncLLM:
    """Tests for LLM-augmented trw_claude_md_sync."""

    def test_sync_without_llm_unchanged(self, tmp_path: Path) -> None:
        """Verify sync still works with LLM unavailable."""
        tools = _get_tools()

        tools["trw_learn"].fn(
            summary="Sync no LLM test learning",
            detail="Should appear as bullet point",
            impact=0.9,
        )

        result = tools["trw_claude_md_sync"].fn(scope="root")
        assert result["status"] == "synced"
        assert result["llm_used"] is False
        # CORE-093: learning promotion removed
        assert result["learnings_promoted"] == 0

        claude_md = tmp_path / "CLAUDE.md"
        content = claude_md.read_text(encoding="utf-8")
        assert "trw:start" in content

    def test_sync_llm_flag_present(self, tmp_path: Path) -> None:
        """Verify llm_used field is in return value."""
        tools = _get_tools()
        tools["trw_learn"].fn(
            summary="Sync flag test",
            detail="Check llm_used field",
            impact=0.9,
        )
        result = tools["trw_claude_md_sync"].fn(scope="root")
        assert "llm_used" in result


class TestClaudeMdSyncAtomicWrite:
    """PRD-CORE-014: merge_trw_section uses atomic writes via _writer."""

    def test_claude_md_sync_uses_atomic_write(self, tmp_path: Path) -> None:
        """trw_claude_md_sync uses _writer.write_text for CLAUDE.md."""
        tools = _get_tools()

        tools["trw_learn"].fn(
            summary="Atomic claude md test learning",
            detail="Triggers sync",
            impact=0.9,
        )

        real_writer = FileStateWriter()
        with patch(
            "trw_mcp.state.claude_md._parser.FileStateWriter",
        ) as mock_cls:
            mock_instance = mock_cls.return_value
            mock_instance.write_text = MagicMock(wraps=real_writer.write_text)
            result = tools["trw_claude_md_sync"].fn(scope="root")
            assert result["status"] == "synced"
            assert mock_instance.write_text.call_count >= 1
