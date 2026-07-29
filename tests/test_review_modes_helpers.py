"""Tests for review helper behavior split from test_review_modes.py."""

from __future__ import annotations

import subprocess
from typing import Any
from unittest.mock import patch

import pytest

from tests._review_modes_support import _make_config
from trw_mcp.tools._review_helpers import (
    REVIEWER_ROLES,
    _compute_verdict,
    _get_git_diff,
    _invoke_cross_model_review,
    _normalize_severity,
    _run_multi_reviewer_analysis,
)


class TestNormalizeSeverity:
    """_normalize_severity maps external labels to internal levels."""

    def test_error_maps_to_critical(self) -> None:
        assert _normalize_severity("error") == "critical"

    def test_critical_maps_to_critical(self) -> None:
        assert _normalize_severity("critical") == "critical"

    def test_high_maps_to_critical(self) -> None:
        assert _normalize_severity("high") == "critical"

    def test_warning_maps_to_warning(self) -> None:
        assert _normalize_severity("warning") == "warning"

    def test_medium_maps_to_warning(self) -> None:
        assert _normalize_severity("medium") == "warning"

    def test_info_maps_to_info(self) -> None:
        assert _normalize_severity("info") == "info"

    def test_unknown_maps_to_info(self) -> None:
        assert _normalize_severity("unknown") == "info"

    def test_empty_string_maps_to_info(self) -> None:
        assert _normalize_severity("") == "info"

    def test_case_insensitive_error(self) -> None:
        assert _normalize_severity("ERROR") == "critical"

    def test_case_insensitive_warning(self) -> None:
        assert _normalize_severity("WARNING") == "warning"

    def test_strips_whitespace(self) -> None:
        assert _normalize_severity("  high  ") == "critical"


class TestInvokeCrossModelReview:
    """_invoke_cross_model_review is the (unwired) provider integration seam."""

    @pytest.mark.parametrize("diff", ["", "+ some diff content\n- removed line"])
    def test_no_transport_wired_returns_none_not_empty_list(self, diff: str) -> None:
        """None means "nothing was contacted" — distinct from "provider said nothing".

        Returning ``[]`` here made handle_cross_model_mode report
        ``provider_returned_empty``, blaming a configured provider TRW never
        called. The caller maps None to ``provider_integration_absent``.
        """
        assert _invoke_cross_model_review(diff, _make_config()) is None


