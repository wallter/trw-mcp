"""Tests for semantic review automation reporting and dataclasses."""

from __future__ import annotations

from trw_mcp.state.semantic_checks import (
    SemanticCheck,
    SemanticCheckResult,
    SemanticFinding,
)


class TestSemanticCheckResult:
    """Result aggregation."""

    def test_counts_by_severity(self) -> None:
        result = SemanticCheckResult(
            findings=[
                SemanticFinding("a", "d", "error", "f.py", 1, "x"),
                SemanticFinding("b", "d", "warning", "f.py", 2, "y"),
                SemanticFinding("c", "d", "warning", "f.py", 3, "z"),
                SemanticFinding("d", "d", "info", "f.py", 4, "w"),
            ]
        )
        assert result.error_count == 1
        assert result.warning_count == 2
        assert result.info_count == 1

    def test_empty_result(self) -> None:
        result = SemanticCheckResult()
        assert result.error_count == 0
        assert result.warning_count == 0
        assert result.info_count == 0


class TestSemanticCheckResultEdgeCases:
    """Edge cases for the result dataclass."""

    def test_only_info_findings(self) -> None:
        """Result with only info findings has 0 errors and 0 warnings."""
        result = SemanticCheckResult(
            findings=[
                SemanticFinding("a", "d", "info", "f.py", 1, "x"),
                SemanticFinding("b", "d", "info", "f.py", 2, "y"),
            ]
        )
        assert result.error_count == 0
        assert result.warning_count == 0
        assert result.info_count == 2

    def test_unknown_severity_not_counted(self) -> None:
        """Findings with an unrecognized severity are not counted by any property."""
        result = SemanticCheckResult(
            findings=[
                SemanticFinding("a", "d", "critical", "f.py", 1, "x"),
            ]
        )
        assert result.error_count == 0
        assert result.warning_count == 0
        assert result.info_count == 0
        assert len(result.findings) == 1

    def test_default_values(self) -> None:
        """Default construction has zero counts and empty findings."""
        result = SemanticCheckResult()
        assert result.checks_run == 0
        assert result.files_scanned == 0
        assert result.findings == []


class TestSemanticCheckDataclass:
    """SemanticCheck dataclass field defaults."""

    def test_defaults(self) -> None:
        check = SemanticCheck(
            id="test",
            description="desc",
            severity="info",
            automated=False,
        )
        assert check.pattern is None
        assert check.language == "any"

    def test_all_fields_set(self) -> None:
        check = SemanticCheck(
            id="test",
            description="desc",
            severity="error",
            automated=True,
            pattern=r"\bfoo\b",
            language="go",
        )
        assert check.id == "test"
        assert check.pattern == r"\bfoo\b"
        assert check.language == "go"


class TestSemanticFindingDataclass:
    """SemanticFinding field access."""

    def test_attributes(self) -> None:
        finding = SemanticFinding(
            check_id="bare-except",
            description="Bare except clause",
            severity="warning",
            file_path="app/main.py",
            line_number=42,
            matched_text="except:",
        )
        assert finding.check_id == "bare-except"
        assert finding.description == "Bare except clause"
        assert finding.severity == "warning"
        assert finding.file_path == "app/main.py"
        assert finding.line_number == 42
        assert finding.matched_text == "except:"
