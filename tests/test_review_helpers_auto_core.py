"""Tests for auto review helper mode core behavior."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from tests._review_helpers_support import _make_config, run_dir
from trw_mcp.tools._review_helpers import handle_auto_mode


class TestHandleAutoMode:
    """handle_auto_mode: confidence filtering and reviewer analysis flow."""

    def test_filters_findings_below_threshold(self, run_dir: Path) -> None:
        config = _make_config(confidence_threshold=80)
        reviewer_findings = [
            {
                "reviewer_role": "correctness",
                "confidence": 90,
                "category": "logic",
                "severity": "warning",
                "description": "High confidence",
            },
            {
                "reviewer_role": "style",
                "confidence": 50,
                "category": "style",
                "severity": "info",
                "description": "Low confidence",
            },
        ]
        with patch("trw_mcp.tools._review_helpers._get_git_diff", return_value=""):
            result = handle_auto_mode(config, run_dir, "review-auto", "2026-03-01T00:00:00Z", reviewer_findings)
        assert result["surfaced_findings_count"] == 1
        assert result["total_findings_count"] == 2

    def test_filters_at_exact_threshold(self, run_dir: Path) -> None:
        """Finding at exactly the threshold is surfaced (>=)."""
        config = _make_config(confidence_threshold=75)
        reviewer_findings = [
            {
                "reviewer_role": "security",
                "confidence": 75,
                "category": "sec",
                "severity": "critical",
                "description": "At threshold",
            },
            {
                "reviewer_role": "style",
                "confidence": 74,
                "category": "style",
                "severity": "warning",
                "description": "Below threshold",
            },
        ]
        with patch("trw_mcp.tools._review_helpers._get_git_diff", return_value=""):
            result = handle_auto_mode(config, run_dir, "review-auto", "2026-03-01T00:00:00Z", reviewer_findings)
        assert result["surfaced_findings_count"] == 1

    def test_verdict_from_surfaced_only(self, run_dir: Path) -> None:
        """Verdict computed from surfaced findings; filtered critical does not block."""
        config = _make_config(confidence_threshold=80)
        reviewer_findings = [
            {
                "reviewer_role": "correctness",
                "confidence": 40,
                "category": "logic",
                "severity": "critical",
                "description": "Low confidence critical",
            },
            {
                "reviewer_role": "style",
                "confidence": 90,
                "category": "style",
                "severity": "warning",
                "description": "High confidence warning",
            },
        ]
        with patch("trw_mcp.tools._review_helpers._get_git_diff", return_value=""):
            result = handle_auto_mode(config, run_dir, "review-auto", "2026-03-01T00:00:00Z", reviewer_findings)
        assert result["verdict"] == "warn"

    def test_all_below_threshold_verdict_is_pass(self, run_dir: Path) -> None:
        config = _make_config(confidence_threshold=80)
        reviewer_findings = [
            {
                "reviewer_role": "correctness",
                "confidence": 20,
                "category": "logic",
                "severity": "critical",
                "description": "Low confidence critical",
            },
        ]
        with patch("trw_mcp.tools._review_helpers._get_git_diff", return_value=""):
            result = handle_auto_mode(config, run_dir, "review-auto", "2026-03-01T00:00:00Z", reviewer_findings)
        assert result["surfaced_findings_count"] == 0
        assert result["verdict"] == "pass"

    def test_all_above_threshold_all_surfaced(self, run_dir: Path) -> None:
        config = _make_config(confidence_threshold=50)
        reviewer_findings = [
            {
                "reviewer_role": "correctness",
                "confidence": 90,
                "category": "logic",
                "severity": "info",
                "description": "A",
            },
            {
                "reviewer_role": "security",
                "confidence": 85,
                "category": "sec",
                "severity": "warning",
                "description": "B",
            },
            {
                "reviewer_role": "style",
                "confidence": 60,
                "category": "style",
                "severity": "info",
                "description": "C",
            },
        ]
        with patch("trw_mcp.tools._review_helpers._get_git_diff", return_value=""):
            result = handle_auto_mode(config, run_dir, "review-auto", "2026-03-01T00:00:00Z", reviewer_findings)
        assert result["surfaced_findings_count"] == 3
        assert result["total_findings_count"] == 3

    def test_uses_precollected_findings_when_provided(self, run_dir: Path) -> None:
        """When reviewer_findings is not None, _run_multi_reviewer_analysis is not called."""
        config = _make_config(confidence_threshold=0)
        reviewer_findings = [
            {
                "reviewer_role": "correctness",
                "confidence": 90,
                "category": "logic",
                "severity": "warning",
                "description": "Pre-collected",
            },
        ]
        with (
            patch("trw_mcp.tools._review_helpers._get_git_diff", return_value="some diff"),
            patch("trw_mcp.tools._review_helpers._run_multi_reviewer_analysis") as mock_analysis,
        ):
            result = handle_auto_mode(config, run_dir, "review-auto", "2026-03-01T00:00:00Z", reviewer_findings)
        mock_analysis.assert_not_called()
        assert result["surfaced_findings_count"] == 1

    def test_calls_multi_reviewer_analysis_when_reviewer_findings_is_none(
        self,
        run_dir: Path,
    ) -> None:
        """When reviewer_findings is None, _run_multi_reviewer_analysis is called."""
        config = _make_config(confidence_threshold=0)
        fake_analysis = {
            "reviewer_roles_run": ["correctness"],
            "reviewer_errors": [],
            "findings": [],
        }
        with (
            patch("trw_mcp.tools._review_helpers._get_git_diff", return_value=""),
            patch(
                "trw_mcp.tools._review_helpers._run_multi_reviewer_analysis",
                return_value=fake_analysis,
            ) as mock_analysis,
        ):
            handle_auto_mode(config, run_dir, "review-auto", "2026-03-01T00:00:00Z", None)
        mock_analysis.assert_called_once()

    def test_confidence_threshold_in_result(self, run_dir: Path) -> None:
        config = _make_config(confidence_threshold=75)
        with patch("trw_mcp.tools._review_helpers._get_git_diff", return_value=""):
            result = handle_auto_mode(config, run_dir, "review-auto", "2026-03-01T00:00:00Z", None)
        assert result["confidence_threshold"] == 75

    def test_mode_field_is_auto(self, run_dir: Path) -> None:
        config = _make_config(confidence_threshold=0)
        with patch("trw_mcp.tools._review_helpers._get_git_diff", return_value=""):
            result = handle_auto_mode(config, run_dir, "review-auto", "2026-03-01T00:00:00Z", None)
        assert result["mode"] == "auto"

    def test_review_id_in_result(self, run_dir: Path) -> None:
        config = _make_config(confidence_threshold=0)
        with patch("trw_mcp.tools._review_helpers._get_git_diff", return_value=""):
            result = handle_auto_mode(config, run_dir, "review-id-check", "2026-03-01T00:00:00Z", None)
        assert result["review_id"] == "review-id-check"

    def test_reviewer_roles_run_in_result_from_precollected(self, run_dir: Path) -> None:
        """reviewer_roles_run comes from REVIEWER_ROLES when using pre-collected findings."""
        from trw_mcp.tools._review_helpers import REVIEWER_ROLES

        config = _make_config(confidence_threshold=0)
        with patch("trw_mcp.tools._review_helpers._get_git_diff", return_value=""):
            result = handle_auto_mode(config, run_dir, "review-auto", "2026-03-01T00:00:00Z", [])
        assert result["reviewer_roles_run"] == list(REVIEWER_ROLES)

    def test_non_dict_items_in_reviewer_findings_are_skipped(self, run_dir: Path) -> None:
        """Non-dict items in reviewer_findings are gracefully skipped."""
        config = _make_config(confidence_threshold=0)
        reviewer_findings = [
            None,
            "not-a-dict",
            {
                "reviewer_role": "correctness",
                "confidence": 90,
                "category": "logic",
                "severity": "info",
                "description": "Valid",
            },
        ]
        with patch("trw_mcp.tools._review_helpers._get_git_diff", return_value=""):
            result = handle_auto_mode(config, run_dir, "review-auto", "2026-03-01T00:00:00Z", reviewer_findings)  # type: ignore[arg-type]
        assert result["surfaced_findings_count"] == 1

    def test_finding_missing_confidence_defaults_to_zero(self, run_dir: Path) -> None:
        """Finding without confidence key defaults to 0 → filtered at threshold > 0."""
        config = _make_config(confidence_threshold=80)
        reviewer_findings = [
            {
                "reviewer_role": "correctness",
                "category": "logic",
                "severity": "critical",
                "description": "No confidence field",
            },
        ]
        with patch("trw_mcp.tools._review_helpers._get_git_diff", return_value=""):
            result = handle_auto_mode(config, run_dir, "review-auto", "2026-03-01T00:00:00Z", reviewer_findings)
        assert result["surfaced_findings_count"] == 0
