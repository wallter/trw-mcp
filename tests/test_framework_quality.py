"""Framework quality validation tests for FRAMEWORK.md v18.1.

Validates emphasis counts, XML structure, cache layout, document integrity,
and readability metrics per PRD-QUAL-004.
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
    """Validates emphasis reduction per FR01, FR02, FR03, FR04."""

    def test_must_count_within_budget(self, framework_text: str) -> None:
        """FR01: Total MUST keyword count within budget."""
        # Count standalone MUST (includes justified RFC 2119 uses in tables/obligations)
        must_count = len(re.findall(r"\bMUST\b", framework_text))
        # Budget allows ~50: 22 justified safety rails + obligation tables + MUST NOT pairs
        assert must_count <= 50, f"MUST count {must_count} exceeds budget of 50"

    def test_no_forbidden_all_caps(self, framework_text: str) -> None:
        """FR01: No non-RFC ALL CAPS used for emphasis (FORBIDDEN, IMMEDIATELY, REQUIRED as emphasis)."""
        # FORBIDDEN should not appear as standalone emphasis
        assert "= FORBIDDEN" not in framework_text, "FORBIDDEN used as emphasis"
        # IMMEDIATELY should not appear as standalone emphasis
        assert "IMMEDIATELY re-read" not in framework_text, "IMMEDIATELY used as emphasis"

    def test_no_anti_pattern_directive(self, framework_text: str) -> None:
        """FR03: Anti-pattern 'LLMs don't need why' must be removed."""
        assert "LLMs don't need" not in framework_text, "Anti-pattern directive still present"
        assert "Rules not explanations" not in framework_text, "Anti-pattern directive still present"

    def test_positive_pattern_present(self, framework_text: str) -> None:
        """FR03: Replacement 'Rules with rationale' must be present."""
        assert "Rules with rationale" in framework_text

    def test_why_markers_present(self, framework_text: str) -> None:
        """FR04: At least 15 inline WHY: markers in the document."""
        why_count = len(re.findall(r"WHY:", framework_text))
        assert why_count >= 15, f"WHY marker count {why_count} below minimum of 15"

    def test_no_stacked_triple_imperative(self, framework_text: str) -> None:
        """FR01: No triple obligation stacking in a single sentence."""
        # The old pattern "SHOULD act. Chat MUST remain. Artifacts MUST be auditable."
        assert "SHOULD act. Chat MUST" not in framework_text, "Triple obligation stacking found"

    def test_formations_positive_framing(self, framework_text: str) -> None:
        """FR02: Formations rule uses positive framing."""
        assert "Formations are wave-scoped" in framework_text

    def test_plan_living_document(self, framework_text: str) -> None:
        """FR01: plan.md uses 'living document' instead of 'NOT frozen'."""
        assert "is a living document" in framework_text
        assert "is NOT frozen" not in framework_text


class TestXMLStructure:
    """Validates XML tag uniqueness and structure per FR06, FR07."""

    def test_no_generic_rules_tag(self, framework_text: str) -> None:
        """FR06: No bare <rules> tags remain."""
        # Match <rules> but not <*_rules> or other prefixed variants
        generic_open = re.findall(r"<rules>", framework_text)
        generic_close = re.findall(r"</rules>", framework_text)
        assert len(generic_open) == 0, f"Found {len(generic_open)} generic <rules> tags"
        assert len(generic_close) == 0, f"Found {len(generic_close)} generic </rules> tags"

    def test_all_xml_tags_unique(self, framework_text: str) -> None:
        """FR06: Every XML tag name appears at most once as opening tag (excluding self-closing and code blocks)."""
        # Remove code blocks to avoid false positives
        cleaned = re.sub(r"```[\s\S]*?```", "", framework_text)
        # Remove inline backtick-quoted tags (e.g., `<context>`)
        cleaned = re.sub(r"`[^`]*`", "", cleaned)
        # Find all opening tags (not closing, not self-closing, not inside comments)
        opening_tags = re.findall(r"<([a-z][a-z_]*(?:\s[^>]*)?)>", cleaned)
        tag_names = [t.split()[0] for t in opening_tags]

        counts = Counter(tag_names)
        duplicates = {tag: count for tag, count in counts.items() if count > 1}
        # Allow meta_files to appear twice (yaml and jsonl variants)
        duplicates.pop("meta_files", None)
        assert not duplicates, f"Duplicate XML tags found: {duplicates}"

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

    def test_key_sections_tagged(self, framework_text: str) -> None:
        """FR07: Six previously untagged sections now have wrapper tags."""
        required_tags = [
            "defaults",
            "confidence_routing",
            "git_conventions",
            "error_handling",
            "tool_retry",
            "turn_hygiene",
        ]
        for tag in required_tags:
            assert f"<{tag}>" in framework_text, f"Missing wrapper tag <{tag}>"
            assert f"</{tag}>" in framework_text, f"Missing closing tag </{tag}>"

    def test_section_specific_rules_tags(self, framework_text: str) -> None:
        """FR06: Section-specific rule tags exist."""
        expected_rule_tags = [
            "persistence_rules",
            "phase_rules",
            "bootstrap_rules",
            "mcp_rules",
            "exploration_rules",
            "depth_rules",
            "parallelism_rules",
            "tdd_rules",
            "test_organization_rules",
            "test_quality_rules",
        ]
        for tag in expected_rule_tags:
            assert f"<{tag}>" in framework_text, f"Missing section-specific tag <{tag}>"

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
        """NFR04: All XML tags use snake_case naming."""
        # Remove code blocks
        cleaned = re.sub(r"```[\s\S]*?```", "", framework_text)
        # Remove HTML comments
        cleaned = re.sub(r"<!--.*?-->", "", cleaned)
        tags = re.findall(r"</?([a-zA-Z][a-zA-Z_0-9]*)(?:\s[^>]*)?>", cleaned)
        for tag in tags:
            # Allow known exceptions (meta_files has format= attribute)
            if tag in ("br", "hr"):
                continue
            assert tag == tag.lower(), f"Tag '{tag}' is not lowercase"
            assert "-" not in tag, f"Tag '{tag}' uses kebab-case instead of snake_case"


