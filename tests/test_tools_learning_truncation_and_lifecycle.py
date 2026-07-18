"""Tests for marker-aware truncation and learning lifecycle tag/obsolete flows."""

from __future__ import annotations

from pathlib import Path

from tests._tools_learning_shared import (
    _CFG,
    _get_tools,
    set_project_root,  # noqa: F401 -- autouse fixture disables dedup (f4ca661c9 flipped embeddings_enabled default True)
)


class TestMarkerAwareTruncation:
    """QUAL-018 FR02: Marker-aware truncation preserves TRW section."""

    def test_trw_section_preserved_on_truncation(self, tmp_path: Path) -> None:
        """When file exceeds max_lines, TRW section is kept intact."""
        from trw_mcp.state.claude_md import (
            TRW_MARKER_END,
            TRW_MARKER_START,
            merge_trw_section,
        )

        target = tmp_path / "CLAUDE.md"
        # 200 lines of user content
        user_content = "\n".join(f"# User line {i}" for i in range(200)) + "\n"
        target.write_text(user_content, encoding="utf-8")

        trw_section = f"\n{TRW_MARKER_START}\n## TRW Auto\n- learning alpha\n- learning beta\n{TRW_MARKER_END}\n"
        merge_trw_section(target, trw_section, max_lines=50)

        content = target.read_text(encoding="utf-8")
        # TRW markers and content must survive
        assert TRW_MARKER_START in content
        assert TRW_MARKER_END in content
        assert "learning alpha" in content
        assert "learning beta" in content
        # User content must be trimmed
        assert "User line 199" not in content
        # Truncation comment should be present
        assert "truncated" in content.lower()

    def test_simple_truncation_fallback(self, tmp_path: Path) -> None:
        """Without TRW markers, truncation falls back to simple line slice."""
        from trw_mcp.state.claude_md import merge_trw_section

        target = tmp_path / "CLAUDE.md"
        content = "\n".join(f"# Line {i}" for i in range(200)) + "\n"
        target.write_text(content, encoding="utf-8")

        merge_trw_section(target, "\n## New\n- item\n", max_lines=30)

        result = target.read_text(encoding="utf-8")
        lines = result.split("\n")
        # Should not exceed max_lines + truncation comment + trailing newline
        assert len(lines) <= 33
        assert "truncated" in result.lower()

    def test_no_truncation_under_limit(self, tmp_path: Path) -> None:
        """Files under the limit are not truncated."""
        from trw_mcp.state.claude_md import (
            TRW_MARKER_END,
            TRW_MARKER_START,
            merge_trw_section,
        )

        target = tmp_path / "CLAUDE.md"
        user_content = "# Short file\n\nSome content.\n"
        target.write_text(user_content, encoding="utf-8")

        trw_section = f"\n{TRW_MARKER_START}\n## TRW\n- item\n{TRW_MARKER_END}\n"
        merge_trw_section(target, trw_section, max_lines=500)

        content = target.read_text(encoding="utf-8")
        assert "truncated" not in content.lower()
        assert TRW_MARKER_START in content
        assert TRW_MARKER_END in content


class TestAutoObsoleteOnCompendium:
    """PRD-FIX-052-FR04: trw_learn auto-marks consolidated_from entries as obsolete."""

    def test_auto_obsolete_on_compendium(self, tmp_path: Path) -> None:
        """Creating a learning with consolidated_from marks those entries as obsolete."""
        tools = _get_tools()

        # First create the source entries
        result1 = tools["trw_learn"].fn(
            summary="Source learning 1",
            detail="Source detail 1",
            tags=["gotcha"],
            impact=0.5,
        )
        result2 = tools["trw_learn"].fn(
            summary="Source learning 2",
            detail="Source detail 2",
            tags=["gotcha"],
            impact=0.5,
        )
        lid1 = result1["learning_id"]
        lid2 = result2["learning_id"]

        # Now create the compendium with consolidated_from
        tools["trw_learn"].fn(
            summary="Compendium of source learnings",
            detail="This consolidates L-001 and L-002",
            tags=["pattern"],
            impact=0.8,
            consolidated_from=[lid1, lid2],
        )

        # Verify both source entries are now obsolete
        from trw_mcp.state.memory_adapter import recall_learnings

        all_entries = recall_learnings(
            tmp_path / _CFG.trw_dir,
            query="*",
            status="obsolete",
            max_results=0,
            compact=False,
        )
        obsolete_ids = {str(e.get("id", "")) for e in all_entries}
        assert lid1 in obsolete_ids
        assert lid2 in obsolete_ids

    def test_no_auto_obsolete_without_consolidated_from(self, tmp_path: Path) -> None:
        """Normal learning without consolidated_from does not obsolete anything."""
        tools = _get_tools()

        result1 = tools["trw_learn"].fn(
            summary="A regular learning",
            detail="Not part of any consolidation",
            tags=["testing"],
            impact=0.6,
        )
        lid1 = result1["learning_id"]

        # Create another regular learning (no consolidated_from)
        tools["trw_learn"].fn(
            summary="Another regular learning",
            detail="Still not consolidating",
            tags=["testing"],
            impact=0.6,
        )

        # First learning should still be active
        from trw_mcp.state.memory_adapter import recall_learnings

        active_entries = recall_learnings(
            tmp_path / _CFG.trw_dir,
            query="regular",
            status="active",
            max_results=0,
            compact=False,
        )
        active_ids = {str(e.get("id", "")) for e in active_entries}
        assert lid1 in active_ids

    def test_auto_obsolete_nonexistent_id_logs_and_continues(self, tmp_path: Path) -> None:
        """consolidated_from with a non-existent ID logs warning but does not raise."""
        tools = _get_tools()

        # Should not raise even though the ID doesn't exist
        result = tools["trw_learn"].fn(
            summary="Compendium with phantom source",
            detail="References a non-existent entry",
            tags=["pattern"],
            impact=0.8,
            consolidated_from=["L-nonexistent-id-999"],
        )
        assert result["status"] == "recorded"


