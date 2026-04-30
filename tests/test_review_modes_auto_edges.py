"""Additional auto review mode tests."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from tests._ceremony_helpers import make_ceremony_server
from tests._review_modes_support import run_dir
from trw_mcp.models.config import TRWConfig, _reset_config


class TestAutoMode:
    """trw_review auto mode behavior with confidence-filtered findings."""

    def test_auto_mode_returns_confidence_threshold_in_result(
        self,
        tmp_path: Path,
        run_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        tools = make_ceremony_server(monkeypatch, tmp_path)
        _reset_config(TRWConfig(review_confidence_threshold=75))

        with (
            patch("trw_mcp.tools.review.find_active_run", return_value=run_dir),
            patch("trw_mcp.tools._review_helpers._get_git_diff", return_value=""),
        ):
            result = tools["trw_review"].fn(mode="auto")

        assert result["confidence_threshold"] == 75

    def test_auto_mode_all_findings_above_threshold_all_surfaced(
        self,
        tmp_path: Path,
        run_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        tools = make_ceremony_server(monkeypatch, tmp_path)
        _reset_config(TRWConfig(review_confidence_threshold=50))

        reviewer_findings: list[dict[str, Any]] = [
            {
                "reviewer_role": "correctness",
                "confidence": 90,
                "category": "logic",
                "severity": "info",
                "description": "First",
            },
            {
                "reviewer_role": "security",
                "confidence": 85,
                "category": "sec",
                "severity": "warning",
                "description": "Second",
            },
            {
                "reviewer_role": "style",
                "confidence": 60,
                "category": "style",
                "severity": "info",
                "description": "Third",
            },
        ]

        with (
            patch("trw_mcp.tools.review.find_active_run", return_value=run_dir),
            patch("trw_mcp.tools._review_helpers._get_git_diff", return_value=""),
        ):
            result = tools["trw_review"].fn(reviewer_findings=reviewer_findings)

        assert result["surfaced_findings_count"] == 3
        assert result["total_findings_count"] == 3

    def test_auto_mode_all_findings_below_threshold_none_surfaced(
        self,
        tmp_path: Path,
        run_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        tools = make_ceremony_server(monkeypatch, tmp_path)
        _reset_config(TRWConfig(review_confidence_threshold=80))

        reviewer_findings: list[dict[str, Any]] = [
            {
                "reviewer_role": "correctness",
                "confidence": 30,
                "category": "logic",
                "severity": "critical",
                "description": "Below threshold critical",
            },
        ]

        with (
            patch("trw_mcp.tools.review.find_active_run", return_value=run_dir),
            patch("trw_mcp.tools._review_helpers._get_git_diff", return_value=""),
        ):
            result = tools["trw_review"].fn(reviewer_findings=reviewer_findings)

        assert result["surfaced_findings_count"] == 0
        assert result["verdict"] == "pass"

    def test_auto_mode_no_run_returns_empty_review_yaml(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        tools = make_ceremony_server(monkeypatch, tmp_path)
        _reset_config(TRWConfig(review_confidence_threshold=0))

        with (
            patch("trw_mcp.tools.review.find_active_run", return_value=None),
            patch("trw_mcp.tools._review_helpers._get_git_diff", return_value=""),
        ):
            result = tools["trw_review"].fn(mode="auto")

        assert result["review_yaml"] == ""
        assert result["run_path"] is None
