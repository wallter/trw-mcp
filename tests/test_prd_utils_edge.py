"""Edge-case tests for trw_mcp.state.prd_utils.

Focused on uncovered branches and boundary conditions not exercised
by test_prd_utils.py or test_prd_audit_claudemd.py.

All tests are pure logic (no filesystem I/O) unless noted.
"""

from __future__ import annotations

import pytest

from trw_mcp.models.requirements import PRDStatus
from trw_mcp.state.prd_utils import (
    VALID_TRANSITIONS,
    _deep_merge,
    compute_content_density,
    extract_prd_refs,
    extract_sections,
    is_valid_transition,
    parse_frontmatter,
)


# =============================================================================
# parse_frontmatter — edge cases
# =============================================================================


class TestParseFrontmatterEdge:
    """Edge cases for parse_frontmatter()."""

    def test_frontmatter_with_scalar_yaml_returns_empty(self) -> None:
        """Frontmatter that parses to a scalar (e.g. just a string) returns {}."""
        content = "---\njust a plain string\n---\n\n# Body"
        result = parse_frontmatter(content)
        assert result == {}

    def test_frontmatter_with_integer_yaml_returns_empty(self) -> None:
        """Frontmatter that parses to an integer returns {}."""
        content = "---\n42\n---\n\n# Body"
        result = parse_frontmatter(content)
        assert result == {}

    def test_prd_key_is_non_dict_not_flattened(self) -> None:
        """If 'prd' key value is a string (not dict), no flattening occurs."""
        content = "---\nprd: some-string-value\ntitle: Test\n---\n\n# Body"
        result = parse_frontmatter(content)
        assert result["prd"] == "some-string-value"
        assert result["title"] == "Test"

    def test_prd_key_is_list_not_flattened(self) -> None:
        """If 'prd' key value is a list, no flattening occurs."""
        content = "---\nprd:\n  - item1\n  - item2\ntitle: Test\n---\n\n# Body"
        result = parse_frontmatter(content)
        assert result["prd"] == ["item1", "item2"]
        assert result["title"] == "Test"

    def test_empty_frontmatter_block_returns_empty(self) -> None:
        """Empty frontmatter block (--- followed immediately by ---) returns {}."""
        content = "---\n\n---\n\n# Body"
        result = parse_frontmatter(content)
        # ruamel parses empty YAML as None, which is not a dict
        assert result == {}

    def test_frontmatter_with_null_value_returns_empty(self) -> None:
        """Frontmatter that parses to explicit null returns {}."""
        content = "---\nnull\n---\n\n# Body"
        result = parse_frontmatter(content)
        assert result == {}

    def test_multiline_string_values_preserved(self) -> None:
        """Multiline YAML values in frontmatter are preserved."""
        content = (
            "---\nid: PRD-CORE-001\ndescription: |\n"
            "  This is a\n  multiline description.\n---\n\n# Body"
        )
        result = parse_frontmatter(content)
        assert result["id"] == "PRD-CORE-001"
        assert "multiline" in str(result["description"])

    def test_nested_prd_with_multiple_top_level_keys(self) -> None:
        """Flattening 'prd' key preserves all other top-level keys."""
        content = (
            "---\nprd:\n  id: PRD-CORE-099\n  status: draft\n"
            "extra1: val1\nextra2: val2\n---\n\n# Body"
        )
        result = parse_frontmatter(content)
        assert result["id"] == "PRD-CORE-099"
        assert result["status"] == "draft"
        assert result["extra1"] == "val1"
        assert result["extra2"] == "val2"
        assert "prd" not in result

    def test_top_level_key_overrides_prd_key_on_conflict(self) -> None:
        """When both prd.X and top-level X exist, top-level wins (flattening order)."""
        content = (
            "---\nprd:\n  id: prd-inner\n"
            "id: top-level\n---\n\n# Body"
        )
        result = parse_frontmatter(content)
        # The flattening loop adds top-level keys AFTER prd contents,
        # so top-level 'id' overwrites prd 'id'
        assert result["id"] == "top-level"

    def test_frontmatter_not_at_start_returns_empty(self) -> None:
        """Frontmatter must be at the very start of the document."""
        content = "\n---\nid: PRD-CORE-001\n---\n\n# Body"
        result = parse_frontmatter(content)
        assert result == {}

    def test_frontmatter_with_boolean_values(self) -> None:
        """Boolean values in frontmatter are preserved as booleans."""
        content = "---\nid: PRD-TEST-001\npublished: true\narchived: false\n---\n"
        result = parse_frontmatter(content)
        assert result["published"] is True
        assert result["archived"] is False

    def test_frontmatter_with_nested_dicts(self) -> None:
        """Deeply nested dicts are preserved."""
        content = (
            "---\nid: PRD-CORE-001\n"
            "meta:\n  dates:\n    created: '2026-01-01'\n    updated: '2026-02-01'\n"
            "---\n\n# Body"
        )
        result = parse_frontmatter(content)
        meta = result["meta"]
        assert isinstance(meta, dict)
        dates = meta["dates"]
        assert isinstance(dates, dict)
        assert str(dates["created"]) == "2026-01-01"


