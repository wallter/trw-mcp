"""Tests for PRD-CORE-018: INDEX.md and ROADMAP.md auto-sync.

Covers:
- PRD frontmatter scanning
- Status grouping
- INDEX.md catalogue rendering and marker-based merge
- ROADMAP.md catalogue rendering and marker-based merge
- Edge cases (empty dir, malformed PRDs, missing markers)
"""

from __future__ import annotations

from pathlib import Path

import pytest

from trw_mcp.state.index_sync import (
    INDEX_CATALOGUE_END,
    INDEX_CATALOGUE_START,
    ROADMAP_CATALOGUE_END,
    ROADMAP_CATALOGUE_START,
    PRDEntry,
    _INDEX_SUMMARY_RE,
    _ROADMAP_TOTAL_RE,
    _build_index_stats,
    _build_roadmap_stats,
    _group_by_status,
    _merge_section,
    _stats_parts,
    _update_header_stats,
    render_index_catalogue,
    render_roadmap_catalogue,
    scan_prd_frontmatters,
    sync_index_md,
    sync_roadmap_md,
)

# --- Fixtures ---


@pytest.fixture()
def prds_dir(tmp_path: Path) -> Path:
    """Create a temp prds directory with sample PRD files."""
    d = tmp_path / "prds"
    d.mkdir()

    # Done PRD
    (d / "PRD-CORE-001.md").write_text(
        "---\nprd:\n  id: PRD-CORE-001\n  title: Base MCP tool suite\n"
        "  status: done\n  priority: P0\n  category: CORE\n---\n# Content\n",
        encoding="utf-8",
    )
    # Review PRD
    (d / "PRD-CORE-009.md").write_text(
        "---\nprd:\n  id: PRD-CORE-009\n  title: Phase gate enforcement\n"
        "  status: review\n  priority: P1\n  category: CORE\n---\n# Content\n",
        encoding="utf-8",
    )
    # Draft PRD
    (d / "PRD-CORE-018.md").write_text(
        "---\nprd:\n  id: PRD-CORE-018\n  title: Auto-sync INDEX.md\n"
        "  status: draft\n  priority: P1\n  category: CORE\n---\n# Content\n",
        encoding="utf-8",
    )
    # Merged PRD
    (d / "PRD-FIX-002.md").write_text(
        "---\nprd:\n  id: PRD-FIX-002\n  title: Prune heuristic\n"
        "  status: merged\n  priority: P2\n  category: FIX\n---\n# Content\n",
        encoding="utf-8",
    )
    return d


# --- scan_prd_frontmatters ---


class TestScanPrdFrontmatters:
    """PRD-CORE-018-FR01: Scan PRD files and extract metadata."""

    def test_scans_all_prd_files(self, prds_dir: Path) -> None:
        entries = scan_prd_frontmatters(prds_dir)
        assert len(entries) == 4

    def test_extracts_correct_fields(self, prds_dir: Path) -> None:
        entries = scan_prd_frontmatters(prds_dir)
        core_001 = next(e for e in entries if e.id == "PRD-CORE-001")
        assert core_001.title == "Base MCP tool suite"
        assert core_001.priority == "P0"
        assert core_001.status == "done"
        assert core_001.category == "CORE"

    def test_sorted_by_filename(self, prds_dir: Path) -> None:
        entries = scan_prd_frontmatters(prds_dir)
        ids = [e.id for e in entries]
        assert ids == sorted(ids)

    def test_empty_directory(self, tmp_path: Path) -> None:
        empty = tmp_path / "empty"
        empty.mkdir()
        assert scan_prd_frontmatters(empty) == []

    def test_nonexistent_directory(self, tmp_path: Path) -> None:
        assert scan_prd_frontmatters(tmp_path / "nope") == []

    def test_skips_malformed_files(self, tmp_path: Path) -> None:
        d = tmp_path / "prds"
        d.mkdir()
        (d / "PRD-BAD-001.md").write_text("no frontmatter here", encoding="utf-8")
        (d / "PRD-GOOD-001.md").write_text(
            "---\nprd:\n  id: PRD-GOOD-001\n  title: Good\n"
            "  status: draft\n  priority: P1\n  category: CORE\n---\n",
            encoding="utf-8",
        )
        entries = scan_prd_frontmatters(d)
        assert len(entries) == 1
        assert entries[0].id == "PRD-GOOD-001"

    def test_skips_template_file(self, tmp_path: Path) -> None:
        """TEMPLATE.md should not be scanned (doesn't match PRD-*.md)."""
        d = tmp_path / "prds"
        d.mkdir()
        (d / "TEMPLATE.md").write_text("---\nprd:\n  id: TEMPLATE\n---\n", encoding="utf-8")
        assert scan_prd_frontmatters(d) == []


