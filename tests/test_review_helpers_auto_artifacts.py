"""Tests for auto review helper mode artifact persistence."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from tests._review_helpers_support import _make_config
from trw_mcp.state.persistence import FileStateReader
from trw_mcp.tools._review_helpers import handle_auto_mode
from ._review_helpers_support import run_dir  # noqa: F401


class TestHandleAutoMode:
    """handle_auto_mode: artifact writes and no-run behavior."""

    def test_writes_review_yaml_with_surfaced_findings(self, run_dir: Path) -> None:
        config = _make_config(confidence_threshold=80)
        reviewer_findings = [
            {
                "reviewer_role": "correctness",
                "confidence": 95,
                "category": "logic",
                "severity": "warning",
                "description": "Surfaced",
            },
            {
                "reviewer_role": "style",
                "confidence": 30,
                "category": "style",
                "severity": "info",
                "description": "Filtered",
            },
        ]
        with patch("trw_mcp.tools._review_helpers._get_git_diff", return_value=""):
            handle_auto_mode(config, run_dir, "review-auto", "2026-03-01T00:00:00Z", reviewer_findings)
        review_path = run_dir / "meta" / "review.yaml"
        assert review_path.exists()
        data = FileStateReader().read_yaml(review_path)
        assert data["mode"] == "auto"
        assert data["surfaced_findings_count"] == 1
        assert len(data["findings"]) == 1

    def test_writes_review_all_yaml_with_all_findings(self, run_dir: Path) -> None:
        config = _make_config(confidence_threshold=80)
        reviewer_findings = [
            {
                "reviewer_role": "correctness",
                "confidence": 95,
                "category": "logic",
                "severity": "warning",
                "description": "High",
            },
            {
                "reviewer_role": "style",
                "confidence": 30,
                "category": "style",
                "severity": "info",
                "description": "Low",
            },
        ]
        with patch("trw_mcp.tools._review_helpers._get_git_diff", return_value=""):
            handle_auto_mode(config, run_dir, "review-auto", "2026-03-01T00:00:00Z", reviewer_findings)
        review_all_path = run_dir / "meta" / "review-all.yaml"
        assert review_all_path.exists()
        data = FileStateReader().read_yaml(review_all_path)
        assert data["total_findings_count"] == 2
        assert len(data["findings"]) == 2

    def test_writes_integration_review_yaml_when_integration_findings_exist(
        self,
        run_dir: Path,
    ) -> None:
        config = _make_config(confidence_threshold=0)
        reviewer_findings = [
            {
                "reviewer_role": "integration",
                "confidence": 90,
                "category": "wiring",
                "severity": "warning",
                "description": "Integration issue",
            },
            {
                "reviewer_role": "correctness",
                "confidence": 85,
                "category": "logic",
                "severity": "info",
                "description": "Normal finding",
            },
        ]
        with patch("trw_mcp.tools._review_helpers._get_git_diff", return_value=""):
            handle_auto_mode(config, run_dir, "review-auto", "2026-03-01T00:00:00Z", reviewer_findings)
        integration_path = run_dir / "meta" / "integration-review.yaml"
        assert integration_path.exists()
        data = FileStateReader().read_yaml(integration_path)
        assert len(data["findings"]) == 1
        assert data["findings"][0]["reviewer_role"] == "integration"

    def test_does_not_write_integration_review_yaml_when_none(self, run_dir: Path) -> None:
        config = _make_config(confidence_threshold=0)
        reviewer_findings = [
            {
                "reviewer_role": "correctness",
                "confidence": 90,
                "category": "logic",
                "severity": "info",
                "description": "No integration findings",
            },
        ]
        with patch("trw_mcp.tools._review_helpers._get_git_diff", return_value=""):
            handle_auto_mode(config, run_dir, "review-auto", "2026-03-01T00:00:00Z", reviewer_findings)
        integration_path = run_dir / "meta" / "integration-review.yaml"
        assert not integration_path.exists()

    def test_no_run_returns_empty_review_yaml(self) -> None:
        config = _make_config(confidence_threshold=0)
        with patch("trw_mcp.tools._review_helpers._get_git_diff", return_value=""):
            result = handle_auto_mode(config, None, "review-none", "2026-03-01T00:00:00Z", None)
        assert result["review_yaml"] == ""
        assert result["run_path"] is None

    def test_no_run_does_not_write_review_all_yaml(self, tmp_path: Path) -> None:
        """With no run, review-all.yaml is not written."""
        config = _make_config(confidence_threshold=0)
        reviewer_findings = [
            {
                "reviewer_role": "correctness",
                "confidence": 90,
                "category": "logic",
                "severity": "info",
                "description": "Finding",
            },
        ]
        with patch("trw_mcp.tools._review_helpers._get_git_diff", return_value=""):
            handle_auto_mode(config, None, "review-none", "2026-03-01T00:00:00Z", reviewer_findings)
        assert not (tmp_path / "meta" / "review-all.yaml").exists()

    def test_review_all_yaml_contains_review_id(self, run_dir: Path) -> None:
        config = _make_config(confidence_threshold=0)
        reviewer_findings = [
            {
                "reviewer_role": "correctness",
                "confidence": 90,
                "category": "logic",
                "severity": "info",
                "description": "Finding",
            },
        ]
        with patch("trw_mcp.tools._review_helpers._get_git_diff", return_value=""):
            handle_auto_mode(config, run_dir, "review-id-check", "2026-03-01T00:00:00Z", reviewer_findings)
        data = FileStateReader().read_yaml(run_dir / "meta" / "review-all.yaml")
        assert data["review_id"] == "review-id-check"

    def test_integration_review_yaml_contains_review_id(self, run_dir: Path) -> None:
        config = _make_config(confidence_threshold=0)
        reviewer_findings = [
            {
                "reviewer_role": "integration",
                "confidence": 90,
                "category": "wiring",
                "severity": "warning",
                "description": "Integration",
            },
        ]
        with patch("trw_mcp.tools._review_helpers._get_git_diff", return_value=""):
            handle_auto_mode(config, run_dir, "review-id-check", "2026-03-01T00:00:00Z", reviewer_findings)
        data = FileStateReader().read_yaml(run_dir / "meta" / "integration-review.yaml")
        assert data["review_id"] == "review-id-check"