# =============================================================================
# extract_sections — edge cases
# =============================================================================


class TestExtractSectionsEdge:
    """Edge cases for extract_sections()."""

    def test_empty_string_returns_empty(self) -> None:
        result = extract_sections("")
        assert result == []

    def test_multi_digit_section_numbers(self) -> None:
        """Section numbers > 9 are matched correctly."""
        content = "## 10. Extended Section\n\n## 12. Another Section\n"
        result = extract_sections(content)
        assert result == ["Extended Section", "Another Section"]

    def test_section_with_special_characters(self) -> None:
        """Section names with parens, dashes, etc. are extracted."""
        content = "## 1. Problem Statement (Critical)\n\n## 2. High-Level Design\n"
        result = extract_sections(content)
        assert result == ["Problem Statement (Critical)", "High-Level Design"]

    def test_section_with_backtick_code(self) -> None:
        """Section names containing backtick-wrapped code are extracted."""
        content = "## 1. The `foo_bar` Module\n"
        result = extract_sections(content)
        assert result == ["The `foo_bar` Module"]

    def test_section_heading_requires_space_after_hash(self) -> None:
        """'##1. No space' should NOT match (requires space after ##)."""
        content = "##1. No Space\n\n## 1. With Space\n"
        result = extract_sections(content)
        assert result == ["With Space"]

    def test_section_heading_requires_dot_after_number(self) -> None:
        """'## 1 No Dot' should NOT match (requires dot after number)."""
        content = "## 1 No Dot\n\n## 1. With Dot\n"
        result = extract_sections(content)
        assert result == ["With Dot"]

    def test_only_whitespace_content(self) -> None:
        result = extract_sections("   \n\n   \n")
        assert result == []

    def test_h2_with_zero_numbered_section(self) -> None:
        """## 0. Preamble should match."""
        content = "## 0. Preamble\n\nSome text\n"
        result = extract_sections(content)
        assert result == ["Preamble"]


# =============================================================================
# compute_content_density — edge cases
# =============================================================================


class TestComputeContentDensityEdge:
    """Edge cases for compute_content_density()."""

    def test_all_substantive_returns_one(self) -> None:
        """Content with no non-substantive lines has density 1.0."""
        content = "Line one content.\nLine two content.\nLine three content."
        density = compute_content_density(content)
        assert density == 1.0

    def test_all_blank_lines_returns_zero(self) -> None:
        content = "\n\n\n\n"
        density = compute_content_density(content)
        assert density == 0.0

    def test_all_horizontal_rules(self) -> None:
        content = "---\n---\n---"
        density = compute_content_density(content)
        assert density == 0.0

    def test_all_html_comments(self) -> None:
        content = "<!-- comment 1 -->\n<!-- comment 2 -->\n<!-- comment 3 -->"
        density = compute_content_density(content)
        assert density == 0.0

    def test_mixed_non_substantive_patterns(self) -> None:
        """Each non-substantive pattern type is correctly filtered."""
        content = "\n".join([
            "",                    # blank
            "---",                 # horizontal rule
            "<!-- placeholder -->", # HTML comment
            "|---|---|",           # table separator
            "# Heading",          # heading
        ])
        density = compute_content_density(content)
        assert density == 0.0

    def test_indented_heading_is_non_substantive(self) -> None:
        """Headings with leading spaces are still non-substantive."""
        content = "  # Indented heading\nSubstantive line."
        density = compute_content_density(content)
        assert density == 0.5

    def test_table_separator_with_colons(self) -> None:
        """Table separator with alignment colons is non-substantive."""
        content = "| Header |\n|:------:|\n| data |"
        density = compute_content_density(content)
        # Header row + data row = 2 substantive, separator = 1 non-substantive
        assert abs(density - 2 / 3) < 0.01

    def test_single_substantive_line(self) -> None:
        content = "This is the only line."
        density = compute_content_density(content)
        assert density == 1.0


