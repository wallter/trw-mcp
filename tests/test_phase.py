"""Tests for trw_mcp.state.phase — forward-only phase tracking."""

from __future__ import annotations

from pathlib import Path

from trw_mcp.models.run import Phase
from trw_mcp.state.persistence import FileStateReader, FileStateWriter
from trw_mcp.state.phase import update_run_phase


class TestUpdateRunPhase:
    """Tests for update_run_phase forward-only behavior."""

    def _setup_run(self, tmp_path: Path, phase: str = "research") -> Path:
        """Create a minimal run directory with run.yaml."""
        meta = tmp_path / "meta"
        meta.mkdir(parents=True)
        writer = FileStateWriter()
        writer.write_yaml(meta / "run.yaml", {
            "run_id": "test-run",
            "task": "test-task",
            "status": "active",
            "phase": phase,
        })
        return tmp_path

    def test_advances_phase_forward(self, tmp_path: Path) -> None:
        """Phase should advance from research to implement."""
        run_path = self._setup_run(tmp_path, "research")
        result = update_run_phase(run_path, Phase.IMPLEMENT)
        assert result is True

        reader = FileStateReader()
        data = reader.read_yaml(run_path / "meta" / "run.yaml")
        assert data["phase"] == "implement"

    def test_skips_same_phase(self, tmp_path: Path) -> None:
        """Should not update when already at target phase."""
        run_path = self._setup_run(tmp_path, "validate")
        result = update_run_phase(run_path, Phase.VALIDATE)
        assert result is False

    def test_skips_backward_phase(self, tmp_path: Path) -> None:
        """Should not revert to an earlier phase."""
        run_path = self._setup_run(tmp_path, "review")
        result = update_run_phase(run_path, Phase.IMPLEMENT)
        assert result is False

        reader = FileStateReader()
        data = reader.read_yaml(run_path / "meta" / "run.yaml")
        assert data["phase"] == "review"

    def test_returns_false_when_no_run_yaml(self, tmp_path: Path) -> None:
        """Should return False when run.yaml doesn't exist."""
        result = update_run_phase(tmp_path, Phase.VALIDATE)
        assert result is False

    def test_advances_research_to_plan(self, tmp_path: Path) -> None:
        """Phase should advance from research to plan."""
        run_path = self._setup_run(tmp_path, "research")
        result = update_run_phase(run_path, Phase.PLAN)
        assert result is True

        reader = FileStateReader()
        data = reader.read_yaml(run_path / "meta" / "run.yaml")
        assert data["phase"] == "plan"

    def test_advances_validate_to_review(self, tmp_path: Path) -> None:
        """Phase should advance from validate to review."""
        run_path = self._setup_run(tmp_path, "validate")
        result = update_run_phase(run_path, Phase.REVIEW)
        assert result is True

        reader = FileStateReader()
        data = reader.read_yaml(run_path / "meta" / "run.yaml")
        assert data["phase"] == "review"

    def test_advances_review_to_deliver(self, tmp_path: Path) -> None:
        """Phase should advance from review to deliver."""
        run_path = self._setup_run(tmp_path, "review")
        result = update_run_phase(run_path, Phase.DELIVER)
        assert result is True

        reader = FileStateReader()
        data = reader.read_yaml(run_path / "meta" / "run.yaml")
        assert data["phase"] == "deliver"

    def test_preserves_other_run_yaml_fields(self, tmp_path: Path) -> None:
        """Phase update should not clobber other run.yaml fields."""
        run_path = self._setup_run(tmp_path, "research")
        result = update_run_phase(run_path, Phase.IMPLEMENT)
        assert result is True

        reader = FileStateReader()
        data = reader.read_yaml(run_path / "meta" / "run.yaml")
        assert data["run_id"] == "test-run"
        assert data["task"] == "test-task"
        assert data["status"] == "active"
        assert data["phase"] == "implement"


class TestPhaseEnterEvent:
    """Tests that update_run_phase logs phase_enter events."""

    def _setup_run(self, tmp_path: Path, phase: str = "research") -> Path:
        meta = tmp_path / "meta"
        meta.mkdir(parents=True)
        writer = FileStateWriter()
        writer.write_yaml(meta / "run.yaml", {
            "run_id": "test-run",
            "task": "test-task",
            "status": "active",
            "phase": phase,
        })
        return tmp_path

    def test_phase_enter_event_logged(self, tmp_path: Path) -> None:
        """Advancing phase should log a phase_enter event to events.jsonl."""
        import json

        run_path = self._setup_run(tmp_path, "research")
        update_run_phase(run_path, Phase.IMPLEMENT)

        events_path = run_path / "meta" / "events.jsonl"
        assert events_path.exists()
        lines = [json.loads(ln) for ln in events_path.read_text().splitlines() if ln.strip()]
        phase_events = [e for e in lines if e.get("event") == "phase_enter"]
        assert len(phase_events) == 1
        assert phase_events[0]["phase"] == "implement"
        assert phase_events[0]["previous_phase"] == "research"

    def test_no_event_when_phase_skipped(self, tmp_path: Path) -> None:
        """No event should be logged when phase update is skipped."""
        run_path = self._setup_run(tmp_path, "review")
        update_run_phase(run_path, Phase.IMPLEMENT)  # backward, should skip

        events_path = run_path / "meta" / "events.jsonl"
        assert not events_path.exists()