# --- _group_by_status ---


class TestGroupByStatus:
    """PRD-CORE-018-FR02: Status grouping logic."""

    def test_groups_done(self) -> None:
        entries = [
            PRDEntry(id="X", title="", priority="P0", status="done", category="CORE"),
            PRDEntry(id="Y", title="", priority="P1", status="implemented", category="CORE"),
        ]
        groups = _group_by_status(entries)
        assert len(groups["done"]) == 2

    def test_groups_review(self) -> None:
        entries = [
            PRDEntry(id="A", title="", priority="P1", status="review", category="CORE"),
            PRDEntry(id="B", title="", priority="P2", status="approved", category="QUAL"),
        ]
        groups = _group_by_status(entries)
        assert len(groups["review"]) == 2

    def test_groups_merged(self) -> None:
        entries = [PRDEntry(id="M", title="", priority="P2", status="merged", category="FIX")]
        groups = _group_by_status(entries)
        assert len(groups["merged"]) == 1

    def test_groups_draft(self) -> None:
        entries = [
            PRDEntry(id="D", title="", priority="P1", status="draft", category="CORE"),
        ]
        groups = _group_by_status(entries)
        assert len(groups["draft"]) == 1

    def test_groups_deprecated(self) -> None:
        entries = [
            PRDEntry(id="E", title="", priority="P3", status="deprecated", category="FIX"),
        ]
        groups = _group_by_status(entries)
        assert len(groups["deprecated"]) == 1
        assert len(groups["draft"]) == 0


# --- render_index_catalogue ---


class TestRenderIndexCatalogue:
    """PRD-CORE-018-FR03: INDEX.md catalogue rendering."""

    def test_includes_markers(self, prds_dir: Path) -> None:
        entries = scan_prd_frontmatters(prds_dir)
        result = render_index_catalogue(entries)
        assert INDEX_CATALOGUE_START in result
        assert INDEX_CATALOGUE_END in result

    def test_includes_all_sections(self, prds_dir: Path) -> None:
        entries = scan_prd_frontmatters(prds_dir)
        result = render_index_catalogue(entries)
        assert "### Done" in result
        assert "### Merged" in result
        assert "### Review" in result
        assert "### Draft" in result

    def test_correct_counts(self, prds_dir: Path) -> None:
        entries = scan_prd_frontmatters(prds_dir)
        result = render_index_catalogue(entries)
        assert "4 total" in result
        assert "1 done" in result
        assert "1 merged" in result
        assert "1 review" in result
        assert "1 draft" in result

    def test_prd_ids_in_tables(self, prds_dir: Path) -> None:
        entries = scan_prd_frontmatters(prds_dir)
        result = render_index_catalogue(entries)
        assert "PRD-CORE-001" in result
        assert "PRD-CORE-009" in result
        assert "PRD-CORE-018" in result
        assert "PRD-FIX-002" in result

    def test_empty_entries(self) -> None:
        result = render_index_catalogue([])
        assert "0 total" in result
        assert INDEX_CATALOGUE_START in result


# --- render_roadmap_catalogue ---


class TestRenderRoadmapCatalogue:
    """PRD-CORE-018-FR04: ROADMAP.md catalogue rendering."""

    def test_includes_markers(self, prds_dir: Path) -> None:
        entries = scan_prd_frontmatters(prds_dir)
        result = render_roadmap_catalogue(entries)
        assert ROADMAP_CATALOGUE_START in result
        assert ROADMAP_CATALOGUE_END in result

    def test_done_entries_bold(self, prds_dir: Path) -> None:
        entries = scan_prd_frontmatters(prds_dir)
        result = render_roadmap_catalogue(entries)
        assert "**Done**" in result

    def test_sorted_by_status_then_id(self, prds_dir: Path) -> None:
        entries = scan_prd_frontmatters(prds_dir)
        result = render_roadmap_catalogue(entries)
        lines = result.split("\n")
        table_rows = [line for line in lines if line.startswith("| PRD-")]
        # Done first, then merged, then review, then draft
        assert "PRD-CORE-001" in table_rows[0]  # done
        assert "PRD-FIX-002" in table_rows[1]  # merged


# --- _merge_section ---


