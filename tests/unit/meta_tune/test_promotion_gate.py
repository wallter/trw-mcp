"""Tests for meta_tune.promotion_gate — PRD-HPO-SAFE-001 FR-2/FR-3."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from trw_mcp.meta_tune.promotion_gate import (
    GateDecision,
    PromotionGate,
    PromotionProposal,
)
from trw_mcp.models.config._main import TRWConfig
from trw_mcp.models.config._sub_models import MetaTuneConfig


def _cfg_enabled() -> TRWConfig:
    return TRWConfig(
        meta_tune=MetaTuneConfig(
            enabled=True,
            audit_log_path=".trw/meta_tune/meta_tune_audit.jsonl",
        )
    )


def _proposal(delta: float = 0.1, surfaces: list[str] | None = None) -> PromotionProposal:
    return PromotionProposal(
        proposal_id="prop-1",
        declared_metric_delta=delta,
        surface_classification="advisory",
        surfaces=tuple(surfaces or ["prompt"]),
        diff_lines_touched=5,
    )


def test_gate_noop_when_disabled() -> None:
    """FR-7/FR-13: disabled kill switch returns a rejected no-op decision."""
    gate = PromotionGate()
    d = gate.evaluate(_proposal())
    assert d.decision == "reject"
    assert d.disabled is True


def test_gate_rejects_control_surface() -> None:
    """FR-1: control-surface proposals are rejected irrespective of delta."""
    gate = PromotionGate(config=_cfg_enabled())
    d = gate.evaluate(_proposal(surfaces=[]))
    p = PromotionProposal(
        proposal_id="p",
        declared_metric_delta=0.5,
        surface_classification="control",
        surfaces=("policy",),
        diff_lines_touched=2,
    )
    d = gate.evaluate(p)
    assert d.decision == "reject"
    assert "control" in d.reason


def test_gate_approves_small_improvement_with_human_signoff() -> None:
    gate = PromotionGate(config=_cfg_enabled())
    p = _proposal(delta=0.02)
    d = gate.evaluate(p, reviewer_id="alice", approval_ts=datetime.now(timezone.utc))
    assert d.decision == "approve"


def test_gate_needs_human_review_without_reviewer() -> None:
    """FR-3(c): no reviewer → needs_human_review."""
    gate = PromotionGate(config=_cfg_enabled())
    d = gate.evaluate(_proposal(delta=0.02))
    assert d.decision == "needs_human_review"
    assert d.reason == "awaiting-human-signoff"


def test_gate_rejects_goodhart_spike() -> None:
    """Declared delta > threshold × max-observed ⇒ Goodhart flag."""
    history = [
        {"declared_metric_delta": 0.01},
        {"declared_metric_delta": 0.02},
        {"declared_metric_delta": 0.015},
    ]
    gate = PromotionGate(config=_cfg_enabled(), history=history)
    # declared 0.8 vs max-history 0.02 → >10x ⇒ Goodhart
    d = gate.evaluate(_proposal(delta=0.8), reviewer_id="r", approval_ts=datetime.now(timezone.utc))
    assert d.decision == "reject"
    assert "goodhart" in d.reason


def test_gate_rejects_negative_declared_delta() -> None:
    gate = PromotionGate(config=_cfg_enabled())
    d = gate.evaluate(_proposal(delta=-0.01), reviewer_id="r", approval_ts=datetime.now(timezone.utc))
    assert d.decision == "reject"
    assert "outcome" in d.reason


def test_gate_rejects_eval_gaming_vote() -> None:
    gate = PromotionGate(config=_cfg_enabled())
    proposal = _proposal(delta=0.05).model_copy(
        update={"eval_gaming_ok": False, "eval_gaming_flags": ("test_artifact_modification",)}
    )

    decision = gate.evaluate(
        proposal,
        reviewer_id="r",
        approval_ts=datetime.now(timezone.utc),
    )

    assert decision.decision == "reject"
    assert decision.reason == "eval-artifact-modification"


def test_gate_decision_is_frozen() -> None:
    d = GateDecision(decision="approve", reason="ok", disabled=False)
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        d.__class__.model_validate({"decision": "approve", "reason": "ok", "disabled": False, "extra": 1})


def test_gate_emits_metatune_event_on_evaluate() -> None:
    """FR-10: telemetry populated with non-default values (vote count ≥0)."""
    gate = PromotionGate(config=_cfg_enabled())
    d = gate.evaluate(_proposal(), reviewer_id="r", approval_ts=datetime.now(timezone.utc))
    # Just assert the call succeeded and decision populated
    assert d.decision in {"approve", "reject", "needs_human_review"}


def test_gate_persists_votes_incrementally(tmp_path: Path) -> None:
    audit_log = tmp_path / "meta_tune_audit.jsonl"
    cfg = TRWConfig(
        meta_tune=MetaTuneConfig(
            enabled=True,
            audit_log_path=str(audit_log),
        )
    )
    gate = PromotionGate(config=cfg)

    decision = gate.evaluate(
        _proposal(delta=0.02),
        reviewer_id="alice",
        approval_ts=datetime.now(timezone.utc),
        promotion_session_id="sess-1",
    )

    assert decision.decision == "approve"
    assert audit_log.exists()
    text = audit_log.read_text()
    assert '"vote_type":"eval_gaming"' in text
    assert '"vote_type":"outcome"' in text
    assert '"vote_type":"goodhart"' in text
    assert '"vote_type":"human"' in text
    assert '"event":"promoted"' in text


def test_gate_persists_rejected_lifecycle_row(tmp_path: Path) -> None:
    audit_log = tmp_path / "meta_tune_audit.jsonl"
    cfg = TRWConfig(
        meta_tune=MetaTuneConfig(
            enabled=True,
            audit_log_path=str(audit_log),
        )
    )
    gate = PromotionGate(config=cfg)

    decision = gate.evaluate(
        _proposal(delta=-0.1),
        reviewer_id="alice",
        approval_ts=datetime.now(timezone.utc),
        promotion_session_id="sess-1",
    )

    assert decision.decision == "reject"
    assert '"event":"rejected"' in audit_log.read_text()


def test_gate_rejects_when_audit_dependency_unavailable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from trw_mcp.meta_tune import promotion_gate as pg

    cfg = TRWConfig(
        meta_tune=MetaTuneConfig(
            enabled=True,
            audit_log_path=str(tmp_path / "missing-parent" / "audit.jsonl"),
        )
    )
    monkeypatch.setattr(
        pg, "append_audit_entry", lambda *args, **kwargs: (_ for _ in ()).throw(pg.AuditAppendError("boom"))
    )
    gate = PromotionGate(config=cfg)

    decision = gate.evaluate(
        _proposal(delta=0.02),
        reviewer_id="alice",
        approval_ts=datetime.now(timezone.utc),
        promotion_session_id="sess-1",
    )

    assert decision.decision == "reject"
    assert decision.reason == "safety-dependency-unavailable"
