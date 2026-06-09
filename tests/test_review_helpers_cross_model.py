"""Tests for cross-model review helper mode."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from tests._review_helpers_support import _make_config
from trw_mcp.state.persistence import FileStateReader
from trw_mcp.tools._review_helpers import handle_cross_model_mode
from ._review_helpers_support import run_dir  # noqa: F401

from ._review_helpers_support import run_dir  # noqa: F401

from ._review_helpers_support import run_dir  # noqa: F401


class TestHandleCrossModelMode:
    """handle_cross_model_mode: enabled/disabled, diff, provider findings."""

    def test_disabled_config_sets_cross_model_skipped_true(self, run_dir: Path) -> None:
        config = _make_config(cross_model_enabled=False)
        with patch("trw_mcp.tools._review_helpers._get_git_diff", return_value="some diff"):
            result = handle_cross_model_mode(config, run_dir, "review-cm", "2026-03-01T00:00:00Z")
        assert result["cross_model_skipped"] is True

    def test_disabled_config_returns_pass_verdict(self, run_dir: Path) -> None:
        config = _make_config(cross_model_enabled=False)
        with patch("trw_mcp.tools._review_helpers._get_git_diff", return_value="some diff"):
            result = handle_cross_model_mode(config, run_dir, "review-cm", "2026-03-01T00:00:00Z")
        assert result["verdict"] == "pass"

    def test_empty_diff_sets_cross_model_skipped_true(self, run_dir: Path) -> None:
        config = _make_config(cross_model_enabled=True)
        with patch("trw_mcp.tools._review_helpers._get_git_diff", return_value=""):
            result = handle_cross_model_mode(config, run_dir, "review-cm", "2026-03-01T00:00:00Z")
        assert result["cross_model_skipped"] is True

    def test_enabled_with_diff_but_stub_returns_empty_skipped(self, run_dir: Path) -> None:
        """When enabled and diff exists but _invoke returns [], skipped=True."""
        config = _make_config(cross_model_enabled=True)
        with patch("trw_mcp.tools._review_helpers._get_git_diff", return_value="diff content"):
            result = handle_cross_model_mode(config, run_dir, "review-cm", "2026-03-01T00:00:00Z")
        assert result["cross_model_skipped"] is True

    def test_mode_field_is_cross_model(self, run_dir: Path) -> None:
        config = _make_config(cross_model_enabled=False)
        with patch("trw_mcp.tools._review_helpers._get_git_diff", return_value=""):
            result = handle_cross_model_mode(config, run_dir, "review-cm", "2026-03-01T00:00:00Z")
        assert result["mode"] == "cross_model"

    def test_provider_field_in_result(self, run_dir: Path) -> None:
        config = _make_config(cross_model_enabled=False, cross_model_provider="test-provider")
        with patch("trw_mcp.tools._review_helpers._get_git_diff", return_value=""):
            result = handle_cross_model_mode(config, run_dir, "review-cm", "2026-03-01T00:00:00Z")
        assert result["cross_model_provider"] == "test-provider"

    def test_persists_review_yaml_with_mode_field(self, run_dir: Path) -> None:
        config = _make_config(cross_model_enabled=False)
        with patch("trw_mcp.tools._review_helpers._get_git_diff", return_value=""):
            result = handle_cross_model_mode(config, run_dir, "review-cm", "2026-03-01T00:00:00Z")
        review_path = run_dir / "meta" / "review.yaml"
        assert review_path.exists()
        data = FileStateReader().read_yaml(review_path)
        assert data["mode"] == "cross_model"
        assert result["review_yaml"] == str(review_path)

    def test_no_run_returns_empty_review_yaml(self) -> None:
        config = _make_config(cross_model_enabled=False)
        with patch("trw_mcp.tools._review_helpers._get_git_diff", return_value=""):
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
            patch("trw_mcp.tools._review_helpers._get_git_diff", return_value="diff content"),
            patch(
                "trw_mcp.tools._review_helpers._invoke_cross_model_review",
                return_value=stub_findings,
            ),
        ):
            result = handle_cross_model_mode(config, run_dir, "review-cm", "2026-03-01T00:00:00Z")

        data = FileStateReader().read_yaml(run_dir / "meta" / "review.yaml")
        severities = [f["severity"] for f in data["cross_model_findings"]]
        assert "critical" in severities
        assert "warning" in severities
        assert result["cross_model_skipped"] is False
        assert result["verdict"] == "block"

    def test_findings_from_provider_tagged_with_source_and_provider(self, run_dir: Path) -> None:
        """Findings from provider are tagged with source='cross_model'."""
        config = _make_config(cross_model_enabled=True, cross_model_provider="gpt-4o")
        stub_findings = [
            {"category": "correctness", "severity": "warning", "description": "Issue"},
        ]
        with (
            patch("trw_mcp.tools._review_helpers._get_git_diff", return_value="diff content"),
            patch(
                "trw_mcp.tools._review_helpers._invoke_cross_model_review",
                return_value=stub_findings,
            ),
        ):
            handle_cross_model_mode(config, run_dir, "review-cm", "2026-03-01T00:00:00Z")

        data = FileStateReader().read_yaml(run_dir / "meta" / "review.yaml")
        assert data["cross_model_findings"][0]["source"] == "cross_model"
        assert data["cross_model_findings"][0]["provider"] == "gpt-4o"

    def test_review_id_in_result(self, run_dir: Path) -> None:
        config = _make_config(cross_model_enabled=False)
        with patch("trw_mcp.tools._review_helpers._get_git_diff", return_value=""):
            result = handle_cross_model_mode(config, run_dir, "review-id-check", "2026-03-01T00:00:00Z")
        assert result["review_id"] == "review-id-check"

    def test_total_findings_zero_when_skipped(self, run_dir: Path) -> None:
        config = _make_config(cross_model_enabled=False)
        with patch("trw_mcp.tools._review_helpers._get_git_diff", return_value=""):
            result = handle_cross_model_mode(config, run_dir, "review-cm", "2026-03-01T00:00:00Z")
        assert result["total_findings"] == 0
