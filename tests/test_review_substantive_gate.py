"""Regression tests for substantive REVIEW readiness (FRAMEWORK v26.1 D-08)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from trw_mcp.models.config import TRWConfig
from trw_mcp.state.persistence import FileStateReader
from trw_mcp.tools._delivery_helpers import (
    _check_review_file_count_gate,
    _check_review_gate,
)
from trw_mcp.tools._delivery_review_gate import (
    _review_data_is_substantive,
    _review_nudge_for_run,
)
from trw_mcp.tools._review_auto import handle_auto_mode, handle_cross_model_mode
from trw_mcp.tools._review_manual import handle_manual_mode


def _write_run(tmp_path: Path, review_yaml: str) -> Path:
    run_dir = tmp_path / "docs" / "task" / "runs" / "20260709T000000Z-review"
    meta = run_dir / "meta"
    meta.mkdir(parents=True)
    (meta / "run.yaml").write_text(
        "run_id: review\nstatus: active\nphase: review\ncomplexity_class: STANDARD\n",
        encoding="utf-8",
    )
    (meta / "review.yaml").write_text(review_yaml, encoding="utf-8")
    return run_dir


def test_empty_manual_artifact_does_not_satisfy_review_gate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Empty reviewer/scope/evidence cannot masquerade as REVIEW readiness."""
    run_dir = _write_run(
        tmp_path,
        "verdict: pass\ncritical_count: 0\nfindings: []\nsubstantive: false\n",
    )
    monkeypatch.setattr(
        "trw_mcp.tools._delivery_helpers.get_config",
        lambda: TRWConfig(review_gate_mode="block"),
    )

    block, warning, advisory = _check_review_gate(run_dir, FileStateReader())

    assert block is not None
    assert "No substantive trw_review" in block
    assert warning is None
    assert advisory is None
    assert _review_nudge_for_run(run_dir, FileStateReader()) is not None


def test_empty_manual_artifact_does_not_bypass_review_scope_gate(tmp_path: Path) -> None:
    run_dir = _write_run(
        tmp_path,
        "verdict: pass\ncritical_count: 0\nfindings: []\nsubstantive: false\n",
    )
    events: list[dict[str, object]] = [{"event": "session_start"}]
    events.extend({"event": "file_modified", "data": {"path": f"src/{index}.py"}} for index in range(6))

    block = _check_review_file_count_gate(run_dir, events)

    assert block is not None
    assert "6 files modified" in block


def test_limited_pattern_scan_is_not_substantive_review(tmp_path: Path) -> None:
    run_dir = _write_run(
        tmp_path,
        "verdict: pass\ncritical_count: 0\nauto_analysis_limited: true\nfindings: []\n",
    )

    _block, warning, _advisory = _check_review_gate(run_dir, FileStateReader())

    assert warning is not None
    assert "No substantive trw_review" in warning


@pytest.mark.parametrize(
    "review_data",
    [
        {},
        {"findings": []},
        {"cross_model_findings": []},
        {"substantive": "true"},
        {"substantive": None},
        {"substantive": 1},
    ],
)
def test_blank_or_invalidly_stamped_review_data_is_not_substantive(review_data: dict[str, object]) -> None:
    assert _review_data_is_substantive(review_data) is False


def test_unstamped_legacy_review_requires_a_schema_valid_finding() -> None:
    review_data: dict[str, object] = {
        "verdict": "warn",
        "findings": [{"category": "correctness", "severity": "warning", "description": "Issue"}],
    }
    assert _review_data_is_substantive(review_data) is True


def test_malformed_review_yaml_uses_standard_block_policy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_dir = _write_run(tmp_path, "verdict: [unterminated\n")
    monkeypatch.setattr(
        "trw_mcp.tools._delivery_helpers.get_config",
        lambda: TRWConfig(review_gate_mode="block"),
    )

    block, warning, advisory = _check_review_gate(run_dir, FileStateReader())

    assert block is not None
    assert "No substantive trw_review" in block
    assert warning is None
    assert advisory is None
    assert _review_nudge_for_run(run_dir, FileStateReader()) is not None


