"""Tests for _review_helpers.py — finding validation, severity counting, mode handlers.

Covers:
- validate_manual_findings: valid pass-through, invalid severity normalization,
  empty list, ReviewFinding validation failures
- count_by_severity: mixed, empty, unknown severity
- handle_manual_mode: verdict/counts, persist with run, no run, empty findings
- handle_cross_model_mode: disabled config, empty diff, findings from provider
- handle_auto_mode: confidence filtering, pre-collected reviewer_findings,
  supplementary artifact writes (review-all.yaml, integration-review.yaml)
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from trw_mcp.models.config import TRWConfig, _reset_config
from trw_mcp.state.persistence import FileStateReader
from trw_mcp.tools._review_helpers import (
    count_by_severity,
    handle_auto_mode,
    handle_cross_model_mode,
    handle_manual_mode,
    validate_manual_findings,
)


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def run_dir(tmp_path: Path) -> Path:
    """Create a minimal run directory with meta/ and events.jsonl."""
    d = tmp_path / "docs" / "task" / "runs" / "20260301T120000Z-helpers-test"
    meta = d / "meta"
    meta.mkdir(parents=True)
    (meta / "run.yaml").write_text(
        "run_id: helpers-test\nstatus: active\nphase: review\ntask_name: helpers-task\n",
        encoding="utf-8",
    )
    (meta / "events.jsonl").write_text("", encoding="utf-8")
    return d


def _make_config(
    *,
    cross_model_enabled: bool = False,
    cross_model_provider: str = "gemini-2.5-pro",
    confidence_threshold: int = 80,
) -> TRWConfig:
    return TRWConfig(
        cross_model_review_enabled=cross_model_enabled,
        cross_model_provider=cross_model_provider,
        review_confidence_threshold=confidence_threshold,
    )


# ---------------------------------------------------------------------------
# validate_manual_findings
# ---------------------------------------------------------------------------


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
        """Finding that fails ReviewFinding validation gets severity normalized."""
        # Missing required fields forces the except path -> _normalize_severity
        finding: dict[str, str] = {"severity": "high"}
        result = validate_manual_findings([finding])
        assert len(result) == 1
        # "high" normalizes to "critical"
        assert result[0]["severity"] == "critical"

    def test_invalid_severity_medium_normalized_to_warning(self) -> None:
        finding: dict[str, str] = {"severity": "medium"}
        result = validate_manual_findings([finding])
        assert result[0]["severity"] == "warning"

    def test_invalid_severity_error_normalized_to_critical(self) -> None:
        finding: dict[str, str] = {"severity": "error"}
        result = validate_manual_findings([finding])
        assert result[0]["severity"] == "critical"

    def test_invalid_severity_unknown_normalized_to_info(self) -> None:
        finding: dict[str, str] = {"severity": "bogus"}
        result = validate_manual_findings([finding])
        assert result[0]["severity"] == "info"

    def test_valid_finding_with_unknown_severity_normalized_to_info(self) -> None:
        """A finding that passes ReviewFinding but has non-canonical severity gets info."""
        # ReviewFinding accepts any string for severity — so this passes validation
        # but the post-validation check normalizes it to "info"
        finding = {
            "category": "style",
            "severity": "unknown-level",
            "description": "Something",
        }
        result = validate_manual_findings([finding])
        assert len(result) == 1
        assert result[0]["severity"] == "info"

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

    def test_findings_missing_severity_key_uses_info_default(self) -> None:
        """Finding with no severity key falls back to 'info' via get default."""
        finding: dict[str, str] = {}
        result = validate_manual_findings([finding])
        assert len(result) == 1
        # The except path uses _normalize_severity(f.get("severity", "info")) → "info"
        assert result[0]["severity"] == "info"

    def test_returns_list_type(self) -> None:
        result = validate_manual_findings([])
        assert isinstance(result, list)


# ---------------------------------------------------------------------------
# count_by_severity
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# handle_manual_mode
# ---------------------------------------------------------------------------


class TestHandleManualMode:
    """handle_manual_mode: verdict computation, persistence, edge cases."""

    def test_empty_findings_returns_pass_verdict(self, run_dir: Path) -> None:
        result = handle_manual_mode([], run_dir, "review-test", "2026-03-01T00:00:00Z")
        assert result["verdict"] == "pass"

    def test_critical_finding_returns_block_verdict(self, run_dir: Path) -> None:
        findings = [{"category": "correctness", "severity": "critical", "description": "Bug"}]
        result = handle_manual_mode(findings, run_dir, "review-test", "2026-03-01T00:00:00Z")
        assert result["verdict"] == "block"

    def test_warning_finding_returns_warn_verdict(self, run_dir: Path) -> None:
        findings = [{"category": "style", "severity": "warning", "description": "Nit"}]
        result = handle_manual_mode(findings, run_dir, "review-test", "2026-03-01T00:00:00Z")
        assert result["verdict"] == "warn"

    def test_counts_match_findings(self, run_dir: Path) -> None:
        findings = [
            {"category": "correctness", "severity": "critical", "description": "A"},
            {"category": "style", "severity": "warning", "description": "B"},
            {"category": "docs", "severity": "info", "description": "C"},
        ]
        result = handle_manual_mode(findings, run_dir, "review-test", "2026-03-01T00:00:00Z")
        assert result["critical_count"] == 1
        assert result["warning_count"] == 1
        assert result["info_count"] == 1
        assert result["total_findings"] == 3

    def test_persists_review_yaml_when_run_exists(self, run_dir: Path) -> None:
        findings = [{"category": "style", "severity": "warning", "description": "Nit"}]
        result = handle_manual_mode(findings, run_dir, "review-abc", "2026-03-01T00:00:00Z")
        review_path = run_dir / "meta" / "review.yaml"
        assert review_path.exists()
        assert result["review_yaml"] == str(review_path)

    def test_review_yaml_contains_correct_verdict(self, run_dir: Path) -> None:
        findings = [{"category": "correctness", "severity": "critical", "description": "Bug"}]
        handle_manual_mode(findings, run_dir, "review-abc", "2026-03-01T00:00:00Z")
        data = FileStateReader().read_yaml(run_dir / "meta" / "review.yaml")
        assert data["verdict"] == "block"

    def test_review_yaml_contains_review_id(self, run_dir: Path) -> None:
        handle_manual_mode([], run_dir, "review-xyz", "2026-03-01T00:00:00Z")
        data = FileStateReader().read_yaml(run_dir / "meta" / "review.yaml")
        assert data["review_id"] == "review-xyz"

    def test_no_run_returns_empty_review_yaml(self) -> None:
        findings = [{"category": "style", "severity": "info", "description": "Note"}]
        result = handle_manual_mode(findings, None, "review-none", "2026-03-01T00:00:00Z")
        assert result["review_yaml"] == ""

    def test_no_run_returns_none_run_path(self) -> None:
        result = handle_manual_mode([], None, "review-none", "2026-03-01T00:00:00Z")
        assert result["run_path"] is None

    def test_run_path_in_result_matches_run_dir(self, run_dir: Path) -> None:
        result = handle_manual_mode([], run_dir, "review-abc", "2026-03-01T00:00:00Z")
        assert result["run_path"] == str(run_dir)

    def test_result_contains_review_id(self, run_dir: Path) -> None:
        result = handle_manual_mode([], run_dir, "review-id-check", "2026-03-01T00:00:00Z")
        assert result["review_id"] == "review-id-check"

    def test_empty_findings_zero_counts(self, run_dir: Path) -> None:
        result = handle_manual_mode([], run_dir, "review-test", "2026-03-01T00:00:00Z")
        assert result["critical_count"] == 0
        assert result["warning_count"] == 0
        assert result["info_count"] == 0
        assert result["total_findings"] == 0


# ---------------------------------------------------------------------------
# handle_cross_model_mode
# ---------------------------------------------------------------------------


class TestHandleCrossModelMode:
    """handle_cross_model_mode: enabled/disabled, diff, provider findings."""

    def test_disabled_config_sets_cross_model_skipped_true(self, run_dir: Path) -> None:
        config = _make_config(cross_model_enabled=False)
        with patch("trw_mcp.tools.review._get_git_diff", return_value="some diff"):
            result = handle_cross_model_mode(config, run_dir, "review-cm", "2026-03-01T00:00:00Z")
        assert result["cross_model_skipped"] is True

    def test_disabled_config_returns_pass_verdict(self, run_dir: Path) -> None:
        config = _make_config(cross_model_enabled=False)
        with patch("trw_mcp.tools.review._get_git_diff", return_value="some diff"):
            result = handle_cross_model_mode(config, run_dir, "review-cm", "2026-03-01T00:00:00Z")
        assert result["verdict"] == "pass"

    def test_empty_diff_sets_cross_model_skipped_true(self, run_dir: Path) -> None:
        config = _make_config(cross_model_enabled=True)
        with patch("trw_mcp.tools.review._get_git_diff", return_value=""):
            result = handle_cross_model_mode(config, run_dir, "review-cm", "2026-03-01T00:00:00Z")
        assert result["cross_model_skipped"] is True

    def test_enabled_with_diff_but_stub_returns_empty_skipped(self, run_dir: Path) -> None:
        """When enabled and diff exists but _invoke returns [], skipped=True."""
        config = _make_config(cross_model_enabled=True)
        with patch("trw_mcp.tools.review._get_git_diff", return_value="diff content"):
            result = handle_cross_model_mode(config, run_dir, "review-cm", "2026-03-01T00:00:00Z")
        # _invoke_cross_model_review is a stub returning [] -> skipped
        assert result["cross_model_skipped"] is True

    def test_mode_field_is_cross_model(self, run_dir: Path) -> None:
        config = _make_config(cross_model_enabled=False)
        with patch("trw_mcp.tools.review._get_git_diff", return_value=""):
            result = handle_cross_model_mode(config, run_dir, "review-cm", "2026-03-01T00:00:00Z")
        assert result["mode"] == "cross_model"

    def test_provider_field_in_result(self, run_dir: Path) -> None:
        config = _make_config(cross_model_enabled=False, cross_model_provider="test-provider")
        with patch("trw_mcp.tools.review._get_git_diff", return_value=""):
            result = handle_cross_model_mode(config, run_dir, "review-cm", "2026-03-01T00:00:00Z")
        assert result["cross_model_provider"] == "test-provider"

    def test_persists_review_yaml_with_mode_field(self, run_dir: Path) -> None:
        config = _make_config(cross_model_enabled=False)
        with patch("trw_mcp.tools.review._get_git_diff", return_value=""):
            result = handle_cross_model_mode(config, run_dir, "review-cm", "2026-03-01T00:00:00Z")
        review_path = run_dir / "meta" / "review.yaml"
        assert review_path.exists()
        data = FileStateReader().read_yaml(review_path)
        assert data["mode"] == "cross_model"
        assert result["review_yaml"] == str(review_path)

    def test_no_run_returns_empty_review_yaml(self) -> None:
        config = _make_config(cross_model_enabled=False)
        with patch("trw_mcp.tools.review._get_git_diff", return_value=""):
            result = handle_cross_model_mode(config, None, "review-none", "2026-03-01T00:00:00Z")
        assert result["review_yaml"] == ""
        assert result["run_path"] is None

    def test_findings_from_provider_normalize_severity(self, run_dir: Path) -> None:
        """When _invoke_cross_model_review returns findings, severity is normalized."""
        config = _make_config(cross_model_enabled=True)
        stub_findings = [
            {"category": "security", "severity": "high", "description": "High severity"},
            {"category": "style", "severity": "medium", "description": "Medium severity"},
        ]
        with (
            patch("trw_mcp.tools.review._get_git_diff", return_value="diff content"),
            patch(
                "trw_mcp.tools.review._invoke_cross_model_review",
                return_value=stub_findings,
            ),
        ):
            result = handle_cross_model_mode(config, run_dir, "review-cm", "2026-03-01T00:00:00Z")

        data = FileStateReader().read_yaml(run_dir / "meta" / "review.yaml")
        severities = [f["severity"] for f in data["cross_model_findings"]]
        assert "critical" in severities  # "high" → "critical"
        assert "warning" in severities   # "medium" → "warning"
        assert result["cross_model_skipped"] is False
        assert result["verdict"] == "block"

    def test_findings_from_provider_tagged_with_source_and_provider(self, run_dir: Path) -> None:
        """Findings from provider are tagged with source='cross_model'."""
        config = _make_config(cross_model_enabled=True, cross_model_provider="gpt-4o")
        stub_findings = [
            {"category": "correctness", "severity": "warning", "description": "Issue"},
        ]
        with (
            patch("trw_mcp.tools.review._get_git_diff", return_value="diff content"),
            patch(
                "trw_mcp.tools.review._invoke_cross_model_review",
                return_value=stub_findings,
            ),
        ):
            handle_cross_model_mode(config, run_dir, "review-cm", "2026-03-01T00:00:00Z")

        data = FileStateReader().read_yaml(run_dir / "meta" / "review.yaml")
        assert data["cross_model_findings"][0]["source"] == "cross_model"
        assert data["cross_model_findings"][0]["provider"] == "gpt-4o"

    def test_review_id_in_result(self, run_dir: Path) -> None:
        config = _make_config(cross_model_enabled=False)
        with patch("trw_mcp.tools.review._get_git_diff", return_value=""):
            result = handle_cross_model_mode(
                config, run_dir, "review-id-check", "2026-03-01T00:00:00Z"
            )
        assert result["review_id"] == "review-id-check"

    def test_total_findings_zero_when_skipped(self, run_dir: Path) -> None:
        config = _make_config(cross_model_enabled=False)
        with patch("trw_mcp.tools.review._get_git_diff", return_value=""):
            result = handle_cross_model_mode(config, run_dir, "review-cm", "2026-03-01T00:00:00Z")
        assert result["total_findings"] == 0


# ---------------------------------------------------------------------------
# handle_auto_mode
# ---------------------------------------------------------------------------


class TestHandleAutoMode:
    """handle_auto_mode: confidence filtering, pre-collected findings, artifacts."""

    def test_filters_findings_below_threshold(self, run_dir: Path) -> None:
        config = _make_config(confidence_threshold=80)
        reviewer_findings = [
            {"reviewer_role": "correctness", "confidence": 90, "category": "logic",
             "severity": "warning", "description": "High confidence"},
            {"reviewer_role": "style", "confidence": 50, "category": "style",
             "severity": "info", "description": "Low confidence"},
        ]
        with patch("trw_mcp.tools.review._get_git_diff", return_value=""):
            result = handle_auto_mode(config, run_dir, "review-auto", "2026-03-01T00:00:00Z",
                                      reviewer_findings)
        assert result["surfaced_findings_count"] == 1
        assert result["total_findings_count"] == 2

    def test_filters_at_exact_threshold(self, run_dir: Path) -> None:
        """Finding at exactly the threshold is surfaced (>=)."""
        config = _make_config(confidence_threshold=75)
        reviewer_findings = [
            {"reviewer_role": "security", "confidence": 75, "category": "sec",
             "severity": "critical", "description": "At threshold"},
            {"reviewer_role": "style", "confidence": 74, "category": "style",
             "severity": "warning", "description": "Below threshold"},
        ]
        with patch("trw_mcp.tools.review._get_git_diff", return_value=""):
            result = handle_auto_mode(config, run_dir, "review-auto", "2026-03-01T00:00:00Z",
                                      reviewer_findings)
        assert result["surfaced_findings_count"] == 1

    def test_verdict_from_surfaced_only(self, run_dir: Path) -> None:
        """Verdict computed from surfaced findings; filtered critical does not block."""
        config = _make_config(confidence_threshold=80)
        reviewer_findings = [
            {"reviewer_role": "correctness", "confidence": 40, "category": "logic",
             "severity": "critical", "description": "Low confidence critical"},
            {"reviewer_role": "style", "confidence": 90, "category": "style",
             "severity": "warning", "description": "High confidence warning"},
        ]
        with patch("trw_mcp.tools.review._get_git_diff", return_value=""):
            result = handle_auto_mode(config, run_dir, "review-auto", "2026-03-01T00:00:00Z",
                                      reviewer_findings)
        assert result["verdict"] == "warn"

    def test_all_below_threshold_verdict_is_pass(self, run_dir: Path) -> None:
        config = _make_config(confidence_threshold=80)
        reviewer_findings = [
            {"reviewer_role": "correctness", "confidence": 20, "category": "logic",
             "severity": "critical", "description": "Low confidence critical"},
        ]
        with patch("trw_mcp.tools.review._get_git_diff", return_value=""):
            result = handle_auto_mode(config, run_dir, "review-auto", "2026-03-01T00:00:00Z",
                                      reviewer_findings)
        assert result["surfaced_findings_count"] == 0
        assert result["verdict"] == "pass"

    def test_all_above_threshold_all_surfaced(self, run_dir: Path) -> None:
        config = _make_config(confidence_threshold=50)
        reviewer_findings = [
            {"reviewer_role": "correctness", "confidence": 90, "category": "logic",
             "severity": "info", "description": "A"},
            {"reviewer_role": "security", "confidence": 85, "category": "sec",
             "severity": "warning", "description": "B"},
            {"reviewer_role": "style", "confidence": 60, "category": "style",
             "severity": "info", "description": "C"},
        ]
        with patch("trw_mcp.tools.review._get_git_diff", return_value=""):
            result = handle_auto_mode(config, run_dir, "review-auto", "2026-03-01T00:00:00Z",
                                      reviewer_findings)
        assert result["surfaced_findings_count"] == 3
        assert result["total_findings_count"] == 3

    def test_uses_precollected_findings_when_provided(self, run_dir: Path) -> None:
        """When reviewer_findings is not None, _run_multi_reviewer_analysis is not called."""
        config = _make_config(confidence_threshold=0)
        reviewer_findings = [
            {"reviewer_role": "correctness", "confidence": 90, "category": "logic",
             "severity": "warning", "description": "Pre-collected"},
        ]
        with (
            patch("trw_mcp.tools.review._get_git_diff", return_value="some diff"),
            patch(
                "trw_mcp.tools.review._run_multi_reviewer_analysis",
            ) as mock_analysis,
        ):
            result = handle_auto_mode(config, run_dir, "review-auto", "2026-03-01T00:00:00Z",
                                      reviewer_findings)
        # _run_multi_reviewer_analysis must NOT be called when reviewer_findings provided
        mock_analysis.assert_not_called()
        assert result["surfaced_findings_count"] == 1

    def test_calls_multi_reviewer_analysis_when_reviewer_findings_is_none(
        self, run_dir: Path,
    ) -> None:
        """When reviewer_findings is None, _run_multi_reviewer_analysis is called."""
        config = _make_config(confidence_threshold=0)
        fake_analysis = {
            "reviewer_roles_run": ["correctness"],
            "reviewer_errors": [],
            "findings": [],
        }
        with (
            patch("trw_mcp.tools.review._get_git_diff", return_value=""),
            patch(
                "trw_mcp.tools.review._run_multi_reviewer_analysis",
                return_value=fake_analysis,
            ) as mock_analysis,
        ):
            handle_auto_mode(config, run_dir, "review-auto", "2026-03-01T00:00:00Z", None)
        mock_analysis.assert_called_once()

    def test_writes_review_yaml_with_surfaced_findings(self, run_dir: Path) -> None:
        config = _make_config(confidence_threshold=80)
        reviewer_findings = [
            {"reviewer_role": "correctness", "confidence": 95, "category": "logic",
             "severity": "warning", "description": "Surfaced"},
            {"reviewer_role": "style", "confidence": 30, "category": "style",
             "severity": "info", "description": "Filtered"},
        ]
        with patch("trw_mcp.tools.review._get_git_diff", return_value=""):
            handle_auto_mode(config, run_dir, "review-auto", "2026-03-01T00:00:00Z",
                             reviewer_findings)
        review_path = run_dir / "meta" / "review.yaml"
        assert review_path.exists()
        data = FileStateReader().read_yaml(review_path)
        assert data["mode"] == "auto"
        assert data["surfaced_findings_count"] == 1
        assert len(data["findings"]) == 1

    def test_writes_review_all_yaml_with_all_findings(self, run_dir: Path) -> None:
        config = _make_config(confidence_threshold=80)
        reviewer_findings = [
            {"reviewer_role": "correctness", "confidence": 95, "category": "logic",
             "severity": "warning", "description": "High"},
            {"reviewer_role": "style", "confidence": 30, "category": "style",
             "severity": "info", "description": "Low"},
        ]
        with patch("trw_mcp.tools.review._get_git_diff", return_value=""):
            handle_auto_mode(config, run_dir, "review-auto", "2026-03-01T00:00:00Z",
                             reviewer_findings)
        review_all_path = run_dir / "meta" / "review-all.yaml"
        assert review_all_path.exists()
        data = FileStateReader().read_yaml(review_all_path)
        assert data["total_findings_count"] == 2
        assert len(data["findings"]) == 2

    def test_writes_integration_review_yaml_when_integration_findings_exist(
        self, run_dir: Path,
    ) -> None:
        config = _make_config(confidence_threshold=0)
        reviewer_findings = [
            {"reviewer_role": "integration", "confidence": 90, "category": "wiring",
             "severity": "warning", "description": "Integration issue"},
            {"reviewer_role": "correctness", "confidence": 85, "category": "logic",
             "severity": "info", "description": "Normal finding"},
        ]
        with patch("trw_mcp.tools.review._get_git_diff", return_value=""):
            handle_auto_mode(config, run_dir, "review-auto", "2026-03-01T00:00:00Z",
                             reviewer_findings)
        integration_path = run_dir / "meta" / "integration-review.yaml"
        assert integration_path.exists()
        data = FileStateReader().read_yaml(integration_path)
        assert len(data["findings"]) == 1
        assert data["findings"][0]["reviewer_role"] == "integration"

    def test_does_not_write_integration_review_yaml_when_none(self, run_dir: Path) -> None:
        config = _make_config(confidence_threshold=0)
        reviewer_findings = [
            {"reviewer_role": "correctness", "confidence": 90, "category": "logic",
             "severity": "info", "description": "No integration findings"},
        ]
        with patch("trw_mcp.tools.review._get_git_diff", return_value=""):
            handle_auto_mode(config, run_dir, "review-auto", "2026-03-01T00:00:00Z",
                             reviewer_findings)
        integration_path = run_dir / "meta" / "integration-review.yaml"
        assert not integration_path.exists()

    def test_no_run_returns_empty_review_yaml(self) -> None:
        config = _make_config(confidence_threshold=0)
        with patch("trw_mcp.tools.review._get_git_diff", return_value=""):
            result = handle_auto_mode(config, None, "review-none", "2026-03-01T00:00:00Z", None)
        assert result["review_yaml"] == ""
        assert result["run_path"] is None

    def test_no_run_does_not_write_review_all_yaml(self, tmp_path: Path) -> None:
        """With no run, review-all.yaml is not written."""
        config = _make_config(confidence_threshold=0)
        reviewer_findings = [
            {"reviewer_role": "correctness", "confidence": 90, "category": "logic",
             "severity": "info", "description": "Finding"},
        ]
        with patch("trw_mcp.tools.review._get_git_diff", return_value=""):
            handle_auto_mode(config, None, "review-none", "2026-03-01T00:00:00Z",
                             reviewer_findings)
        # No run path → meta/ never created under tmp_path
        assert not (tmp_path / "meta" / "review-all.yaml").exists()

    def test_confidence_threshold_in_result(self, run_dir: Path) -> None:
        config = _make_config(confidence_threshold=75)
        with patch("trw_mcp.tools.review._get_git_diff", return_value=""):
            result = handle_auto_mode(config, run_dir, "review-auto", "2026-03-01T00:00:00Z",
                                      None)
        assert result["confidence_threshold"] == 75

    def test_mode_field_is_auto(self, run_dir: Path) -> None:
        config = _make_config(confidence_threshold=0)
        with patch("trw_mcp.tools.review._get_git_diff", return_value=""):
            result = handle_auto_mode(config, run_dir, "review-auto", "2026-03-01T00:00:00Z",
                                      None)
        assert result["mode"] == "auto"

    def test_review_id_in_result(self, run_dir: Path) -> None:
        config = _make_config(confidence_threshold=0)
        with patch("trw_mcp.tools.review._get_git_diff", return_value=""):
            result = handle_auto_mode(config, run_dir, "review-id-check",
                                      "2026-03-01T00:00:00Z", None)
        assert result["review_id"] == "review-id-check"

    def test_reviewer_roles_run_in_result_from_precollected(self, run_dir: Path) -> None:
        """reviewer_roles_run comes from REVIEWER_ROLES when using pre-collected findings."""
        from trw_mcp.tools.review import REVIEWER_ROLES
        config = _make_config(confidence_threshold=0)
        with patch("trw_mcp.tools.review._get_git_diff", return_value=""):
            result = handle_auto_mode(config, run_dir, "review-auto",
                                      "2026-03-01T00:00:00Z", [])
        assert result["reviewer_roles_run"] == list(REVIEWER_ROLES)

    def test_non_dict_items_in_reviewer_findings_are_skipped(self, run_dir: Path) -> None:
        """Non-dict items in reviewer_findings are gracefully skipped."""
        config = _make_config(confidence_threshold=0)
        reviewer_findings = [
            None,
            "not-a-dict",
            {"reviewer_role": "correctness", "confidence": 90, "category": "logic",
             "severity": "info", "description": "Valid"},
        ]
        with patch("trw_mcp.tools.review._get_git_diff", return_value=""):
            result = handle_auto_mode(config, run_dir, "review-auto",
                                      "2026-03-01T00:00:00Z", reviewer_findings)  # type: ignore[arg-type]
        assert result["surfaced_findings_count"] == 1

    def test_finding_missing_confidence_defaults_to_zero(self, run_dir: Path) -> None:
        """Finding without confidence key defaults to 0 → filtered at threshold > 0."""
        config = _make_config(confidence_threshold=80)
        reviewer_findings = [
            {"reviewer_role": "correctness", "category": "logic",
             "severity": "critical", "description": "No confidence field"},
        ]
        with patch("trw_mcp.tools.review._get_git_diff", return_value=""):
            result = handle_auto_mode(config, run_dir, "review-auto",
                                      "2026-03-01T00:00:00Z", reviewer_findings)
        assert result["surfaced_findings_count"] == 0

    def test_review_all_yaml_contains_review_id(self, run_dir: Path) -> None:
        config = _make_config(confidence_threshold=0)
        reviewer_findings = [
            {"reviewer_role": "correctness", "confidence": 90, "category": "logic",
             "severity": "info", "description": "Finding"},
        ]
        with patch("trw_mcp.tools.review._get_git_diff", return_value=""):
            handle_auto_mode(config, run_dir, "review-id-check",
                             "2026-03-01T00:00:00Z", reviewer_findings)
        data = FileStateReader().read_yaml(run_dir / "meta" / "review-all.yaml")
        assert data["review_id"] == "review-id-check"

    def test_integration_review_yaml_contains_review_id(self, run_dir: Path) -> None:
        config = _make_config(confidence_threshold=0)
        reviewer_findings = [
            {"reviewer_role": "integration", "confidence": 90, "category": "wiring",
             "severity": "warning", "description": "Integration"},
        ]
        with patch("trw_mcp.tools.review._get_git_diff", return_value=""):
            handle_auto_mode(config, run_dir, "review-id-check",
                             "2026-03-01T00:00:00Z", reviewer_findings)
        data = FileStateReader().read_yaml(run_dir / "meta" / "integration-review.yaml")
        assert data["review_id"] == "review-id-check"
