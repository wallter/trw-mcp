"""Framework quality validation tests for FRAMEWORK.md v18.0.

Validates emphasis counts, XML structure, document integrity,
and readability metrics for the bundled framework copy.
"""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

import pytest

# Path to the bundled framework copy (authoritative for tests)
FRAMEWORK_PATH = Path(__file__).parent.parent / "src" / "trw_mcp" / "data" / "framework.md"


@pytest.fixture(scope="module")
def framework_text() -> str:
    """Load the bundled framework.md content."""
    return FRAMEWORK_PATH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def framework_lines(framework_text: str) -> list[str]:
    """Split framework into lines."""
    return framework_text.splitlines()


class TestEmphasisAudit:
    """Validates emphasis usage follows RFC 2119 conventions."""

    def test_must_count_within_budget(self, framework_text: str) -> None:
        """Total MUST keyword count within budget."""
        must_count = len(re.findall(r"\bMUST\b", framework_text))
        # Budget: obligation tables + safety rails + MUST NOT pairs
        assert must_count <= 75, f"MUST count {must_count} exceeds budget of 75"

    def test_rfc_terms_used_correctly(self, framework_text: str) -> None:
        """RFC 2119 terms appear in ALL CAPS only (no mixed-case 'Must' or 'Should')."""
        # Look for mixed-case RFC terms that should be either lowercase or ALL CAPS
        mixed_must = re.findall(r"\bMust\b", framework_text)
        mixed_shall = re.findall(r"\bShall\b", framework_text)
        assert len(mixed_must) == 0, f"Found {len(mixed_must)} mixed-case 'Must' (should be MUST or must)"
        assert len(mixed_shall) == 0, f"Found {len(mixed_shall)} mixed-case 'Shall' (should be SHALL or shall)"

    def test_design_principles_present(self, framework_text: str) -> None:
        """Design principles section exists with all three principles."""
        assert "Behavioral > Structural" in framework_text
        assert "Prevention > Detection" in framework_text
        assert "External > Internal" in framework_text

    def test_formation_scope_documented(self, framework_text: str) -> None:
        """Formations scope rule is documented."""
        assert "Formation scope:" in framework_text or "Formations are wave-scoped" in framework_text

    def test_why_markers_present(self, framework_text: str) -> None:
        """At least 1 inline WHY: marker in the document."""
        why_count = len(re.findall(r"WHY:", framework_text))
        assert why_count >= 1, f"WHY marker count {why_count} — expected at least 1"

    def test_adaptive_planning_documented(self, framework_text: str) -> None:
        """Adaptive planning section documents plan.md update rules."""
        assert "ADAPTIVE PLANNING" in framework_text
        assert "reports/plan.md" in framework_text


