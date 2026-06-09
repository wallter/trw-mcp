"""Tests for review mode detection and cross-model behavior."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from tests._ceremony_helpers import make_ceremony_server
from trw_mcp.models.config import TRWConfig, _reset_config
from trw_mcp.state.persistence import FileStateReader
from ._review_modes_support import run_dir  # noqa: F401

from ._review_modes_support import run_dir  # noqa: F401


class TestModeDetection:
    """trw_review mode detection logic."""

    def test_explicit_findings_uses_manual_mode(
        self,
        tmp_path: Path,
        run_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        tools = make_ceremony_server(monkeypatch, tmp_path)
        _reset_config()

        with patch("trw_mcp.tools.review.find_active_run", return_value=run_dir):
            result = tools["trw_review"].fn(
                findings=[{"category": "style", "severity": "info", "description": "Nit"}],
            )

        assert "critical_count" in result
        assert "warning_count" in result
        assert result["verdict"] == "pass"

    def test_explicit_mode_cross_model_uses_cross_model(
        self,
        tmp_path: Path,
        run_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        tools = make_ceremony_server(monkeypatch, tmp_path)
        _reset_config(TRWConfig(cross_model_review_enabled=False))

        with (
            patch("trw_mcp.tools.review.find_active_run", return_value=run_dir),
            patch("trw_mcp.tools._review_helpers._get_git_diff", return_value=""),
        ):
            result = tools["trw_review"].fn(mode="cross_model")

        assert result.get("mode") == "cross_model"

    def test_explicit_mode_auto_uses_auto(
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

        assert result.get("mode") == "auto"

    def test_reviewer_findings_without_mode_uses_auto(
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
            result = tools["trw_review"].fn(
                reviewer_findings=[
                    {
                        "reviewer_role": "correctness",
                        "confidence": 90,
                        "category": "logic",
                        "severity": "warning",
                        "description": "Edge case missed",
                    },
                ],
            )

        assert result.get("mode") == "auto"

    def test_no_args_uses_manual_mode(
        self,
        tmp_path: Path,
        run_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        tools = make_ceremony_server(monkeypatch, tmp_path)
        _reset_config()

        with patch("trw_mcp.tools.review.find_active_run", return_value=run_dir):
            result = tools["trw_review"].fn()

        assert "critical_count" in result
        assert result["verdict"] == "pass"


class TestCrossModelMode:
    """trw_review cross_model mode behavior."""

    def test_cross_model_disabled_sets_skipped_true(
        self,
        tmp_path: Path,
        run_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        tools = make_ceremony_server(monkeypatch, tmp_path)
        _reset_config(TRWConfig(cross_model_review_enabled=False))

        with (
            patch("trw_mcp.tools.review.find_active_run", return_value=run_dir),
            patch("trw_mcp.tools._review_helpers._get_git_diff", return_value="some diff"),
        ):
            result = tools["trw_review"].fn(mode="cross_model")

        assert result["cross_model_skipped"] is True

    def test_cross_model_no_diff_sets_skipped_true(
        self,
        tmp_path: Path,
        run_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        tools = make_ceremony_server(monkeypatch, tmp_path)
        _reset_config(TRWConfig(cross_model_review_enabled=True))

        with (
            patch("trw_mcp.tools.review.find_active_run", return_value=run_dir),
            patch("trw_mcp.tools._review_helpers._get_git_diff", return_value=""),
        ):
            result = tools["trw_review"].fn(mode="cross_model")

        assert result["cross_model_skipped"] is True

    def test_cross_model_writes_review_yaml_with_mode_field(
        self,
        tmp_path: Path,
        run_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        tools = make_ceremony_server(monkeypatch, tmp_path)
        _reset_config(TRWConfig(cross_model_review_enabled=False))

        with (
            patch("trw_mcp.tools.review.find_active_run", return_value=run_dir),
            patch("trw_mcp.tools._review_helpers._get_git_diff", return_value=""),
        ):
            result = tools["trw_review"].fn(mode="cross_model")

        review_path = run_dir / "meta" / "review.yaml"
        assert review_path.exists()
        data = FileStateReader().read_yaml(review_path)
        assert data["mode"] == "cross_model"
        assert result["review_yaml"] == str(review_path)

    def test_cross_model_returns_provider_in_result(
        self,
        tmp_path: Path,
        run_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        tools = make_ceremony_server(monkeypatch, tmp_path)
        _reset_config(
            TRWConfig(
                cross_model_review_enabled=False,
                cross_model_provider="gemini-2.5-pro",
            )
        )

        with (
            patch("trw_mcp.tools.review.find_active_run", return_value=run_dir),
            patch("trw_mcp.tools._review_helpers._get_git_diff", return_value=""),
        ):
            result = tools["trw_review"].fn(mode="cross_model")

        assert result["cross_model_provider"] == "gemini-2.5-pro"

    def test_cross_model_mode_field_in_result(
        self,
        tmp_path: Path,
        run_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        tools = make_ceremony_server(monkeypatch, tmp_path)
        _reset_config(TRWConfig(cross_model_review_enabled=False))

        with (
            patch("trw_mcp.tools.review.find_active_run", return_value=run_dir),
            patch("trw_mcp.tools._review_helpers._get_git_diff", return_value=""),
        ):
            result = tools["trw_review"].fn(mode="cross_model")

        assert result["mode"] == "cross_model"

    def test_cross_model_skipped_yields_pass_verdict(
        self,
        tmp_path: Path,
        run_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        tools = make_ceremony_server(monkeypatch, tmp_path)
        _reset_config(TRWConfig(cross_model_review_enabled=False))

        with (
            patch("trw_mcp.tools.review.find_active_run", return_value=run_dir),
            patch("trw_mcp.tools._review_helpers._get_git_diff", return_value=""),
        ):
            result = tools["trw_review"].fn(mode="cross_model")

        assert result["verdict"] == "pass"

    def test_cross_model_logs_review_complete_event(
        self,
        tmp_path: Path,
        run_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        tools = make_ceremony_server(monkeypatch, tmp_path)
        _reset_config(TRWConfig(cross_model_review_enabled=False))

        with (
            patch("trw_mcp.tools.review.find_active_run", return_value=run_dir),
            patch("trw_mcp.tools._review_helpers._get_git_diff", return_value=""),
        ):
            result = tools["trw_review"].fn(mode="cross_model")

        events_path = run_dir / "meta" / "events.jsonl"
        lines = [line for line in events_path.read_text(encoding="utf-8").strip().split("\n") if line]
        assert len(lines) >= 1
        review_events = [json.loads(line) for line in lines if "review_complete" in line]
        assert len(review_events) >= 1
        event = review_events[-1]
        assert event["event"] == "review_complete"
        assert event["mode"] == "cross_model"
        assert event["review_id"] == result["review_id"]

    def test_cross_model_no_run_returns_empty_review_yaml(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        tools = make_ceremony_server(monkeypatch, tmp_path)
        _reset_config(TRWConfig(cross_model_review_enabled=False))

        with (
            patch("trw_mcp.tools.review.find_active_run", return_value=None),
            patch("trw_mcp.tools._review_helpers._get_git_diff", return_value=""),
        ):
            result = tools["trw_review"].fn(mode="cross_model")

        assert result["review_yaml"] == ""
        assert result["run_path"] is None
