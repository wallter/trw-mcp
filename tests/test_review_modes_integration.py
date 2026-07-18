"""Tests for review mode integration and manual-mode edge cases."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from tests._ceremony_helpers import make_ceremony_server
from trw_mcp.models.config import TRWConfig, _reset_config
from trw_mcp.state.persistence import FileStateReader

from ._review_modes_support import run_dir  # noqa: F401


class TestIntegration:
    """Integration scenarios spanning multiple mode paths."""

    @pytest.mark.parametrize(
        ("tool_kwargs", "review_patches"),
        [
            (
                {
                    "findings": [{"category": "correctness", "severity": "warning", "description": "Warning"}],
                },
                (),
            ),
            (
                {"mode": "cross_model"},
                (("trw_mcp.tools._review_helpers._get_git_diff", ""),),
            ),
            (
                {
                    "mode": "auto",
                    "reviewer_findings": [
                        {
                            "reviewer_role": "correctness",
                            "confidence": 95,
                            "category": "logic",
                            "severity": "warning",
                            "description": "Surfaced",
                        },
                    ],
                },
                (("trw_mcp.tools._review_helpers._get_git_diff", ""),),
            ),
        ],
    )
    def test_review_modes_emit_audit_cycles_only_for_explicit_prd_ids(
        self,
        tmp_path: Path,
        run_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
        tool_kwargs: dict[str, Any],
        review_patches: tuple[tuple[str, object], ...],
    ) -> None:
        tools = make_ceremony_server(monkeypatch, tmp_path)
        _reset_config(TRWConfig(review_confidence_threshold=0, cross_model_review_enabled=False))

        run_yaml = run_dir / "meta" / "run.yaml"
        run_yaml.write_text(
            "\n".join(
                [
                    "run_id: review-modes-test",
                    "status: active",
                    "phase: review",
                    "task_name: review-modes-task",
                    "prd_scope:",
                    "  - PRD-QUAL-056",
                    "  - PRD-CORE-104",
                    "  - PRD-CORE-125",
                    "",
                ],
            ),
            encoding="utf-8",
        )

        context_managers = [patch("trw_mcp.tools.review.find_active_run", return_value=run_dir)]
        context_managers.extend(patch(target, return_value=value) for target, value in review_patches)

        with context_managers[0]:
            if len(context_managers) == 1:
                result = tools["trw_review"].fn(
                    prd_ids=["PRD-CORE-104", "PRD-CORE-125"],
                    **tool_kwargs,
                )
            else:
                with context_managers[1]:
                    result = tools["trw_review"].fn(
                        prd_ids=["PRD-CORE-104", "PRD-CORE-125"],
                        **tool_kwargs,
                    )

        assert result["review_yaml"]
        events = FileStateReader().read_jsonl(run_dir / "meta" / "events.jsonl")
        audit_events = [event for event in events if event["event"] == "audit_cycle_complete"]
        assert [event["prd_id"] for event in audit_events] == ["PRD-CORE-104", "PRD-CORE-125"]

    def test_no_run_available_manual_mode_returns_result(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
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
        self,
        tmp_path: Path,
        run_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
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
        self,
        tmp_path: Path,
        run_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        tools = make_ceremony_server(monkeypatch, tmp_path)
        _reset_config(TRWConfig(cross_model_review_enabled=False))

        with patch("trw_mcp.tools._review_helpers._get_git_diff", return_value=""):
            result = tools["trw_review"].fn(
                mode="cross_model",
                run_path=str(run_dir),
            )

        assert result["run_path"] == str(run_dir)
        review_path = run_dir / "meta" / "review.yaml"
        assert review_path.exists()

    def test_degraded_cross_model_pattern_scan_is_publicly_non_substantive(
        self,
        tmp_path: Path,
        run_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Provider degradation must not turn a limited fallback into REVIEW evidence."""
        tools = make_ceremony_server(monkeypatch, tmp_path)
        _reset_config(TRWConfig(cross_model_review_enabled=False))

        with patch("trw_mcp.tools._review_helpers._get_git_diff", return_value="diff content"):
            result = tools["trw_review"].fn(
                mode="cross_model",
                run_path=str(run_dir),
            )

        assert result["review_family_coverage"] == "single_family"
        assert result["auto_analysis_limited"] is True
        assert "pattern-scan only" in result["limited_reason"]
        assert result["substantive"] is False

        artifact = FileStateReader().read_yaml(run_dir / "meta" / "review.yaml")
        assert artifact["auto_analysis_limited"] is True
        assert artifact["limited_reason"] == result["limited_reason"]
        assert artifact["substantive"] is False

        from trw_mcp.state.ceremony_progress import read_ceremony_state

        assert read_ceremony_state(tmp_path / ".trw").review_called is False

    @pytest.mark.parametrize(
        "reviewer_findings",
        [
            [],
            [{}],
            [{"category": "correctness", "severity": "warning"}],
            [{"category": " ", "severity": "warning", "description": "Issue"}],
            [{"category": "correctness", "severity": "warning", "description": "  "}],
            [{"category": "correctness", "severity": "bogus", "description": "Issue"}],
            [
                {
                    "category": "correctness",
                    "severity": "warning",
                    "description": "Issue",
                    "confidence": "garbage",
                }
            ],
        ],
    )
    def test_auto_invalid_precollected_evidence_is_publicly_non_substantive(
        self,
        tmp_path: Path,
        run_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
        reviewer_findings: list[dict[str, object]],
    ) -> None:
        """Only schema-valid, non-placeholder findings can satisfy REVIEW."""
        tools = make_ceremony_server(monkeypatch, tmp_path)
        _reset_config(TRWConfig(review_confidence_threshold=0))

        with patch("trw_mcp.tools._review_helpers._get_git_diff", return_value="diff content"):
            result = tools["trw_review"].fn(
                reviewer_findings=reviewer_findings,
                run_path=str(run_dir),
            )

        assert result["total_findings_count"] == 0
        assert result["surfaced_findings_count"] == 0
        assert result["auto_analysis_limited"] is True
        assert "no schema-valid findings" in result["limited_reason"]
        assert result["substantive"] is False

        artifact = FileStateReader().read_yaml(run_dir / "meta" / "review.yaml")
        assert artifact["findings"] == []
        assert artifact["auto_analysis_limited"] is True
        assert artifact["substantive"] is False

        from trw_mcp.state.ceremony_progress import read_ceremony_state

        assert read_ceremony_state(tmp_path / ".trw").review_called is False

    def test_explicit_run_path_auto_mode(
        self,
        tmp_path: Path,
        run_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        tools = make_ceremony_server(monkeypatch, tmp_path)
        _reset_config(TRWConfig(review_confidence_threshold=0))

        with patch("trw_mcp.tools._review_helpers._get_git_diff", return_value=""):
            result = tools["trw_review"].fn(
                mode="auto",
                run_path=str(run_dir),
            )

        assert result["run_path"] == str(run_dir)
        review_path = run_dir / "meta" / "review.yaml"
        assert review_path.exists()

    def test_review_id_is_unique_across_calls(
        self,
        tmp_path: Path,
        run_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        tools = make_ceremony_server(monkeypatch, tmp_path)
        _reset_config()

        with patch("trw_mcp.tools.review.find_active_run", return_value=run_dir):
            result1 = tools["trw_review"].fn()
            result2 = tools["trw_review"].fn()

        assert result1["review_id"] != result2["review_id"]

    def test_auto_mode_findings_none_filtered_correctly(
        self,
        tmp_path: Path,
        run_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        tools = make_ceremony_server(monkeypatch, tmp_path)
        _reset_config(TRWConfig(review_confidence_threshold=0))

        reviewer_findings: list[Any] = [
            None,
            "not-a-dict",
            {
                "reviewer_role": "correctness",
                "confidence": 90,
                "category": "logic",
                "severity": "info",
                "description": "Valid finding",
            },
        ]

        with (
            patch("trw_mcp.tools.review.find_active_run", return_value=run_dir),
            patch("trw_mcp.tools._review_helpers._get_git_diff", return_value=""),
        ):
            result = tools["trw_review"].fn(reviewer_findings=reviewer_findings)

        assert result["surfaced_findings_count"] == 1

    def test_auto_mode_finding_missing_confidence_defaults_to_zero(
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
                "category": "logic",
                "severity": "critical",
                "description": "No confidence field",
            },
        ]

        with (
            patch("trw_mcp.tools.review.find_active_run", return_value=run_dir),
            patch("trw_mcp.tools._review_helpers._get_git_diff", return_value=""),
        ):
            result = tools["trw_review"].fn(reviewer_findings=reviewer_findings)

        assert result["surfaced_findings_count"] == 0

    def test_cross_model_with_findings_from_stub(
        self,
        tmp_path: Path,
        run_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        tools = make_ceremony_server(monkeypatch, tmp_path)
        _reset_config(
            TRWConfig(
                cross_model_review_enabled=True,
                cross_model_provider="test-provider",
            )
        )

        stub_findings = [
            {"category": "security", "severity": "high", "description": "High severity finding"},
            {"category": "style", "severity": "medium", "description": "Medium severity finding"},
        ]

        with (
            patch("trw_mcp.tools.review.find_active_run", return_value=run_dir),
            patch("trw_mcp.tools._review_helpers._get_git_diff", return_value="diff content"),
            patch(
                "trw_mcp.tools._review_helpers._invoke_cross_model_review",
                return_value=stub_findings,
            ),
        ):
            result = tools["trw_review"].fn(mode="cross_model")

        review_path = run_dir / "meta" / "review.yaml"
        data = FileStateReader().read_yaml(review_path)
        severities = [finding["severity"] for finding in data["cross_model_findings"]]
        assert "critical" in severities
        assert "warning" in severities
        assert result["verdict"] == "block"
        assert result["cross_model_skipped"] is False


class TestTrwReviewEdgeCases:
    """Edge cases for the trw_review MCP tool function."""

    def test_empty_findings_list_returns_pass_verdict(
        self,
        tmp_path: Path,
        run_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
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
        tools = make_ceremony_server(monkeypatch, tmp_path)
        _reset_config()

        with patch("trw_mcp.tools.review.find_active_run", return_value=run_dir):
            result = tools["trw_review"].fn(
                findings=[
                    {"category": "style", "description": "Missing severity key"},
                    {"category": "correctness"},
                ]
            )

        assert result["verdict"] == "pass"
        assert result["critical_count"] == 0

    def test_findings_missing_description_key_does_not_crash(
        self,
        tmp_path: Path,
        run_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        tools = make_ceremony_server(monkeypatch, tmp_path)
        _reset_config()

        with patch("trw_mcp.tools.review.find_active_run", return_value=run_dir):
            result = tools["trw_review"].fn(findings=[{"category": "security", "severity": "critical"}])

        assert result["verdict"] == "pass"
        assert result["critical_count"] == 0
        assert result["total_findings"] == 0
        assert result["substantive"] is False

    def test_findings_with_only_warning_severities_returns_warn(
        self,
        tmp_path: Path,
        run_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
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