class TestXMLStructure:
    """Validates XML tag structure and conventions."""

    def test_all_tags_properly_closed(self, framework_text: str) -> None:
        """All XML tags have matching close tags."""
        # Remove code blocks
        cleaned = re.sub(r"```[\s\S]*?```", "", framework_text)
        # Remove HTML comments
        cleaned = re.sub(r"<!--.*?-->", "", cleaned)
        # Remove inline backtick-quoted tags
        cleaned = re.sub(r"`[^`]*`", "", cleaned)
        opening = re.findall(r"<([a-z][a-z_]*)(?:\s[^>]*)?>", cleaned)
        closing = re.findall(r"</([a-z][a-z_]*)>", cleaned)

        open_counts = Counter(opening)
        close_counts = Counter(closing)

        for tag in open_counts:
            assert tag in close_counts, f"Tag <{tag}> opened but never closed"
            assert open_counts[tag] == close_counts[tag], (
                f"Tag <{tag}> opened {open_counts[tag]}x but closed {close_counts[tag]}x"
            )

    def test_key_semantic_tags_present(self, framework_text: str) -> None:
        """Key semantic wrapper tags exist in the document."""
        required_tags = [
            "critical",
            "design_principles",
            "standards",
            "variables",
            "critical_files",
            "phase_transitions",
            "mcp_check",
            "patterns",
            "mandatory_triggers",
            "mandatory_reads",
        ]
        for tag in required_tags:
            assert f"<{tag}>" in framework_text, f"Missing semantic tag <{tag}>"
            assert f"</{tag}>" in framework_text, f"Missing closing tag </{tag}>"

    def test_max_nesting_depth_three(self, framework_text: str) -> None:
        """XML nesting does not exceed depth 3."""
        # Remove code blocks
        cleaned = re.sub(r"```[\s\S]*?```", "", framework_text)
        # Remove HTML comments
        cleaned = re.sub(r"<!--.*?-->", "", cleaned)
        # Remove inline backtick-quoted tags
        cleaned = re.sub(r"`[^`]*`", "", cleaned)

        depth = 0
        max_depth = 0
        for match in re.finditer(r"<(/?)([a-z][a-z_]*)(?:\s[^>]*)?>", cleaned):
            is_closing = match.group(1) == "/"
            if is_closing:
                depth = max(0, depth - 1)
            else:
                depth += 1
                max_depth = max(max_depth, depth)

        assert max_depth <= 3, f"XML nesting depth {max_depth} exceeds maximum of 3"

    def test_snake_case_tag_names(self, framework_text: str) -> None:
        """All XML tags use snake_case naming."""
        # Remove code blocks
        cleaned = re.sub(r"```[\s\S]*?```", "", framework_text)
        # Remove HTML comments
        cleaned = re.sub(r"<!--.*?-->", "", cleaned)
        tags = re.findall(r"</?([a-zA-Z][a-zA-Z_0-9]*)(?:\s[^>]*)?>", cleaned)
        for tag in tags:
            if tag in ("br", "hr"):
                continue
            assert tag == tag.lower(), f"Tag '{tag}' is not lowercase"
            assert "-" not in tag, f"Tag '{tag}' uses kebab-case instead of snake_case"

    def test_meta_files_tags_have_format(self, framework_text: str) -> None:
        """meta_files tags include format attribute."""
        yaml_meta = re.findall(r'<meta_files\s+format="yaml">', framework_text)
        jsonl_meta = re.findall(r'<meta_files\s+format="jsonl">', framework_text)
        assert len(yaml_meta) == 1, "Expected one <meta_files format=\"yaml\"> tag"
        assert len(jsonl_meta) == 1, "Expected one <meta_files format=\"jsonl\"> tag"


class TestDocumentLayout:
    """Validates document layout and structural conventions."""

    def test_variables_block_exists(self, framework_text: str) -> None:
        """Variables block is present in the document."""
        assert "<variables>" in framework_text
        assert "TASK" in framework_text
        assert "RUN_ROOT" in framework_text
        assert "ORC" in framework_text

    def test_version_header_present(self, framework_text: str) -> None:
        """Version and date appear in the document header."""
        assert "Version date:" in framework_text
        assert "v18.0_TRW" in framework_text

    def test_execution_model_summary_near_top(self, framework_text: str) -> None:
        """Execution model summary appears in the first 5% of the document."""
        summary_pos = framework_text.find("## EXECUTION MODEL SUMMARY")
        assert summary_pos != -1, "Execution model summary not found"
        total_len = len(framework_text)
        position_pct = summary_pos / total_len
        assert position_pct <= 0.05, (
            f"Execution model summary at {position_pct:.1%}, should be <= 5%"
        )

    def test_model_section_near_end(self, framework_text: str) -> None:
        """MODEL section appears in the last 25% of the document."""
        model_pos = framework_text.find("## MODEL")
        assert model_pos != -1, "MODEL section not found"
        total_len = len(framework_text)
        position_pct = model_pos / total_len
        assert position_pct >= 0.75, (
            f"MODEL section at {position_pct:.1%}, should be >= 75%"
        )