class TestMergeSection:
    """PRD-CORE-018-FR05: Marker-based merge preserves surrounding content."""

    def test_replaces_between_markers(self) -> None:
        content = "# Header\n\n<!-- start -->\nold content\n<!-- end -->\n\n## Footer\n"
        result = _merge_section(content, "<!-- start -->\nnew\n<!-- end -->", "<!-- start -->", "<!-- end -->")
        assert "new" in result
        assert "old content" not in result
        assert "# Header" in result
        assert "## Footer" in result

    def test_appends_when_no_markers(self) -> None:
        content = "# Header\n\nSome text\n"
        result = _merge_section(content, "<!-- s -->\ninserted\n<!-- e -->", "<!-- s -->", "<!-- e -->")
        assert "# Header" in result
        assert "inserted" in result

    def test_empty_content(self) -> None:
        result = _merge_section("", "<!-- s -->\nnew\n<!-- e -->", "<!-- s -->", "<!-- e -->")
        assert "new" in result


# --- sync_index_md ---


class TestSyncIndexMd:
    """PRD-CORE-018-FR06: Full INDEX.md sync integration."""

    def test_creates_index_when_missing(self, tmp_path: Path, prds_dir: Path) -> None:
        index_path = tmp_path / "INDEX.md"
        result = sync_index_md(index_path, prds_dir)
        assert index_path.exists()
        assert result["total_prds"] == 4
        content = index_path.read_text(encoding="utf-8")
        assert INDEX_CATALOGUE_START in content

    def test_updates_existing_index(self, tmp_path: Path, prds_dir: Path) -> None:
        index_path = tmp_path / "INDEX.md"
        header = "# My Index\n\nManual content here.\n\n"
        footer = "\n## Prompts\n\nSome prompts.\n"
        index_path.write_text(
            header + INDEX_CATALOGUE_START + "\nold\n" + INDEX_CATALOGUE_END + footer,
            encoding="utf-8",
        )
        sync_index_md(index_path, prds_dir)
        content = index_path.read_text(encoding="utf-8")
        assert "# My Index" in content
        assert "Manual content here." in content
        assert "## Prompts" in content
        assert "PRD-CORE-001" in content
        assert "old" not in content

    def test_returns_correct_counts(self, tmp_path: Path, prds_dir: Path) -> None:
        index_path = tmp_path / "INDEX.md"
        result = sync_index_md(index_path, prds_dir)
        assert result["done"] == 1
        assert result["merged"] == 1
        assert result["review"] == 1
        assert result["draft"] == 1


# --- sync_roadmap_md ---


class TestSyncRoadmapMd:
    """PRD-CORE-018-FR07: Full ROADMAP.md sync integration."""

    def test_creates_roadmap_when_missing(self, tmp_path: Path, prds_dir: Path) -> None:
        roadmap_path = tmp_path / "ROADMAP.md"
        result = sync_roadmap_md(roadmap_path, prds_dir)
        assert roadmap_path.exists()
        assert result["total_prds"] == 4

    def test_preserves_sprint_details(self, tmp_path: Path, prds_dir: Path) -> None:
        roadmap_path = tmp_path / "ROADMAP.md"
        content = (
            "# Roadmap\n\n"
            + ROADMAP_CATALOGUE_START + "\nold table\n" + ROADMAP_CATALOGUE_END
            + "\n\n## Sprint 1\n\nDetailed sprint info.\n"
        )
        roadmap_path.write_text(content, encoding="utf-8")
        sync_roadmap_md(roadmap_path, prds_dir)
        updated = roadmap_path.read_text(encoding="utf-8")
        assert "# Roadmap" in updated
        assert "## Sprint 1" in updated
        assert "Detailed sprint info." in updated
        assert "PRD-CORE-001" in updated
        assert "old table" not in updated

    def test_idempotent(self, tmp_path: Path, prds_dir: Path) -> None:
        roadmap_path = tmp_path / "ROADMAP.md"
        sync_roadmap_md(roadmap_path, prds_dir)
        first = roadmap_path.read_text(encoding="utf-8")
        sync_roadmap_md(roadmap_path, prds_dir)
        second = roadmap_path.read_text(encoding="utf-8")
        assert first == second


# --- _build_stats_summary ---


