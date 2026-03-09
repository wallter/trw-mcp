"""Tests for Self-Improving Ceremony (PRD-CORE-069)."""

from __future__ import annotations

from pathlib import Path

import pytest

from trw_mcp.models.config import TRWConfig, _reset_config
from trw_mcp.state.ceremony_feedback import (
    TaskClass,
    _pending_proposals,
    approve_proposal,
    apply_auto_escalation,
    check_auto_escalation,
    classify_task_class,
    generate_reduction_proposal,
    get_ceremony_status,
    has_sufficient_samples,
    read_ceremony_history,
    read_feedback_data,
    read_overrides,
    record_session_outcome,
    register_proposal,
    revert_change,
)


@pytest.fixture()
def feedback_env(tmp_path: Path):
    """Set up a .trw directory for ceremony feedback."""
    trw_dir = tmp_path / ".trw"
    trw_dir.mkdir()
    (trw_dir / "context").mkdir(parents=True)
    (trw_dir / "logs").mkdir(parents=True)

    config = TRWConfig()
    _reset_config(config)
    _pending_proposals.clear()

    yield trw_dir, config
    _reset_config()
    _pending_proposals.clear()


class TestTaskClassifier:
    """FR01: Task Class Classifier."""

    def test_feature_with_oauth(self) -> None:
        assert classify_task_class("feat: add OAuth login") == TaskClass.FEATURE

    def test_documentation_catch_all(self) -> None:
        assert classify_task_class("update README and docstrings") == TaskClass.DOCUMENTATION

    def test_security_bypass(self) -> None:
        assert classify_task_class("fix auth bypass vulnerability") == TaskClass.SECURITY

    def test_refactor_auth(self) -> None:
        assert classify_task_class("refactor auth module") == TaskClass.REFACTOR

    def test_security_xss(self) -> None:
        assert classify_task_class("patch XSS in user input") == TaskClass.SECURITY

    def test_empty_is_documentation(self) -> None:
        assert classify_task_class("") == TaskClass.DOCUMENTATION

    def test_infrastructure(self) -> None:
        assert classify_task_class("deploy to production") == TaskClass.INFRASTRUCTURE


class TestQualityOutcomeTracker:
    """FR02: Quality Outcome Tracker."""

    def test_record_outcome_full_quality(self, feedback_env: tuple[Path, TRWConfig]) -> None:
        trw_dir, _ = feedback_env
        entry = record_session_outcome(
            trw_dir, "feat: add login", 85.0, True, 2.0, 0, True,
            "STANDARD", "/runs/test", "session-1",
        )
        assert entry["outcome_quality"] == 1.0
        assert entry["task_class"] == "feature"

    def test_record_outcome_low_quality(self, feedback_env: tuple[Path, TRWConfig]) -> None:
        trw_dir, _ = feedback_env
        entry = record_session_outcome(
            trw_dir, "feat: add login", 50.0, False, -1.0, 2, False,
            "STANDARD", "/runs/test", "session-1",
        )
        assert entry["outcome_quality"] == 0.0

    def test_prune_to_50(self, feedback_env: tuple[Path, TRWConfig]) -> None:
        trw_dir, _ = feedback_env
        for i in range(55):
            record_session_outcome(
                trw_dir, "feat: work", 80.0, True, 1.0, 0, True,
                "STANDARD", f"/runs/{i}", f"s-{i}",
            )
        data = read_feedback_data(trw_dir)
        tc = data["task_classes"]
        assert isinstance(tc, dict)
        sessions = tc["feature"]["sessions"]
        assert len(sessions) == 50


class TestStatisticalSignificance:
    """FR03: Statistical Significance Calculator."""

    def test_sufficient_samples(self, feedback_env: tuple[Path, TRWConfig]) -> None:
        trw_dir, config = feedback_env
        for i in range(10):
            record_session_outcome(
                trw_dir, "feat: work", 85.0, True, 1.0, 0, True,
                "STANDARD", f"/r/{i}", f"s-{i}",
            )
        data = read_feedback_data(trw_dir)
        assert has_sufficient_samples("feature", data, config) is True

    def test_insufficient_samples(self, feedback_env: tuple[Path, TRWConfig]) -> None:
        trw_dir, config = feedback_env
        for i in range(9):
            record_session_outcome(
                trw_dir, "feat: work", 85.0, True, 1.0, 0, True,
                "STANDARD", f"/r/{i}", f"s-{i}",
            )
        data = read_feedback_data(trw_dir)
        assert has_sufficient_samples("feature", data, config) is False

    def test_custom_min_samples(self, feedback_env: tuple[Path, TRWConfig]) -> None:
        trw_dir, _ = feedback_env
        custom = TRWConfig(ceremony_feedback_min_samples=5)
        for i in range(5):
            record_session_outcome(
                trw_dir, "feat: work", 85.0, True, 1.0, 0, True,
                "STANDARD", f"/r/{i}", f"s-{i}",
            )
        data = read_feedback_data(trw_dir)
        assert has_sufficient_samples("feature", data, custom) is True