class TestDocumentIntegrity:
    """Validates overall document structure and content integrity."""

    def test_required_h2_sections_present(self, framework_text: str) -> None:
        """All required H2 sections exist."""
        required_sections = [
            "## EXECUTION MODEL SUMMARY",
            "## DEFAULTS",
            "## CONFIDENCE",
            "## PERSISTENCE",
            "## PHASES",
            "## FORMATIONS",
            "## GATES",
            "## BOOTSTRAP",
            "## MCP TOOLS",
            "## FILE STRUCTURE",
            "## EXPLORATION & PLANNING SHARDS",
            "## WAVE ORCHESTRATION",
            "## OUTPUT CONTRACTS",
            "## SELF-DIRECTING SHARDS",
            "## ADAPTIVE PLANNING",
            "## PARALLELISM",
            "## REQUIREMENTS",
            "## TDD & CODE QUALITY",
            "## TESTING STRATEGY",
            "## TOOL RETRY",
            "## ERROR HANDLING",
            "## GIT",
            "## TURN HYGIENE",
            "## MODEL",
            "## TODO REGISTRY",
            "## SELF-IMPROVEMENT & LEARNING",
            "## ARTIFACT & PROMPT PATTERNS",
            "## FRAMEWORK ADHERENCE",
            "## RISK REGISTRY",
            "## QOL CHANGES",
        ]
        for section in required_sections:
            assert section in framework_text, f"Missing required section: {section}"

    def test_line_count_in_range(self, framework_lines: list[str]) -> None:
        """Document line count between 900-1100."""
        line_count = len(framework_lines)
        assert 900 <= line_count <= 1100, f"Line count {line_count} outside range 900-1100"

    def test_rfc_declaration_present(self, framework_text: str) -> None:
        """RFC 2119 declaration is present."""
        assert "RFC 2119" in framework_text
        assert "MUST, MUST NOT" in framework_text

    def test_no_todo_markers(self, framework_text: str) -> None:
        """No unresolved TODO markers in the document."""
        lines = framework_text.splitlines()
        in_todo_section = False
        for line in lines:
            if "## TODO REGISTRY" in line:
                in_todo_section = True
            elif line.startswith("## ") and in_todo_section:
                in_todo_section = False
            if not in_todo_section and "TODO:" in line:
                stripped = line.strip()
                if not stripped.startswith("```") and not stripped.startswith("#"):
                    assert False, f"Unresolved TODO marker found: {line.strip()}"

    def test_version_is_v18_0(self, framework_text: str) -> None:
        """Version stamp is v18.0_TRW."""
        assert "v18.0_TRW" in framework_text

    def test_phase_sequence_documented(self, framework_text: str) -> None:
        """The six-phase sequence is documented."""
        assert "RESEARCH" in framework_text
        assert "PLAN" in framework_text
        assert "IMPLEMENT" in framework_text
        assert "VALIDATE" in framework_text
        assert "REVIEW" in framework_text
        assert "DELIVER" in framework_text

    def test_mcp_tools_table_present(self, framework_text: str) -> None:
        """MCP tools obligation table is present."""
        assert "trw_init" in framework_text
        assert "trw_status" in framework_text
        assert "trw_phase_check" in framework_text
        assert "trw_reflect" in framework_text
        assert "trw_claude_md_sync" in framework_text


class TestReadability:
    """Validates readability metrics."""

    def test_no_extremely_long_lines(self, framework_lines: list[str]) -> None:
        """No lines exceed 400 characters (allows for tables, inline code, and shard card fields)."""
        for i, line in enumerate(framework_lines, 1):
            assert len(line) <= 400, (
                f"Line {i} has {len(line)} chars (max 400): {line[:80]}..."
            )

    def test_average_line_length(self, framework_lines: list[str]) -> None:
        """Average non-empty line length between 20-150 chars."""
        non_empty = [line for line in framework_lines if line.strip()]
        if not non_empty:
            pytest.skip("No non-empty lines")
        avg_len = sum(len(line) for line in non_empty) / len(non_empty)
        assert 20 <= avg_len <= 150, f"Average line length {avg_len:.1f} outside range 20-150"

    def test_balanced_section_sizes(self, framework_text: str) -> None:
        """No H2 section exceeds 25% of total document."""
        sections = re.split(r"\n## ", framework_text)
        total_len = len(framework_text)
        for section in sections[1:]:  # Skip content before first H2
            section_name = section.split("\n")[0]
            section_pct = len(section) / total_len
            assert section_pct <= 0.25, (
                f"Section '{section_name}' is {section_pct:.1%} of document (max 25%)"
            )
