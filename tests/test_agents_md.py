"""Tests for PRD-INFRA-001: AGENTS.md cross-tool compatibility sync."""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from unittest.mock import patch

from fastmcp import FastMCP

from trw_mcp.models.config import TRWConfig
from trw_mcp.state.claude_md import TRW_MARKER_END, TRW_MARKER_START, merge_trw_section
from trw_mcp.tools.learning import register_learning_tools

# Reusable TRW section for merge_trw_section tests
_TRW_SECTION = f"\n{TRW_MARKER_START}\n## TRW Section\n- test\n{TRW_MARKER_END}\n"


def _extract_trw_section(content: str) -> str:
    """Extract the TRW marker-delimited section from file content."""
    start = content.index(TRW_MARKER_START)
    end = content.index(TRW_MARKER_END) + len(TRW_MARKER_END)
    return content[start:end]


@contextmanager
def _patched_learning_env(
    project_root: Path,
    *,
    agents_md_enabled: bool = True,
) -> Generator[dict[str, Any], None, None]:
    """Patch learning tool dependencies and yield a tool-name-to-tool map.

    Centralizes the 5 patches + server setup shared across integration tests.
    """
    with (
        patch("trw_mcp.tools.learning.resolve_trw_dir", return_value=project_root / ".trw"),
        patch("trw_mcp.tools.learning._config", TRWConfig(agents_md_enabled=agents_md_enabled)),
        patch("trw_mcp.state.claude_md._config", TRWConfig()),
        patch("trw_mcp.state.claude_md.resolve_project_root", return_value=project_root),
        patch("trw_mcp.state.claude_md.resolve_trw_dir", return_value=project_root / ".trw"),
    ):
        server = FastMCP("test")
        register_learning_tools(server)
        tools = {t.name: t for t in server._tool_manager._tools.values()}
        yield tools


