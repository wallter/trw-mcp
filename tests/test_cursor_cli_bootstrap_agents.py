"""Unit tests for cursor-cli AGENTS.md bootstrap generators (PRD-CORE-137)."""

from __future__ import annotations

from pathlib import Path


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
        agents_file.write_text(pre_content + "<!-- TRW:BEGIN -->\nOld TRW content\n<!-- TRW:END -->" + post_content)

        generate_cursor_cli_agents_md(tmp_path, "New TRW content")
        content = agents_file.read_text()
        assert "Be concise." in content
        assert "Don't break things." in content
        assert "New TRW content" in content
        assert "Old TRW content" not in content

    def test_updated_in_result(self, tmp_path: Path) -> None:
        from trw_mcp.bootstrap._cursor_cli import generate_cursor_cli_agents_md

        agents_file = tmp_path / "AGENTS.md"
        agents_file.write_text("<!-- TRW:BEGIN -->\nOld content\n<!-- TRW:END -->\n")
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
        begin_idx = content.index("<!-- TRW:BEGIN -->")
        original_idx = content.index("Be careful.")
        assert begin_idx < original_idx
        assert "Be careful." in content


class TestAgentsMdCursorCliContentGating:
    """cursor-cli AGENTS.md must omit claude-code-only surfaces."""

    def test_cursor_cli_agents_md_omits_retired_peer_team_content(self, tmp_path: Path) -> None:
        """cursor-cli dispatcher output must not contain retired peer-team language."""
        from trw_mcp.bootstrap._ide_targets import _update_cursor_cli_artifacts

        (tmp_path / ".cursor").mkdir()
        result: dict[str, list[str]] = {"created": [], "updated": [], "preserved": []}
        _update_cursor_cli_artifacts(tmp_path, result)

        agents_md = (tmp_path / "AGENTS.md").read_text()
        assert ("Team" + "Create") not in agents_md
        assert ("Agent " + "Teams") not in agents_md
        assert ("Send" + "Message") not in agents_md
        assert "FRAMEWORK.md" not in agents_md

    def test_cursor_cli_agents_md_contains_expected_surface(self, tmp_path: Path) -> None:
        """cursor-cli AGENTS.md DOES contain TRW MCP tool guidance + ceremony workflow."""
        from trw_mcp.bootstrap._ide_targets import _update_cursor_cli_artifacts

        (tmp_path / ".cursor").mkdir()
        result: dict[str, list[str]] = {"created": [], "updated": [], "preserved": []}
        _update_cursor_cli_artifacts(tmp_path, result)

        agents_md = (tmp_path / "AGENTS.md").read_text()
        assert "trw_session_start" in agents_md
        assert "trw_deliver" in agents_md
        assert "<!-- TRW:BEGIN -->" in agents_md
        assert "<!-- TRW:END -->" in agents_md


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