# =============================================================================
# extract_prd_refs — edge cases
# =============================================================================


class TestExtractPrdRefsEdge:
    """Edge cases for extract_prd_refs()."""

    def test_lowercase_prd_not_matched(self) -> None:
        """Pattern requires uppercase PRD-."""
        content = "References prd-core-001 and prd-FIX-002."
        result = extract_prd_refs(content)
        assert result == []

    def test_partial_pattern_not_matched(self) -> None:
        """Incomplete patterns like PRD-CORE or PRD-CORE-AB do not match."""
        content = "PRD-CORE is incomplete. PRD-CORE-AB is non-numeric."
        result = extract_prd_refs(content)
        assert result == []

    def test_prd_refs_in_backtick_code(self) -> None:
        """PRD refs inside backticks are still extracted."""
        content = "See `PRD-CORE-007` for details."
        result = extract_prd_refs(content)
        assert result == ["PRD-CORE-007"]

    def test_prd_refs_adjacent_no_space(self) -> None:
        """Adjacent PRD refs without space are extracted as separate refs."""
        content = "PRD-CORE-001PRD-FIX-002"
        result = extract_prd_refs(content)
        assert "PRD-CORE-001" in result
        assert "PRD-FIX-002" in result

    def test_prd_ref_with_four_digit_seq(self) -> None:
        """Pattern expects exactly 3 digits; 4 digits should match first 3."""
        content = "PRD-CORE-0010 mentioned here."
        result = extract_prd_refs(content)
        # Regex matches PRD-CORE-001 (first 3 digits), trailing 0 is ignored
        assert result == ["PRD-CORE-001"]

    def test_empty_string(self) -> None:
        result = extract_prd_refs("")
        assert result == []

    def test_many_duplicate_refs_deduplicated(self) -> None:
        content = " ".join(["PRD-CORE-001"] * 50)
        result = extract_prd_refs(content)
        assert result == ["PRD-CORE-001"]

    def test_refs_sorted_alphabetically(self) -> None:
        """Result is sorted alphabetically by full PRD ID."""
        content = "PRD-QUAL-001 PRD-CORE-001 PRD-FIX-001 PRD-INFRA-001"
        result = extract_prd_refs(content)
        assert result == sorted(result)
        assert result == ["PRD-CORE-001", "PRD-FIX-001", "PRD-INFRA-001", "PRD-QUAL-001"]


# =============================================================================
# is_valid_transition — comprehensive coverage
# =============================================================================


