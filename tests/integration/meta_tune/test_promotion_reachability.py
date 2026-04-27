"""Integration reachability tests for the shipped SAFE-001 promotion paths."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from tests.conftest import extract_tool_fn, make_test_server
from trw_mcp.meta_tune import dispatch
from trw_mcp.meta_tune.promotion_gate import PromotionGate, PromotionProposal
from trw_mcp.meta_tune.sandbox import SandboxResult
from trw_mcp.models.config import reload_config
from trw_mcp.models.config._main import TRWConfig
from trw_mcp.models.config._sub_models import MetaTuneConfig


def _config(tmp_path: Path) -> TRWConfig:
    return TRWConfig(
        meta_tune=MetaTuneConfig(
            enabled=True,
            audit_log_path=str(tmp_path / "audit" / "meta_tune_audit.jsonl"),
            sandbox_timeout_seconds=3.5,
        )
    )


def _sandbox_ok() -> SandboxResult:
    return SandboxResult(
        exit_code=0,
        stdout=json.dumps(
            {
                "declared_metric_delta": 0.25,
                "outcome_trace": [
                    {"task": "t1", "score": 0.4},
                    {"task": "t2", "score": 0.5},
                    {"task": "t3", "score": 0.7},
                    {"task": "t4", "score": 0.6},
                    {"task": "t5", "score": 0.8},
                ],
            }
        ),
        stderr="",
        wall_ms=12.0,
        rss_peak_mb=1.0,
        network_attempted=False,
        writes_outside_tmp=[],
        timed_out=False,
    )


def _sandbox_escape(*, writes_outside_tmp: list[str], network_attempted: bool) -> SandboxResult:
    return SandboxResult(
        exit_code=0,
        stdout=json.dumps(
            {
                "declared_metric_delta": 0.25,
                "outcome_trace": [
                    {"task": "t1", "score": 0.4},
                    {"task": "t2", "score": 0.5},
                    {"task": "t3", "score": 0.7},
                    {"task": "t4", "score": 0.6},
                    {"task": "t5", "score": 0.8},
                ],
            }
        ),
        stderr="",
        wall_ms=12.0,
        rss_peak_mb=1.0,
        network_attempted=network_attempted,
        writes_outside_tmp=writes_outside_tmp,
        timed_out=False,
    )


def test_direct_dispatch_invokes_promotion_gate_and_writes_live_surface(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = _config(tmp_path)
    target = tmp_path / "CLAUDE.md"
    target.write_text("before\n", encoding="utf-8")
    calls: list[str] = []

    original_evaluate = PromotionGate.evaluate

    def _spy_evaluate(self: PromotionGate, proposal: PromotionProposal, **kwargs: Any) -> object:
        calls.append(proposal.proposal_id)
        return original_evaluate(self, proposal, **kwargs)

    monkeypatch.setattr(dispatch, "run_sandboxed", lambda *args, **kwargs: _sandbox_ok())
    monkeypatch.setattr(PromotionGate, "evaluate", _spy_evaluate)

    result = dispatch.promote_candidate(
        target_path=target,
        candidate_content="after\n",
        proposer_id="agent-1",
        reviewer_id="alice",
        approval_ts=datetime.now(timezone.utc),
        sandbox_command=["python", "-c", "print('unused in test')"],
        _config=cfg,
        state_dir=tmp_path / "state",
    )

    audit_text = (tmp_path / "audit" / "meta_tune_audit.jsonl").read_text(encoding="utf-8")
    assert calls == [result.edit_id]
    assert result.promoted is True
    assert target.read_text(encoding="utf-8") == "after\n"
    assert '"event":"proposed"' in audit_text
    assert '"event":"sandboxed"' in audit_text
    assert '"event":"promoted"' in audit_text


def test_direct_dispatch_rejects_off_allowlist_write_before_gate_or_live_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = _config(tmp_path)
    target = tmp_path / "CLAUDE.md"
    target.write_text("before\n", encoding="utf-8")

    def _fail_evaluate(*args: object, **kwargs: Any) -> object:
        raise AssertionError("promotion gate should not run after sandbox write escape")

    def _fail_eval_gaming(*args: object, **kwargs: Any) -> object:
        raise AssertionError("eval-gaming detector should not run after sandbox write escape")

    monkeypatch.setattr(
        dispatch,
        "run_sandboxed",
        lambda *args, **kwargs: _sandbox_escape(
            writes_outside_tmp=["/repo/outside-allowlist.txt"],
            network_attempted=False,
        ),
    )
    monkeypatch.setattr(PromotionGate, "evaluate", _fail_evaluate)
    monkeypatch.setattr(dispatch, "detect_eval_gaming", _fail_eval_gaming)

    result = dispatch.promote_candidate(
        target_path=target,
        candidate_content="after\n",
        proposer_id="agent-escape",
        reviewer_id="alice",
        approval_ts=datetime.now(timezone.utc),
        sandbox_command=["python", "-c", "print('unused in test')"],
        _config=cfg,
        state_dir=tmp_path / "state",
    )

    audit_text = (tmp_path / "audit" / "meta_tune_audit.jsonl").read_text(encoding="utf-8")
    assert result.promoted is False
    assert result.decision == "reject"
    assert result.reason == "sandbox-policy-violation"
    assert target.read_text(encoding="utf-8") == "before\n"
    assert '"event":"sandboxed"' in audit_text
    assert '"event":"rejected"' in audit_text
    assert '"writes_outside_tmp":["/repo/outside-allowlist.txt"]' in audit_text


def test_direct_dispatch_rejects_network_attempt_before_gate_or_live_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = _config(tmp_path)
    target = tmp_path / "CLAUDE.md"
    target.write_text("before\n", encoding="utf-8")

    def _fail_evaluate(*args: object, **kwargs: Any) -> object:
        raise AssertionError("promotion gate should not run after sandbox network attempt")

    def _fail_eval_gaming(*args: object, **kwargs: Any) -> object:
        raise AssertionError("eval-gaming detector should not run after sandbox network attempt")

    monkeypatch.setattr(
        dispatch,
        "run_sandboxed",
        lambda *args, **kwargs: _sandbox_escape(
            writes_outside_tmp=[],
            network_attempted=True,
        ),
    )
    monkeypatch.setattr(PromotionGate, "evaluate", _fail_evaluate)
    monkeypatch.setattr(dispatch, "detect_eval_gaming", _fail_eval_gaming)

    result = dispatch.promote_candidate(
        target_path=target,
        candidate_content="after\n",
        proposer_id="agent-network",
        reviewer_id="alice",
        approval_ts=datetime.now(timezone.utc),
        sandbox_command=["python", "-c", "print('unused in test')"],
        _config=cfg,
        state_dir=tmp_path / "state",
    )

    audit_text = (tmp_path / "audit" / "meta_tune_audit.jsonl").read_text(encoding="utf-8")
    assert result.promoted is False
    assert result.decision == "reject"
    assert result.reason == "sandbox-policy-violation"
    assert target.read_text(encoding="utf-8") == "before\n"
    assert '"event":"sandboxed"' in audit_text
    assert '"event":"rejected"' in audit_text
    assert '"network_attempted":true' in audit_text


def test_mcp_tool_path_invokes_same_promotion_gate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = _config(tmp_path)
    reload_config(cfg)
    target = tmp_path / "CLAUDE.md"
    target.write_text("before\n", encoding="utf-8")
    calls: list[str] = []

    original_evaluate = PromotionGate.evaluate

    def _spy_evaluate(self: PromotionGate, proposal: PromotionProposal, **kwargs: Any) -> object:
        calls.append(proposal.proposal_id)
        return original_evaluate(self, proposal, **kwargs)

    monkeypatch.setattr(dispatch, "run_sandboxed", lambda *args, **kwargs: _sandbox_ok())
    monkeypatch.setattr(PromotionGate, "evaluate", _spy_evaluate)

    try:
        server = make_test_server("meta_tune")
        propose = extract_tool_fn(server, "trw_meta_tune_propose")
        result = propose(
            target_path=str(target),
            candidate_content="after-from-tool\n",
            proposer_id="agent-2",
            reviewer_id="alice",
            approval_ts=datetime.now(timezone.utc).isoformat(),
            sandbox_command=["python", "-c", "print('unused in test')"],
            state_dir=str(tmp_path / "state"),
        )
    finally:
        reload_config(None)

    assert len(calls) == 1
    assert result["decision"] == "approve"
    assert target.read_text(encoding="utf-8") == "after-from-tool\n"