class TestPatternTagAutoSuggestion:
    """PRD-FIX-052-FR05: trw_learn auto-adds 'pattern' tag for solution summaries."""

    def _get_entry_tags(self, tmp_path: Path, learning_id: str) -> list[str]:
        """Retrieve the tags for a specific learning entry via SQLite recall.

        Uses recall_learnings() which queries the same SQLite backend that
        trw_learn writes to, ensuring test isolation regardless of trw_dir patching.
        """
        from trw_mcp.state._paths import resolve_trw_dir
        from trw_mcp.state.memory_adapter import recall_learnings

        trw_dir = resolve_trw_dir()
        results = recall_learnings(trw_dir, query="*", max_results=0, compact=False)
        for entry in results:
            if entry.get("id") == learning_id:
                raw_tags = entry.get("tags", [])
                return [str(t) for t in raw_tags] if isinstance(raw_tags, list) else []
        return []

    def test_pattern_tag_auto_add_use_instead_of(self, tmp_path: Path) -> None:
        """Summary with 'use ... instead' gets 'pattern' tag auto-added."""
        tools = _get_tools()
        result = tools["trw_learn"].fn(
            summary="use Field(ge=0) instead of manual validation",
            detail="Pydantic v2 Field with ge constraint is cleaner",
            tags=["pydantic-v2"],
            impact=0.6,
        )
        lid = result["learning_id"]
        assert result["status"] == "recorded"
        tags = self._get_entry_tags(tmp_path, lid)
        assert "pattern" in tags

    def test_pattern_tag_auto_add_prefer(self, tmp_path: Path) -> None:
        """Summary with 'prefer ...' gets 'pattern' tag auto-added."""
        tools = _get_tools()
        result = tools["trw_learn"].fn(
            summary="prefer structlog over print for logging",
            detail="structlog provides structured output",
            tags=["logging"],
            impact=0.6,
        )
        lid = result["learning_id"]
        tags = self._get_entry_tags(tmp_path, lid)
        assert "pattern" in tags

    def test_pattern_tag_auto_add_best_practice(self, tmp_path: Path) -> None:
        """Summary with 'best practice' gets 'pattern' tag auto-added."""
        tools = _get_tools()
        result = tools["trw_learn"].fn(
            summary="best practice: always use atomic writes for YAML",
            detail="Prevents partial file corruption on crash",
            tags=["yaml"],
            impact=0.7,
        )
        lid = result["learning_id"]
        tags = self._get_entry_tags(tmp_path, lid)
        assert "pattern" in tags

    def test_pattern_tag_not_added_for_problem_summary(self, tmp_path: Path) -> None:
        """Problem-style summary does not get 'pattern' tag."""
        tools = _get_tools()
        result = tools["trw_learn"].fn(
            summary="module crashes on startup when config is missing",
            detail="The config file must be present before import",
            tags=["gotcha"],
            impact=0.6,
        )
        lid = result["learning_id"]
        tags = self._get_entry_tags(tmp_path, lid)
        assert "pattern" not in tags

    def test_pattern_tag_not_duplicated_if_already_present(self, tmp_path: Path) -> None:
        """'pattern' is not added twice if it's already in the tags list."""
        tools = _get_tools()
        result = tools["trw_learn"].fn(
            summary="use async/await instead of threading",
            detail="More readable and avoids GIL issues",
            tags=["pattern", "async"],
            impact=0.7,
        )
        lid = result["learning_id"]
        tags = self._get_entry_tags(tmp_path, lid)
        assert tags.count("pattern") == 1