class TestAgentsMdCreation:
    """Test AGENTS.md file creation via trw_claude_md_sync."""

    def test_agents_md_created_on_root_sync(self, tmp_project: Path) -> None:
        """AGENTS.md is created when scope='root' and agents_md_enabled=True."""
        with _patched_learning_env(tmp_project, agents_md_enabled=True) as tools:
            result = tools["trw_claude_md_sync"].fn(scope="root")

        assert result["agents_md_synced"] is True
        agents_path = tmp_project / "AGENTS.md"
        assert agents_path.exists()
        content = agents_path.read_text(encoding="utf-8")
        assert TRW_MARKER_START in content
        assert TRW_MARKER_END in content

    def test_agents_md_content_matches_claude_md(self, tmp_project: Path) -> None:
        """AGENTS.md TRW section matches CLAUDE.md TRW section."""
        claude_target = tmp_project / "CLAUDE.md"
        agents_target = tmp_project / "AGENTS.md"

        merge_trw_section(claude_target, _TRW_SECTION, 200)
        merge_trw_section(agents_target, _TRW_SECTION, 200)

        claude_section = _extract_trw_section(claude_target.read_text(encoding="utf-8"))
        agents_section = _extract_trw_section(agents_target.read_text(encoding="utf-8"))

        assert claude_section == agents_section

    def test_agents_md_disabled_config(self, tmp_project: Path) -> None:
        """AGENTS.md is NOT created when agents_md_enabled=False."""
        with _patched_learning_env(tmp_project, agents_md_enabled=False) as tools:
            result = tools["trw_claude_md_sync"].fn(scope="root")

        assert result["agents_md_synced"] is False
        assert result["agents_md_path"] is None
        assert not (tmp_project / "AGENTS.md").exists()

    def test_agents_md_preserves_existing_content(self, tmp_project: Path) -> None:
        """Existing non-TRW content in AGENTS.md is preserved."""
        agents_path = tmp_project / "AGENTS.md"
        agents_path.write_text(
            "# My Custom Agents Config\n\nSome existing content.\n",
            encoding="utf-8",
        )

        merge_trw_section(agents_path, _TRW_SECTION, 200)

        content = agents_path.read_text(encoding="utf-8")
        assert "# My Custom Agents Config" in content
        assert "Some existing content." in content
        assert TRW_MARKER_START in content

    def test_agents_md_idempotent(self, tmp_project: Path) -> None:
        """Running sync three times stabilizes content (idempotent after first)."""
        agents_path = tmp_project / "AGENTS.md"
        trw_section = f"\n{TRW_MARKER_START}\n## TRW Section\n- test learning\n{TRW_MARKER_END}\n"

        # First call creates the file
        merge_trw_section(agents_path, trw_section, 200)

        # Second call replaces markers -- may differ from first due to .lstrip()
        merge_trw_section(agents_path, trw_section, 200)
        second_content = agents_path.read_text(encoding="utf-8")

        # Third call should produce identical content to second
        merge_trw_section(agents_path, trw_section, 200)
        third_content = agents_path.read_text(encoding="utf-8")

        assert second_content == third_content

    def test_truncation_preserves_trw_markers(self, tmp_project: Path) -> None:
        """QUAL-018: Truncation never cuts inside TRW marker boundaries."""
        target = tmp_project / "CLAUDE.md"
        # Create a large user section that will exceed the line limit
        user_lines = [f"# Line {i}" for i in range(200)]
        user_content = "\n".join(user_lines) + "\n"
        target.write_text(user_content, encoding="utf-8")

        trw_section = f"\n{TRW_MARKER_START}\n## TRW Section\n- learning 1\n- learning 2\n{TRW_MARKER_END}\n"
        merge_trw_section(target, trw_section, max_lines=100)

        content = target.read_text(encoding="utf-8")
        assert TRW_MARKER_START in content, "TRW start marker must survive truncation"
        assert TRW_MARKER_END in content, "TRW end marker must survive truncation"
        assert "learning 1" in content, "TRW section content must survive truncation"
        assert "truncated" in content.lower(), "Truncation comment should be present"
        assert content.split("\n").__len__() <= 102, "Total lines should respect limit"

    def test_truncation_without_markers_falls_back(self, tmp_project: Path) -> None:
        """QUAL-018: Without TRW markers, truncation falls back to simple slice."""
        target = tmp_project / "CLAUDE.md"
        content = "\n".join([f"# Line {i}" for i in range(200)]) + "\n"
        target.write_text(content, encoding="utf-8")

        # No TRW section — just truncate
        merge_trw_section(target, "\n## New Section\n- content\n", max_lines=50)

        result = target.read_text(encoding="utf-8")
        lines = result.split("\n")
        assert len(lines) <= 52  # 50 + truncation comment + possible trailing newline

    def test_truncation_user_content_trimmed_not_trw(self, tmp_project: Path) -> None:
        """QUAL-018: User content is trimmed, TRW section is preserved intact."""
        target = tmp_project / "CLAUDE.md"
        user_lines = [f"# User line {i}" for i in range(150)]
        target.write_text("\n".join(user_lines) + "\n", encoding="utf-8")

        trw_section = f"\n{TRW_MARKER_START}\n## TRW Generated\n- item a\n- item b\n- item c\n{TRW_MARKER_END}\n"
        merge_trw_section(target, trw_section, max_lines=50)

        content = target.read_text(encoding="utf-8")
        # TRW section must be fully intact
        assert TRW_MARKER_START in content
        assert TRW_MARKER_END in content
        assert "item a" in content
        assert "item b" in content
        assert "item c" in content
        # User content should be truncated
        assert "User line 149" not in content

    def test_agents_md_root_scope_only(self, tmp_project: Path) -> None:
        """AGENTS.md is only synced for root scope, not sub scope."""
        sub_dir = tmp_project / "submodule"
        sub_dir.mkdir()

        with _patched_learning_env(tmp_project, agents_md_enabled=True) as tools:
            result = tools["trw_claude_md_sync"].fn(
                scope="sub", target_dir=str(sub_dir),
            )

        assert result["agents_md_synced"] is False
        assert not (tmp_project / "AGENTS.md").exists()
