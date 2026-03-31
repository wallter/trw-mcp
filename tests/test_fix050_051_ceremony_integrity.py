"""Tests for PRD-FIX-050 and PRD-FIX-051 ceremony data integrity fixes.

Covers:
- FIX-050-FR01/FR02: Test isolation (resolve_trw_dir patched to tmp dir)
- FIX-050-FR03 / FIX-051-FR02: task_name -> task field fix in _step_ceremony_feedback
- FIX-051-FR04: Zero-score escalation guard in check_auto_escalation
- FIX-051-FR01/FR05: compute_ceremony_score reads session-events.jsonl
- FIX-050-FR06: sessions_count -> sessions_tracked migration + increment on session_start
"""

from __future__ import annotations

from pathlib import Path

from trw_mcp.state.analytics.counters import (
    _read_analytics,
    increment_session_start_counter,
)
from trw_mcp.state.analytics.report import compute_ceremony_score
from trw_mcp.state.ceremony_feedback import (
    check_auto_escalation,
    classify_task_class,
    record_session_outcome,
)
from trw_mcp.state.persistence import FileStateWriter

# ---------------------------------------------------------------------------
# FIX-050-FR01/FR02: Test isolation — autouse fixture in conftest redirects
# resolve_trw_dir() to tmp_path, so the real .trw/ is never touched.
# ---------------------------------------------------------------------------


class TestIsolation:
    """FIX-050-FR01/FR02: Verify _isolate_trw_dir autouse fixture works."""

    def test_resolve_trw_dir_points_to_tmp(self, tmp_path: Path) -> None:
        """resolve_trw_dir() must NOT point at the real project .trw/ during tests.

        The _isolate_trw_dir autouse fixture patches resolve_trw_dir() to return
        tmp_path/.trw — verify that it does NOT return the real project .trw/.
        """
        from trw_mcp.state._paths import resolve_trw_dir

        result = resolve_trw_dir()
        # Verify isolation: result must not be the real project .trw/
        real_project_trw = (Path(__file__).parent.parent / ".trw").resolve()
        result_resolved = result.resolve()
        assert result_resolved != real_project_trw, (
            f"resolve_trw_dir() returned the real project .trw/ during tests: {result}"
        )
        # Verify it's under tmp_path (platform-neutral isolation check)
        assert result_resolved.is_relative_to(tmp_path.resolve()), (
            f"Expected path under tmp_path {tmp_path}, got: {result}"
        )

    def test_ceremony_feedback_writes_to_tmp_not_real_project(self, tmp_path: Path) -> None:
        """Ceremony feedback must not write to the real project .trw/."""
        from trw_mcp.state._paths import resolve_trw_dir

        trw_dir = resolve_trw_dir()
        writer = FileStateWriter()
        writer.ensure_dir(trw_dir / "context")
        # Write ceremony data to wherever resolve_trw_dir points
        record_session_outcome(
            trw_dir,
            "test-task",
            80.0,
            True,
            0.0,
            0,
            True,
            "STANDARD",
            "/runs/test",
            "test-session",
        )
        # The real project .trw/ must NOT have been modified
        real_feedback = Path(__file__).parent.parent / ".trw" / "context" / "ceremony-feedback.yaml"
        if real_feedback.exists():
            content = real_feedback.read_text()
            assert "test-session" not in content, "Test wrote to real project .trw/ — isolation fixture failed"


# ---------------------------------------------------------------------------
# FIX-051-FR04: Zero-score escalation guard
# ---------------------------------------------------------------------------


