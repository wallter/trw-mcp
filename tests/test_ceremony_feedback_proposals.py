"""Proposal, escalation, and approval ceremony feedback tests."""

from __future__ import annotations

import pytest

from tests._ceremony_feedback_support import FeedbackEnv, record_sessions
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

from ._ceremony_feedback_support import feedback_env  # noqa: F401


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


# ---------------------------------------------------------------------------
# The unmeasured component in outcome_quality
# ---------------------------------------------------------------------------


def _sessions(count: int, *, unmeasured: float, quality: float) -> dict[str, object]:
    return {
        "task_classes": {
            "feature": {
                "sessions": [
                    {
                        "session_id": f"s{i}",
                        "ceremony_score": 95.0,
                        "outcome_quality": quality,
                        "unmeasured_quality_weight": unmeasured,
                        "current_tier": "STANDARD",
                        "task_class": "feature",
                    }
                    for i in range(count)
                ]
            }
        }
    }


def test_an_unmeasured_mutation_score_is_recorded_as_unmeasured(tmp_path: Path) -> None:
    """None means NOT MEASURED and must not masquerade as a passing check.

    The numeric contribution is deliberately preserved -- zeroing it would
    silently disable ceremony reduction, which is a product decision -- so the
    only thing separating a fabricated 0.2 from a real one is this field.
    """
    from trw_mcp.state.ceremony_feedback import record_session_outcome

    entry = record_session_outcome(
        trw_dir=tmp_path,
        task_name="add a feature",
        ceremony_score=95.0,
        build_passed=True,
        coverage_delta=0.1,
        critical_findings=0,
        mutation_score_ok=None,
        current_tier="STANDARD",
        run_path="",
        session_id="s1",
    )
    assert entry["outcome_quality"] == 1.0, "the numeric contribution must be unchanged"
    assert entry["unmeasured_quality_weight"] == 0.2, entry


def test_a_MEASURED_score_records_no_unmeasured_weight(tmp_path: Path) -> None:
    """Non-vacuity partner: an unconditional 0.2 would satisfy the test above
    while making the field meaningless. A real pass and a real failure must both
    report zero unmeasured weight -- they were measured."""
    from trw_mcp.state.ceremony_feedback import record_session_outcome

    for measured, expected_quality in ((True, 1.0), (False, 0.8)):
        entry = record_session_outcome(
            trw_dir=tmp_path,
            task_name="add a feature",
            ceremony_score=95.0,
            build_passed=True,
            coverage_delta=0.1,
            critical_findings=0,
            mutation_score_ok=measured,
            current_tier="STANDARD",
            run_path="",
            session_id="s1",
        )
        assert entry["unmeasured_quality_weight"] == 0.0, entry
        assert entry["outcome_quality"] == expected_quality, entry


def test_a_reduction_proposal_discloses_what_it_rests_on() -> None:
    """The human gate is the designed safeguard; it must not be fed a number
    whose provenance it cannot see.

    Reduction needs avg_quality > 0.9 while the MEASURED ceiling is 0.8, so an
    all-unmeasured average means the proposal exists only because of the part
    nobody computed. A human approving less ceremony rigor has to be told that.
    """
    from trw_mcp.state.ceremony_feedback import generate_reduction_proposal

    proposal = generate_reduction_proposal("feature", _sessions(10, unmeasured=0.2, quality=1.0))
    assert proposal is not None, "conditions were chosen to produce a proposal"
    assert proposal["avg_outcome_quality"] == 1.0
    assert proposal["avg_unmeasured_quality_weight"] == 0.2
    assert proposal["avg_outcome_quality_measured_only"] == 0.8
    assert proposal["rests_on_unmeasured_evidence"] is True


def test_a_FULLY_MEASURED_proposal_is_not_flagged() -> None:
    """Non-vacuity partner. A flag that is always True carries no information,
    and would train an approver to ignore it -- the worst outcome for a warning
    whose whole job is to be rare."""
    from trw_mcp.state.ceremony_feedback import generate_reduction_proposal

    proposal = generate_reduction_proposal("feature", _sessions(10, unmeasured=0.0, quality=1.0))
    assert proposal is not None
    assert proposal["avg_unmeasured_quality_weight"] == 0.0
    assert proposal["avg_outcome_quality_measured_only"] == 1.0
    assert proposal["rests_on_unmeasured_evidence"] is False
