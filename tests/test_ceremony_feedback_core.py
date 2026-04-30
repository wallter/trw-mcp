"""Core ceremony feedback tests."""

from __future__ import annotations

from trw_mcp.models.config import TRWConfig
from trw_mcp.state.ceremony_feedback import (
    TaskClass,
    classify_task_class,
    has_sufficient_samples,
    read_feedback_data,
    record_session_outcome,
)

from tests._ceremony_feedback_support import FeedbackEnv, feedback_env, record_sessions


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

    def test_record_outcome_full_quality(self, feedback_env: FeedbackEnv) -> None:
        trw_dir, _ = feedback_env
        entry = record_session_outcome(
            trw_dir,
            "feat: add login",
            85.0,
            True,
            2.0,
            0,
            True,
            "STANDARD",
            "/runs/test",
            "session-1",
        )
        assert entry["outcome_quality"] == 1.0
        assert entry["task_class"] == "feature"

    def test_record_outcome_low_quality(self, feedback_env: FeedbackEnv) -> None:
        trw_dir, _ = feedback_env
        entry = record_session_outcome(
            trw_dir,
            "feat: add login",
            50.0,
            False,
            -1.0,
            2,
            False,
            "STANDARD",
            "/runs/test",
            "session-1",
        )
        assert entry["outcome_quality"] == 0.0

    def test_prune_to_50(self, feedback_env: FeedbackEnv) -> None:
        trw_dir, _ = feedback_env
        record_sessions(trw_dir, [80.0] * 55, run_prefix="/runs")
        data = read_feedback_data(trw_dir)
        tc = data["task_classes"]
        assert isinstance(tc, dict)
        sessions = tc["feature"]["sessions"]
        assert len(sessions) == 50


class TestStatisticalSignificance:
    """FR03: Statistical Significance Calculator."""

    def test_sufficient_samples(self, feedback_env: FeedbackEnv) -> None:
        trw_dir, config = feedback_env
        record_sessions(trw_dir, [85.0] * 10)
        data = read_feedback_data(trw_dir)
        assert has_sufficient_samples("feature", data, config) is True

    def test_insufficient_samples(self, feedback_env: FeedbackEnv) -> None:
        trw_dir, config = feedback_env
        record_sessions(trw_dir, [85.0] * 9)
        data = read_feedback_data(trw_dir)
        assert has_sufficient_samples("feature", data, config) is False

    def test_custom_min_samples(self, feedback_env: FeedbackEnv) -> None:
        trw_dir, _ = feedback_env
        custom = TRWConfig(ceremony_feedback_min_samples=5)
        record_sessions(trw_dir, [85.0] * 5)
        data = read_feedback_data(trw_dir)
        assert has_sufficient_samples("feature", data, custom) is True


class TestOutcomeQualityExtraction:
    """FIX-050-FR04: outcome_quality reflects actual build results; no IEEE 754 artifacts."""

    def test_outcome_quality_no_ieee754_artifacts(self, feedback_env: FeedbackEnv) -> None:
        """Default combination must not produce 0.6000000000000001."""
        trw_dir, _ = feedback_env
        entry = record_session_outcome(
            trw_dir,
            "feat: test",
            80.0,
            True,
            0.0,
            0,
            True,
            "STANDARD",
            "/runs/test",
            "session-1",
        )
        val = float(str(entry["outcome_quality"]))
        assert val == round(val, 4)
        assert val == 1.0

    def test_outcome_quality_build_failed_with_negative_coverage_is_low(
        self,
        feedback_env: FeedbackEnv,
    ) -> None:
        """build_passed=False + negative coverage_delta yields outcome_quality < 0.6.

        Formula: 0.0 (build failed) + 0.0 (coverage < 0) + 0.2 (no findings) + 0.2
        (mutation ok) = 0.4 which is < 0.6.
        """
        trw_dir, _ = feedback_env
        entry = record_session_outcome(
            trw_dir,
            "feat: test",
            80.0,
            False,
            -1.0,
            0,
            True,
            "STANDARD",
            "/runs/test",
            "session-fail",
        )
        assert float(str(entry["outcome_quality"])) < 0.6

    def test_outcome_quality_varies_across_sessions(self, feedback_env: FeedbackEnv) -> None:
        """Different build outcomes must produce distinct outcome_quality values."""
        trw_dir, _ = feedback_env
        e1 = record_session_outcome(
            trw_dir,
            "feat: a",
            80.0,
            True,
            1.0,
            0,
            True,
            "STANDARD",
            "/r/1",
            "s1",
        )
        e2 = record_session_outcome(
            trw_dir,
            "feat: b",
            80.0,
            False,
            -1.0,
            2,
            False,
            "STANDARD",
            "/r/2",
            "s2",
        )
        q1 = float(str(e1["outcome_quality"]))
        q2 = float(str(e2["outcome_quality"]))
        assert q1 != q2
        assert q1 > q2
