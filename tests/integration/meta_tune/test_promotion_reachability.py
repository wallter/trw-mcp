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


def _sandbox_with_delta(delta: float) -> SandboxResult:
    return SandboxResult(
        exit_code=0,
        stdout=json.dumps(
            {
                "declared_metric_delta": delta,
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


def test_goodhart_gate_rejects_spike_once_history_exists(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """SAFE-001 FR-2: the Goodhart detector is re-armed by loading recent
    promotion history from the durable audit log. Once a baseline of small
    deltas exists, an implausible spike delta is rejected (goodhart-spike)."""
    cfg = _config(tmp_path)
    target = tmp_path / "CLAUDE.md"
    target.write_text("v0\n", encoding="utf-8")
    state_dir = tmp_path / "state"

    # Build a baseline of >= _GOODHART_MIN_HISTORY small-delta promotions so the
    # lookback window is populated when the next candidate is evaluated.
    small_deltas = [0.02, 0.03, 0.025]
    for i, d in enumerate(small_deltas):
        monkeypatch.setattr(dispatch, "run_sandboxed", lambda *a, _d=d, **k: _sandbox_with_delta(_d))
        res = dispatch.promote_candidate(
            target_path=target,
            candidate_content=f"v{i + 1}\n",
            proposer_id="agent-baseline",
            reviewer_id="alice",
            approval_ts=datetime.now(timezone.utc),
            sandbox_command=["python", "-c", "print('unused')"],
            _config=cfg,
            state_dir=state_dir,
        )
        assert res.decision == "approve", f"baseline promotion {i} should approve"

    # Now a candidate declaring a delta far above the spike ratio (10x max prior
    # ~= 0.3) must be rejected as a Goodhart spike — proving the history is loaded.
    monkeypatch.setattr(dispatch, "run_sandboxed", lambda *a, **k: _sandbox_with_delta(5.0))
    spike = dispatch.promote_candidate(
        target_path=target,
        candidate_content="v-spike\n",
        proposer_id="agent-hacker",
        reviewer_id="alice",
        approval_ts=datetime.now(timezone.utc),
        sandbox_command=["python", "-c", "print('unused')"],
        _config=cfg,
        state_dir=state_dir,
    )

    assert spike.promoted is False
    assert spike.decision == "reject"
    assert spike.reason == "goodhart-flag"
    # The live surface must retain the last legitimate promotion, not the spike.
    assert target.read_text(encoding="utf-8") == "v3\n"


def test_goodhart_gate_allows_legitimate_delta_within_band_after_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The re-armed Goodhart gate must NOT block legitimate promotions whose
    delta sits within the normal band relative to recent history."""
    cfg = _config(tmp_path)
    target = tmp_path / "CLAUDE.md"
    target.write_text("v0\n", encoding="utf-8")
    state_dir = tmp_path / "state"

    for i, d in enumerate([0.05, 0.06, 0.05]):
        monkeypatch.setattr(dispatch, "run_sandboxed", lambda *a, _d=d, **k: _sandbox_with_delta(_d))
        res = dispatch.promote_candidate(
            target_path=target,
            candidate_content=f"v{i + 1}\n",
            proposer_id="agent-baseline",
            reviewer_id="alice",
            approval_ts=datetime.now(timezone.utc),
            sandbox_command=["python", "-c", "print('unused')"],
            _config=cfg,
            state_dir=state_dir,
        )
        assert res.decision == "approve"

    # A modest improvement (well within 10x the max prior 0.06 = 0.6) approves.
    monkeypatch.setattr(dispatch, "run_sandboxed", lambda *a, **k: _sandbox_with_delta(0.1))
    ok = dispatch.promote_candidate(
        target_path=target,
        candidate_content="v-legit\n",
        proposer_id="agent-honest",
        reviewer_id="alice",
        approval_ts=datetime.now(timezone.utc),
        sandbox_command=["python", "-c", "print('unused')"],
        _config=cfg,
        state_dir=state_dir,
    )

    assert ok.promoted is True
    assert ok.decision == "approve"
    assert target.read_text(encoding="utf-8") == "v-legit\n"


def test_load_recent_history_parses_promoted_deltas(tmp_path: Path) -> None:
    """_load_recent_history extracts declared_metric_delta from promoted events
    only, ignoring other event types and bounding to max_rows."""
    audit_log = tmp_path / "audit.jsonl"
    cfg = _config(tmp_path).meta_tune.model_copy(update={"audit_log_path": str(audit_log)})
    full_cfg = _config(tmp_path).model_copy(update={"meta_tune": cfg})

    from trw_mcp.meta_tune.audit import append_audit_entry

    # A non-promoted event (must be ignored).
    append_audit_entry(
        audit_log,
        edit_id="e0",
        event="proposed",
        surface_classification="advisory",
        gate_decision="pending",
        payload={"declared_metric_delta": 99.0},
        _config=full_cfg,
    )
    # Three promoted events with known deltas.
    for i, d in enumerate([0.01, 0.02, 0.03]):
        append_audit_entry(
            audit_log,
            edit_id=f"e{i + 1}",
            event="promoted",
            surface_classification="advisory",
            gate_decision="approve",
            payload={"declared_metric_delta": d},
            _config=full_cfg,
        )

    history = dispatch._load_recent_history(audit_log, max_rows=2)
    # Only promoted events, bounded to the most recent 2.
    assert [row["declared_metric_delta"] for row in history] == [0.02, 0.03]


def test_rejected_dispatch_leaves_no_staging_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Round-2 fix: a rejected (sandbox-policy-violation) dispatch must not leak
    a staging directory to disk. The staging dir is a transient sandbox
    workspace cleaned up on every exit path, not just approve."""
    cfg = _config(tmp_path)
    target = tmp_path / "CLAUDE.md"
    target.write_text("before\n", encoding="utf-8")
    state_dir = tmp_path / "state"

    monkeypatch.setattr(
        dispatch,
        "run_sandboxed",
        lambda *args, **kwargs: _sandbox_escape(
            writes_outside_tmp=["/repo/outside-allowlist.txt"],
            network_attempted=False,
        ),
    )

    result = dispatch.promote_candidate(
        target_path=target,
        candidate_content="after\n",
        proposer_id="agent-escape",
        reviewer_id="alice",
        approval_ts=datetime.now(timezone.utc),
        sandbox_command=["python", "-c", "print('unused in test')"],
        _config=cfg,
        state_dir=state_dir,
    )

    assert result.decision == "reject"
    staging_root = state_dir / "staging"
    # The per-edit staging dir must be gone; nothing left under staging/.
    assert not (staging_root / result.edit_id).exists()
    assert not staging_root.exists() or not any(staging_root.iterdir())


def test_sandbox_failed_dispatch_leaves_no_staging_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A sandbox-replay-failed (non-zero exit) dispatch also cleans up staging."""
    cfg = _config(tmp_path)
    target = tmp_path / "CLAUDE.md"
    target.write_text("before\n", encoding="utf-8")
    state_dir = tmp_path / "state"

    failed = SandboxResult(
        exit_code=1,
        stdout="",
        stderr="boom",
        wall_ms=5.0,
        rss_peak_mb=1.0,
        network_attempted=False,
        writes_outside_tmp=[],
        timed_out=False,
    )
    monkeypatch.setattr(dispatch, "run_sandboxed", lambda *args, **kwargs: failed)

    result = dispatch.promote_candidate(
        target_path=target,
        candidate_content="after\n",
        proposer_id="agent-fail",
        reviewer_id="alice",
        approval_ts=datetime.now(timezone.utc),
        sandbox_command=["python", "-c", "print('unused in test')"],
        _config=cfg,
        state_dir=state_dir,
    )

    assert result.decision == "reject"
    assert result.reason == "sandbox-replay-failed"
    assert not (state_dir / "staging" / result.edit_id).exists()


def test_approved_dispatch_leaves_no_staging_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An approved dispatch writes the live target + snapshot but still cleans up
    the transient staging dir (the staged copy is not needed afterward)."""
    cfg = _config(tmp_path)
    target = tmp_path / "CLAUDE.md"
    target.write_text("before\n", encoding="utf-8")
    state_dir = tmp_path / "state"

    monkeypatch.setattr(dispatch, "run_sandboxed", lambda *args, **kwargs: _sandbox_ok())

    result = dispatch.promote_candidate(
        target_path=target,
        candidate_content="after\n",
        proposer_id="agent-ok",
        reviewer_id="alice",
        approval_ts=datetime.now(timezone.utc),
        sandbox_command=["python", "-c", "print('unused in test')"],
        _config=cfg,
        state_dir=state_dir,
    )

    assert result.promoted is True
    assert target.read_text(encoding="utf-8") == "after\n"
    assert not (state_dir / "staging" / result.edit_id).exists()


def test_concurrent_promotes_serialize_via_target_write_lock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Round-2 fix: concurrent promotes against the same target must not corrupt it.

    The read-modify-write of the live target is guarded by a per-target advisory
    lock. With two threads racing on the same file, the final content must equal
    exactly one whole candidate (never interleaved/truncated bytes).
    """
    import threading

    cfg = _config(tmp_path)
    target = tmp_path / "CLAUDE.md"
    target.write_text("before\n", encoding="utf-8")

    monkeypatch.setattr(dispatch, "run_sandboxed", lambda *args, **kwargs: _sandbox_ok())

    state_dir = tmp_path / "state"
    barrier = threading.Barrier(2)
    results: list[Any] = []
    errors: list[Exception] = []
    candidates = {"AAAA\n" * 200, "BBBB\n" * 200}

    def _promote(content: str) -> None:
        barrier.wait()
        try:
            res = dispatch.promote_candidate(
                target_path=target,
                candidate_content=content,
                proposer_id="agent-1",
                reviewer_id="alice",
                approval_ts=datetime.now(timezone.utc),
                sandbox_command=["python", "-c", "print('unused')"],
                _config=cfg,
                state_dir=state_dir,
            )
            results.append(res)
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=_promote, args=(c,)) for c in candidates]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"concurrent promote raised: {errors}"
    # Final content is exactly one whole candidate — no interleaving.
    assert target.read_text(encoding="utf-8") in candidates
    # The per-target lock sidecar was created under the state dir.
    assert (state_dir / "locks").is_dir()