class TestReductionProposal:
    """FR04: Ceremony Reduction Proposal Generator."""

    def test_proposal_generated(self, feedback_env: tuple[Path, TRWConfig]) -> None:
        trw_dir, config = feedback_env
        for i in range(15):
            record_session_outcome(
                trw_dir, "feat: work", 85.0, True, 1.0, 0, True,
                "STANDARD", f"/r/{i}", f"s-{i}",
            )
        data = read_feedback_data(trw_dir)
        proposal = generate_reduction_proposal("feature", data, config)
        assert proposal is not None
        assert proposal["from_tier"] == "STANDARD"
        assert proposal["to_tier"] == "MINIMAL"
        assert proposal["sample_count"] == 10

    def test_no_proposal_low_score(self, feedback_env: tuple[Path, TRWConfig]) -> None:
        trw_dir, config = feedback_env
        for i in range(15):
            record_session_outcome(
                trw_dir, "feat: work", 75.0, True, 1.0, 0, True,
                "STANDARD", f"/r/{i}", f"s-{i}",
            )
        data = read_feedback_data(trw_dir)
        assert generate_reduction_proposal("feature", data, config) is None

    def test_no_proposal_low_quality(self, feedback_env: tuple[Path, TRWConfig]) -> None:
        trw_dir, config = feedback_env
        for i in range(15):
            record_session_outcome(
                trw_dir, "feat: work", 85.0, False, -1.0, 0, True,
                "STANDARD", f"/r/{i}", f"s-{i}",
            )
        data = read_feedback_data(trw_dir)
        assert generate_reduction_proposal("feature", data, config) is None

    def test_no_proposal_already_minimal(self, feedback_env: tuple[Path, TRWConfig]) -> None:
        trw_dir, config = feedback_env
        for i in range(15):
            record_session_outcome(
                trw_dir, "feat: work", 95.0, True, 1.0, 0, True,
                "MINIMAL", f"/r/{i}", f"s-{i}",
            )
        data = read_feedback_data(trw_dir)
        assert generate_reduction_proposal("feature", data, config) is None


class TestAutoEscalation:
    """FR05: Auto-Escalation."""

    def test_escalation_triggered(self, feedback_env: tuple[Path, TRWConfig]) -> None:
        trw_dir, config = feedback_env
        scores = [55, 58, 52, 59, 48]
        for i, score in enumerate(scores):
            record_session_outcome(
                trw_dir, "feat: work", float(score), True, 1.0, 0, True,
                "STANDARD", f"/r/{i}", f"s-{i}",
            )
        data = read_feedback_data(trw_dir)
        result = check_auto_escalation("feature", data, config)
        assert result is not None
        assert result["triggered"] is True
        assert result["new_tier"] == "COMPREHENSIVE"

    def test_no_escalation_one_above(self, feedback_env: tuple[Path, TRWConfig]) -> None:
        trw_dir, config = feedback_env
        scores = [55, 63, 52, 58, 48]
        for i, score in enumerate(scores):
            record_session_outcome(
                trw_dir, "feat: work", float(score), True, 1.0, 0, True,
                "STANDARD", f"/r/{i}", f"s-{i}",
            )
        data = read_feedback_data(trw_dir)
        assert check_auto_escalation("feature", data, config) is None

    def test_apply_escalation(self, feedback_env: tuple[Path, TRWConfig]) -> None:
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

    def test_approve_proposal(self, feedback_env: tuple[Path, TRWConfig]) -> None:
        trw_dir, config = feedback_env
        for i in range(15):
            record_session_outcome(
                trw_dir, "feat: work", 85.0, True, 1.0, 0, True,
                "STANDARD", f"/r/{i}", f"s-{i}",
            )
        data = read_feedback_data(trw_dir)
        proposal = generate_reduction_proposal("feature", data, config)
        assert proposal is not None
        register_proposal(proposal)
        result = approve_proposal(trw_dir, str(proposal["proposal_id"]))
        assert result["status"] == "approved"
        overrides = read_overrides(trw_dir)
        assert overrides.get("feature") == "MINIMAL"

    def test_approve_invalid_proposal(self, feedback_env: tuple[Path, TRWConfig]) -> None:
        trw_dir, _ = feedback_env
        with pytest.raises(ValueError, match="No pending proposal"):
            approve_proposal(trw_dir, "nonexistent")

    def test_revert_change(self, feedback_env: tuple[Path, TRWConfig]) -> None:
        trw_dir, config = feedback_env
        for i in range(15):
            record_session_outcome(
                trw_dir, "feat: work", 85.0, True, 1.0, 0, True,
                "STANDARD", f"/r/{i}", f"s-{i}",
            )
        data = read_feedback_data(trw_dir)
        proposal = generate_reduction_proposal("feature", data, config)
        assert proposal is not None
        register_proposal(proposal)
        approval = approve_proposal(trw_dir, str(proposal["proposal_id"]))
        result = revert_change(trw_dir, str(approval["change_id"]))
        assert result["status"] == "reverted"
        assert result["restored_tier"] == "STANDARD"


class TestCeremonyStatus:
    """FR08: trw_ceremony_status."""

    def test_status_single_class(self, feedback_env: tuple[Path, TRWConfig]) -> None:
        trw_dir, _ = feedback_env
        for i in range(5):
            record_session_outcome(
                trw_dir, "feat: work", 80.0, True, 1.0, 0, True,
                "STANDARD", f"/r/{i}", f"s-{i}",
            )
        result = get_ceremony_status(trw_dir, "feature")
        assert len(result["task_classes"]) == 1
        tc_list = result["task_classes"]
        assert isinstance(tc_list, list)
        assert tc_list[0]["session_count"] == 5

    def test_status_all_classes(self, feedback_env: tuple[Path, TRWConfig]) -> None:
        trw_dir, _ = feedback_env
        result = get_ceremony_status(trw_dir)
        assert len(result["task_classes"]) == 5

    def test_status_invalid_class(self, feedback_env: tuple[Path, TRWConfig]) -> None:
        trw_dir, _ = feedback_env
        with pytest.raises(ValueError, match="Invalid task_class"):
            get_ceremony_status(trw_dir, "unknown")
