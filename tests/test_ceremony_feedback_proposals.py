"""Proposal, escalation, and approval ceremony feedback tests."""

from __future__ import annotations

import pytest

from trw_mcp.state.ceremony_feedback import (
    apply_auto_escalation,
    approve_proposal,
    check_auto_escalation,
    generate_reduction_proposal,
    read_ceremony_history,
    read_feedback_data,
    read_overrides,
    register_proposal,
    revert_change,
)

from tests._ceremony_feedback_support import FeedbackEnv, feedback_env, record_sessions


class TestReductionProposal:
    """FR04: Ceremony Reduction Proposal Generator."""

    def test_proposal_generated(self, feedback_env: FeedbackEnv) -> None:
        trw_dir, config = feedback_env
        record_sessions(trw_dir, [85.0] * 15)
        data = read_feedback_data(trw_dir)
        proposal = generate_reduction_proposal("feature", data, config)
        assert proposal is not None
        assert proposal["from_tier"] == "STANDARD"
        assert proposal["to_tier"] == "MINIMAL"
        assert proposal["sample_count"] == 10

    def test_no_proposal_low_score(self, feedback_env: FeedbackEnv) -> None:
        trw_dir, config = feedback_env
        record_sessions(trw_dir, [75.0] * 15)
        data = read_feedback_data(trw_dir)
        assert generate_reduction_proposal("feature", data, config) is None

    def test_no_proposal_low_quality(self, feedback_env: FeedbackEnv) -> None:
        trw_dir, config = feedback_env
        record_sessions(trw_dir, [85.0] * 15, build_passed=False, coverage_delta=-1.0)
        data = read_feedback_data(trw_dir)
        assert generate_reduction_proposal("feature", data, config) is None

    def test_no_proposal_already_minimal(self, feedback_env: FeedbackEnv) -> None:
        trw_dir, config = feedback_env
        record_sessions(trw_dir, [95.0] * 15, ceremony_tier="MINIMAL")
        data = read_feedback_data(trw_dir)
        assert generate_reduction_proposal("feature", data, config) is None


class TestAutoEscalation:
    """FR05: Auto-Escalation."""

    def test_escalation_triggered(self, feedback_env: FeedbackEnv) -> None:
        trw_dir, config = feedback_env
        record_sessions(trw_dir, [55, 58, 52, 59, 48])
        data = read_feedback_data(trw_dir)
        result = check_auto_escalation("feature", data, config)
        assert result is not None
        assert result["triggered"] is True
        assert result["new_tier"] == "COMPREHENSIVE"

    def test_no_escalation_one_above(self, feedback_env: FeedbackEnv) -> None:
        trw_dir, config = feedback_env
        record_sessions(trw_dir, [55, 63, 52, 58, 48])
        data = read_feedback_data(trw_dir)
        assert check_auto_escalation("feature", data, config) is None

    def test_apply_escalation(self, feedback_env: FeedbackEnv) -> None:
        trw_dir, _ = feedback_env
        escalation: dict[str, object] = {
            "triggered": True,
            "new_tier": "COMPREHENSIVE",
            "from_tier": "STANDARD",
            "reason": "test",
        }
        apply_auto_escalation(trw_dir, "feature", escalation)
        overrides = read_overrides(trw_dir)
        assert overrides.get("feature") == "COMPREHENSIVE"
        history = read_ceremony_history(trw_dir)
        assert len(history) == 1
        assert history[0]["triggered_by"] == "auto_escalation"


class TestHumanApproval:
    """FR06: Human Approval Gate."""

    def test_approve_proposal(self, feedback_env: FeedbackEnv) -> None:
        trw_dir, config = feedback_env
        record_sessions(trw_dir, [85.0] * 15)
        data = read_feedback_data(trw_dir)
        proposal = generate_reduction_proposal("feature", data, config)
        assert proposal is not None
        register_proposal(proposal)
        result = approve_proposal(trw_dir, str(proposal["proposal_id"]))
        assert result["status"] == "approved"
        overrides = read_overrides(trw_dir)
        assert overrides.get("feature") == "MINIMAL"

    def test_approve_invalid_proposal(self, feedback_env: FeedbackEnv) -> None:
        trw_dir, _ = feedback_env
        with pytest.raises(ValueError, match="No pending proposal"):
            approve_proposal(trw_dir, "nonexistent")

    def test_revert_change(self, feedback_env: FeedbackEnv) -> None:
        trw_dir, config = feedback_env
        record_sessions(trw_dir, [85.0] * 15)
        data = read_feedback_data(trw_dir)
        proposal = generate_reduction_proposal("feature", data, config)
        assert proposal is not None
        register_proposal(proposal)
        approval = approve_proposal(trw_dir, str(proposal["proposal_id"]))
        result = revert_change(trw_dir, str(approval["change_id"]))
        assert result["status"] == "reverted"
        assert result["restored_tier"] == "STANDARD"
