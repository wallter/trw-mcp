"""Tests for review helper validation and severity counting."""

from __future__ import annotations

from trw_mcp.tools._review_helpers import count_by_severity, validate_manual_findings


class TestValidateManualFindings:
    """validate_manual_findings: normalization, pass-through, edge cases."""

    def test_valid_critical_finding_passes_through(self) -> None:
        findings = [{"category": "correctness", "severity": "critical", "description": "Bug"}]
        result = validate_manual_findings(findings)
        assert len(result) == 1
        assert result[0]["severity"] == "critical"

    def test_valid_warning_finding_passes_through(self) -> None:
        findings = [{"category": "style", "severity": "warning", "description": "Nit"}]
        result = validate_manual_findings(findings)
        assert result[0]["severity"] == "warning"

    def test_valid_info_finding_passes_through(self) -> None:
        findings = [{"category": "docs", "severity": "info", "description": "Comment missing"}]
        result = validate_manual_findings(findings)
        assert result[0]["severity"] == "info"

    def test_empty_list_returns_empty(self) -> None:
        result = validate_manual_findings([])
        assert result == []

    def test_invalid_severity_normalized_via_normalize_severity(self) -> None:
        """A complete finding using the supported high alias is normalized."""
        finding = {"category": "security", "severity": "high", "description": "Bug"}
        result = validate_manual_findings([finding])
        assert len(result) == 1
        assert result[0]["severity"] == "critical"

    def test_invalid_severity_medium_normalized_to_warning(self) -> None:
        finding = {"category": "style", "severity": "medium", "description": "Issue"}
        result = validate_manual_findings([finding])
        assert result[0]["severity"] == "warning"

    def test_invalid_severity_error_normalized_to_critical(self) -> None:
        finding = {"category": "correctness", "severity": "error", "description": "Failure"}
        result = validate_manual_findings([finding])
        assert result[0]["severity"] == "critical"

    def test_incomplete_finding_is_rejected(self) -> None:
        finding: dict[str, str] = {"severity": "high"}
        result = validate_manual_findings([finding])
        assert result == []

    def test_finding_with_unknown_severity_is_rejected(self) -> None:
        finding = {
            "category": "style",
            "severity": "unknown-level",
            "description": "Something",
        }
        result = validate_manual_findings([finding])
        assert result == []

    def test_multiple_findings_all_validated(self) -> None:
        findings = [
            {"category": "correctness", "severity": "critical", "description": "A"},
            {"category": "style", "severity": "warning", "description": "B"},
            {"category": "docs", "severity": "info", "description": "C"},
        ]
        result = validate_manual_findings(findings)
        assert len(result) == 3

    def test_result_contains_all_original_keys(self) -> None:
        finding = {
            "category": "security",
            "severity": "critical",
            "description": "SQL injection",
            "file_path": "app/db.py",
        }
        result = validate_manual_findings([finding])
        assert result[0]["file_path"] == "app/db.py"

    def test_findings_missing_severity_key_are_rejected(self) -> None:
        finding: dict[str, str] = {}
        result = validate_manual_findings([finding])
        assert result == []

    def test_blank_category_or_description_is_rejected(self) -> None:
        findings = [
            {"category": " ", "severity": "info", "description": "Issue"},
            {"category": "style", "severity": "info", "description": "  "},
        ]
        assert validate_manual_findings(findings) == []

    def test_returns_list_type(self) -> None:
        result = validate_manual_findings([])
        assert isinstance(result, list)


class TestCountBySeverity:
    """count_by_severity: returns (critical, warning, info) tuple."""

    def test_empty_list_returns_zeros(self) -> None:
        assert count_by_severity([]) == (0, 0, 0)

    def test_single_critical(self) -> None:
        findings = [{"severity": "critical"}]
        assert count_by_severity(findings) == (1, 0, 0)

    def test_single_warning(self) -> None:
        findings = [{"severity": "warning"}]
        assert count_by_severity(findings) == (0, 1, 0)

    def test_single_info(self) -> None:
        findings = [{"severity": "info"}]
        assert count_by_severity(findings) == (0, 0, 1)

    def test_mixed_severities_counted_correctly(self) -> None:
        findings = [
            {"severity": "critical"},
            {"severity": "warning"},
            {"severity": "info"},
            {"severity": "critical"},
            {"severity": "info"},
        ]
        assert count_by_severity(findings) == (2, 1, 2)

    def test_unknown_severity_not_counted_in_any_bucket(self) -> None:
        findings = [{"severity": "unknown"}, {"severity": "high"}]
        critical, warning, info = count_by_severity(findings)
        assert critical == 0
        assert warning == 0
        assert info == 0

    def test_missing_severity_key_not_counted(self) -> None:
        findings = [{"category": "style"}]
        assert count_by_severity(findings) == (0, 0, 0)

    def test_returns_tuple_type(self) -> None:
        result = count_by_severity([])
        assert isinstance(result, tuple)
        assert len(result) == 3

    def test_all_critical_findings(self) -> None:
        findings = [{"severity": "critical"} for _ in range(5)]
        assert count_by_severity(findings) == (5, 0, 0)

    def test_all_info_findings(self) -> None:
        findings = [{"severity": "info"} for _ in range(3)]
        assert count_by_severity(findings) == (0, 0, 3)