class TestCeremonyScoreBoolCompat:
    """Tests that compute_ceremony_score handles both bool and string tests_passed."""

    def test_bool_true_tests_passed(self) -> None:
        """Bool True in event data should set build_passed=True."""
        from trw_mcp.state.analytics.report import compute_ceremony_score

        events: list[dict[str, object]] = [
            {"event": "build_check_complete", "tests_passed": True},
        ]
        result = compute_ceremony_score(events)
        assert result["build_passed"] is True

    def test_string_true_tests_passed(self) -> None:
        """String 'True' in event data should set build_passed=True."""
        from trw_mcp.state.analytics.report import compute_ceremony_score

        events: list[dict[str, object]] = [
            {"event": "build_check_complete", "tests_passed": "True"},
        ]
        result = compute_ceremony_score(events)
        assert result["build_passed"] is True

    def test_bool_false_tests_passed(self) -> None:
        """Bool False in event data should set build_passed=False."""
        from trw_mcp.state.analytics.report import compute_ceremony_score

        events: list[dict[str, object]] = [
            {"event": "build_check_complete", "tests_passed": False},
        ]
        result = compute_ceremony_score(events)
        assert result["build_passed"] is False

    def test_string_false_tests_passed(self) -> None:
        """String 'False' in event data should set build_passed=False."""
        from trw_mcp.state.analytics.report import compute_ceremony_score

        events: list[dict[str, object]] = [
            {"event": "build_check_complete", "tests_passed": "False"},
        ]
        result = compute_ceremony_score(events)
        assert result["build_passed"] is False

    def test_tool_invocation_without_tests_passed_preserves_none(self) -> None:
        """tool_invocation event lacking tests_passed should not set build_passed."""
        from trw_mcp.state.analytics.report import compute_ceremony_score

        events: list[dict[str, object]] = [
            {"event": "tool_invocation", "tool_name": "trw_build_check"},
        ]
        result = compute_ceremony_score(events)
        # build_passed should remain None (not False) since no tests_passed data
        assert result["build_passed"] is None

    def test_tool_invocation_then_build_complete_uses_build_complete(self) -> None:
        """build_check_complete after tool_invocation should use the complete event."""
        from trw_mcp.state.analytics.report import compute_ceremony_score

        events: list[dict[str, object]] = [
            {"event": "tool_invocation", "tool_name": "trw_build_check"},
            {"event": "build_check_complete", "tests_passed": True},
        ]
        result = compute_ceremony_score(events)
        assert result["build_passed"] is True


class TestCeremonyHelpersBuildGateCompat:
    """Tests that _build_passed in _ceremony_helpers handles bool and string."""

    def test_build_gate_bool_true(self, tmp_path: Path) -> None:
        """Build gate passes when event has bool True tests_passed."""
        import json

        from trw_mcp.tools._ceremony_helpers import check_delivery_gates

        # Set up run with events
        meta = tmp_path / "meta"
        meta.mkdir(parents=True)
        events_path = meta / "events.jsonl"
        event = {"ts": "2026-01-01T00:00:00Z", "event": "build_check_complete",
                 "data": {"tests_passed": True}}
        events_path.write_text(json.dumps(event) + "\n", encoding="utf-8")

        result = check_delivery_gates(tmp_path, FileStateReader())
        assert "build_gate_warning" not in result

    def test_build_gate_string_true(self, tmp_path: Path) -> None:
        """Build gate passes when event has string 'True' tests_passed."""
        import json

        from trw_mcp.tools._ceremony_helpers import check_delivery_gates

        meta = tmp_path / "meta"
        meta.mkdir(parents=True)
        events_path = meta / "events.jsonl"
        event = {"ts": "2026-01-01T00:00:00Z", "event": "build_check_complete",
                 "data": {"tests_passed": "True"}}
        events_path.write_text(json.dumps(event) + "\n", encoding="utf-8")

        result = check_delivery_gates(tmp_path, FileStateReader())
        assert "build_gate_warning" not in result
