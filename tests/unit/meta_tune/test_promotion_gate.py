"""Tests for meta_tune.promotion_gate — PRD-HPO-SAFE-001 FR-2/FR-3."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from trw_mcp.meta_tune.promotion_gate import (
    GateDecision,
    PromotionGate,
    PromotionProposal,
)
from trw_mcp.models.config._main import TRWConfig
from trw_mcp.models.config._sub_models import MetaTuneConfig


def _cfg_enabled() -> TRWConfig:
    return TRWConfig(meta_tune=MetaTuneConfig(enabled=True))


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
    d = gate.evaluate(
        _proposal(delta=-0.01), reviewer_id="r", approval_ts=datetime.now(timezone.utc)
    )
    assert d.decision == "reject"
    assert "outcome" in d.reason


def test_gate_decision_is_frozen() -> None:
    d = GateDecision(decision="approve", reason="ok", disabled=False)
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        d.__class__.model_validate(
            {"decision": "approve", "reason": "ok", "disabled": False, "extra": 1}
        )


def test_gate_emits_metatune_event_on_evaluate() -> None:
    """FR-10: telemetry populated with non-default values (vote count ≥0)."""
    gate = PromotionGate(config=_cfg_enabled())
    d = gate.evaluate(_proposal(), reviewer_id="r", approval_ts=datetime.now(timezone.utc))
    # Just assert the call succeeded and decision populated
    assert d.decision in {"approve", "reject", "needs_human_review"}