class TestIsValidTransitionComprehensive:
    """Comprehensive transition validation."""

    # All explicitly valid forward transitions
    @pytest.mark.parametrize(
        "current, target",
        [
            (PRDStatus.DRAFT, PRDStatus.REVIEW),
            (PRDStatus.DRAFT, PRDStatus.MERGED),
            (PRDStatus.REVIEW, PRDStatus.APPROVED),
            (PRDStatus.REVIEW, PRDStatus.DRAFT),
            (PRDStatus.REVIEW, PRDStatus.MERGED),
            (PRDStatus.APPROVED, PRDStatus.IMPLEMENTED),
            (PRDStatus.APPROVED, PRDStatus.DEPRECATED),
            (PRDStatus.APPROVED, PRDStatus.MERGED),
            (PRDStatus.IMPLEMENTED, PRDStatus.DONE),
            (PRDStatus.IMPLEMENTED, PRDStatus.DEPRECATED),
        ],
    )
    def test_valid_transitions(self, current: PRDStatus, target: PRDStatus) -> None:
        assert is_valid_transition(current, target) is True

    # Invalid transitions: terminal states have no outgoing edges
    @pytest.mark.parametrize(
        "current, target",
        [
            (PRDStatus.DONE, PRDStatus.DRAFT),
            (PRDStatus.DONE, PRDStatus.REVIEW),
            (PRDStatus.DONE, PRDStatus.APPROVED),
            (PRDStatus.DONE, PRDStatus.IMPLEMENTED),
            (PRDStatus.DONE, PRDStatus.MERGED),
            (PRDStatus.DONE, PRDStatus.DEPRECATED),
            (PRDStatus.MERGED, PRDStatus.DRAFT),
            (PRDStatus.MERGED, PRDStatus.DONE),
            (PRDStatus.DEPRECATED, PRDStatus.DRAFT),
            (PRDStatus.DEPRECATED, PRDStatus.DONE),
        ],
    )
    def test_terminal_states_reject_transitions(
        self, current: PRDStatus, target: PRDStatus
    ) -> None:
        assert is_valid_transition(current, target) is False

    # Invalid skip transitions
    @pytest.mark.parametrize(
        "current, target",
        [
            (PRDStatus.DRAFT, PRDStatus.APPROVED),   # skip review
            (PRDStatus.DRAFT, PRDStatus.IMPLEMENTED), # skip review+approved
            (PRDStatus.DRAFT, PRDStatus.DONE),        # skip all
            (PRDStatus.REVIEW, PRDStatus.IMPLEMENTED), # skip approved
            (PRDStatus.REVIEW, PRDStatus.DONE),       # skip approved+implemented
            (PRDStatus.APPROVED, PRDStatus.DONE),     # skip implemented
        ],
    )
    def test_skip_transitions_rejected(
        self, current: PRDStatus, target: PRDStatus
    ) -> None:
        assert is_valid_transition(current, target) is False

    def test_draft_cannot_deprecate(self) -> None:
        """DRAFT has no path to DEPRECATED (only APPROVED and IMPLEMENTED can)."""
        assert is_valid_transition(PRDStatus.DRAFT, PRDStatus.DEPRECATED) is False

    def test_review_cannot_deprecate(self) -> None:
        """REVIEW has no path to DEPRECATED."""
        assert is_valid_transition(PRDStatus.REVIEW, PRDStatus.DEPRECATED) is False


# =============================================================================
# VALID_TRANSITIONS — structural checks
# =============================================================================


class TestValidTransitionsStructure:
    """Verify the VALID_TRANSITIONS dict is well-formed."""

    def test_all_prd_statuses_have_entries(self) -> None:
        """Every PRDStatus enum value must have an entry in VALID_TRANSITIONS."""
        for status in PRDStatus:
            assert status in VALID_TRANSITIONS, f"{status} missing from VALID_TRANSITIONS"

    def test_terminal_states_have_empty_sets(self) -> None:
        """DONE, MERGED, DEPRECATED must have empty transition sets."""
        for terminal in (PRDStatus.DONE, PRDStatus.MERGED, PRDStatus.DEPRECATED):
            assert VALID_TRANSITIONS[terminal] == set(), (
                f"{terminal} should have no outgoing transitions"
            )

    def test_no_self_loops_in_explicit_transitions(self) -> None:
        """No status should list itself as an explicit transition target.

        Identity transitions are handled separately in is_valid_transition.
        """
        for status, targets in VALID_TRANSITIONS.items():
            assert status not in targets, (
                f"{status} has itself in explicit transitions"
            )

    def test_transition_targets_are_all_valid_statuses(self) -> None:
        """All target statuses in the transition map must be valid PRDStatus values."""
        valid_statuses = set(PRDStatus)
        for source, targets in VALID_TRANSITIONS.items():
            for target in targets:
                assert target in valid_statuses, (
                    f"Invalid target {target} from {source}"
                )


# =============================================================================
# _deep_merge — edge cases
# =============================================================================


