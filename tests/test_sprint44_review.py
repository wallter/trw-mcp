"""Tests for Sprint 44 review changes — confidence filtering, SOC 2 fields, integration review.

Coverage:
- Confidence threshold filtering uses float 0.0-1.0 scale (INFRA-028-FR06)
- review-all.yaml gets all findings, review.yaml gets only high-confidence ones
- integration-review.yaml verdict computation (block/warn/pass)
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from trw_mcp.models.config import TRWConfig


class TestConfidenceThresholdFiltering:
    """Tests for float-scale confidence threshold filtering in handle_auto_mode."""

    def _make_config(self, threshold: float = 0.8) -> TRWConfig:
        cfg = TRWConfig()
        object.__setattr__(cfg, "confidence_threshold", threshold)
        return cfg

    def _make_findings(self) -> list[dict[str, object]]:
        return [
            {"category": "quality", "severity": "info", "description": "low conf", "confidence": 0.5, "reviewer_role": "quality"},
            {"category": "security", "severity": "warning", "description": "mid conf", "confidence": 0.75, "reviewer_role": "security"},
            {"category": "security", "severity": "critical", "description": "high conf", "confidence": 0.9, "reviewer_role": "security"},
            {"category": "quality", "severity": "info", "description": "exact threshold", "confidence": 0.8, "reviewer_role": "quality"},
        ]

    @patch("trw_mcp.tools.review._get_git_diff", return_value="diff content")
    @patch("trw_mcp.tools.review._run_multi_reviewer_analysis")
    @patch("trw_mcp.tools.review._persist_review")
    def test_confidence_threshold_filters_low_findings(
        self,
        mock_persist: MagicMock,
        mock_analysis: MagicMock,
        mock_diff: MagicMock,
        tmp_path: Path,
    ) -> None:
        from trw_mcp.tools._review_helpers import handle_auto_mode

        findings = self._make_findings()
        mock_analysis.return_value = {
            "reviewer_roles_run": ["quality", "security"],
            "reviewer_errors": [],
            "findings": findings,
        }
        mock_persist.return_value = str(tmp_path / "meta" / "review.yaml")

        config = self._make_config(threshold=0.8)
        run_path = tmp_path
        (run_path / "meta").mkdir(parents=True, exist_ok=True)

        result = handle_auto_mode(config, run_path, "rev-001", "2026-03-03T00:00:00Z", None)

        # Only findings with confidence >= 0.8 should be surfaced
        assert result["surfaced_findings_count"] == 2  # 0.9 and 0.8
        assert result["total_findings_count"] == 4

    @patch("trw_mcp.tools.review._get_git_diff", return_value="diff content")
    @patch("trw_mcp.tools.review._run_multi_reviewer_analysis")
    @patch("trw_mcp.tools.review._persist_review")
    def test_confidence_threshold_zero_passes_all(
        self,
        mock_persist: MagicMock,
        mock_analysis: MagicMock,
        mock_diff: MagicMock,
        tmp_path: Path,
    ) -> None:
        from trw_mcp.tools._review_helpers import handle_auto_mode

        findings = self._make_findings()
        mock_analysis.return_value = {
            "reviewer_roles_run": [],
            "reviewer_errors": [],
            "findings": findings,
        }
        mock_persist.return_value = str(tmp_path / "meta" / "review.yaml")

        config = self._make_config(threshold=0.0)
        run_path = tmp_path
        (run_path / "meta").mkdir(parents=True, exist_ok=True)

        result = handle_auto_mode(config, run_path, "rev-002", "2026-03-03T00:00:00Z", None)

        # All 4 findings pass threshold of 0.0
        assert result["surfaced_findings_count"] == 4

    @patch("trw_mcp.tools.review._get_git_diff", return_value="diff")
    @patch("trw_mcp.tools.review._run_multi_reviewer_analysis")
    @patch("trw_mcp.tools.review._persist_review")
    def test_review_all_yaml_written_with_all_findings(
        self,
        mock_persist: MagicMock,
        mock_analysis: MagicMock,
        mock_diff: MagicMock,
        tmp_path: Path,
    ) -> None:
        from trw_mcp.state.persistence import FileStateReader
        from trw_mcp.tools._review_helpers import handle_auto_mode

        findings = self._make_findings()
        mock_analysis.return_value = {
            "reviewer_roles_run": [],
            "reviewer_errors": [],
            "findings": findings,
        }
        mock_persist.return_value = str(tmp_path / "meta" / "review.yaml")

        config = self._make_config(threshold=0.9)  # Only 1 passes
        run_path = tmp_path
        (run_path / "meta").mkdir(parents=True, exist_ok=True)

        handle_auto_mode(config, run_path, "rev-003", "2026-03-03T00:00:00Z", None)

        reader = FileStateReader()
        review_all = reader.read_yaml(run_path / "meta" / "review-all.yaml")
        assert review_all["total_findings_count"] == 4
        all_findings = review_all["findings"]
        assert isinstance(all_findings, list)
        assert len(all_findings) == 4


class TestIntegrationReviewVerdictComputation:
    """Tests for integration-review.yaml verdict computation."""

    @patch("trw_mcp.tools.review._get_git_diff", return_value="diff")
    @patch("trw_mcp.tools.review._run_multi_reviewer_analysis")
    @patch("trw_mcp.tools.review._persist_review")
    def test_integration_review_block_when_critical(
        self,
        mock_persist: MagicMock,
        mock_analysis: MagicMock,
        mock_diff: MagicMock,
        tmp_path: Path,
    ) -> None:
        from trw_mcp.state.persistence import FileStateReader
        from trw_mcp.tools._review_helpers import handle_auto_mode

        findings = [
            {
                "category": "integration",
                "severity": "critical",
                "description": "API contract mismatch",
                "reviewer_role": "integration",
                "confidence": 0.95,
            }
        ]
        mock_analysis.return_value = {
            "reviewer_roles_run": ["integration"],
            "reviewer_errors": [],
            "findings": findings,
        }
        mock_persist.return_value = str(tmp_path / "meta" / "review.yaml")

        config = TRWConfig()
        run_path = tmp_path
        (run_path / "meta").mkdir(parents=True, exist_ok=True)

        handle_auto_mode(config, run_path, "rev-004", "2026-03-03T00:00:00Z", None)

        reader = FileStateReader()
        int_review = reader.read_yaml(run_path / "meta" / "integration-review.yaml")
        assert int_review["verdict"] == "block"

    @patch("trw_mcp.tools.review._get_git_diff", return_value="diff")
    @patch("trw_mcp.tools.review._run_multi_reviewer_analysis")
    @patch("trw_mcp.tools.review._persist_review")
    def test_integration_review_warn_when_no_critical(
        self,
        mock_persist: MagicMock,
        mock_analysis: MagicMock,
        mock_diff: MagicMock,
        tmp_path: Path,
    ) -> None:
        from trw_mcp.state.persistence import FileStateReader
        from trw_mcp.tools._review_helpers import handle_auto_mode

        findings = [
            {
                "category": "integration",
                "severity": "warning",
                "description": "Import not found",
                "reviewer_role": "integration",
                "confidence": 0.85,
            }
        ]
        mock_analysis.return_value = {
            "reviewer_roles_run": ["integration"],
            "reviewer_errors": [],
            "findings": findings,
        }
        mock_persist.return_value = str(tmp_path / "meta" / "review.yaml")

        config = TRWConfig()
        run_path = tmp_path
        (run_path / "meta").mkdir(parents=True, exist_ok=True)

        handle_auto_mode(config, run_path, "rev-005", "2026-03-03T00:00:00Z", None)

        reader = FileStateReader()
        int_review = reader.read_yaml(run_path / "meta" / "integration-review.yaml")
        assert int_review["verdict"] == "warn"

    @patch("trw_mcp.tools.review._get_git_diff", return_value="diff")
    @patch("trw_mcp.tools.review._run_multi_reviewer_analysis")
    @patch("trw_mcp.tools.review._persist_review")
    def test_no_integration_review_when_no_integration_findings(
        self,
        mock_persist: MagicMock,
        mock_analysis: MagicMock,
        mock_diff: MagicMock,
        tmp_path: Path,
    ) -> None:
        from trw_mcp.tools._review_helpers import handle_auto_mode

        findings = [
            {
                "category": "quality",
                "severity": "info",
                "description": "Style issue",
                "reviewer_role": "quality",
                "confidence": 0.9,
            }
        ]
        mock_analysis.return_value = {
            "reviewer_roles_run": ["quality"],
            "reviewer_errors": [],
            "findings": findings,
        }
        mock_persist.return_value = str(tmp_path / "meta" / "review.yaml")

        config = TRWConfig()
        run_path = tmp_path
        (run_path / "meta").mkdir(parents=True, exist_ok=True)

        handle_auto_mode(config, run_path, "rev-006", "2026-03-03T00:00:00Z", None)

        # No integration-review.yaml should be written
        assert not (run_path / "meta" / "integration-review.yaml").exists()

    @patch("trw_mcp.tools.review._get_git_diff", return_value="diff")
    @patch("trw_mcp.tools.review._run_multi_reviewer_analysis")
    @patch("trw_mcp.tools.review._persist_review")
    def test_soc2_fields_in_review_yaml(
        self,
        mock_persist: MagicMock,
        mock_analysis: MagicMock,
        mock_diff: MagicMock,
        tmp_path: Path,
    ) -> None:
        from trw_mcp.tools._review_helpers import handle_auto_mode

        mock_analysis.return_value = {
            "reviewer_roles_run": [],
            "reviewer_errors": [],
            "findings": [],
        }
        mock_persist.return_value = str(tmp_path / "meta" / "review.yaml")

        config = TRWConfig()
        run_path = tmp_path
        (run_path / "meta").mkdir(parents=True, exist_ok=True)

        handle_auto_mode(config, run_path, "rev-007", "2026-03-03T00:00:00Z", None)

        # Verify _persist_review was called with SOC 2 fields
        call_args = mock_persist.call_args
        full_data = call_args[0][1]  # second positional arg
        assert "reviewer_id" in full_data
        assert "reviewer_role" in full_data
        assert "git_diff_hash" in full_data
        assert "human_escalation_path" in full_data
        assert "retention_expires" in full_data
