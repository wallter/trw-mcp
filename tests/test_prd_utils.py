"""Tests for trw_mcp.state.prd_utils — PRD-FIX-006.

Tests cover 5 public functions:
  - parse_frontmatter
  - extract_sections
  - compute_content_density
  - extract_prd_refs
  - update_frontmatter
"""

from __future__ import annotations

from pathlib import Path

import pytest

from trw_mcp.exceptions import StateError
from trw_mcp.state.prd_utils import (
    compute_content_density,
    extract_prd_refs,
    extract_sections,
    parse_frontmatter,
    update_frontmatter,
)

# ---------- parse_frontmatter ----------


class TestParseFrontmatter:
    """Tests for parse_frontmatter()."""

    def test_valid_yaml_returns_dict(self) -> None:
        content = "---\nid: PRD-CORE-001\ntitle: Test\n---\n\n# Body"
        result = parse_frontmatter(content)
        assert result["id"] == "PRD-CORE-001"
        assert result["title"] == "Test"

    def test_nested_prd_key_flattened(self) -> None:
        content = "---\nprd:\n  id: PRD-FIX-006\n  title: Extract\n---\n\n# Body"
        result = parse_frontmatter(content)
        assert result["id"] == "PRD-FIX-006"
        assert result["title"] == "Extract"
        assert "prd" not in result

    def test_nested_prd_preserves_other_top_level(self) -> None:
        content = "---\nprd:\n  id: PRD-CORE-001\nextra: value\n---\n\n# Body"
        result = parse_frontmatter(content)
        assert result["id"] == "PRD-CORE-001"
        assert result["extra"] == "value"

    def test_no_frontmatter_returns_empty(self) -> None:
        content = "# Just a heading\n\nSome text"
        result = parse_frontmatter(content)
        assert result == {}

    def test_malformed_yaml_returns_empty(self) -> None:
        content = "---\n: invalid: yaml: [broken\n---\n\n# Body"
        result = parse_frontmatter(content)
        assert result == {}

    def test_empty_string_returns_empty(self) -> None:
        result = parse_frontmatter("")
        assert result == {}

    def test_frontmatter_with_lists(self) -> None:
        content = "---\nid: PRD-CORE-001\ntags:\n  - core\n  - mcp\n---\n"
        result = parse_frontmatter(content)
        assert result["tags"] == ["core", "mcp"]


# ---------- extract_sections ----------


class TestExtractSections:
    """Tests for extract_sections()."""

    def test_standard_12_sections(self) -> None:
        sections_md = "\n".join(
            f"## {i}. Section {i}\n\nContent for section {i}."
            for i in range(1, 13)
        )
        result = extract_sections(sections_md)
        assert len(result) == 12

    def test_no_numbered_headings_returns_empty(self) -> None:
        content = "# Title\n\n## Appendix\n\n### Sub"
        result = extract_sections(content)
        assert result == []

    def test_partial_sections(self) -> None:
        content = "## 1. Problem Statement\n\nText.\n\n## 4. Functional Requirements\n"
        result = extract_sections(content)
        assert result == ["Problem Statement", "Functional Requirements"]

    def test_ignores_unnumbered_h2(self) -> None:
        content = "## Appendix\n\n## Quality Checklist\n\n## 1. Problem Statement\n"
        result = extract_sections(content)
        assert result == ["Problem Statement"]

    def test_ignores_h3_headings(self) -> None:
        content = "### 1. Sub Heading\n\n## 1. Problem Statement\n"
        result = extract_sections(content)
        assert result == ["Problem Statement"]


# ---------- compute_content_density ----------


class TestComputeContentDensity:
    """Tests for compute_content_density()."""

    def test_fully_written_high_density(self) -> None:
        content = "\n".join([
            "This is a substantive line.",
            "Another real content line with details.",
            "Technical specification: 500ms latency target.",
            "The module handles all edge cases explicitly.",
            "Users authenticate via JWT tokens.",
        ])
        density = compute_content_density(content)
        assert density > 0.6

    def test_template_placeholders_low_density(self) -> None:
        content = "\n".join([
            "---",
            "<!-- Goal 1 -->",
            "<!-- Goal 2 -->",
            "",
            "---",
            "<!-- Describe the problem -->",
            "",
            "<!-- Impact details -->",
            "",
            "---",
        ])
        density = compute_content_density(content)
        assert density < 0.4

    def test_empty_string_returns_zero(self) -> None:
        assert compute_content_density("") == 0.0

    def test_mixed_content(self) -> None:
        content = "\n".join([
            "## 1. Problem Statement",
            "",
            "The system has a critical bug.",
            "<!-- More detail needed -->",
            "",
            "Users are affected when they login.",
            "---",
        ])
        density = compute_content_density(content)
        assert 0.2 < density < 0.6

    def test_table_separators_non_substantive(self) -> None:
        content = "\n".join([
            "| Header | Value |",
            "|--------|-------|",
            "| data   | 42    |",
        ])
        density = compute_content_density(content)
        # 2 substantive (header row + data row), 1 non-substantive (separator)
        assert 0.5 < density < 0.8


# ---------- extract_prd_refs ----------


