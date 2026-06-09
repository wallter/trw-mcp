"""Tests for auto review mode behavior."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from tests._ceremony_helpers import make_ceremony_server
from trw_mcp.models.config import TRWConfig, _reset_config
from trw_mcp.state.persistence import FileStateReader
from ._review_modes_support import run_dir  # noqa: F401

from ._review_modes_support import run_dir  # noqa: F401


class TestAutoMode:
    """trw_review auto mode behavior with confidence-filtered findings."""

    def test_auto_mode_filters_findings_below_threshold(
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
                "confidence": 90,
                "category": "logic",
                "severity": "warning",
                "description": "High confidence finding",
            },
            {
                "reviewer_role": "style",
                "confidence": 50,
                "category": "style",
                "severity": "info",
                "description": "Low confidence finding",
            },
        ]

        with (
            patch("trw_mcp.tools.review.find_active_run", return_value=run_dir),
            patch("trw_mcp.tools._review_helpers._get_git_diff", return_value=""),
        ):
            result = tools["trw_review"].fn(reviewer_findings=reviewer_findings)

        assert result["surfaced_findings_count"] == 1
        assert result["total_findings_count"] == 2

    def test_auto_mode_surfaces_findings_at_or_above_threshold(
        self,
        tmp_path: Path,
        run_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        tools = make_ceremony_server(monkeypatch, tmp_path)
        _reset_config(TRWConfig(review_confidence_threshold=75))

        reviewer_findings: list[dict[str, Any]] = [
            {
                "reviewer_role": "security",
                "confidence": 75,
                "category": "security",
                "severity": "critical",
                "description": "At-threshold finding",
            },
            {
                "reviewer_role": "performance",
                "confidence": 74,
                "category": "perf",
                "severity": "warning",
                "description": "Below-threshold finding",
            },
        ]

        with (
            patch("trw_mcp.tools.review.find_active_run", return_value=run_dir),
            patch("trw_mcp.tools._review_helpers._get_git_diff", return_value=""),
        ):
            result = tools["trw_review"].fn(reviewer_findings=reviewer_findings)

        assert result["surfaced_findings_count"] == 1
        assert result["total_findings_count"] == 2

    def test_auto_mode_verdict_from_surfaced_findings_only(
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
                "confidence": 50,
                "category": "logic",
                "severity": "critical",
                "description": "Low confidence critical — should not block",
            },
            {
                "reviewer_role": "style",
                "confidence": 90,
                "category": "style",
                "severity": "warning",
                "description": "High confidence warning — should warn",
            },
        ]

        with (
            patch("trw_mcp.tools.review.find_active_run", return_value=run_dir),
            patch("trw_mcp.tools._review_helpers._get_git_diff", return_value=""),
        ):
            result = tools["trw_review"].fn(reviewer_findings=reviewer_findings)

        assert result["verdict"] == "warn"

    def test_auto_mode_writes_review_yaml_with_surfaced_findings(
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
                "confidence": 95,
                "category": "logic",
                "severity": "warning",
                "description": "Surfaced finding",
            },
            {
                "reviewer_role": "style",
                "confidence": 30,
                "category": "style",
                "severity": "info",
                "description": "Filtered finding",
            },
        ]

        with (
            patch("trw_mcp.tools.review.find_active_run", return_value=run_dir),
            patch("trw_mcp.tools._review_helpers._get_git_diff", return_value=""),
        ):
            tools["trw_review"].fn(reviewer_findings=reviewer_findings)

        review_path = run_dir / "meta" / "review.yaml"
        assert review_path.exists()
        data = FileStateReader().read_yaml(review_path)
        assert data["mode"] == "auto"
        assert data["surfaced_findings_count"] == 1
        assert len(data["findings"]) == 1

    def test_auto_mode_writes_review_all_yaml_with_all_findings(
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
                "confidence": 95,
                "category": "logic",
                "severity": "warning",
                "description": "High confidence",
            },
            {
                "reviewer_role": "style",
                "confidence": 30,
                "category": "style",
                "severity": "info",
                "description": "Low confidence",
            },
        ]

        with (
            patch("trw_mcp.tools.review.find_active_run", return_value=run_dir),
            patch("trw_mcp.tools._review_helpers._get_git_diff", return_value=""),
        ):
            tools["trw_review"].fn(reviewer_findings=reviewer_findings)

        review_all_path = run_dir / "meta" / "review-all.yaml"
        assert review_all_path.exists()
        data = FileStateReader().read_yaml(review_all_path)
        assert data["total_findings_count"] == 2
        assert len(data["findings"]) == 2

    def test_auto_mode_writes_integration_review_yaml_when_present(
        self,
        tmp_path: Path,
        run_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        tools = make_ceremony_server(monkeypatch, tmp_path)
        _reset_config(TRWConfig(review_confidence_threshold=0))

        reviewer_findings: list[dict[str, Any]] = [
            {
                "reviewer_role": "integration",
                "confidence": 90,
                "category": "wiring",
                "severity": "warning",
                "description": "Integration finding",
            },
            {
                "reviewer_role": "correctness",
                "confidence": 85,
                "category": "logic",
                "severity": "info",
                "description": "Normal finding",
            },
        ]

        with (
            patch("trw_mcp.tools.review.find_active_run", return_value=run_dir),
            patch("trw_mcp.tools._review_helpers._get_git_diff", return_value=""),
        ):
            tools["trw_review"].fn(reviewer_findings=reviewer_findings)

        integration_path = run_dir / "meta" / "integration-review.yaml"
        assert integration_path.exists()
        data = FileStateReader().read_yaml(integration_path)
        assert len(data["findings"]) == 1
        assert data["findings"][0]["reviewer_role"] == "integration"

    def test_auto_mode_no_integration_review_yaml_when_none_present(
        self,
        tmp_path: Path,
        run_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        tools = make_ceremony_server(monkeypatch, tmp_path)
        _reset_config(TRWConfig(review_confidence_threshold=0))

        reviewer_findings: list[dict[str, Any]] = [
            {
                "reviewer_role": "correctness",
                "confidence": 90,
                "category": "logic",
                "severity": "info",
                "description": "No integration findings here",
            },
        ]

        with (
            patch("trw_mcp.tools.review.find_active_run", return_value=run_dir),
            patch("trw_mcp.tools._review_helpers._get_git_diff", return_value=""),
        ):
            tools["trw_review"].fn(reviewer_findings=reviewer_findings)

        integration_path = run_dir / "meta" / "integration-review.yaml"
        assert not integration_path.exists()

    def test_auto_mode_without_reviewer_findings_runs_basic_analysis(
        self,
        tmp_path: Path,
        run_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        tools = make_ceremony_server(monkeypatch, tmp_path)
        _reset_config(TRWConfig(review_confidence_threshold=80))

        with (
            patch("trw_mcp.tools.review.find_active_run", return_value=run_dir),
            patch("trw_mcp.tools._review_helpers._get_git_diff", return_value=""),
        ):
            result = tools["trw_review"].fn(mode="auto")

        assert result["mode"] == "auto"
        assert "reviewer_roles_run" in result
        assert isinstance(result["reviewer_roles_run"], list)

    def test_auto_mode_logs_review_complete_event(
        self,
        tmp_path: Path,
        run_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        tools = make_ceremony_server(monkeypatch, tmp_path)
        _reset_config(TRWConfig(review_confidence_threshold=0))

        with (
            patch("trw_mcp.tools.review.find_active_run", return_value=run_dir),
            patch("trw_mcp.tools._review_helpers._get_git_diff", return_value=""),
        ):
            result = tools["trw_review"].fn(mode="auto")

        events_path = run_dir / "meta" / "events.jsonl"
        lines = [line for line in events_path.read_text(encoding="utf-8").strip().split("\n") if line]
        assert len(lines) >= 1
        review_events = [json.loads(line) for line in lines if "review_complete" in line]
        assert len(review_events) >= 1
        event = review_events[-1]
        assert event["event"] == "review_complete"
        assert event["mode"] == "auto"
        assert event["review_id"] == result["review_id"]