def test_malformed_critical_count_uses_standard_block_policy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_dir = _write_run(
        tmp_path,
        "substantive: true\nverdict: block\ncritical_count: garbage\n",
    )
    monkeypatch.setattr(
        "trw_mcp.tools._delivery_helpers.get_config",
        lambda: TRWConfig(review_gate_mode="block"),
    )

    block, warning, advisory = _check_review_gate(run_dir, FileStateReader())

    assert block is not None
    assert "No substantive trw_review" in block
    assert warning is None
    assert advisory is None


def test_degraded_cross_model_artifact_does_not_satisfy_review_gate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The cross-model fallback must persist the same limited stamp the gate reads."""
    run_dir = _write_run(tmp_path, "verdict: pass\n")
    config = TRWConfig(cross_model_review_enabled=False, review_gate_mode="block")
    monkeypatch.setattr("trw_mcp.tools._delivery_helpers.get_config", lambda: config)

    with patch("trw_mcp.tools._review_helpers._get_git_diff", return_value="diff content"):
        result = handle_cross_model_mode(config, run_dir, "review-degraded", "2026-07-09T00:00:00Z")

    assert result["auto_analysis_limited"] is True
    assert result["substantive"] is False
    block, warning, advisory = _check_review_gate(run_dir, FileStateReader())
    assert block is not None
    assert "No substantive trw_review" in block
    assert warning is None
    assert advisory is None


@pytest.mark.parametrize(
    "findings",
    [
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
def test_invalid_manual_finding_artifact_does_not_satisfy_review_gate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    findings: list[dict[str, str]],
) -> None:
    run_dir = _write_run(tmp_path, "verdict: pass\n")
    config = TRWConfig(review_gate_mode="block")
    monkeypatch.setattr("trw_mcp.tools._delivery_helpers.get_config", lambda: config)

    result = handle_manual_mode(findings, run_dir, "review-invalid-manual", "2026-07-09T00:00:00Z")

    assert result["total_findings"] == 0
    assert result["substantive"] is False
    block, warning, advisory = _check_review_gate(run_dir, FileStateReader())
    assert block is not None
    assert "No substantive trw_review" in block
    assert warning is None
    assert advisory is None


@pytest.mark.parametrize(
    "reviewer_findings",
    [
        [],
        [{}],
        [{"category": "correctness", "severity": "warning"}],
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
def test_invalid_auto_finding_artifact_does_not_satisfy_review_gate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    reviewer_findings: list[dict[str, object]],
) -> None:
    run_dir = _write_run(tmp_path, "verdict: pass\n")
    config = TRWConfig(review_confidence_threshold=0, review_gate_mode="block")
    monkeypatch.setattr("trw_mcp.tools._delivery_helpers.get_config", lambda: config)

    with patch("trw_mcp.tools._review_helpers._get_git_diff", return_value="diff content"):
        result = handle_auto_mode(
            config,
            run_dir,
            "review-invalid-auto",
            "2026-07-09T00:00:00Z",
            reviewer_findings,
        )

    assert result["total_findings_count"] == 0
    assert result["substantive"] is False
    block, warning, advisory = _check_review_gate(run_dir, FileStateReader())
    assert block is not None
    assert "No substantive trw_review" in block
    assert warning is None
    assert advisory is None


def test_legacy_unstamped_review_with_valid_finding_is_not_evidence(tmp_path: Path) -> None:
    """Unstamped legacy prose cannot satisfy the typed review-evidence gate."""
    run_dir = _write_run(
        tmp_path,
        "verdict: warn\ncritical_count: 0\n"
        "findings:\n  - category: correctness\n    severity: warning\n    description: Issue\n",
    )

    block, warning, advisory = _check_review_gate(run_dir, FileStateReader())

    assert block is None
    assert warning is not None and "No substantive trw_review" in warning
    assert advisory is None