class TestRunMultiReviewerAnalysis:
    """_run_multi_reviewer_analysis performs basic structural diff analysis."""

    def test_empty_diff_returns_empty_findings(self) -> None:
        config = _make_config()
        result = _run_multi_reviewer_analysis("", config)
        assert result["findings"] == []

    def test_empty_diff_claims_no_reviewer_roles(self) -> None:
        """A scan that ran nothing must not claim the full role list.

        This previously reported all six roles for a TODO/FIXME grep, and that
        claim was persisted verbatim as the receipt's realized_reviewer_roles.
        """
        result = _run_multi_reviewer_analysis("", _make_config())
        assert result["reviewer_roles_run"] == []

    def test_marker_scan_claims_only_the_role_it_actually_ran(self) -> None:
        diff = "+++ b/foo.py\n+ # TODO: fix this later\n"
        result = _run_multi_reviewer_analysis(diff, _make_config())
        assert result["reviewer_roles_run"] == ["style"]
        assert set(result["reviewer_roles_run"]).issubset(set(REVIEWER_ROLES))

    def test_empty_diff_has_no_errors(self) -> None:
        config = _make_config()
        result = _run_multi_reviewer_analysis("", config)
        assert result["reviewer_errors"] == []

    def test_detects_todo_in_added_lines(self) -> None:
        config = _make_config()
        diff = "+++ b/foo.py\n+ # TODO: fix this later\n- removed line\n"
        result = _run_multi_reviewer_analysis(diff, config)
        findings = result["findings"]
        assert len(findings) >= 1
        descriptions = [str(f["description"]) for f in findings]
        assert any("TODO" in description or "todo" in description.lower() for description in descriptions)

    def test_detects_fixme_in_added_lines(self) -> None:
        config = _make_config()
        diff = "+++ b/bar.py\n+ # FIXME: this is broken\n"
        result = _run_multi_reviewer_analysis(diff, config)
        findings = result["findings"]
        assert len(findings) >= 1

    def test_detects_hack_in_added_lines(self) -> None:
        config = _make_config()
        diff = "+++ b/baz.py\n+ # HACK: workaround for issue #42\n"
        result = _run_multi_reviewer_analysis(diff, config)
        findings = result["findings"]
        assert len(findings) >= 1

    def test_detects_xxx_in_added_lines(self) -> None:
        config = _make_config()
        diff = "+++ b/qux.py\n+ # XXX: needs attention\n"
        result = _run_multi_reviewer_analysis(diff, config)
        findings = result["findings"]
        assert len(findings) >= 1

    def test_no_findings_for_clean_diff(self) -> None:
        config = _make_config()
        diff = "+++ b/clean.py\n+ def add(a, b):\n+     return a + b\n- def old_add(a, b):\n"
        result = _run_multi_reviewer_analysis(diff, config)
        assert result["findings"] == []

    def test_ignores_diff_header_lines(self) -> None:
        """Lines starting with +++ (file headers) are not treated as added lines."""
        config = _make_config()
        diff = "+++ b/TODO.py\n"
        result = _run_multi_reviewer_analysis(diff, config)
        assert result["findings"] == []

    def test_finding_has_correct_structure(self) -> None:
        config = _make_config()
        diff = "+++ b/work.py\n+ # TODO: finish implementation\n"
        result = _run_multi_reviewer_analysis(diff, config)
        findings = result["findings"]
        assert len(findings) >= 1
        finding = findings[0]
        assert "reviewer_role" in finding
        assert "confidence" in finding
        assert "category" in finding
        assert "severity" in finding
        assert "description" in finding

    def test_finding_severity_is_info(self) -> None:
        config = _make_config()
        diff = "+++ b/work.py\n+ # TODO: finish implementation\n"
        result = _run_multi_reviewer_analysis(diff, config)
        findings = result["findings"]
        assert all(finding["severity"] == "info" for finding in findings)

    def test_finding_reviewer_role_is_style(self) -> None:
        config = _make_config()
        diff = "+++ b/work.py\n+ # TODO: finish implementation\n"
        result = _run_multi_reviewer_analysis(diff, config)
        findings = result["findings"]
        assert all(finding["reviewer_role"] == "style" for finding in findings)

    def test_result_carries_every_key_handle_auto_mode_reads(self) -> None:
        """The five keys the auto-mode handler consumes must all be present.

        Replaces an ``isinstance(result, dict)`` check, which an empty ``{}``
        would satisfy — and an empty dict is exactly the failure that matters
        here: ``handle_auto_mode`` reads ``auto_analysis_limited`` with a
        ``False`` default, so a result missing that key would be treated as a
        SUBSTANTIVE review instead of a limited pattern scan.
        """
        config = _make_config()

        result = _run_multi_reviewer_analysis("some diff", config)

        assert set(result) >= {
            "reviewer_roles_run",
            "reviewer_errors",
            "findings",
            "auto_analysis_limited",
            "limited_reason",
        }
        # The pattern scan is never substantive, and it says so in the payload.
        assert result["auto_analysis_limited"] is True
        assert result["limited_reason"]