class TestZeroScoreEscalationGuard:
    """FIX-051-FR04: check_auto_escalation must skip escalation when all scores are 0.0."""

    def test_all_zero_scores_returns_none(self, tmp_path: Path) -> None:
        """When all window scores are exactly 0.0, escalation must be skipped."""
        from trw_mcp.models.config import TRWConfig

        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        (trw_dir / "context").mkdir()

        # Record enough sessions with score=0.0 to meet the escalation window
        for i in range(6):
            record_session_outcome(
                trw_dir,
                "test-task",
                0.0,
                False,
                0.0,
                0,
                True,
                "STANDARD",
                f"/runs/{i}",
                f"s-{i}",
            )

        from trw_mcp.state.ceremony_feedback import read_feedback_data

        data = read_feedback_data(trw_dir)
        config = TRWConfig(ceremony_feedback_escalation_window=5)

        result = check_auto_escalation("documentation", data, config)
        assert result is None, f"Expected None (no escalation) when all scores are 0.0, got: {result}"

    def test_genuine_low_scores_still_escalate(self, tmp_path: Path) -> None:
        """Non-zero but below-threshold scores must still trigger escalation (guard not over-blocking)."""
        from trw_mcp.models.config import TRWConfig

        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        (trw_dir / "context").mkdir()

        # Score 15.0 — below default threshold (60.0) but non-zero
        for i in range(6):
            record_session_outcome(
                trw_dir,
                "test-task",
                15.0,
                False,
                0.0,
                0,
                True,
                "STANDARD",
                f"/runs/{i}",
                f"s-{i}",
            )

        from trw_mcp.state.ceremony_feedback import read_feedback_data

        data = read_feedback_data(trw_dir)
        config = TRWConfig(ceremony_feedback_escalation_window=5)

        result = check_auto_escalation("documentation", data, config)
        assert result is not None, "Expected escalation for genuine low scores (15.0 < 60.0)"
        assert result["triggered"] is True

    def test_mixed_scores_with_nonzero_triggers_normally(self, tmp_path: Path) -> None:
        """If even one score is non-zero but below threshold, normal escalation applies."""
        from trw_mcp.models.config import TRWConfig

        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        (trw_dir / "context").mkdir()

        # 4 zeros + 1 non-zero — not all zeros, so guard does NOT apply
        scores = [0.0, 0.0, 0.0, 0.0, 30.0]  # all < 60 threshold
        for i, score in enumerate(scores):
            record_session_outcome(
                trw_dir,
                "test-task",
                score,
                False,
                0.0,
                0,
                True,
                "STANDARD",
                f"/runs/{i}",
                f"s-{i}",
            )

        from trw_mcp.state.ceremony_feedback import read_feedback_data

        data = read_feedback_data(trw_dir)
        config = TRWConfig(ceremony_feedback_escalation_window=5)

        result = check_auto_escalation("documentation", data, config)
        assert result is not None, "Expected escalation — not all zeros, all below threshold"


# ---------------------------------------------------------------------------
# FIX-050-FR03 / FIX-051-FR02: task_name -> task field fix
# ---------------------------------------------------------------------------


class TestTaskFieldFix:
    """FIX-050-FR03 / FIX-051-FR02: Verify _step_ceremony_feedback reads 'task' not 'task_name'."""

    def test_step_ceremony_feedback_reads_task_field(self, tmp_path: Path) -> None:
        """When run.yaml has task: 'feat-add-auth', ceremony entry records task_class 'feature'.

        FIX-050-FR03 / FIX-051-FR02: _step_ceremony_feedback must read the 'task'
        field (not 'task_name') from run.yaml and pass it to classify_task_class().
        """
        from trw_mcp.state.persistence import FileStateWriter as W
        from trw_mcp.tools._deferred_delivery import _step_ceremony_feedback

        # Create a minimal run directory
        run_dir = tmp_path / "docs" / "feat-add-auth" / "runs" / "20260313T120000Z-test"
        meta = run_dir / "meta"
        meta.mkdir(parents=True)

        W().write_yaml(
            meta / "run.yaml",
            {
                "run_id": "20260313T120000Z-test",
                "task": "feat-add-auth",  # correct field name (not task_name)
                "status": "active",
                "phase": "implement",
            },
        )
        W().append_jsonl(
            meta / "events.jsonl",
            {
                "ts": "2026-03-13T12:00:00Z",
                "event": "run_init",
                "task": "feat-add-auth",
            },
        )

        deliver_results: dict[str, object] = {
            "telemetry": {"ceremony_score": 80, "build_passed": True},
        }
        result = _step_ceremony_feedback(run_dir, deliver_results)

        # Must return a dict — not raise
        assert isinstance(result, dict), f"Expected dict, got: {type(result)}"

        # Verify the task_class in the written ceremony-feedback.yaml
        from trw_mcp.state._paths import resolve_trw_dir
        from trw_mcp.state.ceremony_feedback import read_feedback_data

        trw_dir = resolve_trw_dir()
        feedback_path = trw_dir / "context" / "ceremony-feedback.yaml"
        assert feedback_path.exists(), (
            "ceremony-feedback.yaml was not written — _step_ceremony_feedback skipped entirely"
        )
        data = read_feedback_data(trw_dir)
        task_classes = data.get("task_classes", {})
        assert isinstance(task_classes, dict), f"task_classes is not a dict: {task_classes}"
        # "feat-add-auth" must classify as 'feature', not 'documentation'
        assert "feature" in task_classes, (
            f"Expected 'feature' task class from 'feat-add-auth' task name. "
            f"Got task_classes keys: {list(task_classes.keys())}. "
            f"If 'documentation' is present, the bug is still reading 'task_name' not 'task'."
        )
        assert "documentation" not in task_classes, (
            "Classifier returned 'documentation' — the 'task' field was not read correctly"
        )

    def test_task_name_field_never_returns_task_class_documentation_for_feat(self) -> None:
        """classify_task_class with 'feat-add-auth' must return FEATURE, not DOCUMENTATION."""
        from trw_mcp.state.ceremony_feedback import TaskClass

        result = classify_task_class("feat-add-auth")
        assert result == TaskClass.FEATURE, f"Expected FEATURE, got {result}"

    def test_task_name_field_security_audit_classifies_correctly(self) -> None:
        """classify_task_class('security-audit') must return SECURITY."""
        from trw_mcp.state.ceremony_feedback import TaskClass

        result = classify_task_class("security-audit")
        assert result == TaskClass.SECURITY, f"Expected SECURITY, got {result}"


