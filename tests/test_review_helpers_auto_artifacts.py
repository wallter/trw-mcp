"""Tests for auto review helper mode artifact persistence."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from tests._review_helpers_support import _make_config
from trw_mcp.models.run import IntegrationReviewArtifact
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
        artifact = IntegrationReviewArtifact.model_validate(data)
        assert artifact.verdict == "warn"
        assert data["review_id"] == "review-auto"
        assert data["mode"] == "auto"

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

    def test_does_not_write_integration_review_for_malformed_findings(self, run_dir: Path) -> None:
        config = _make_config(confidence_threshold=0)
        with patch("trw_mcp.tools._review_helpers._get_git_diff", return_value=""):
            handle_auto_mode(
                config,
                run_dir,
                "review-auto",
                "2026-03-01T00:00:00Z",
                [{"reviewer_role": "integration"}],
            )

        assert not (run_dir / "meta" / "integration-review.yaml").exists()

    def test_internal_invalid_integration_finding_fails_before_write(self, run_dir: Path) -> None:
        config = _make_config(confidence_threshold=0)
        invalid_analysis = {
            "reviewer_roles_run": ["integration"],
            "reviewer_errors": [],
            "findings": [
                {
                    "reviewer_role": "integration",
                    "confidence": "invalid",
                    "category": "wiring",
                    "severity": "warning",
                    "description": "Invalid internal finding",
                }
            ],
            "auto_analysis_limited": False,
            "limited_reason": "",
        }
        with (
            patch("trw_mcp.tools._review_helpers._get_git_diff", return_value=""),
            patch("trw_mcp.tools._review_helpers._run_multi_reviewer_analysis", return_value=invalid_analysis),
            pytest.raises(ValidationError),
        ):
            handle_auto_mode(config, run_dir, "review-auto", "2026-03-01T00:00:00Z", None)

        meta = run_dir / "meta"
        assert not (meta / "integration-review.yaml").exists()
        assert not (meta / "review.yaml").exists()
        assert not (meta / "review.md").exists()
        assert not (meta / "review-all.yaml").exists()
        assert (meta / "events.jsonl").read_text(encoding="utf-8") == ""

    def test_integration_finding_fields_round_trip_and_critical_blocks(self, run_dir: Path) -> None:
        config = _make_config(confidence_threshold=0)
        reviewer_findings = [
            {
                "reviewer_role": "integration",
                "confidence": 0.9,
                "category": "wiring",
                "severity": "critical",
                "description": "Broken boundary",
                "file_path": "src/boundary.py",
                "suggestion": "Restore the adapter",
                "evidence": "Call path is disconnected",
            }
        ]
        with patch("trw_mcp.tools._review_helpers._get_git_diff", return_value="diff"):
            handle_auto_mode(config, run_dir, "review-auto", "2026-03-01T00:00:00Z", reviewer_findings)

        data = FileStateReader().read_yaml(run_dir / "meta" / "integration-review.yaml")
        artifact = IntegrationReviewArtifact.model_validate(data)
        assert artifact.verdict == "block"
        assert artifact.findings[0].file_path == "src/boundary.py"
        assert artifact.findings[0].suggestion == "Restore the adapter"
        assert artifact.findings[0].evidence == "Call path is disconnected"

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