class TestDeepMergeEdge:
    """Additional edge cases for _deep_merge()."""

    def test_three_level_nested_merge(self) -> None:
        """Merge works at 3+ levels of nesting."""
        target: dict[str, object] = {
            "a": {"b": {"c": "original", "d": "keep"}},
        }
        source: dict[str, object] = {"a": {"b": {"c": "updated"}}}
        _deep_merge(target, source)
        a_val = target["a"]
        assert isinstance(a_val, dict)
        b_val = a_val["b"]
        assert isinstance(b_val, dict)
        assert b_val["c"] == "updated"
        assert b_val["d"] == "keep"

    def test_overwrite_non_dict_with_dict(self) -> None:
        """If target has a scalar and source has a dict, scalar is overwritten."""
        target: dict[str, object] = {"key": "scalar"}
        source: dict[str, object] = {"key": {"nested": "value"}}
        _deep_merge(target, source)
        assert target["key"] == {"nested": "value"}

    def test_overwrite_dict_with_scalar(self) -> None:
        """If target has a dict and source has a scalar, dict is overwritten."""
        target: dict[str, object] = {"key": {"nested": "value"}}
        source: dict[str, object] = {"key": "scalar"}
        _deep_merge(target, source)
        assert target["key"] == "scalar"

    def test_add_new_keys(self) -> None:
        """Keys in source that don't exist in target are added."""
        target: dict[str, object] = {"existing": "value"}
        source: dict[str, object] = {"new_key": "new_value"}
        _deep_merge(target, source)
        assert target["existing"] == "value"
        assert target["new_key"] == "new_value"

    def test_empty_source_is_noop(self) -> None:
        """Merging empty source does not change target."""
        target: dict[str, object] = {"key": "value"}
        _deep_merge(target, {})
        assert target == {"key": "value"}

    def test_empty_target_gets_populated(self) -> None:
        """Merging into empty target populates it."""
        target: dict[str, object] = {}
        source: dict[str, object] = {"key": "value"}
        _deep_merge(target, source)
        assert target == {"key": "value"}

    def test_list_values_overwritten_not_merged(self) -> None:
        """Lists are overwritten entirely (not appended/merged)."""
        target: dict[str, object] = {"tags": ["a", "b"]}
        source: dict[str, object] = {"tags": ["c"]}
        _deep_merge(target, source)
        assert target["tags"] == ["c"]

    def test_int_target_value_is_noop(self) -> None:
        """If target is an int (not dict), _deep_merge returns silently."""
        _deep_merge(42, {"key": "value"})  # type: ignore[arg-type]
        # No exception = success

    def test_list_target_value_is_noop(self) -> None:
        """If target is a list (not dict), _deep_merge returns silently."""
        _deep_merge(["a", "b"], {"key": "value"})  # type: ignore[arg-type]
        # No exception = success


# =============================================================================
# TransitionResult model
# =============================================================================


class TestTransitionResultModel:
    """Test TransitionResult Pydantic model defaults and construction."""

    def test_defaults(self) -> None:
        from trw_mcp.state.prd_utils import TransitionResult

        result = TransitionResult(allowed=True)
        assert result.allowed is True
        assert result.reason == ""
        assert result.guard_details == {}

    def test_full_construction(self) -> None:
        from trw_mcp.state.prd_utils import TransitionResult

        result = TransitionResult(
            allowed=False,
            reason="Density too low",
            guard_details={"density": 0.3, "threshold": 0.5},
        )
        assert result.allowed is False
        assert result.reason == "Density too low"
        assert result.guard_details["density"] == 0.3


# =============================================================================
# Regex pattern validation
# =============================================================================


class TestRegexPatterns:
    """Validate the compiled regex patterns at module level."""

    def test_frontmatter_regex_requires_start_of_string(self) -> None:
        """_FRONTMATTER_RE must anchor to start of string."""
        from trw_mcp.state.prd_utils import _FRONTMATTER_RE

        # Should NOT match frontmatter in the middle of a document
        content = "Some preamble\n---\nid: test\n---\n"
        assert _FRONTMATTER_RE.match(content) is None

    def test_frontmatter_regex_matches_valid(self) -> None:
        from trw_mcp.state.prd_utils import _FRONTMATTER_RE

        content = "---\nid: test\nstatus: draft\n---\n\n# Body"
        match = _FRONTMATTER_RE.match(content)
        assert match is not None
        assert "id: test" in match.group(1)

    def test_section_heading_regex_captures_name(self) -> None:
        from trw_mcp.state.prd_utils import _SECTION_HEADING_RE

        matches = _SECTION_HEADING_RE.findall("## 1. Problem Statement\n## 2. Goals\n")
        assert matches == ["Problem Statement", "Goals"]

    def test_prd_ref_regex_exact_three_digits(self) -> None:
        from trw_mcp.state.prd_utils import _PRD_REF_RE

        # Exactly 3 digits
        assert _PRD_REF_RE.search("PRD-CORE-007") is not None
        # 2 digits — no match
        assert _PRD_REF_RE.search("PRD-CORE-07") is None
        # Category must be uppercase letters
        assert _PRD_REF_RE.search("PRD-core-007") is None
