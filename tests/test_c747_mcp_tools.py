"""Tests for c747 risk-report MCP tools (PRD-DIST-1990).

The before-edit-hint-batch tool (PRD-DIST-1989) was deleted whole under
PRD-CORE-300: ``compute_before_edit_hint_batch``, ``BeforeYouEditBatchPayload``
and ``BeforeEditHintBatchResult`` no longer exist. ``trw_code(mode="hint")``
retains a per-file batch-artifact FALLBACK (see test_before_edit_hint_tool.py
TestBatchArtifactFallback) but there is no whole-sidecar batch dump anymore.
"""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from trw_mcp.state._entitlements import sign_entitlement_for_dev
from trw_mcp.tools._sidecar_substrate import SCHEMA_VERSION_ACCEPTED
from trw_mcp.tools.codebase_risk_report import (
    CodebaseRiskReportResult,
    FileRiskScorePayload,
    compute_codebase_risk_report,
)


def _make_git_repo(path: Path) -> str:
    path.mkdir(parents=True, exist_ok=True)
    (path / "foo.py").write_text("x = 1\n")
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=path, check=True)
    subprocess.run(["git", "add", "."], cwd=path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=path, check=True)
    return (
        subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=path,
        )
        .decode()
        .strip()
    )


def _write_entitlement(trw_dir: Path, tier: str) -> None:
    trw_dir.mkdir(parents=True, exist_ok=True)
    future = (datetime.now(tz=timezone.utc) + timedelta(days=30)).isoformat()
    sig = sign_entitlement_for_dev(
        tier=tier,
        issued_to="t@t",
        expires_at=future,  # type: ignore[arg-type]
    )
    (trw_dir / "entitlements.yaml").write_text(
        f"tier: {tier}\nissued_to: t@t\nexpires_at: '{future}'\nsignature: {sig}\n",
    )


