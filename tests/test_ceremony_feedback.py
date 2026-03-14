"""Tests for Self-Improving Ceremony (PRD-CORE-069)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from trw_mcp.models.config import TRWConfig, _reset_config
from trw_mcp.state.ceremony_feedback import (
    TaskClass,
    _derive_agent_id,
    _pending_proposals,
    _sanitize_flag_path,
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
    sanitize_ceremony_feedback,
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


# ===========================================================================
# FIX-050-FR04: Real outcome quality extraction
# ===========================================================================

class TestOutcomeQualityExtraction:
    """FIX-050-FR04: outcome_quality reflects actual build results; no IEEE 754 artifacts."""

    def test_outcome_quality_no_ieee754_artifacts(
        self, feedback_env: tuple[Path, TRWConfig]
    ) -> None:
        """Default combination must not produce 0.6000000000000001."""
        trw_dir, _ = feedback_env
        entry = record_session_outcome(
            trw_dir, "feat: test", 80.0, True, 0.0, 0, True,
            "STANDARD", "/runs/test", "session-1",
        )
        val = float(str(entry["outcome_quality"]))
        # Must have at most 4 decimal places — no IEEE 754 noise.
        assert val == round(val, 4)
        # Specifically: build_passed + coverage_delta==0 (>=0) + no critical + mutation ok = 1.0
        assert val == 1.0

    def test_outcome_quality_build_failed_with_negative_coverage_is_low(
        self, feedback_env: tuple[Path, TRWConfig]
    ) -> None:
        """build_passed=False + negative coverage_delta yields outcome_quality < 0.6.

        Formula: 0.0 (build failed) + 0.0 (coverage < 0) + 0.2 (no findings) + 0.2 (mutation ok)
        = 0.4 which is < 0.6.
        """
        trw_dir, _ = feedback_env
        entry = record_session_outcome(
            trw_dir, "feat: test", 80.0, False, -1.0, 0, True,
            "STANDARD", "/runs/test", "session-fail",
        )
        assert float(str(entry["outcome_quality"])) < 0.6

    def test_outcome_quality_varies_across_sessions(
        self, feedback_env: tuple[Path, TRWConfig]
    ) -> None:
        """Different build outcomes must produce distinct outcome_quality values."""
        trw_dir, _ = feedback_env
        e1 = record_session_outcome(
            trw_dir, "feat: a", 80.0, True, 1.0, 0, True,
            "STANDARD", "/r/1", "s1",
        )
        e2 = record_session_outcome(
            trw_dir, "feat: b", 80.0, False, -1.0, 2, False,
            "STANDARD", "/r/2", "s2",
        )
        q1 = float(str(e1["outcome_quality"]))
        q2 = float(str(e2["outcome_quality"]))
        assert q1 != q2
        assert q1 > q2


# ===========================================================================
# FIX-050-FR05: agent_id derivation
# ===========================================================================

class TestDeriveAgentId:
    """FIX-050-FR05: _derive_agent_id uses priority chain: env > run_id > pid."""

    def test_env_var_takes_highest_priority(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TRW_AGENT_ID", "custom-agent")
        assert _derive_agent_id(run_id="run-abc") == "custom-agent"
        assert _derive_agent_id(run_id=None) == "custom-agent"

    def test_run_id_used_when_no_env_var(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("TRW_AGENT_ID", raising=False)
        result = _derive_agent_id(run_id="20260313T120000Z-abc123")
        assert result == "20260313T120000Z-abc123"

    def test_pid_fallback_when_no_env_or_run_id(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("TRW_AGENT_ID", raising=False)
        result = _derive_agent_id(run_id=None)
        assert result.startswith("pid-")
        assert result != "unknown"
        assert str(os.getpid()) in result

    def test_agent_id_not_unknown_with_run_id(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Core acceptance: agent_id must not be 'unknown' when run_id is available."""
        monkeypatch.delenv("TRW_AGENT_ID", raising=False)
        result = _derive_agent_id(run_id="session-xyz")
        assert result != "unknown"

    def test_apply_escalation_uses_derived_agent_id(
        self,
        feedback_env: tuple[Path, TRWConfig],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """apply_auto_escalation should log a non-unknown agent_id."""
        monkeypatch.delenv("TRW_AGENT_ID", raising=False)
        trw_dir, _ = feedback_env
        escalation: dict[str, object] = {
            "triggered": True,
            "new_tier": "COMPREHENSIVE",
            "from_tier": "STANDARD",
            "reason": "test",
        }
        apply_auto_escalation(trw_dir, "feature", escalation)
        history = read_ceremony_history(trw_dir)
        assert len(history) == 1
        # With no env var and no run_id, falls back to pid-N
        assert history[0]["agent_id"] != "unknown"
        assert str(history[0]["agent_id"]).startswith("pid-")


# ===========================================================================
# FIX-050-FR07: Sanitize test-polluted ceremony feedback
# ===========================================================================

class TestSanitizeCeremonyFeedback:
    """FIX-050-FR07: Remove test-polluted entries from ceremony-feedback.yaml."""

    def test_removes_tmp_pytest_entries(
        self, feedback_env: tuple[Path, TRWConfig]
    ) -> None:
        """Entries with /tmp/pytest paths must be removed."""
        trw_dir, _ = feedback_env
        # Write real + polluted entries
        record_session_outcome(
            trw_dir, "feat: real", 80.0, True, 0.0, 0, True,
            "STANDARD", "/home/user/project/.trw/runs/abc", "real-session",
        )
        record_session_outcome(
            trw_dir, "feat: polluted", 0.0, False, 0.0, 0, True,
            "STANDARD", "/tmp/pytest-of-root/test_0/.trw/runs/xyz", "test",
        )

        result = sanitize_ceremony_feedback(trw_dir)
        assert result.get("removed_count") == 1

        data = read_feedback_data(trw_dir)
        sessions = data["task_classes"]["feature"]["sessions"]
        assert isinstance(sessions, list)
        assert len(sessions) == 1
        assert "/tmp/" not in str(sessions[0].get("run_path", ""))

    def test_removes_known_test_session_ids(
        self, feedback_env: tuple[Path, TRWConfig]
    ) -> None:
        """Entries with session_id in test sentinel set must be removed."""
        trw_dir, _ = feedback_env
        record_session_outcome(
            trw_dir, "feat: real", 80.0, True, 0.0, 0, True,
            "STANDARD", "/real/path", "real-session-001",
        )
        record_session_outcome(
            trw_dir, "feat: gate", 0.0, False, 0.0, 0, True,
            "STANDARD", "/real/path2", "gate-test",
        )
        record_session_outcome(
            trw_dir, "feat: advisory", 0.0, False, 0.0, 0, True,
            "STANDARD", "/real/path3", "advisory-test",
        )

        result = sanitize_ceremony_feedback(trw_dir)
        assert result.get("removed_count") == 2

    def test_idempotent_via_flag_file(
        self, feedback_env: tuple[Path, TRWConfig]
    ) -> None:
        """Running sanitize twice should skip the second time."""
        trw_dir, _ = feedback_env
        record_session_outcome(
            trw_dir, "feat: polluted", 0.0, False, 0.0, 0, True,
            "STANDARD", "/tmp/pytest-of-root/test", "test",
        )
        result1 = sanitize_ceremony_feedback(trw_dir)
        assert result1.get("removed_count") == 1

        # Write another polluted entry after first run
        record_session_outcome(
            trw_dir, "feat: more-polluted", 0.0, False, 0.0, 0, True,
            "STANDARD", "/tmp/pytest-of-root/test2", "test",
        )
        result2 = sanitize_ceremony_feedback(trw_dir)
        assert result2.get("skipped") is True
        # Second polluted entry still present (sanitization was idempotent/skipped)

    def test_flag_file_written_to_context_dir(
        self, feedback_env: tuple[Path, TRWConfig]
    ) -> None:
        """Flag must be written to .trw/context/.sanitized_ceremony_v1."""
        trw_dir, _ = feedback_env
        sanitize_ceremony_feedback(trw_dir)
        flag = _sanitize_flag_path(trw_dir)
        assert flag.exists()

    def test_preserves_real_entries(
        self, feedback_env: tuple[Path, TRWConfig]
    ) -> None:
        """Real session entries (no /tmp/ or pytest paths) must survive sanitization."""
        trw_dir, _ = feedback_env
        record_session_outcome(
            trw_dir, "feat: real", 85.0, True, 1.0, 0, True,
            "STANDARD", "/home/user/myproject/.trw/runs/run-abc", "session-20260313",
        )
        sanitize_ceremony_feedback(trw_dir)
        data = read_feedback_data(trw_dir)
        sessions = data["task_classes"]["feature"]["sessions"]
        assert isinstance(sessions, list)
        assert len(sessions) == 1


# ===========================================================================
# FIX-051-FR03: De-escalation wiring
# ===========================================================================

class TestDeEscalationWiring:
    """FIX-051-FR03: Proposals persisted to disk; trw_ceremony_status reads them."""

    def test_ceremony_status_reads_disk_proposals(
        self, feedback_env: tuple[Path, TRWConfig]
    ) -> None:
        """Proposals written to ceremony-overrides.yaml should appear in get_ceremony_status."""
        trw_dir, _ = feedback_env
        from trw_mcp.state.ceremony_feedback import _overrides_path
        from trw_mcp.state.persistence import FileStateWriter

        # Simulate a proposal written by the deferred thread
        fake_proposal: dict[str, object] = {
            "proposal_id": "prop-disk001",
            "task_class": "feature",
            "from_tier": "COMPREHENSIVE",
            "to_tier": "STANDARD",
            "sample_count": 5,
            "avg_ceremony_score": 85.0,
            "avg_outcome_quality": 0.95,
            "generated_at": "2026-03-13T12:00:00Z",
            "status": "pending",
        }
        overrides: dict[str, object] = {
            "_pending_proposals": {"prop-disk001": fake_proposal}
        }
        FileStateWriter().write_yaml(_overrides_path(trw_dir), overrides)

        _pending_proposals.clear()  # Ensure nothing in memory

        status = get_ceremony_status(trw_dir, "feature")
        tc_list = status["task_classes"]
        assert isinstance(tc_list, list)
        proposals = tc_list[0]["proposals"]
        assert isinstance(proposals, list)
        # The disk proposal should be surfaced
        proposal_ids = [str(p.get("proposal_id")) for p in proposals]
        assert "prop-disk001" in proposal_ids

    def test_generate_reduction_proposal_with_good_scores(
        self, feedback_env: tuple[Path, TRWConfig]
    ) -> None:
        """5 sessions with score=85, quality=0.95 at COMPREHENSIVE should yield a proposal."""
        trw_dir, config = feedback_env
        for i in range(15):
            record_session_outcome(
                trw_dir, "feat: work", 85.0, True, 1.0, 0, True,
                "COMPREHENSIVE", f"/r/{i}", f"s-{i}",
            )
        data = read_feedback_data(trw_dir)
        proposal = generate_reduction_proposal("feature", data, config)
        assert proposal is not None
        assert proposal["from_tier"] == "COMPREHENSIVE"
        assert proposal["to_tier"] == "STANDARD"


# ===========================================================================
# FIX-051-FR06: Task description pass-through
# ===========================================================================

class TestTaskDescriptionPassThrough:
    """FIX-051-FR06: classify_task_class uses both task_name and task_description."""

    def test_classify_with_description_only_matches(self) -> None:
        """Classification should use description keywords when task name is generic."""
        # task name alone doesn't match anything → documentation
        assert classify_task_class("auth-v2") == TaskClass.DOCUMENTATION
        # With description, security keyword matches
        result = classify_task_class("auth-v2", task_description="implement security authentication")
        assert result == TaskClass.SECURITY

    def test_classify_task_name_still_works_without_description(self) -> None:
        """Existing keyword matching via task name must not regress."""
        assert classify_task_class("refactor-utils") == TaskClass.REFACTOR
        assert classify_task_class("feat-add-login") == TaskClass.FEATURE

    def test_classify_task_name_overridden_by_higher_priority_in_description(
        self,
    ) -> None:
        """SECURITY has higher priority than FEATURE — description keyword wins."""
        result = classify_task_class(
            "add-new-feature",
            task_description="fix XSS vulnerability in user input",
        )
        assert result == TaskClass.SECURITY

    def test_classify_empty_description_preserves_task_name_result(self) -> None:
        """Empty task_description must not change classification behavior."""
        assert classify_task_class("feat-login", task_description="") == TaskClass.FEATURE
        assert classify_task_class("feat-login", task_description=None) == TaskClass.FEATURE

    # P2-006: FR06 objective pass-through classification
    def test_auth_v2_with_security_objective_classifies_security(self) -> None:
        """P2-006: task='auth-v2' + objective='implement security authentication' → SECURITY.

        This is the exact scenario from the PRD: a generic task name that only
        resolves to SECURITY when the objective/description is considered.
        """
        result = classify_task_class(
            "auth-v2",
            task_description="implement security authentication",
        )
        assert result == TaskClass.SECURITY, (
            f"task='auth-v2' with security-keyed description must return SECURITY, got {result}"
        )

    def test_auth_v2_without_description_is_documentation(self) -> None:
        """P2-006 negative: task='auth-v2' alone → DOCUMENTATION (no description keywords)."""
        result = classify_task_class("auth-v2")
        assert result == TaskClass.DOCUMENTATION, (
            f"task='auth-v2' alone must default to DOCUMENTATION (generic name), got {result}"
        )

    def test_description_infrastructure_keyword_overrides_generic_task(self) -> None:
        """Infrastructure keyword in description overrides generic task name."""
        result = classify_task_class(
            "task-001",
            task_description="deploy to production kubernetes cluster",
        )
        assert result == TaskClass.INFRASTRUCTURE

    def test_description_refactor_keyword_used_when_task_generic(self) -> None:
        """Refactor keyword in description classifies as REFACTOR."""
        result = classify_task_class(
            "sprint-42",
            task_description="refactor authentication module for clarity",
        )
        assert result == TaskClass.REFACTOR