class TestCacheLayout:
    """Validates cache optimization per FR05."""

    def test_variables_block_near_end(self, framework_text: str) -> None:
        """FR05: Variables block appears in the last 10% of the document."""
        var_pos = framework_text.find("<variables>")
        assert var_pos != -1, "Variables block not found"
        total_len = len(framework_text)
        position_pct = var_pos / total_len
        assert position_pct >= 0.90, (
            f"Variables block at {position_pct:.1%} of document, should be >= 90%"
        )

    def test_cache_boundary_marker_exists(self, framework_text: str) -> None:
        """FR05: Cache boundary marker present."""
        assert "<!-- cache_boundary:" in framework_text

    def test_static_prefix_coverage(self, framework_text: str) -> None:
        """NFR02: Static prefix (before cache boundary) >= 80% of document."""
        boundary_pos = framework_text.find("<!-- cache_boundary:")
        assert boundary_pos != -1, "Cache boundary marker not found"
        total_len = len(framework_text)
        prefix_pct = boundary_pos / total_len
        assert prefix_pct >= 0.80, (
            f"Static prefix is {prefix_pct:.1%} of document, should be >= 80%"
        )

    def test_model_section_after_boundary(self, framework_text: str) -> None:
        """FR05: MODEL section appears after cache boundary."""
        boundary_pos = framework_text.find("<!-- cache_boundary:")
        model_pos = framework_text.find("## MODEL")
        assert model_pos > boundary_pos, "MODEL section should be after cache boundary"

    def test_version_date_at_end(self, framework_text: str) -> None:
        """FR05: Version date appears in the last 5% of the document."""
        version_pos = framework_text.find("Version date:")
        assert version_pos != -1, "Version date line not found"
        total_len = len(framework_text)
        position_pct = version_pos / total_len
        assert position_pct >= 0.95, (
            f"Version date at {position_pct:.1%} of document, should be >= 95%"
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
        """Document line count between 700-900."""
        line_count = len(framework_lines)
        assert 700 <= line_count <= 900, f"Line count {line_count} outside range 700-900"

    def test_rfc_declaration_present(self, framework_text: str) -> None:
        """RFC 2119 declaration is present."""
        assert "RFC 2119" in framework_text
        assert "MUST, MUST NOT" in framework_text

    def test_no_todo_markers(self, framework_text: str) -> None:
        """No unresolved TODO markers in the document."""
        # Look for TODO outside of the TODO REGISTRY section
        lines = framework_text.splitlines()
        in_todo_section = False
        for line in lines:
            if "## TODO REGISTRY" in line:
                in_todo_section = True
            elif line.startswith("## ") and in_todo_section:
                in_todo_section = False
            if not in_todo_section and "TODO:" in line:
                # Allow TODO in code blocks and descriptions
                stripped = line.strip()
                if not stripped.startswith("```") and not stripped.startswith("#"):
                    assert False, f"Unresolved TODO marker found: {line.strip()}"

    def test_version_is_v18_1(self, framework_text: str) -> None:
        """Version stamp is v18.1_TRW."""
        assert "v18.1_TRW" in framework_text


class TestReadability:
    """Validates readability metrics."""

    def test_no_extremely_long_lines(self, framework_lines: list[str]) -> None:
        """No lines exceed 300 characters (allows for tables and inline code)."""
        for i, line in enumerate(framework_lines, 1):
            assert len(line) <= 300, (
                f"Line {i} has {len(line)} chars (max 300): {line[:80]}..."
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
