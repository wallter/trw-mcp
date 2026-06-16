"""Sanitization, agent identity, and classification ceremony feedback tests."""

from __future__ import annotations

import os

import pytest

from tests._ceremony_feedback_support import FeedbackEnv
from tests._ceremony_feedback_support import feedback_env as feedback_env
from trw_mcp.state.ceremony_feedback import (
    TaskClass,
    _derive_agent_id,
    _sanitize_flag_path,
    apply_auto_escalation,
    classify_task_class,
    read_ceremony_history,
    read_feedback_data,
    record_session_outcome,
    sanitize_ceremony_feedback,
)


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

    def test_pid_fallback_when_no_env_or_run_id(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("TRW_AGENT_ID", raising=False)
        result = _derive_agent_id(run_id=None)
        assert result.startswith("pid-")
        assert result != "unknown"
        assert str(os.getpid()) in result

    def test_agent_id_not_unknown_with_run_id(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Core acceptance: agent_id must not be 'unknown' when run_id is available."""
        monkeypatch.delenv("TRW_AGENT_ID", raising=False)
        result = _derive_agent_id(run_id="session-xyz")
        assert result != "unknown"

    def test_apply_escalation_uses_derived_agent_id(
        self,
        feedback_env: FeedbackEnv,
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
        assert history[0]["agent_id"] != "unknown"
        assert str(history[0]["agent_id"]).startswith("pid-")


class TestSanitizeCeremonyFeedback:
    """FIX-050-FR07: Remove test-polluted entries from ceremony-feedback.yaml."""

    def test_removes_tmp_pytest_entries(self, feedback_env: FeedbackEnv) -> None:
        """Entries with /tmp/pytest paths must be removed."""
        trw_dir, _ = feedback_env
        record_session_outcome(
            trw_dir,
            "feat: real",
            80.0,
            True,
            0.0,
            0,
            True,
            "STANDARD",
            "/home/user/project/.trw/runs/abc",
            "real-session",
        )
        record_session_outcome(
            trw_dir,
            "feat: polluted",
            0.0,
            False,
            0.0,
            0,
            True,
            "STANDARD",
            "/tmp/pytest-of-root/test_0/.trw/runs/xyz",
            "test",
        )

        result = sanitize_ceremony_feedback(trw_dir)
        assert result.get("removed_count") == 1

        data = read_feedback_data(trw_dir)
        sessions = data["task_classes"]["feature"]["sessions"]
        assert isinstance(sessions, list)
        assert len(sessions) == 1
        assert "/tmp/" not in str(sessions[0].get("run_path", ""))

    def test_removes_known_test_session_ids(self, feedback_env: FeedbackEnv) -> None:
        """Entries with session_id in test sentinel set must be removed."""
        trw_dir, _ = feedback_env
        record_session_outcome(
            trw_dir,
            "feat: real",
            80.0,
            True,
            0.0,
            0,
            True,
            "STANDARD",
            "/real/path",
            "real-session-001",
        )
        record_session_outcome(
            trw_dir,
            "feat: gate",
            0.0,
            False,
            0.0,
            0,
            True,
            "STANDARD",
            "/real/path2",
            "gate-test",
        )
        record_session_outcome(
            trw_dir,
            "feat: advisory",
            0.0,
            False,
            0.0,
            0,
            True,
            "STANDARD",
            "/real/path3",
            "advisory-test",
        )

        result = sanitize_ceremony_feedback(trw_dir)
        assert result.get("removed_count") == 2

    def test_idempotent_via_flag_file(self, feedback_env: FeedbackEnv) -> None:
        """Running sanitize twice should skip the second time."""
        trw_dir, _ = feedback_env
        record_session_outcome(
            trw_dir,
            "feat: polluted",
            0.0,
            False,
            0.0,
            0,
            True,
            "STANDARD",
            "/tmp/pytest-of-root/test",
            "test",
        )
        result1 = sanitize_ceremony_feedback(trw_dir)
        assert result1.get("removed_count") == 1

        record_session_outcome(
            trw_dir,
            "feat: more-polluted",
            0.0,
            False,
            0.0,
            0,
            True,
            "STANDARD",
            "/tmp/pytest-of-root/test2",
            "test",
        )
        result2 = sanitize_ceremony_feedback(trw_dir)
        assert result2.get("skipped") is True

    def test_flag_file_written_to_context_dir(self, feedback_env: FeedbackEnv) -> None:
        """Flag must be written to .trw/context/.sanitized_ceremony_v1."""
        trw_dir, _ = feedback_env
        sanitize_ceremony_feedback(trw_dir)
        flag = _sanitize_flag_path(trw_dir)
        assert flag.exists()

    def test_preserves_real_entries(self, feedback_env: FeedbackEnv) -> None:
        """Real session entries (no /tmp/ or pytest paths) must survive sanitization."""
        trw_dir, _ = feedback_env
        record_session_outcome(
            trw_dir,
            "feat: real",
            85.0,
            True,
            1.0,
            0,
            True,
            "STANDARD",
            "/home/user/myproject/.trw/runs/run-abc",
            "session-20260313",
        )
        sanitize_ceremony_feedback(trw_dir)
        data = read_feedback_data(trw_dir)
        sessions = data["task_classes"]["feature"]["sessions"]
        assert isinstance(sessions, list)
        assert len(sessions) == 1


class TestCeremonyFeedbackPersistence:
    """PRD-QUAL-085: ceremony feedback YAML should not churn trailing whitespace."""

    def test_record_session_outcome_rewrites_blank_run_path_without_trailing_whitespace(
        self,
        feedback_env: FeedbackEnv,
    ) -> None:
        trw_dir, _ = feedback_env
        feedback_path = trw_dir / "context" / "ceremony-feedback.yaml"
        feedback_path.parent.mkdir(parents=True, exist_ok=True)
        feedback_path.write_text(
            "task_classes:\n"
            "  feature:\n"
            "    sessions:\n"
            "    - session_id: legacy-session\n"
            "      run_path: \n"
            "      ceremony_score: 80.0\n"
            "      outcome_quality: 1.0\n"
            "      current_tier: STANDARD\n"
            "      task_name: 'feat: legacy'\n"
            "      task_class: feature\n"
            "      completed_at: '2026-05-20T00:00:00+00:00'\n",
            encoding="utf-8",
        )

        record_session_outcome(
            trw_dir,
            "feat: next",
            90.0,
            True,
            0.0,
            0,
            True,
            "STANDARD",
            "",
            "next-session",
        )

        raw_text = feedback_path.read_text(encoding="utf-8")
        trailing_lines = [
            line_number for line_number, line in enumerate(raw_text.splitlines(), start=1) if line.rstrip(" \t") != line
        ]
        assert trailing_lines == []

        data = read_feedback_data(trw_dir)
        sessions = data["task_classes"]["feature"]["sessions"]
        assert isinstance(sessions, list)
        assert sessions[0]["run_path"] == ""


class TestTaskDescriptionPassThrough:
    """FIX-051-FR06: classify_task_class uses both task_name and task_description."""

    def test_classify_with_description_only_matches(self) -> None:
        """Classification should use description keywords when task name is generic."""
        assert classify_task_class("auth-v2") == TaskClass.DOCUMENTATION
        result = classify_task_class("auth-v2", task_description="implement security authentication")
        assert result == TaskClass.SECURITY

    def test_classify_task_name_still_works_without_description(self) -> None:
        """Existing keyword matching via task name must not regress."""
        assert classify_task_class("refactor-utils") == TaskClass.REFACTOR
        assert classify_task_class("feat-add-login") == TaskClass.FEATURE

    def test_classify_task_name_overridden_by_higher_priority_in_description(self) -> None:
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