class TestStatsParts:
    """Header stats string builder."""

    def test_basic_parts(self) -> None:
        groups = _group_by_status([
            PRDEntry(id="A", title="", priority="P0", status="done", category="C"),
            PRDEntry(id="B", title="", priority="P1", status="draft", category="C"),
        ])
        parts = _stats_parts(groups)
        assert parts == ["1 done", "1 draft"]

    def test_all_statuses(self) -> None:
        groups = _group_by_status([
            PRDEntry(id="A", title="", priority="P0", status="done", category="C"),
            PRDEntry(id="B", title="", priority="P1", status="merged", category="C"),
            PRDEntry(id="C", title="", priority="P2", status="deprecated", category="C"),
            PRDEntry(id="D", title="", priority="P1", status="review", category="C"),
            PRDEntry(id="E", title="", priority="P1", status="draft", category="C"),
        ])
        parts = _stats_parts(groups)
        assert "1 done" in parts
        assert "1 merged" in parts
        assert "1 deprecated" in parts
        assert "1 review" in parts
        assert "1 draft" in parts


class TestBuildIndexStats:
    """INDEX.md format: ``(N total: X done, ...)``."""

    def test_format(self) -> None:
        groups = _group_by_status([
            PRDEntry(id="A", title="", priority="P0", status="done", category="C"),
            PRDEntry(id="B", title="", priority="P1", status="draft", category="C"),
        ])
        result = _build_index_stats(groups, 2)
        assert result == "(2 total: 1 done, 1 draft)"


class TestBuildRoadmapStats:
    """ROADMAP.md format: ``N (X done, ...)``."""

    def test_format(self) -> None:
        groups = _group_by_status([
            PRDEntry(id="A", title="", priority="P0", status="done", category="C"),
            PRDEntry(id="B", title="", priority="P1", status="draft", category="C"),
        ])
        result = _build_roadmap_stats(groups, 2)
        assert result == "2 (1 done, 1 draft)"


# --- _update_header_stats ---


class TestUpdateHeaderStats:
    """Header stats line replacement outside catalogue markers."""

    def test_updates_index_summary_line(self) -> None:
        content = "# Index\n\n## Summary (10 total: 5 done, 5 draft)\n\nMore text."
        groups = _group_by_status([
            PRDEntry(id="A", title="", priority="P0", status="done", category="C"),
            PRDEntry(id="B", title="", priority="P1", status="draft", category="C"),
        ])
        result = _update_header_stats(
            content, groups, 2, _INDEX_SUMMARY_RE, index_format=True,
        )
        assert "## Summary (2 total: 1 done, 1 draft)" in result
        assert "More text." in result

    def test_updates_roadmap_total_line(self) -> None:
        content = "# Roadmap\n\n**PRD Total**: 100 (50 done, 50 draft)\n\nSprints."
        groups = _group_by_status([
            PRDEntry(id="A", title="", priority="P0", status="done", category="C"),
        ])
        result = _update_header_stats(content, groups, 1, _ROADMAP_TOTAL_RE)
        assert "**PRD Total**: 1 (1 done, 0 draft)" in result
        assert "Sprints." in result

    def test_no_match_returns_unchanged(self) -> None:
        content = "# Header\n\nNo stats here."
        groups = _group_by_status([])
        result = _update_header_stats(content, groups, 0, _INDEX_SUMMARY_RE)
        assert result == content


# --- sync_index_md header stats ---


class TestSyncIndexMdHeaderStats:
    """Verify sync_index_md updates header stats outside markers."""

    def test_updates_summary_line(self, tmp_path: Path, prds_dir: Path) -> None:
        index_path = tmp_path / "INDEX.md"
        content = (
            "# Index\n\n## Summary (0 total: 0 done, 0 draft)\n\n"
            + INDEX_CATALOGUE_START + "\nold\n" + INDEX_CATALOGUE_END
        )
        index_path.write_text(content, encoding="utf-8")
        sync_index_md(index_path, prds_dir)
        updated = index_path.read_text(encoding="utf-8")
        assert "## Summary (4 total: 1 done," in updated
        assert "0 total" not in updated


class TestSyncRoadmapMdHeaderStats:
    """Verify sync_roadmap_md updates header stats outside markers."""

    def test_updates_prd_total_line(self, tmp_path: Path, prds_dir: Path) -> None:
        roadmap_path = tmp_path / "ROADMAP.md"
        content = (
            "# Roadmap\n\n**PRD Total**: 0 (0 done, 0 draft)\n\n"
            + ROADMAP_CATALOGUE_START + "\nold\n" + ROADMAP_CATALOGUE_END
        )
        roadmap_path.write_text(content, encoding="utf-8")
        sync_roadmap_md(roadmap_path, prds_dir)
        updated = roadmap_path.read_text(encoding="utf-8")
        assert "**PRD Total**: 4 (1 done," in updated
        assert "0 total" not in updated
