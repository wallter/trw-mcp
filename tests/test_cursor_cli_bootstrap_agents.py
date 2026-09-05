"""Unit tests for cursor-cli AGENTS.md bootstrap generators (PRD-CORE-137).

PRD-CORE-243-FR06/FR08 (2026-09-03): ``generate_cursor_cli_agents_md`` no
longer owns a private ``<!-- TRW:BEGIN -->``/``<!-- TRW:END -->`` sentinel
dialect. It merges into the SAME ``<!-- trw:start -->``/``<!-- trw:end -->``
block every other AGENTS.md/CLAUDE.md writer uses, through the shared
``merge_trw_section`` seam. A file that still carries the retired legacy block
(from before this fix) is migrated in place -- see the
``TestLegacyDialectMigration`` class below.
"""

from __future__ import annotations

from pathlib import Path

_START = "<!-- trw:start -->"
_END = "<!-- trw:end -->"
_LEGACY_START = "<!-- TRW:BEGIN -->"
_LEGACY_END = "<!-- TRW:END -->"


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
        assert _START in content
        assert _END in content

    def test_trw_section_inside_block(self, tmp_path: Path) -> None:
        from trw_mcp.bootstrap._cursor_cli import generate_cursor_cli_agents_md

        generate_cursor_cli_agents_md(tmp_path, "Ceremony content here")
        content = (tmp_path / "AGENTS.md").read_text()
        begin_idx = content.index(_START)
        end_idx = content.index(_END)
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
        agents_file.write_text(pre_content + f"{_START}\nOld TRW content\n{_END}" + post_content)

        generate_cursor_cli_agents_md(tmp_path, "New TRW content")
        content = agents_file.read_text()
        assert "Be concise." in content
        assert "Don't break things." in content
        assert "New TRW content" in content
        assert "Old TRW content" not in content

    def test_updated_in_result(self, tmp_path: Path) -> None:
        from trw_mcp.bootstrap._cursor_cli import generate_cursor_cli_agents_md

        agents_file = tmp_path / "AGENTS.md"
        agents_file.write_text(f"{_START}\nOld content\n{_END}\n")
        result = generate_cursor_cli_agents_md(tmp_path, "New content")
        assert "AGENTS.md" in result["updated"]


class TestAgentsMdNoSentinels:
    """test_agents_md_no_sentinels_appends_block.

    PRD-CORE-243-FR06/FR08: cursor-cli's writer used to PREPEND its own block
    above existing content when no sentinels were found -- the one respect in
    which its private dialect differed from every other AGENTS.md/CLAUDE.md
    writer, which APPENDS (see ``render_merged_content``'s no-markers-found
    branch). Routing cursor-cli through the shared ``merge_trw_section`` seam
    makes this consistent with the rest of the system: existing content is
    preserved and the TRW block lands after it, not before.
    """

    def test_no_sentinels_appends(self, tmp_path: Path) -> None:
        from trw_mcp.bootstrap._cursor_cli import generate_cursor_cli_agents_md

        agents_file = tmp_path / "AGENTS.md"
        original = "# My existing rules\nBe careful.\n"
        agents_file.write_text(original)

        generate_cursor_cli_agents_md(tmp_path, "TRW content")
        content = agents_file.read_text()
        begin_idx = content.index(_START)
        original_idx = content.index("Be careful.")
        assert original_idx < begin_idx
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
        assert _START in agents_md
        assert _END in agents_md
        # The retired dialect must never be emitted by a fresh write.
        assert _LEGACY_START not in agents_md
        assert _LEGACY_END not in agents_md


class TestLegacyDialectMigration:
    """PRD-CORE-243-FR06/FR08: a dead legacy block is migrated, not duplicated.

    Reproduces the live defect: an ``update-project`` run that PREPENDED a
    second, uppercase-dialect cursor-cli block onto an AGENTS.md that already
    carried the shared lowercase block (two writers, two dialects, one file --
    ``make instruction-surface-lint-strict``'s ``duplicate_block`` finding).
    """

    def test_legacy_alone_is_migrated_to_the_shared_dialect(self, tmp_path: Path) -> None:
        from trw_mcp.bootstrap._cursor_cli import generate_cursor_cli_agents_md

        agents_file = tmp_path / "AGENTS.md"
        agents_file.write_text(f"{_LEGACY_START}\nOld install body\n{_LEGACY_END}\n", encoding="utf-8")

        generate_cursor_cli_agents_md(tmp_path, "New TRW content")
        content = agents_file.read_text(encoding="utf-8")

        assert _LEGACY_START not in content
        assert _LEGACY_END not in content
        assert content.count(_START) == 1
        assert "New TRW content" in content
        assert "Old install body" not in content

    def test_legacy_plus_shared_collapse_to_one_block_and_preserve_user_content(self, tmp_path: Path) -> None:
        """The exact HEAD-of-repo shape: a dead legacy block prepended onto a

        file that already carries the live shared block. After the writer
        runs, exactly one TRW block of any dialect remains and the pointer
        prose that sat below both is untouched.
        """
        from trw_mcp.bootstrap._cursor_cli import generate_cursor_cli_agents_md

        agents_file = tmp_path / "AGENTS.md"
        agents_file.write_text(
            f"{_LEGACY_START}\ndead legacy install body\n{_LEGACY_END}\n\n"
            "# AGENTS.md\n\nUser pointer prose.\n\n"
            f"{_START}\nlive shared body\n{_END}\n",
            encoding="utf-8",
        )

        generate_cursor_cli_agents_md(tmp_path, "New TRW content")
        content = agents_file.read_text(encoding="utf-8")

        assert _LEGACY_START not in content
        assert _LEGACY_END not in content
        assert content.count(_START) == 1
        assert "User pointer prose." in content
        assert "New TRW content" in content
        assert "dead legacy install body" not in content
        assert "live shared body" not in content

    def test_migration_is_idempotent_on_a_second_run(self, tmp_path: Path) -> None:
        from trw_mcp.bootstrap._cursor_cli import generate_cursor_cli_agents_md

        agents_file = tmp_path / "AGENTS.md"
        agents_file.write_text(
            f"{_LEGACY_START}\ndead\n{_LEGACY_END}\n\n# AGENTS.md\n\nUser prose.\n",
            encoding="utf-8",
        )

        generate_cursor_cli_agents_md(tmp_path, "Body")
        first = agents_file.read_text(encoding="utf-8")
        generate_cursor_cli_agents_md(tmp_path, "Body")
        second = agents_file.read_text(encoding="utf-8")

        assert first == second
        assert second.count(_START) == 1
