"""Tests for reconciliation helper parsing utilities."""

from __future__ import annotations

import pytest

from trw_mcp.tools._review_helpers import (
    _added_lines_only,
    _extract_identifiers,
    _extract_section,
)


class TestAddedLinesOnly:
    """_added_lines_only: filter diff to only added lines."""

    @pytest.mark.unit
    def test_keeps_added_lines(self) -> None:
        diff = "+added line\n-removed line\n context line"
        result = _added_lines_only(diff)
        assert "+added line" in result
        assert "context line" in result

    @pytest.mark.unit
    def test_removes_deleted_lines(self) -> None:
        diff = "+added\n-removed\n context"
        result = _added_lines_only(diff)
        assert "-removed" not in result

    @pytest.mark.unit
    def test_keeps_diff_header_lines(self) -> None:
        diff = "--- a/file.py\n+++ b/file.py\n-old\n+new"
        result = _added_lines_only(diff)
        assert "--- a/file.py" in result
        assert "+++ b/file.py" in result

    @pytest.mark.unit
    def test_empty_diff(self) -> None:
        assert _added_lines_only("") == ""


class TestExtractSection:
    """_extract_section: markdown section extraction."""

    @pytest.mark.unit
    def test_extracts_numbered_section(self) -> None:
        content = "## 3. Functional Requirements\n\nFR01 content here\n\n## 4. Other\n\nOther content"
        result = _extract_section(content, "Functional Requirements")
        assert "FR01 content here" in result
        assert "Other content" not in result

    @pytest.mark.unit
    def test_extracts_unnumbered_section(self) -> None:
        content = "## Functional Requirements\n\nFR01 content\n\n## Other\n\nMore"
        result = _extract_section(content, "Functional Requirements")
        assert "FR01 content" in result

    @pytest.mark.unit
    def test_returns_empty_for_missing_section(self) -> None:
        content = "## Some Section\n\nContent here"
        result = _extract_section(content, "Nonexistent Section")
        assert result == ""

    @pytest.mark.unit
    def test_extracts_to_end_when_no_following_section(self) -> None:
        content = "## 3. Functional Requirements\n\nFR01 content here\nMore content"
        result = _extract_section(content, "Functional Requirements")
        assert "FR01 content here" in result
        assert "More content" in result

    @pytest.mark.unit
    def test_case_insensitive(self) -> None:
        content = "## functional requirements\n\nContent"
        result = _extract_section(content, "Functional Requirements")
        assert "Content" in result


class TestExtractIdentifiers:
    """_extract_identifiers: extract code identifiers from FR text."""

    @pytest.mark.unit
    def test_extracts_backtick_identifiers(self) -> None:
        text = "The `UserValidator` class MUST call `validate()` method."
        result = _extract_identifiers(text)
        assert "UserValidator" in result
        assert "validate()" in result

    @pytest.mark.unit
    def test_extracts_flag_identifiers(self) -> None:
        text = "Supports --strict and --dry-run flags."
        result = _extract_identifiers(text)
        assert "--strict" in result
        assert "--dry-run" in result

    @pytest.mark.unit
    def test_extracts_pascal_case_class_names(self) -> None:
        text = "The UserValidator and DataProcessor classes are used."
        result = _extract_identifiers(text)
        assert "UserValidator" in result
        assert "DataProcessor" in result

    @pytest.mark.unit
    def test_deduplicates_preserving_order(self) -> None:
        text = "Use `foo` and `foo` again. Also `bar`."
        result = _extract_identifiers(text)
        assert result.count("foo") == 1
        assert result.index("foo") < result.index("bar")

    @pytest.mark.unit
    def test_empty_text_returns_empty(self) -> None:
        result = _extract_identifiers("")
        assert result == []

    @pytest.mark.unit
    def test_no_identifiers_returns_empty(self) -> None:
        text = "This text has no code identifiers or flags."
        result = _extract_identifiers(text)
        assert all(not item.startswith("--") for item in result)

    @pytest.mark.unit
    def test_single_uppercase_word_not_extracted_as_pascal_case(self) -> None:
        """Single-word uppercase identifiers like 'MUST' are not PascalCase."""
        text = "The system MUST validate."
        result = _extract_identifiers(text)
        assert "MUST" not in result

    @pytest.mark.unit
    def test_combined_extraction(self) -> None:
        text = "The `UserValidator` uses --strict flag and creates ValidationResult objects."
        result = _extract_identifiers(text)
        assert "UserValidator" in result
        assert "--strict" in result
        assert "ValidationResult" in result
