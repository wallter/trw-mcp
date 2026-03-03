"""Tests for Sprint 43 review tool extensions.

PRD-QUAL-026: Cross-model review mode — routes diff to external model.
PRD-QUAL-027: Multi-agent review with confidence-scored findings.

Covers:
- Helper functions: _normalize_severity, _invoke_cross_model_review,
  _run_multi_reviewer_analysis, _compute_verdict, _get_git_diff
- Mode detection logic in trw_review
- Cross-model mode behavior (enabled/disabled, no diff, artifact writes)
- Auto mode behavior (confidence filtering, artifact writes, event logging)
- Integration: no-run fallback, explicit run_path
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from tests._ceremony_helpers import make_ceremony_server
from trw_mcp.models.config import TRWConfig, _reset_config
from trw_mcp.state.persistence import FileStateReader
from trw_mcp.tools.review import (
    REVIEWER_ROLES,
    _compute_verdict,
    _get_git_diff,
    _invoke_cross_model_review,
    _normalize_severity,
    _run_multi_reviewer_analysis,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def run_dir(tmp_path: Path) -> Path:
    """Create a minimal run directory structure for review tests."""
    d = tmp_path / "docs" / "task" / "runs" / "20260301T120000Z-review-modes-test"
    meta = d / "meta"
    meta.mkdir(parents=True)
    (meta / "run.yaml").write_text(
        "run_id: review-modes-test\nstatus: active\nphase: review\ntask_name: review-modes-task\n",
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
    """Build a minimal TRWConfig for testing review modes."""
    return TRWConfig(
        cross_model_review_enabled=cross_model_enabled,
        cross_model_provider=cross_model_provider,
        review_confidence_threshold=confidence_threshold,
    )


# ---------------------------------------------------------------------------
# Helper: _normalize_severity
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Helper: _invoke_cross_model_review
# ---------------------------------------------------------------------------


class TestInvokeCrossModelReview:
    """_invoke_cross_model_review is an integration stub."""

    def test_empty_diff_returns_empty_list(self) -> None:
        config = _make_config()
        result = _invoke_cross_model_review("", config)
        assert result == []

    def test_non_empty_diff_returns_empty_list(self) -> None:
        """Stub returns empty list until provider is configured."""
        config = _make_config()
        result = _invoke_cross_model_review("+ some diff content\n- removed line", config)
        assert result == []

    def test_returns_list_type(self) -> None:
        config = _make_config()
        result = _invoke_cross_model_review("diff content", config)
        assert isinstance(result, list)


# ---------------------------------------------------------------------------
# Helper: _run_multi_reviewer_analysis
# ---------------------------------------------------------------------------


class TestRunMultiReviewerAnalysis:
    """_run_multi_reviewer_analysis performs basic structural diff analysis."""

    def test_empty_diff_returns_empty_findings(self) -> None:
        config = _make_config()
        result = _run_multi_reviewer_analysis("", config)
        assert result["findings"] == []

    def test_empty_diff_lists_reviewer_roles(self) -> None:
        config = _make_config()
        result = _run_multi_reviewer_analysis("", config)
        assert result["reviewer_roles_run"] == list(REVIEWER_ROLES)

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
        assert any("TODO" in d or "todo" in d.lower() for d in descriptions)

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
        # +++ header lines are explicitly excluded — no finding expected
        assert result["findings"] == []

    def test_finding_has_correct_structure(self) -> None:
        config = _make_config()
        diff = "+++ b/work.py\n+ # TODO: finish implementation\n"
        result = _run_multi_reviewer_analysis(diff, config)
        findings = result["findings"]
        assert len(findings) >= 1
        f = findings[0]
        assert "reviewer_role" in f
        assert "confidence" in f
        assert "category" in f
        assert "severity" in f
        assert "description" in f

    def test_finding_severity_is_info(self) -> None:
        config = _make_config()
        diff = "+++ b/work.py\n+ # TODO: finish implementation\n"
        result = _run_multi_reviewer_analysis(diff, config)
        findings = result["findings"]
        assert all(f["severity"] == "info" for f in findings)

    def test_finding_reviewer_role_is_style(self) -> None:
        config = _make_config()
        diff = "+++ b/work.py\n+ # TODO: finish implementation\n"
        result = _run_multi_reviewer_analysis(diff, config)
        findings = result["findings"]
        assert all(f["reviewer_role"] == "style" for f in findings)

    def test_returns_dict_type(self) -> None:
        config = _make_config()
        result = _run_multi_reviewer_analysis("some diff", config)
        assert isinstance(result, dict)


# ---------------------------------------------------------------------------
# Helper: _compute_verdict
# ---------------------------------------------------------------------------


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
        findings: list[dict[str, str]] = [
            {"severity": "critical"},
            {"severity": "warning"},
        ]
        assert _compute_verdict(findings) == "block"

    def test_multiple_warnings_no_critical_returns_warn(self) -> None:
        findings: list[dict[str, str]] = [
            {"severity": "warning"},
            {"severity": "info"},
            {"severity": "warning"},
        ]
        assert _compute_verdict(findings) == "warn"

    def test_multiple_info_returns_pass(self) -> None:
        findings: list[dict[str, str]] = [
            {"severity": "info"},
            {"severity": "info"},
        ]
        assert _compute_verdict(findings) == "pass"

    def test_missing_severity_key_returns_pass(self) -> None:
        """Findings without severity key contribute 0 to counts."""
        findings: list[dict[str, str]] = [{"category": "style"}]
        assert _compute_verdict(findings) == "pass"


# ---------------------------------------------------------------------------
# Mode detection tests (via trw_review MCP tool)
# ---------------------------------------------------------------------------


class TestModeDetection:
    """trw_review mode detection logic."""

    def test_explicit_findings_uses_manual_mode(
        self, tmp_path: Path, run_dir: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """findings=[...] explicitly passed → manual mode (backward compat)."""
        tools = make_ceremony_server(monkeypatch, tmp_path)
        _reset_config()

        with patch("trw_mcp.tools.review.find_active_run", return_value=run_dir):
            result = tools["trw_review"].fn(
                findings=[{"category": "style", "severity": "info", "description": "Nit"}],
            )

        # Manual mode result has critical_count / warning_count fields
        assert "critical_count" in result
        assert "warning_count" in result
        assert result["verdict"] == "pass"

    def test_explicit_mode_cross_model_uses_cross_model(
        self, tmp_path: Path, run_dir: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """mode='cross_model' explicitly set → cross_model mode."""
        tools = make_ceremony_server(monkeypatch, tmp_path)
        _reset_config(TRWConfig(cross_model_review_enabled=False))

        with (
            patch("trw_mcp.tools.review.find_active_run", return_value=run_dir),
            patch("trw_mcp.tools.review._get_git_diff", return_value=""),
        ):
            result = tools["trw_review"].fn(mode="cross_model")

        assert result.get("mode") == "cross_model"

    def test_explicit_mode_auto_uses_auto(
        self, tmp_path: Path, run_dir: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """mode='auto' explicitly set → auto mode."""
        tools = make_ceremony_server(monkeypatch, tmp_path)
        _reset_config(TRWConfig(review_confidence_threshold=0))

        with (
            patch("trw_mcp.tools.review.find_active_run", return_value=run_dir),
            patch("trw_mcp.tools.review._get_git_diff", return_value=""),
        ):
            result = tools["trw_review"].fn(mode="auto")

        assert result.get("mode") == "auto"

    def test_reviewer_findings_without_mode_uses_auto(
        self, tmp_path: Path, run_dir: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """reviewer_findings provided (no mode) → auto mode (inferred)."""
        tools = make_ceremony_server(monkeypatch, tmp_path)
        _reset_config(TRWConfig(review_confidence_threshold=0))

        with (
            patch("trw_mcp.tools.review.find_active_run", return_value=run_dir),
            patch("trw_mcp.tools.review._get_git_diff", return_value=""),
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
        self, tmp_path: Path, run_dir: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """No args provided → manual mode (backward compat)."""
        tools = make_ceremony_server(monkeypatch, tmp_path)
        _reset_config()

        with patch("trw_mcp.tools.review.find_active_run", return_value=run_dir):
            result = tools["trw_review"].fn()

        # Manual mode result has critical_count / warning_count fields
        assert "critical_count" in result
        assert result["verdict"] == "pass"


# ---------------------------------------------------------------------------
# Cross-model mode tests (QUAL-026)
# ---------------------------------------------------------------------------


class TestCrossModelMode:
    """trw_review cross_model mode behavior."""

    def test_cross_model_disabled_sets_skipped_true(
        self, tmp_path: Path, run_dir: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """cross_model mode with config.cross_model_review_enabled=False → skipped."""
        tools = make_ceremony_server(monkeypatch, tmp_path)
        _reset_config(TRWConfig(cross_model_review_enabled=False))

        with (
            patch("trw_mcp.tools.review.find_active_run", return_value=run_dir),
            patch("trw_mcp.tools.review._get_git_diff", return_value="some diff"),
        ):
            result = tools["trw_review"].fn(mode="cross_model")

        assert result["cross_model_skipped"] is True

    def test_cross_model_no_diff_sets_skipped_true(
        self, tmp_path: Path, run_dir: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """cross_model mode with no diff → skipped."""
        tools = make_ceremony_server(monkeypatch, tmp_path)
        _reset_config(TRWConfig(cross_model_review_enabled=True))

        with (
            patch("trw_mcp.tools.review.find_active_run", return_value=run_dir),
            patch("trw_mcp.tools.review._get_git_diff", return_value=""),
        ):
            result = tools["trw_review"].fn(mode="cross_model")

        assert result["cross_model_skipped"] is True

    def test_cross_model_writes_review_yaml_with_mode_field(
        self, tmp_path: Path, run_dir: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """cross_model mode writes review.yaml with mode='cross_model'."""
        tools = make_ceremony_server(monkeypatch, tmp_path)
        _reset_config(TRWConfig(cross_model_review_enabled=False))

        with (
            patch("trw_mcp.tools.review.find_active_run", return_value=run_dir),
            patch("trw_mcp.tools.review._get_git_diff", return_value=""),
        ):
            result = tools["trw_review"].fn(mode="cross_model")

        review_path = run_dir / "meta" / "review.yaml"
        assert review_path.exists()
        data = FileStateReader().read_yaml(review_path)
        assert data["mode"] == "cross_model"
        assert result["review_yaml"] == str(review_path)

    def test_cross_model_returns_provider_in_result(
        self, tmp_path: Path, run_dir: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """cross_model mode returns cross_model_provider in result."""
        tools = make_ceremony_server(monkeypatch, tmp_path)
        _reset_config(TRWConfig(
            cross_model_review_enabled=False,
            cross_model_provider="gemini-2.5-pro",
        ))

        with (
            patch("trw_mcp.tools.review.find_active_run", return_value=run_dir),
            patch("trw_mcp.tools.review._get_git_diff", return_value=""),
        ):
            result = tools["trw_review"].fn(mode="cross_model")

        assert result["cross_model_provider"] == "gemini-2.5-pro"

    def test_cross_model_mode_field_in_result(
        self, tmp_path: Path, run_dir: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """cross_model result always has mode='cross_model'."""
        tools = make_ceremony_server(monkeypatch, tmp_path)
        _reset_config(TRWConfig(cross_model_review_enabled=False))

        with (
            patch("trw_mcp.tools.review.find_active_run", return_value=run_dir),
            patch("trw_mcp.tools.review._get_git_diff", return_value=""),
        ):
            result = tools["trw_review"].fn(mode="cross_model")

        assert result["mode"] == "cross_model"

    def test_cross_model_skipped_yields_pass_verdict(
        self, tmp_path: Path, run_dir: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When skipped, no findings → verdict is pass."""
        tools = make_ceremony_server(monkeypatch, tmp_path)
        _reset_config(TRWConfig(cross_model_review_enabled=False))

        with (
            patch("trw_mcp.tools.review.find_active_run", return_value=run_dir),
            patch("trw_mcp.tools.review._get_git_diff", return_value=""),
        ):
            result = tools["trw_review"].fn(mode="cross_model")

        assert result["verdict"] == "pass"

    def test_cross_model_logs_review_complete_event(
        self, tmp_path: Path, run_dir: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """cross_model mode logs review_complete event."""
        tools = make_ceremony_server(monkeypatch, tmp_path)
        _reset_config(TRWConfig(cross_model_review_enabled=False))

        with (
            patch("trw_mcp.tools.review.find_active_run", return_value=run_dir),
            patch("trw_mcp.tools.review._get_git_diff", return_value=""),
        ):
            result = tools["trw_review"].fn(mode="cross_model")

        events_path = run_dir / "meta" / "events.jsonl"
        lines = [
            line for line in events_path.read_text(encoding="utf-8").strip().split("\n")
            if line
        ]
        assert len(lines) >= 1
        # Find review_complete event (may not be last due to tool_invocation decorator)
        review_events = [
            json.loads(l) for l in lines if "review_complete" in l
        ]
        assert len(review_events) >= 1
        event = review_events[-1]
        assert event["event"] == "review_complete"
        assert event["mode"] == "cross_model"
        assert event["review_id"] == result["review_id"]

    def test_cross_model_no_run_returns_empty_review_yaml(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """cross_model mode with no run → review_yaml=''."""
        tools = make_ceremony_server(monkeypatch, tmp_path)
        _reset_config(TRWConfig(cross_model_review_enabled=False))

        with (
            patch("trw_mcp.tools.review.find_active_run", return_value=None),
            patch("trw_mcp.tools.review._get_git_diff", return_value=""),
        ):
            result = tools["trw_review"].fn(mode="cross_model")

        assert result["review_yaml"] == ""
        assert result["run_path"] is None


# ---------------------------------------------------------------------------
# Auto mode tests (QUAL-027)
# ---------------------------------------------------------------------------


class TestAutoMode:
    """trw_review auto mode behavior with confidence-filtered findings."""

    def test_auto_mode_filters_findings_below_threshold(
        self, tmp_path: Path, run_dir: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Auto mode with reviewer_findings filters by confidence_threshold."""
        tools = make_ceremony_server(monkeypatch, tmp_path)
        _reset_config(TRWConfig(review_confidence_threshold=80))

        reviewer_findings: list[dict[str, Any]] = [
            {"reviewer_role": "correctness", "confidence": 90, "category": "logic",
             "severity": "warning", "description": "High confidence finding"},
            {"reviewer_role": "style", "confidence": 50, "category": "style",
             "severity": "info", "description": "Low confidence finding"},
        ]

        with (
            patch("trw_mcp.tools.review.find_active_run", return_value=run_dir),
            patch("trw_mcp.tools.review._get_git_diff", return_value=""),
        ):
            result = tools["trw_review"].fn(reviewer_findings=reviewer_findings)

        assert result["surfaced_findings_count"] == 1
        assert result["total_findings_count"] == 2

    def test_auto_mode_surfaces_findings_at_or_above_threshold(
        self, tmp_path: Path, run_dir: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Findings at or above threshold are surfaced; below are hidden."""
        tools = make_ceremony_server(monkeypatch, tmp_path)
        _reset_config(TRWConfig(review_confidence_threshold=75))

        reviewer_findings: list[dict[str, Any]] = [
            {"reviewer_role": "security", "confidence": 75, "category": "security",
             "severity": "critical", "description": "At-threshold finding"},
            {"reviewer_role": "performance", "confidence": 74, "category": "perf",
             "severity": "warning", "description": "Below-threshold finding"},
        ]

        with (
            patch("trw_mcp.tools.review.find_active_run", return_value=run_dir),
            patch("trw_mcp.tools.review._get_git_diff", return_value=""),
        ):
            result = tools["trw_review"].fn(reviewer_findings=reviewer_findings)

        assert result["surfaced_findings_count"] == 1
        assert result["total_findings_count"] == 2

    def test_auto_mode_verdict_from_surfaced_findings_only(
        self, tmp_path: Path, run_dir: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Verdict is computed from surfaced (above-threshold) findings only."""
        tools = make_ceremony_server(monkeypatch, tmp_path)
        _reset_config(TRWConfig(review_confidence_threshold=80))

        # critical finding is below threshold, warning is above
        reviewer_findings: list[dict[str, Any]] = [
            {"reviewer_role": "correctness", "confidence": 50, "category": "logic",
             "severity": "critical", "description": "Low confidence critical — should not block"},
            {"reviewer_role": "style", "confidence": 90, "category": "style",
             "severity": "warning", "description": "High confidence warning — should warn"},
        ]

        with (
            patch("trw_mcp.tools.review.find_active_run", return_value=run_dir),
            patch("trw_mcp.tools.review._get_git_diff", return_value=""),
        ):
            result = tools["trw_review"].fn(reviewer_findings=reviewer_findings)

        # Verdict should be "warn" (from surfaced warning), not "block" (from filtered critical)
        assert result["verdict"] == "warn"

    def test_auto_mode_writes_review_yaml_with_surfaced_findings(
        self, tmp_path: Path, run_dir: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Auto mode writes review.yaml containing only surfaced findings."""
        tools = make_ceremony_server(monkeypatch, tmp_path)
        _reset_config(TRWConfig(review_confidence_threshold=80))

        reviewer_findings: list[dict[str, Any]] = [
            {"reviewer_role": "correctness", "confidence": 95, "category": "logic",
             "severity": "warning", "description": "Surfaced finding"},
            {"reviewer_role": "style", "confidence": 30, "category": "style",
             "severity": "info", "description": "Filtered finding"},
        ]

        with (
            patch("trw_mcp.tools.review.find_active_run", return_value=run_dir),
            patch("trw_mcp.tools.review._get_git_diff", return_value=""),
        ):
            tools["trw_review"].fn(reviewer_findings=reviewer_findings)

        review_path = run_dir / "meta" / "review.yaml"
        assert review_path.exists()
        data = FileStateReader().read_yaml(review_path)
        assert data["mode"] == "auto"
        assert data["surfaced_findings_count"] == 1
        assert len(data["findings"]) == 1

    def test_auto_mode_writes_review_all_yaml_with_all_findings(
        self, tmp_path: Path, run_dir: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Auto mode writes review-all.yaml containing ALL findings unfiltered."""
        tools = make_ceremony_server(monkeypatch, tmp_path)
        _reset_config(TRWConfig(review_confidence_threshold=80))

        reviewer_findings: list[dict[str, Any]] = [
            {"reviewer_role": "correctness", "confidence": 95, "category": "logic",
             "severity": "warning", "description": "High confidence"},
            {"reviewer_role": "style", "confidence": 30, "category": "style",
             "severity": "info", "description": "Low confidence"},
        ]

        with (
            patch("trw_mcp.tools.review.find_active_run", return_value=run_dir),
            patch("trw_mcp.tools.review._get_git_diff", return_value=""),
        ):
            tools["trw_review"].fn(reviewer_findings=reviewer_findings)

        review_all_path = run_dir / "meta" / "review-all.yaml"
        assert review_all_path.exists()
        data = FileStateReader().read_yaml(review_all_path)
        assert data["total_findings_count"] == 2
        assert len(data["findings"]) == 2

    def test_auto_mode_writes_integration_review_yaml_when_present(
        self, tmp_path: Path, run_dir: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Auto mode writes integration-review.yaml when integration findings exist."""
        tools = make_ceremony_server(monkeypatch, tmp_path)
        _reset_config(TRWConfig(review_confidence_threshold=0))

        reviewer_findings: list[dict[str, Any]] = [
            {"reviewer_role": "integration", "confidence": 90, "category": "wiring",
             "severity": "warning", "description": "Integration finding"},
            {"reviewer_role": "correctness", "confidence": 85, "category": "logic",
             "severity": "info", "description": "Normal finding"},
        ]

        with (
            patch("trw_mcp.tools.review.find_active_run", return_value=run_dir),
            patch("trw_mcp.tools.review._get_git_diff", return_value=""),
        ):
            tools["trw_review"].fn(reviewer_findings=reviewer_findings)

        integration_path = run_dir / "meta" / "integration-review.yaml"
        assert integration_path.exists()
        data = FileStateReader().read_yaml(integration_path)
        assert len(data["findings"]) == 1
        assert data["findings"][0]["reviewer_role"] == "integration"

    def test_auto_mode_no_integration_review_yaml_when_none_present(
        self, tmp_path: Path, run_dir: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """integration-review.yaml is NOT written when no integration findings exist."""
        tools = make_ceremony_server(monkeypatch, tmp_path)
        _reset_config(TRWConfig(review_confidence_threshold=0))

        reviewer_findings: list[dict[str, Any]] = [
            {"reviewer_role": "correctness", "confidence": 90, "category": "logic",
             "severity": "info", "description": "No integration findings here"},
        ]

        with (
            patch("trw_mcp.tools.review.find_active_run", return_value=run_dir),
            patch("trw_mcp.tools.review._get_git_diff", return_value=""),
        ):
            tools["trw_review"].fn(reviewer_findings=reviewer_findings)

        integration_path = run_dir / "meta" / "integration-review.yaml"
        assert not integration_path.exists()

    def test_auto_mode_without_reviewer_findings_runs_basic_analysis(
        self, tmp_path: Path, run_dir: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Auto mode without reviewer_findings runs _run_multi_reviewer_analysis."""
        tools = make_ceremony_server(monkeypatch, tmp_path)
        _reset_config(TRWConfig(review_confidence_threshold=80))

        with (
            patch("trw_mcp.tools.review.find_active_run", return_value=run_dir),
            patch("trw_mcp.tools.review._get_git_diff", return_value=""),
        ):
            result = tools["trw_review"].fn(mode="auto")

        assert result["mode"] == "auto"
        assert "reviewer_roles_run" in result
        assert isinstance(result["reviewer_roles_run"], list)

    def test_auto_mode_logs_review_complete_event(
        self, tmp_path: Path, run_dir: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Auto mode logs review_complete event with mode='auto'."""
        tools = make_ceremony_server(monkeypatch, tmp_path)
        _reset_config(TRWConfig(review_confidence_threshold=0))

        with (
            patch("trw_mcp.tools.review.find_active_run", return_value=run_dir),
            patch("trw_mcp.tools.review._get_git_diff", return_value=""),
        ):
            result = tools["trw_review"].fn(mode="auto")

        events_path = run_dir / "meta" / "events.jsonl"
        lines = [
            line for line in events_path.read_text(encoding="utf-8").strip().split("\n")
            if line
        ]
        assert len(lines) >= 1
        # Find review_complete event (may not be last due to tool_invocation decorator)
        review_events = [
            json.loads(l) for l in lines if "review_complete" in l
        ]
        assert len(review_events) >= 1
        event = review_events[-1]
        assert event["event"] == "review_complete"
        assert event["mode"] == "auto"
        assert event["review_id"] == result["review_id"]

    def test_auto_mode_returns_confidence_threshold_in_result(
        self, tmp_path: Path, run_dir: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Auto mode result includes the configured confidence_threshold."""
        tools = make_ceremony_server(monkeypatch, tmp_path)
        _reset_config(TRWConfig(review_confidence_threshold=75))

        with (
            patch("trw_mcp.tools.review.find_active_run", return_value=run_dir),
            patch("trw_mcp.tools.review._get_git_diff", return_value=""),
        ):
            result = tools["trw_review"].fn(mode="auto")

        assert result["confidence_threshold"] == 75

    def test_auto_mode_all_findings_above_threshold_all_surfaced(
        self, tmp_path: Path, run_dir: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When all findings exceed threshold, all are surfaced."""
        tools = make_ceremony_server(monkeypatch, tmp_path)
        _reset_config(TRWConfig(review_confidence_threshold=50))

        reviewer_findings: list[dict[str, Any]] = [
            {"reviewer_role": "correctness", "confidence": 90, "category": "logic",
             "severity": "info", "description": "First"},
            {"reviewer_role": "security", "confidence": 85, "category": "sec",
             "severity": "warning", "description": "Second"},
            {"reviewer_role": "style", "confidence": 60, "category": "style",
             "severity": "info", "description": "Third"},
        ]

        with (
            patch("trw_mcp.tools.review.find_active_run", return_value=run_dir),
            patch("trw_mcp.tools.review._get_git_diff", return_value=""),
        ):
            result = tools["trw_review"].fn(reviewer_findings=reviewer_findings)

        assert result["surfaced_findings_count"] == 3
        assert result["total_findings_count"] == 3

    def test_auto_mode_all_findings_below_threshold_none_surfaced(
        self, tmp_path: Path, run_dir: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When all findings are below threshold, none are surfaced."""
        tools = make_ceremony_server(monkeypatch, tmp_path)
        _reset_config(TRWConfig(review_confidence_threshold=80))

        reviewer_findings: list[dict[str, Any]] = [
            {"reviewer_role": "correctness", "confidence": 30, "category": "logic",
             "severity": "critical", "description": "Below threshold critical"},
        ]

        with (
            patch("trw_mcp.tools.review.find_active_run", return_value=run_dir),
            patch("trw_mcp.tools.review._get_git_diff", return_value=""),
        ):
            result = tools["trw_review"].fn(reviewer_findings=reviewer_findings)

        # No findings surfaced → verdict is pass
        assert result["surfaced_findings_count"] == 0
        assert result["verdict"] == "pass"

    def test_auto_mode_no_run_returns_empty_review_yaml(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Auto mode with no run → review_yaml=''."""
        tools = make_ceremony_server(monkeypatch, tmp_path)
        _reset_config(TRWConfig(review_confidence_threshold=0))

        with (
            patch("trw_mcp.tools.review.find_active_run", return_value=None),
            patch("trw_mcp.tools.review._get_git_diff", return_value=""),
        ):
            result = tools["trw_review"].fn(mode="auto")

        assert result["review_yaml"] == ""
        assert result["run_path"] is None


# ---------------------------------------------------------------------------
# REVIEWER_ROLES constant
# ---------------------------------------------------------------------------


class TestReviewerRolesConstant:
    """REVIEWER_ROLES constant is correctly defined."""

    def test_reviewer_roles_is_tuple(self) -> None:
        assert isinstance(REVIEWER_ROLES, tuple)

    def test_reviewer_roles_has_six_entries(self) -> None:
        assert len(REVIEWER_ROLES) == 6

    def test_reviewer_roles_contains_expected_roles(self) -> None:
        expected = {"correctness", "security", "test-quality", "performance", "style", "spec-compliance"}
        assert set(REVIEWER_ROLES) == expected


# ---------------------------------------------------------------------------
# Integration tests
# ---------------------------------------------------------------------------


class TestIntegration:
    """Integration scenarios spanning multiple mode paths."""

    def test_no_run_available_manual_mode_returns_result(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """No run available → review still returns result but review_yaml=''."""
        tools = make_ceremony_server(monkeypatch, tmp_path)
        _reset_config()

        with patch("trw_mcp.tools.review.find_active_run", return_value=None):
            result = tools["trw_review"].fn(
                findings=[{"category": "correctness", "severity": "critical", "description": "Bug"}],
            )

        assert result["run_path"] is None
        assert result["verdict"] == "block"
        assert result["review_yaml"] == ""

    def test_explicit_run_path_manual_mode(
        self, tmp_path: Path, run_dir: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Explicit run_path works for manual mode."""
        tools = make_ceremony_server(monkeypatch, tmp_path)
        _reset_config()

        result = tools["trw_review"].fn(
            findings=[{"category": "correctness", "severity": "warning", "description": "Warning"}],
            run_path=str(run_dir),
        )

        assert result["run_path"] == str(run_dir)
        review_path = run_dir / "meta" / "review.yaml"
        assert review_path.exists()

    def test_explicit_run_path_cross_model_mode(
        self, tmp_path: Path, run_dir: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Explicit run_path works for cross_model mode."""
        tools = make_ceremony_server(monkeypatch, tmp_path)
        _reset_config(TRWConfig(cross_model_review_enabled=False))

        with patch("trw_mcp.tools.review._get_git_diff", return_value=""):
            result = tools["trw_review"].fn(
                mode="cross_model",
                run_path=str(run_dir),
            )

        assert result["run_path"] == str(run_dir)
        review_path = run_dir / "meta" / "review.yaml"
        assert review_path.exists()

    def test_explicit_run_path_auto_mode(
        self, tmp_path: Path, run_dir: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Explicit run_path works for auto mode."""
        tools = make_ceremony_server(monkeypatch, tmp_path)
        _reset_config(TRWConfig(review_confidence_threshold=0))

        with patch("trw_mcp.tools.review._get_git_diff", return_value=""):
            result = tools["trw_review"].fn(
                mode="auto",
                run_path=str(run_dir),
            )

        assert result["run_path"] == str(run_dir)
        review_path = run_dir / "meta" / "review.yaml"
        assert review_path.exists()

    def test_review_id_is_unique_across_calls(
        self, tmp_path: Path, run_dir: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Each trw_review call produces a unique review_id."""
        tools = make_ceremony_server(monkeypatch, tmp_path)
        _reset_config()

        with patch("trw_mcp.tools.review.find_active_run", return_value=run_dir):
            result1 = tools["trw_review"].fn()
            result2 = tools["trw_review"].fn()

        assert result1["review_id"] != result2["review_id"]

    def test_auto_mode_findings_none_filtered_correctly(
        self, tmp_path: Path, run_dir: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Non-dict items in reviewer_findings are gracefully skipped."""
        tools = make_ceremony_server(monkeypatch, tmp_path)
        _reset_config(TRWConfig(review_confidence_threshold=0))

        reviewer_findings: list[Any] = [
            None,
            "not-a-dict",
            {"reviewer_role": "correctness", "confidence": 90, "category": "logic",
             "severity": "info", "description": "Valid finding"},
        ]

        with (
            patch("trw_mcp.tools.review.find_active_run", return_value=run_dir),
            patch("trw_mcp.tools.review._get_git_diff", return_value=""),
        ):
            result = tools["trw_review"].fn(reviewer_findings=reviewer_findings)

        # Only the valid dict finding should be surfaced
        assert result["surfaced_findings_count"] == 1

    def test_auto_mode_finding_missing_confidence_defaults_to_zero(
        self, tmp_path: Path, run_dir: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Findings without confidence key default to 0 and are filtered at threshold > 0."""
        tools = make_ceremony_server(monkeypatch, tmp_path)
        _reset_config(TRWConfig(review_confidence_threshold=80))

        reviewer_findings: list[dict[str, Any]] = [
            {"reviewer_role": "correctness", "category": "logic",
             "severity": "critical", "description": "No confidence field"},
        ]

        with (
            patch("trw_mcp.tools.review.find_active_run", return_value=run_dir),
            patch("trw_mcp.tools.review._get_git_diff", return_value=""),
        ):
            result = tools["trw_review"].fn(reviewer_findings=reviewer_findings)

        # confidence=0 < threshold=80 → filtered out
        assert result["surfaced_findings_count"] == 0

    def test_cross_model_with_findings_from_stub(
        self, tmp_path: Path, run_dir: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """cross_model mode when stub returns findings normalizes severity."""
        tools = make_ceremony_server(monkeypatch, tmp_path)
        _reset_config(TRWConfig(
            cross_model_review_enabled=True,
            cross_model_provider="test-provider",
        ))

        stub_findings = [
            {"category": "security", "severity": "high", "description": "High severity finding"},
            {"category": "style", "severity": "medium", "description": "Medium severity finding"},
        ]

        with (
            patch("trw_mcp.tools.review.find_active_run", return_value=run_dir),
            patch("trw_mcp.tools.review._get_git_diff", return_value="diff content"),
            patch(
                "trw_mcp.tools.review._invoke_cross_model_review",
                return_value=stub_findings,
            ),
        ):
            result = tools["trw_review"].fn(mode="cross_model")

        # Verify severity normalization: high → critical, medium → warning
        review_path = run_dir / "meta" / "review.yaml"
        data = FileStateReader().read_yaml(review_path)
        severities = [f["severity"] for f in data["cross_model_findings"]]
        assert "critical" in severities
        assert "warning" in severities
        # Verdict should be "block" (has critical)
        assert result["verdict"] == "block"
        assert result["cross_model_skipped"] is False


# ---------------------------------------------------------------------------
# Edge cases: _get_git_diff
# ---------------------------------------------------------------------------


class TestGetGitDiff:
    """_get_git_diff returns diff text or empty string on any error."""

    @patch("trw_mcp.tools.review.subprocess.run")
    def test_returns_stdout_on_success(self, mock_run: Any) -> None:
        """Returns subprocess stdout when git diff succeeds."""
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="diff --git a/foo.py b/foo.py\n+new line\n",
            stderr="",
        )
        result = _get_git_diff()
        assert "diff --git" in result
        assert "+new line" in result

    @patch(
        "trw_mcp.tools.review.subprocess.run",
        side_effect=FileNotFoundError("git: command not found"),
    )
    def test_returns_empty_string_on_file_not_found(self, mock_run: Any) -> None:
        """FileNotFoundError (git not installed) → empty string, no exception."""
        result = _get_git_diff()
        assert result == ""

    @patch(
        "trw_mcp.tools.review.subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd="git", timeout=30),
    )
    def test_returns_empty_string_on_timeout(self, mock_run: Any) -> None:
        """TimeoutExpired → empty string, no exception."""
        result = _get_git_diff()
        assert result == ""

    @patch(
        "trw_mcp.tools.review.subprocess.run",
        side_effect=OSError("permission denied"),
    )
    def test_returns_empty_string_on_oserror(self, mock_run: Any) -> None:
        """OSError (e.g. permission denied on git binary) → empty string."""
        result = _get_git_diff()
        assert result == ""

    @patch("trw_mcp.tools.review.subprocess.run")
    def test_returns_empty_string_on_empty_diff(self, mock_run: Any) -> None:
        """Empty stdout (no changes staged) → empty string."""
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=""
        )
        result = _get_git_diff()
        assert result == ""

    @patch("trw_mcp.tools.review.subprocess.run")
    def test_returns_stdout_regardless_of_returncode(self, mock_run: Any) -> None:
        """Returns stdout even when returncode != 0 (git exits non-zero in some cases)."""
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="some diff output\n", stderr=""
        )
        result = _get_git_diff()
        assert result == "some diff output\n"


# ---------------------------------------------------------------------------
# Edge cases: _normalize_severity with unusual inputs
# ---------------------------------------------------------------------------


class TestNormalizeSeverityEdgeCases:
    """Additional edge cases for _normalize_severity."""

    def test_low_maps_to_info(self) -> None:
        """'low' is not critical or warning → maps to info."""
        assert _normalize_severity("low") == "info"

    def test_none_like_string_maps_to_info(self) -> None:
        """String 'none' maps to info (not a recognized high/warning tier)."""
        assert _normalize_severity("none") == "info"

    def test_mixed_case_medium_maps_to_warning(self) -> None:
        """'Medium' (mixed case) maps to warning via lower()."""
        assert _normalize_severity("Medium") == "warning"

    def test_padded_critical_maps_to_critical(self) -> None:
        """'  critical  ' with surrounding spaces → critical (strip handles it)."""
        assert _normalize_severity("  critical  ") == "critical"

    def test_tab_padded_high_maps_to_critical(self) -> None:
        """'\\thigh\\t' with tab padding → critical."""
        assert _normalize_severity("\thigh\t") == "critical"

    def test_very_long_unknown_string_maps_to_info(self) -> None:
        """An arbitrarily long unknown string → info (no crash)."""
        assert _normalize_severity("x" * 10_000) == "info"


# ---------------------------------------------------------------------------
# Edge cases: trw_review with empty/missing-key findings
# ---------------------------------------------------------------------------


class TestTrwReviewEdgeCases:
    """Edge cases for the trw_review MCP tool function."""

    def test_empty_findings_list_returns_pass_verdict(
        self,
        tmp_path: Path,
        run_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """findings=[] (explicit empty list) → manual mode, verdict=pass."""
        from tests._ceremony_helpers import make_ceremony_server
        tools = make_ceremony_server(monkeypatch, tmp_path)
        _reset_config()

        with patch("trw_mcp.tools.review.find_active_run", return_value=run_dir):
            result = tools["trw_review"].fn(findings=[])

        assert result["verdict"] == "pass"
        assert result["critical_count"] == 0
        assert result["warning_count"] == 0

    def test_findings_missing_severity_key_treated_as_info(
        self,
        tmp_path: Path,
        run_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Findings dict without 'severity' key → no critical/warning counted → pass."""
        from tests._ceremony_helpers import make_ceremony_server
        tools = make_ceremony_server(monkeypatch, tmp_path)
        _reset_config()

        with patch("trw_mcp.tools.review.find_active_run", return_value=run_dir):
            result = tools["trw_review"].fn(
                findings=[
                    {"category": "style", "description": "Missing severity key"},
                    {"category": "correctness"},
                ]
            )

        # No severity → not counted as critical or warning → pass
        assert result["verdict"] == "pass"
        assert result["critical_count"] == 0

    def test_findings_missing_description_key_does_not_crash(
        self,
        tmp_path: Path,
        run_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Findings without 'description' → no crash, verdict computed from severity."""
        from tests._ceremony_helpers import make_ceremony_server
        tools = make_ceremony_server(monkeypatch, tmp_path)
        _reset_config()

        with patch("trw_mcp.tools.review.find_active_run", return_value=run_dir):
            result = tools["trw_review"].fn(
                findings=[{"category": "security", "severity": "critical"}]
            )

        assert result["verdict"] == "block"
        assert result["critical_count"] == 1

    def test_findings_with_only_warning_severities_returns_warn(
        self,
        tmp_path: Path,
        run_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Multiple warning-severity findings → verdict=warn."""
        from tests._ceremony_helpers import make_ceremony_server
        tools = make_ceremony_server(monkeypatch, tmp_path)
        _reset_config()

        with patch("trw_mcp.tools.review.find_active_run", return_value=run_dir):
            result = tools["trw_review"].fn(
                findings=[
                    {"category": "style", "severity": "warning", "description": "A"},
                    {"category": "perf", "severity": "warning", "description": "B"},
                ]
            )

        assert result["verdict"] == "warn"
        assert result["warning_count"] == 2
        assert result["critical_count"] == 0

    def test_manual_mode_writes_review_yaml(
        self,
        tmp_path: Path,
        run_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Manual mode with findings writes review.yaml with correct structure.

        Manual mode does not include a 'mode' key in review.yaml (by design —
        only cross_model and auto modes include it). The artifact has verdict,
        critical_count, warning_count, info_count, and findings.
        """
        from tests._ceremony_helpers import make_ceremony_server
        tools = make_ceremony_server(monkeypatch, tmp_path)
        _reset_config()

        with patch("trw_mcp.tools.review.find_active_run", return_value=run_dir):
            tools["trw_review"].fn(
                findings=[
                    {"category": "security", "severity": "critical", "description": "XSS"},
                ]
            )

        review_path = run_dir / "meta" / "review.yaml"
        assert review_path.exists()
        data = FileStateReader().read_yaml(review_path)
        assert data["verdict"] == "block"
        assert data["critical_count"] == 1
        assert "findings" in data
        assert len(data["findings"]) == 1