# ---------------------------------------------------------------------------
# FIX-051-FR01/FR05: compute_ceremony_score reads session-events.jsonl
# ---------------------------------------------------------------------------


class TestCeremonyScoreSessionEvents:
    """FIX-051-FR01/FR05: compute_ceremony_score must read session-events.jsonl."""

    def test_score_includes_session_start_from_session_events(self, tmp_path: Path) -> None:
        """When session_start is only in session-events.jsonl (not events.jsonl), score must include 25 pts."""
        trw_dir = tmp_path / ".trw"
        (trw_dir / "context").mkdir(parents=True)
        writer = FileStateWriter()

        # Write session_start event to session-events.jsonl (the fallback path)
        session_events_path = trw_dir / "context" / "session-events.jsonl"
        writer.append_jsonl(
            session_events_path,
            {
                "ts": "2026-03-13T12:00:00Z",
                "event": "tool_invocation",
                "tool_name": "trw_session_start",
            },
        )

        # events.jsonl (run-level) has NO session_start — just a checkpoint
        run_events: list[dict[str, object]] = [
            {"ts": "2026-03-13T12:01:00Z", "event": "tool_invocation", "tool_name": "trw_checkpoint"},
        ]

        # Without trw_dir: should NOT get session_start points (25)
        result_no_trw_dir = compute_ceremony_score(run_events)
        assert result_no_trw_dir["session_start"] is False
        assert result_no_trw_dir["score"] == 20  # only checkpoint = 20 pts

        # With trw_dir: should GET session_start points from session-events.jsonl
        result_with_trw_dir = compute_ceremony_score(run_events, trw_dir=trw_dir)
        assert result_with_trw_dir["session_start"] is True
        assert result_with_trw_dir["score"] == 45  # checkpoint(20) + session_start(25)

    def test_score_backward_compat_without_trw_dir(self) -> None:
        """compute_ceremony_score called without trw_dir must behave identically to old code."""
        events: list[dict[str, object]] = [
            {"ts": "2026-03-13T12:00:00Z", "event": "session_start"},
            {"ts": "2026-03-13T12:01:00Z", "event": "tool_invocation", "tool_name": "trw_checkpoint"},
        ]
        result = compute_ceremony_score(events)
        assert result["session_start"] is True
        assert result["checkpoint_count"] == 1
        assert result["score"] == 45  # session_start(25) + checkpoint(20)

    def test_score_with_missing_session_events_file(self, tmp_path: Path) -> None:
        """When session-events.jsonl doesn't exist, score must not fail."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        # No context/ dir — session-events.jsonl doesn't exist
        run_events: list[dict[str, object]] = [
            {"ts": "2026-03-13T12:00:00Z", "event": "session_start"},
        ]
        result = compute_ceremony_score(run_events, trw_dir=trw_dir)
        assert result["session_start"] is True
        assert result["score"] == 25

    def test_full_score_when_all_events_in_session_events(self, tmp_path: Path) -> None:
        """A session where all ceremony tools are in session-events.jsonl scores non-zero."""
        trw_dir = tmp_path / ".trw"
        (trw_dir / "context").mkdir(parents=True)
        writer = FileStateWriter()

        session_events_path = trw_dir / "context" / "session-events.jsonl"
        for evt in [
            {"ts": "2026-03-13T12:00:00Z", "event": "tool_invocation", "tool_name": "trw_session_start"},
            {"ts": "2026-03-13T12:01:00Z", "event": "tool_invocation", "tool_name": "trw_checkpoint"},
            {"ts": "2026-03-13T12:02:00Z", "event": "tool_invocation", "tool_name": "trw_learn"},
        ]:
            writer.append_jsonl(session_events_path, evt)

        # Run-level events: just the deliver
        run_events: list[dict[str, object]] = [
            {"ts": "2026-03-13T12:03:00Z", "event": "tool_invocation", "tool_name": "trw_deliver"},
        ]

        result = compute_ceremony_score(run_events, trw_dir=trw_dir)
        assert result["session_start"] is True
        assert result["checkpoint_count"] >= 1
        assert result["learn_count"] >= 1
        assert result["deliver"] is True
        assert result["score"] == 80  # 25+25+20+10


# ---------------------------------------------------------------------------
# FIX-050-FR06: sessions_count -> sessions_tracked migration + increment
# ---------------------------------------------------------------------------


class TestSessionsCountMigration:
    """FIX-050-FR06: sessions_count field migration and session_start counter increment."""

    def test_sessions_count_migrated_to_sessions_tracked(self, tmp_path: Path) -> None:
        """Legacy sessions_count must be migrated to sessions_tracked on read."""
        from trw_mcp.models.config import TRWConfig

        trw_dir = tmp_path / ".trw"
        (trw_dir / "context").mkdir(parents=True)
        writer = FileStateWriter()

        # Write analytics.yaml with legacy sessions_count field
        analytics_path = trw_dir / "context" / "analytics.yaml"
        writer.write_yaml(
            analytics_path,
            {
                "sessions_count": 42,
                "sessions_tracked": 0,
                "total_learnings": 100,
            },
        )

        _reset_config_for_test = TRWConfig()
        from trw_mcp.models.config import _reset_config

        _reset_config(_reset_config_for_test)

        _, data = _read_analytics(trw_dir)

        assert "sessions_count" not in data, "sessions_count must be removed after migration"
        assert data.get("sessions_tracked") == 42, (
            f"Expected sessions_tracked=42 (migrated from sessions_count), got {data.get('sessions_tracked')}"
        )

    def test_sessions_migration_uses_max_value(self, tmp_path: Path) -> None:
        """Migration must use max(sessions_count, sessions_tracked) to avoid data loss."""
        from trw_mcp.models.config import TRWConfig, _reset_config

        trw_dir = tmp_path / ".trw"
        (trw_dir / "context").mkdir(parents=True)
        writer = FileStateWriter()

        analytics_path = trw_dir / "context" / "analytics.yaml"
        writer.write_yaml(
            analytics_path,
            {
                "sessions_count": 10,  # legacy (lower value)
                "sessions_tracked": 25,  # current (higher value — must be preserved)
            },
        )

        _reset_config(TRWConfig())
        _, data = _read_analytics(trw_dir)

        assert "sessions_count" not in data
        assert data.get("sessions_tracked") == 25, (
            f"Expected sessions_tracked=25 (max of 10 and 25), got {data.get('sessions_tracked')}"
        )

    def test_increment_session_start_counter_increments_by_one(self, tmp_path: Path) -> None:
        """increment_session_start_counter must increment sessions_tracked by 1."""
        from trw_mcp.models.config import TRWConfig, _reset_config

        trw_dir = tmp_path / ".trw"
        (trw_dir / "context").mkdir(parents=True)
        writer = FileStateWriter()

        analytics_path = trw_dir / "context" / "analytics.yaml"
        writer.write_yaml(analytics_path, {"sessions_tracked": 5})

        _reset_config(TRWConfig())
        increment_session_start_counter(trw_dir)

        _, data = _read_analytics(trw_dir)
        assert data.get("sessions_tracked") == 6, (
            f"Expected sessions_tracked=6 after increment, got {data.get('sessions_tracked')}"
        )

    def test_increment_session_start_counter_starts_from_zero(self, tmp_path: Path) -> None:
        """increment_session_start_counter must work on empty analytics.yaml."""
        from trw_mcp.models.config import TRWConfig, _reset_config

        trw_dir = tmp_path / ".trw"
        (trw_dir / "context").mkdir(parents=True)

        _reset_config(TRWConfig())
        increment_session_start_counter(trw_dir)

        _, data = _read_analytics(trw_dir)
        assert data.get("sessions_tracked") == 1, (
            f"Expected sessions_tracked=1 on first increment, got {data.get('sessions_tracked')}"
        )

    def test_no_sessions_count_in_clean_analytics(self, tmp_path: Path) -> None:
        """A fresh analytics.yaml without sessions_count must be unaffected by migration."""
        from trw_mcp.models.config import TRWConfig, _reset_config

        trw_dir = tmp_path / ".trw"
        (trw_dir / "context").mkdir(parents=True)
        writer = FileStateWriter()

        analytics_path = trw_dir / "context" / "analytics.yaml"
        writer.write_yaml(analytics_path, {"sessions_tracked": 7, "total_learnings": 50})

        _reset_config(TRWConfig())
        _, data = _read_analytics(trw_dir)

        assert "sessions_count" not in data
        assert data.get("sessions_tracked") == 7


# ---------------------------------------------------------------------------
# P2-005: sessions_tracked end-to-end integration test
# ---------------------------------------------------------------------------


class TestSessionsTrackedEndToEnd:
    """P2-005: sessions_tracked increments end-to-end via session_start → analytics.yaml."""

    def test_sessions_tracked_increments_on_each_session_start(self, tmp_path: Path) -> None:
        """Multiple increment_session_start_counter calls accumulate correctly in analytics.yaml."""
        from trw_mcp.models.config import TRWConfig, _reset_config

        trw_dir = tmp_path / ".trw"
        (trw_dir / "context").mkdir(parents=True)
        _reset_config(TRWConfig())

        # Simulate three distinct session starts
        increment_session_start_counter(trw_dir)
        increment_session_start_counter(trw_dir)
        increment_session_start_counter(trw_dir)

        # Verify the final state in analytics.yaml
        _, data = _read_analytics(trw_dir)
        assert data.get("sessions_tracked") == 3, (
            f"Expected sessions_tracked=3 after 3 increments, got {data.get('sessions_tracked')}"
        )
        # Persisted to disk: re-read must match
        analytics_path = trw_dir / "context" / "analytics.yaml"
        assert analytics_path.exists(), "analytics.yaml must be written to disk"
        raw = FileStateWriter._instance if False else None  # just check file exists
        reader = FileStateWriter()
        from trw_mcp.state.persistence import FileStateReader as FSR

        disk_data: dict[str, object] = FSR().read_yaml(analytics_path)
        assert disk_data.get("sessions_tracked") == 3, (
            f"Disk analytics.yaml must show sessions_tracked=3, got {disk_data.get('sessions_tracked')}"
        )

    def test_sessions_tracked_persists_after_config_reset(self, tmp_path: Path) -> None:
        """sessions_tracked value survives a config singleton reset between reads."""
        from trw_mcp.models.config import TRWConfig, _reset_config

        trw_dir = tmp_path / ".trw"
        (trw_dir / "context").mkdir(parents=True)
        _reset_config(TRWConfig())

        increment_session_start_counter(trw_dir)

        # Simulate a config reset (as happens between tests)
        _reset_config(TRWConfig())

        # Must still read the correct value from disk
        _, data = _read_analytics(trw_dir)
        assert data.get("sessions_tracked") == 1, (
            "sessions_tracked must survive a config singleton reset (reads from disk)"
        )