class TestExtractPrdRefs:
    """Tests for extract_prd_refs()."""

    def test_multiple_refs_deduplicated_sorted(self) -> None:
        content = "Depends on PRD-CORE-007 and PRD-FIX-006, also PRD-CORE-007 again."
        result = extract_prd_refs(content)
        assert result == ["PRD-CORE-007", "PRD-FIX-006"]

    def test_no_refs_returns_empty(self) -> None:
        content = "This document has no PRD references."
        result = extract_prd_refs(content)
        assert result == []

    def test_various_categories(self) -> None:
        content = "PRD-CORE-001 PRD-FIX-003 PRD-QUAL-005 PRD-INFRA-001"
        result = extract_prd_refs(content)
        assert result == [
            "PRD-CORE-001", "PRD-FIX-003", "PRD-INFRA-001", "PRD-QUAL-005",
        ]

    def test_refs_in_markdown_context(self) -> None:
        content = "| DEP-001 | PRD-FIX-006 | Pending | Yes |\n- Depends on: `PRD-CORE-008`"
        result = extract_prd_refs(content)
        assert "PRD-CORE-008" in result
        assert "PRD-FIX-006" in result

    def test_single_ref(self) -> None:
        content = "Implements PRD-CORE-004."
        result = extract_prd_refs(content)
        assert result == ["PRD-CORE-004"]


# ---------- update_frontmatter ----------


class TestUpdateFrontmatter:
    """Tests for update_frontmatter()."""

    def test_updates_single_field(self, tmp_path: Path) -> None:
        prd_file = tmp_path / "PRD-TEST-001.md"
        prd_file.write_text(
            "---\nprd:\n  id: PRD-TEST-001\n  status: draft\n  title: Test\n---\n\n# Body\n",
            encoding="utf-8",
        )
        update_frontmatter(prd_file, {"status": "approved"})
        result = parse_frontmatter(prd_file.read_text(encoding="utf-8"))
        assert result["status"] == "approved"
        assert result["id"] == "PRD-TEST-001"
        assert result["title"] == "Test"

    def test_updates_nested_field(self, tmp_path: Path) -> None:
        prd_file = tmp_path / "PRD-TEST-002.md"
        prd_file.write_text(
            "---\nprd:\n  id: PRD-TEST-002\n  dates:\n    created: '2026-01-01'\n    updated: '2026-01-01'\n---\n\n# Body\n",
            encoding="utf-8",
        )
        update_frontmatter(prd_file, {"dates": {"updated": "2026-02-08"}})
        result = parse_frontmatter(prd_file.read_text(encoding="utf-8"))
        # dates.updated changed
        dates = result.get("dates", {})
        assert isinstance(dates, dict)
        assert str(dates.get("updated")) == "2026-02-08"
        # dates.created preserved
        assert str(dates.get("created")) == "2026-01-01"

    def test_raises_for_nonexistent_file(self, tmp_path: Path) -> None:
        missing = tmp_path / "DOES_NOT_EXIST.md"
        with pytest.raises(StateError, match="not found"):
            update_frontmatter(missing, {"status": "approved"})

    def test_raises_for_no_frontmatter(self, tmp_path: Path) -> None:
        prd_file = tmp_path / "NO_FM.md"
        prd_file.write_text("# No frontmatter here\n", encoding="utf-8")
        with pytest.raises(StateError, match="No YAML frontmatter"):
            update_frontmatter(prd_file, {"status": "approved"})

    def test_preserves_body_content(self, tmp_path: Path) -> None:
        body = "\n# PRD Body\n\n## 1. Problem Statement\n\nImportant content.\n"
        prd_file = tmp_path / "PRD-TEST-003.md"
        prd_file.write_text(
            f"---\nprd:\n  id: PRD-TEST-003\n  status: draft\n---\n{body}",
            encoding="utf-8",
        )
        update_frontmatter(prd_file, {"status": "review"})
        new_content = prd_file.read_text(encoding="utf-8")
        assert "Important content." in new_content
        assert "## 1. Problem Statement" in new_content

    def test_atomic_write_no_corruption(self, tmp_path: Path) -> None:
        """Verify original is intact if we read after a successful update."""
        prd_file = tmp_path / "PRD-TEST-004.md"
        original = "---\nprd:\n  id: PRD-TEST-004\n  status: draft\n---\n\n# Body\n"
        prd_file.write_text(original, encoding="utf-8")
        update_frontmatter(prd_file, {"status": "review"})
        content = prd_file.read_text(encoding="utf-8")
        # File should be valid — parseable frontmatter
        fm = parse_frontmatter(content)
        assert fm["status"] == "review"
        assert fm["id"] == "PRD-TEST-004"

    def test_top_level_frontmatter_without_prd_key(self, tmp_path: Path) -> None:
        prd_file = tmp_path / "PRD-TEST-005.md"
        prd_file.write_text(
            "---\nid: PRD-TEST-005\nstatus: draft\n---\n\n# Body\n",
            encoding="utf-8",
        )
        update_frontmatter(prd_file, {"status": "approved"})
        result = parse_frontmatter(prd_file.read_text(encoding="utf-8"))
        assert result["status"] == "approved"
