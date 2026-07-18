"""PRD-CORE-205 FR07 — typed GateDecision dispatch and stable projection."""

from __future__ import annotations

from trw_mcp.models.gate_decision import (
    PUBLIC_GATE_KEYS,
    DeliveryDecisionSet,
    GateDecision,
    GateOverridePolicy,
    GateStatus,
    gate_decision_enabled,
)


def _decision(gate_id: str, policy: GateOverridePolicy, status: GateStatus, message: str = "") -> GateDecision:
    return GateDecision(
        decision_id=f"d-{gate_id}",
        gate_id=gate_id,
        status=status,
        override_policy=policy,
        reason_code=gate_id,
        message=message,
    )


class TestGateDecisionPreservesDispatchPrecedenceAndProjection:
    def test_gate_decision_is_enabled_by_v26_1_compatibility_closure(self) -> None:
        assert gate_decision_enabled() is True
        assert gate_decision_enabled(closure_record_present=False) is False
        assert gate_decision_enabled(closure_record_present=True) is True

    def test_no_escape_wins_over_structured_and_advisory(self) -> None:
        decisions = DeliveryDecisionSet(
            decisions=(
                _decision("build_gate_warning", GateOverridePolicy.ADVISORY, GateStatus.WARN, "warn"),
                _decision("delivery_blocked", GateOverridePolicy.STRUCTURED, GateStatus.BLOCK, "blocked"),
                _decision("review_scope_block", GateOverridePolicy.NO_ESCAPE, GateStatus.BLOCK, "scope"),
                _decision("review_block", GateOverridePolicy.STRUCTURED, GateStatus.BLOCK, "review"),
            )
        )
        controlling = decisions.controlling()
        assert controlling is not None
        assert controlling.gate_id == "review_scope_block"
        assert controlling.override_policy is GateOverridePolicy.NO_ESCAPE

    def test_structured_wins_over_advisory(self) -> None:
        decisions = DeliveryDecisionSet(
            decisions=(
                _decision("build_gate_warning", GateOverridePolicy.ADVISORY, GateStatus.WARN, "warn"),
                _decision("delivery_blocked", GateOverridePolicy.STRUCTURED, GateStatus.BLOCK, "blocked"),
            )
        )
        controlling = decisions.controlling()
        assert controlling is not None and controlling.gate_id == "delivery_blocked"

    def test_advisory_only_is_never_controlling(self) -> None:
        decisions = DeliveryDecisionSet(
            decisions=(_decision("build_gate_warning", GateOverridePolicy.ADVISORY, GateStatus.WARN, "warn"),)
        )
        assert decisions.controlling() is None

    def test_projection_reproduces_public_keys(self) -> None:
        block = GateDecision(
            decision_id="d1",
            gate_id="delivery_blocked",
            status=GateStatus.BLOCK,
            override_policy=GateOverridePolicy.STRUCTURED,
            reason_code="missing_build",
            message="Delivery blocked: no passing build check",
            task_type="coding",
            missing_evidence=("build_check",),
        )
        scope = _decision("review_scope_block", GateOverridePolicy.NO_ESCAPE, GateStatus.BLOCK, ">5 files no review")
        projection = DeliveryDecisionSet(decisions=(block, scope)).project_public_keys()
        assert projection["delivery_blocked"] == "Delivery blocked: no passing build check"
        assert projection["review_scope_block"] == ">5 files no review"
        assert projection["missing_gate"] == "build_check"
        assert projection["blocked_task_type"] == "coding"
        # Every projected key is a recognized public key.
        assert set(projection).issubset(set(PUBLIC_GATE_KEYS))