class TestComputeVerdict:
    """_compute_verdict derives pass/warn/block from finding severities."""

    def test_empty_findings_returns_pass(self) -> None:
        assert _compute_verdict([]) == "pass"

    def test_info_only_returns_pass(self) -> None:
        findings: list[dict[str, str]] = [{"severity": "info"}]
        assert _compute_verdict(findings) == "pass"

    def test_warning_returns_warn(self) -> None:
        findings: list[dict[str, str]] = [{"severity": "warning"}]
        assert _compute_verdict(findings) == "warn"

    def test_critical_returns_block(self) -> None:
        findings: list[dict[str, str]] = [{"severity": "critical"}]
        assert _compute_verdict(findings) == "block"

    def test_critical_with_warning_returns_block(self) -> None:
        findings: list[dict[str, str]] = [{"severity": "critical"}, {"severity": "warning"}]
        assert _compute_verdict(findings) == "block"

    def test_multiple_warnings_no_critical_returns_warn(self) -> None:
        findings: list[dict[str, str]] = [
            {"severity": "warning"},
            {"severity": "info"},
            {"severity": "warning"},
        ]
        assert _compute_verdict(findings) == "warn"

    def test_multiple_info_returns_pass(self) -> None:
        findings: list[dict[str, str]] = [{"severity": "info"}, {"severity": "info"}]
        assert _compute_verdict(findings) == "pass"

    def test_missing_severity_key_returns_pass(self) -> None:
        findings: list[dict[str, str]] = [{"category": "style"}]
        assert _compute_verdict(findings) == "pass"


class TestGetGitDiff:
    """_get_git_diff returns diff text or empty string on any error."""

    @patch("trw_mcp.tools._review_helpers.subprocess.run")
    def test_returns_stdout_on_success(self, mock_run: Any) -> None:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="diff --git a/foo.py b/foo.py\n+new line\n",
            stderr="",
        )
        result = _get_git_diff()
        assert "diff --git" in result
        assert "+new line" in result

    @patch(
        "trw_mcp.tools._review_helpers.subprocess.run",
        side_effect=FileNotFoundError("git: command not found"),
    )
    def test_returns_empty_string_on_file_not_found(self, mock_run: Any) -> None:
        result = _get_git_diff()
        assert result == ""

    @patch(
        "trw_mcp.tools._review_helpers.subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd="git", timeout=30),
    )
    def test_returns_empty_string_on_timeout(self, mock_run: Any) -> None:
        result = _get_git_diff()
        assert result == ""

    @patch(
        "trw_mcp.tools._review_helpers.subprocess.run",
        side_effect=OSError("permission denied"),
    )
    def test_returns_empty_string_on_oserror(self, mock_run: Any) -> None:
        result = _get_git_diff()
        assert result == ""

    @patch("trw_mcp.tools._review_helpers.subprocess.run")
    def test_returns_empty_string_on_empty_diff(self, mock_run: Any) -> None:
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        result = _get_git_diff()
        assert result == ""

    @patch("trw_mcp.tools._review_helpers.subprocess.run")
    def test_returns_stdout_regardless_of_returncode(self, mock_run: Any) -> None:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=1,
            stdout="some diff output\n",
            stderr="",
        )
        result = _get_git_diff()
        assert result == "some diff output\n"


class TestNormalizeSeverityEdgeCases:
    """Additional edge cases for _normalize_severity."""

    def test_low_maps_to_info(self) -> None:
        assert _normalize_severity("low") == "info"

    def test_none_like_string_maps_to_info(self) -> None:
        assert _normalize_severity("none") == "info"

    def test_mixed_case_medium_maps_to_warning(self) -> None:
        assert _normalize_severity("Medium") == "warning"

    def test_padded_critical_maps_to_critical(self) -> None:
        assert _normalize_severity("  critical  ") == "critical"

    def test_tab_padded_high_maps_to_critical(self) -> None:
        assert _normalize_severity("\thigh\t") == "critical"

    def test_very_long_unknown_string_maps_to_info(self) -> None:
        assert _normalize_severity("x" * 10_000) == "info"


class TestReviewerRolesConstant:
    """REVIEWER_ROLES constant is correctly defined."""

    def test_reviewer_roles_is_tuple(self) -> None:
        assert isinstance(REVIEWER_ROLES, tuple)

    def test_reviewer_roles_has_six_entries(self) -> None:
        assert len(REVIEWER_ROLES) == 6

    def test_reviewer_roles_contains_expected_roles(self) -> None:
        expected = {"correctness", "security", "test-quality", "performance", "style", "spec-compliance"}
        assert set(REVIEWER_ROLES) == expected