def _write_envelope(path: Path, sha: str, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    envelope = {
        "schema_version": SCHEMA_VERSION_ACCEPTED,
        "sha": sha,
        "generated_at_unix": 1714000000.0,
        "payload": payload,
    }
    path.write_text(json.dumps(envelope))


class TestRiskReportTool:
    def test_free_tier_blocked(self, tmp_path: Path) -> None:
        _make_git_repo(tmp_path)
        r = compute_codebase_risk_report(repo_root=str(tmp_path))
        assert r.tier == "free"
        assert r.distill_status == "tier_required"

    def test_pro_no_sidecar(self, tmp_path: Path) -> None:
        _make_git_repo(tmp_path)
        _write_entitlement(tmp_path / ".trw", "pro")
        r = compute_codebase_risk_report(repo_root=str(tmp_path))
        assert r.tier == "pro"
        assert r.distill_status == "sidecar_missing"
        assert "risk-report" in (r.distill_action or "")

    def test_pro_with_sidecar_happy(self, tmp_path: Path) -> None:
        sha = _make_git_repo(tmp_path)
        cache_dir = tmp_path / ".trw" / "distill" / "map-cache"
        _write_envelope(
            cache_dir / f"risk-report-{sha}.json",
            sha,
            [
                {
                    "target_path": "foo.py",
                    "target_exists_in_map": True,
                    "composite_score": 0.8,
                    "fanin_score": 1.0,
                    "fanout_score": 0.2,
                    "untested_score": 1.0,
                    "undocumented_score": 0.5,
                    "size_score": 0.4,
                    "churn_score": 0.6,
                    "fanin_count": 10,
                    "fanout_count": 2,
                    "test_edge_count": 0,
                    "doc_edge_count": 0,
                    "line_count": 500,
                },
                {
                    "target_path": "bar.py",
                    "target_exists_in_map": True,
                    "composite_score": 0.4,
                    "fanin_score": 0.2,
                    "fanout_score": 0.1,
                    "untested_score": 0.5,
                    "undocumented_score": 0.5,
                    "size_score": 0.5,
                    "churn_score": 0.3,
                    "fanin_count": 2,
                    "fanout_count": 1,
                    "test_edge_count": 1,
                    "doc_edge_count": 0,
                    "line_count": 200,
                },
            ],
        )
        _write_entitlement(tmp_path / ".trw", "pro")
        r = compute_codebase_risk_report(repo_root=str(tmp_path))
        assert r.distill_status == "hint_available"
        assert r.n_scores == 2
        assert r.risk_report[0].target_path == "foo.py"
        assert r.risk_report[0].composite_score == 0.8
        assert r.risk_report[1].target_path == "bar.py"

    def test_top_n_truncation(self, tmp_path: Path) -> None:
        sha = _make_git_repo(tmp_path)
        cache_dir = tmp_path / ".trw" / "distill" / "map-cache"
        _write_envelope(
            cache_dir / f"risk-report-{sha}.json",
            sha,
            [
                {
                    "target_path": f"f{i}.py",
                    "target_exists_in_map": True,
                    "composite_score": 1.0 - i * 0.1,
                    "fanin_score": 0.5,
                    "fanout_score": 0.5,
                    "untested_score": 0.5,
                    "undocumented_score": 0.5,
                    "size_score": 0.5,
                    "churn_score": 0.0,
                    "fanin_count": 0,
                    "fanout_count": 0,
                    "test_edge_count": 0,
                    "doc_edge_count": 0,
                    "line_count": 100,
                }
                for i in range(10)
            ],
        )
        _write_entitlement(tmp_path / ".trw", "pro")
        r = compute_codebase_risk_report(repo_root=str(tmp_path), top_n=3)
        assert r.n_scores == 3
        # top-N preserves DESC order from sidecar
        assert r.risk_report[0].target_path == "f0.py"

    def test_top_n_zero_returns_all(self, tmp_path: Path) -> None:
        sha = _make_git_repo(tmp_path)
        cache_dir = tmp_path / ".trw" / "distill" / "map-cache"
        _write_envelope(
            cache_dir / f"risk-report-{sha}.json",
            sha,
            [
                {
                    "target_path": f"f{i}.py",
                    "target_exists_in_map": True,
                    "composite_score": 0.5,
                    "fanin_score": 0.5,
                    "fanout_score": 0.5,
                    "untested_score": 0.5,
                    "undocumented_score": 0.5,
                    "size_score": 0.5,
                    "churn_score": 0.0,
                    "fanin_count": 0,
                    "fanout_count": 0,
                    "test_edge_count": 0,
                    "doc_edge_count": 0,
                    "line_count": 100,
                }
                for i in range(5)
            ],
        )
        _write_entitlement(tmp_path / ".trw", "pro")
        r = compute_codebase_risk_report(repo_root=str(tmp_path), top_n=0)
        assert r.n_scores == 5

    def test_pro_payload_not_a_list(self, tmp_path: Path) -> None:
        sha = _make_git_repo(tmp_path)
        cache_dir = tmp_path / ".trw" / "distill" / "map-cache"
        _write_envelope(
            cache_dir / f"risk-report-{sha}.json",
            sha,
            {"not_a_list": "boom"},
        )
        _write_entitlement(tmp_path / ".trw", "pro")
        r = compute_codebase_risk_report(repo_root=str(tmp_path))
        assert r.distill_status == "sidecar_malformed"


class TestModelContracts:
    def test_risk_report_result_frozen(self) -> None:
        r = CodebaseRiskReportResult(tier="free")
        with pytest.raises(Exception):
            r.tier = "pro"  # type: ignore[misc]

    def test_file_risk_score_extra_forbid(self) -> None:
        with pytest.raises(Exception):
            FileRiskScorePayload(  # type: ignore[call-arg]
                target_path="x",
                target_exists_in_map=True,
                composite_score=0.5,
                fanin_score=0.5,
                fanout_score=0.5,
                untested_score=0.5,
                undocumented_score=0.5,
                size_score=0.5,
                unknown_field="boom",
            )
