"""Tests for semantic review automation reporting and dataclasses."""

from __future__ import annotations

from pathlib import Path

from trw_mcp.state.semantic_checks import (
    SemanticCheck,
    SemanticCheckResult,
    SemanticFinding,
    format_semantic_report,
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


class TestFormatSemanticReport:
    """FR-4: Build report semantic section."""

    def test_no_findings_report(self) -> None:
        result = SemanticCheckResult(checks_run=5, files_scanned=3)
        report = format_semantic_report(result)
        assert "No semantic issues found" in report
        assert "5 checks" in report
        assert "3 files" in report

    def test_findings_grouped_by_severity(self) -> None:
        result = SemanticCheckResult(
            checks_run=5,
            files_scanned=2,
            findings=[
                SemanticFinding("bare-except", "Bare except", "warning", "f.py", 10, "except:"),
                SemanticFinding("hardcoded-secret", "Secret", "error", "g.py", 5, 'key="abc"'),
            ],
        )
        report = format_semantic_report(result)
        assert "ERROR" in report
        assert "WARNING" in report
        assert "bare-except" in report
        assert "hardcoded-secret" in report

    def test_includes_file_location(self) -> None:
        result = SemanticCheckResult(
            findings=[
                SemanticFinding("test", "desc", "warning", "app/main.py", 42, "match"),
            ]
        )
        report = format_semantic_report(result)
        assert "app/main.py:42" in report

    def test_report_header(self) -> None:
        result = SemanticCheckResult(
            checks_run=3,
            files_scanned=2,
            findings=[
                SemanticFinding("x", "d", "info", "f.py", 1, "m"),
            ],
        )
        report = format_semantic_report(result)
        assert "## Semantic Warnings" in report
        assert "1 issue(s)" in report


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


class TestFormatSemanticReportEdgeCases:
    """Edge cases for the markdown report formatter."""

    def test_info_only_findings_no_error_warning_sections(self) -> None:
        """Report with only info findings shows INFO section, no ERROR/WARNING."""
        result = SemanticCheckResult(
            checks_run=1,
            files_scanned=1,
            findings=[
                SemanticFinding("todo", "TODO found", "info", "f.py", 1, "TODO"),
            ],
        )
        report = format_semantic_report(result)
        assert "INFO (1)" in report
        assert "ERROR" not in report
        assert "WARNING" not in report

    def test_empty_matched_text_omits_code_block(self) -> None:
        """When matched_text is empty, no inline code block is appended."""
        result = SemanticCheckResult(
            findings=[
                SemanticFinding("test", "desc", "warning", "f.py", 1, ""),
            ],
        )
        report = format_semantic_report(result)
        assert "f.py:1" in report
        lines = report.split("\n")
        for index, line in enumerate(lines):
            if "f.py:1" in line:
                if index + 1 < len(lines) and lines[index + 1].strip():
                    assert lines[index + 1].strip() != "``"
                break

    def test_long_matched_text_truncated_at_80_chars(self) -> None:
        """Matched text longer than 80 chars is truncated in the report."""
        long_text = "A" * 120
        result = SemanticCheckResult(
            findings=[
                SemanticFinding("test", "desc", "error", "f.py", 1, long_text),
            ],
        )
        report = format_semantic_report(result)
        assert long_text not in report
        assert "A" * 80 in report

    def test_findings_capped_at_20_per_severity(self, tmp_path: Path) -> None:
        """No more than 20 findings shown per severity group."""
        findings = [SemanticFinding(f"check-{i}", "desc", "warning", "f.py", i, "match") for i in range(30)]
        result = SemanticCheckResult(
            checks_run=1,
            files_scanned=1,
            findings=findings,
        )
        report = format_semantic_report(result)
        check_mentions = report.count("**check-")
        assert check_mentions == 20

    def test_severity_ordering_in_report(self) -> None:
        """Errors appear before warnings, which appear before info."""
        result = SemanticCheckResult(
            checks_run=3,
            files_scanned=1,
            findings=[
                SemanticFinding("info-1", "d", "info", "f.py", 3, "m"),
                SemanticFinding("warn-1", "d", "warning", "f.py", 2, "m"),
                SemanticFinding("err-1", "d", "error", "f.py", 1, "m"),
            ],
        )
        report = format_semantic_report(result)
        error_pos = report.index("ERROR")
        warning_pos = report.index("WARNING")
        info_pos = report.index("INFO")
        assert error_pos < warning_pos < info_pos

    def test_severity_icons(self) -> None:
        """Each severity gets its correct icon prefix."""
        result = SemanticCheckResult(
            checks_run=3,
            files_scanned=1,
            findings=[
                SemanticFinding("e", "d", "error", "f.py", 1, "m"),
                SemanticFinding("w", "d", "warning", "f.py", 2, "m"),
                SemanticFinding("i", "d", "info", "f.py", 3, "m"),
            ],
        )
        report = format_semantic_report(result)
        assert "[!!!]" in report
        assert "[!!]" in report
        assert "[i]" in report

    def test_report_ends_with_newline(self) -> None:
        """Both empty and non-empty reports end with a newline."""
        empty = format_semantic_report(SemanticCheckResult(checks_run=1))
        assert empty.endswith("\n")

        non_empty = format_semantic_report(
            SemanticCheckResult(
                findings=[
                    SemanticFinding("x", "d", "warning", "f.py", 1, "m"),
                ],
            )
        )
        assert non_empty.endswith("\n")


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
