"""Tests for live nudge-effectiveness analysis (nudge-deep-dive #1/#2/#4).

Covers compute_nudge_analysis (responsiveness, per-step resistance,
recall-pull, timing aggregation), compute_nudge_timing (live timing-validity),
write/persist artifact, the compact summary, and fail-open behaviour.
"""

from __future__ import annotations

import json
from pathlib import Path

from trw_mcp.state._ceremony_progress_state import CeremonyState, write_ceremony_state
from trw_mcp.state.nudge_analysis import (
    DEFAULT_RESISTANCE_THRESHOLD,
    analysis_summary,
    compute_nudge_analysis,
    compute_nudge_timing,
    persist_nudge_analysis,
    write_nudge_analysis,
)
from trw_mcp.state.surface_tracking import log_surface_event


def _trw_dir(tmp_path: Path) -> Path:
    d = tmp_path / ".trw"
    d.mkdir(exist_ok=True)
    return d


class TestResponsivenessAndResistance:
    def test_no_nudges_not_applicable(self, tmp_path: Path) -> None:
        trw_dir = _trw_dir(tmp_path)
        write_ceremony_state(trw_dir, CeremonyState(session_started=True))
        result = compute_nudge_analysis(trw_dir)
        assert result.applicable is False
        assert result.total_nudges == 0
        assert result.nudge_responsiveness == 0.0
        assert result.resistance_by_step == {}

    def test_all_nudged_steps_completed_full_responsiveness(self, tmp_path: Path) -> None:
        trw_dir = _trw_dir(tmp_path)
        write_ceremony_state(
            trw_dir,
            CeremonyState(
                session_started=True,
                checkpoint_count=2,
                nudge_counts={"checkpoint": 3, "session_start": 1},
            ),
        )
        result = compute_nudge_analysis(trw_dir)
        assert result.applicable is True
        assert result.total_nudges == 4
        assert result.nudged_step_count == 2
        assert result.nudge_step_completed == {"checkpoint": True, "session_start": True}
        assert result.nudge_responsiveness == 1.0
        assert result.resistance_by_step == {}

    def test_partial_completion_responsiveness_and_resistance(self, tmp_path: Path) -> None:
        trw_dir = _trw_dir(tmp_path)
        # checkpoint done (count>0), build_check never ran -> resistance.
        write_ceremony_state(
            trw_dir,
            CeremonyState(
                session_started=True,
                checkpoint_count=1,
                build_check_result=None,
                nudge_counts={"checkpoint": 1, "build_check": 2},
            ),
        )
        result = compute_nudge_analysis(trw_dir)
        assert result.nudge_step_completed == {"checkpoint": True, "build_check": False}
        assert result.nudge_responsiveness == 0.5
        assert result.resistance_by_step == {"build_check": 2}
        # Below default threshold (3) -> no structural flag yet.
        assert result.resistance_flags == []

    def test_resistance_flag_fires_at_threshold(self, tmp_path: Path) -> None:
        trw_dir = _trw_dir(tmp_path)
        write_ceremony_state(
            trw_dir,
            CeremonyState(
                session_started=True,
                review_called=False,
                nudge_counts={"review": DEFAULT_RESISTANCE_THRESHOLD},
            ),
        )
        result = compute_nudge_analysis(trw_dir)
        assert result.resistance_by_step == {"review": DEFAULT_RESISTANCE_THRESHOLD}
        assert len(result.resistance_flags) == 1
        flag = result.resistance_flags[0]
        assert flag["step"] == "review"
        assert flag["nudge_count"] == DEFAULT_RESISTANCE_THRESHOLD

    def test_build_check_failed_still_counts_as_responded(self, tmp_path: Path) -> None:
        """A failing build is still a behavioral response to a build_check nudge."""
        trw_dir = _trw_dir(tmp_path)
        write_ceremony_state(
            trw_dir,
            CeremonyState(
                session_started=True,
                build_check_result="failed",
                nudge_counts={"build_check": 2},
            ),
        )
        result = compute_nudge_analysis(trw_dir)
        assert result.nudge_step_completed == {"build_check": True}
        assert result.nudge_responsiveness == 1.0
        assert result.resistance_by_step == {}


class TestComputeNudgeTiming:
    def test_timely_when_step_pending(self) -> None:
        state = CeremonyState()  # nothing done
        is_timely, distance = compute_nudge_timing("session_start", state)
        assert is_timely is True
        assert distance == -1  # no completed step yet; nudged index 0

    def test_untimely_when_step_already_done(self) -> None:
        state = CeremonyState(
            session_started=True,
            checkpoint_count=1,
            files_modified_since_checkpoint=0,
            build_check_result="passed",
            review_called=True,
            deliver_called=True,
        )
        is_timely, distance = compute_nudge_timing("session_start", state)
        assert is_timely is False
        assert distance == 4  # furthest-complete=deliver(4) minus session_start(0)

    def test_unknown_step_distance_zero(self) -> None:
        state = CeremonyState(session_started=True)
        is_timely, distance = compute_nudge_timing("not_a_step", state)
        assert is_timely is True
        assert distance == 0


class TestTimingAggregation:
    def test_aggregates_is_timely_from_surface_events(self, tmp_path: Path) -> None:
        trw_dir = _trw_dir(tmp_path)
        write_ceremony_state(trw_dir, CeremonyState(nudge_counts={"checkpoint": 1}))
        log_surface_event(trw_dir, learning_id="L-1", surface_type="nudge", is_timely=True)
        log_surface_event(trw_dir, learning_id="L-2", surface_type="nudge", is_timely=True)
        log_surface_event(trw_dir, learning_id="L-3", surface_type="nudge", is_timely=False)
        # An older nudge with no timing flag -> unknown.
        log_surface_event(trw_dir, learning_id="L-4", surface_type="nudge")
        # A recall event must be ignored by timing aggregation.
        log_surface_event(trw_dir, learning_id="L-5", surface_type="recall", is_timely=True)
        result = compute_nudge_analysis(trw_dir)
        assert result.timing.timely_count == 2
        assert result.timing.untimely_count == 1
        assert result.timing.unknown_count == 1
        assert result.timing.validity_rate == round(2 / 3, 4)


class TestVariantBreakdown:
    def test_variant_counts_tallied_from_surface_events(self, tmp_path: Path) -> None:
        trw_dir = _trw_dir(tmp_path)
        write_ceremony_state(trw_dir, CeremonyState(nudge_counts={"checkpoint": 1}))
        log_surface_event(trw_dir, learning_id="L-1", surface_type="nudge", nudge_variant="armA")
        log_surface_event(trw_dir, learning_id="L-2", surface_type="nudge", nudge_variant="armA")
        log_surface_event(trw_dir, learning_id="L-3", surface_type="nudge", nudge_variant="armB")
        # Unlabelled nudge is not counted toward any arm.
        log_surface_event(trw_dir, learning_id="L-4", surface_type="nudge")
        result = compute_nudge_analysis(trw_dir)
        assert result.variant_breakdown == {"armA": 2, "armB": 1}


class TestVariantWiring:
    """Wiring: config.nudge_variant must reach the emitted nudge surface event."""

    def test_emit_helper_stamps_config_variant(self, tmp_path: Path) -> None:
        import json as _json

        from trw_mcp.models.config import TRWConfig
        from trw_mcp.tools._ceremony_status_helpers import _emit_nudge_surface_event

        trw_dir = _trw_dir(tmp_path)
        cfg = TRWConfig(trw_dir=str(trw_dir), nudge_variant="structural-v2")
        state = CeremonyState(session_started=True)  # checkpoint pending
        _emit_nudge_surface_event(
            trw_dir,
            cfg=cfg,
            state=state,
            messenger="contextual",
            client_id="claude-code",
            learning_id="L-wire",
            target_file="src/x.py",
            pending_step="checkpoint",
        )
        event = _json.loads((trw_dir / "logs" / "surface_tracking.jsonl").read_text().strip())
        assert event["nudge_variant"] == "structural-v2"
        assert event["messenger"] == "contextual"
        assert event["nudge_step"] == "checkpoint"
        assert event["is_timely"] is True  # checkpoint pending


class TestRecallPull:
    def test_recall_pull_rate_when_nudged_learning_recalled(self, tmp_path: Path) -> None:
        trw_dir = _trw_dir(tmp_path)
        write_ceremony_state(trw_dir, CeremonyState(nudge_counts={"checkpoint": 1}))
        log_surface_event(trw_dir, learning_id="L-pull", surface_type="nudge", session_id="s1")
        log_surface_event(trw_dir, learning_id="L-pull", surface_type="recall", session_id="s1")
        result = compute_nudge_analysis(trw_dir, session_id="s1")
        assert result.recall_nudge_count == 1
        assert result.recall_pull_rate == 1.0

    def test_recall_pull_rate_zero_when_not_recalled(self, tmp_path: Path) -> None:
        trw_dir = _trw_dir(tmp_path)
        write_ceremony_state(trw_dir, CeremonyState(nudge_counts={"checkpoint": 1}))
        log_surface_event(trw_dir, learning_id="L-x", surface_type="nudge", session_id="s1")
        result = compute_nudge_analysis(trw_dir, session_id="s1")
        assert result.recall_nudge_count == 1
        assert result.recall_pull_rate == 0.0


class TestArtifactWrite:
    def test_write_creates_artifact_with_schema(self, tmp_path: Path) -> None:
        trw_dir = _trw_dir(tmp_path)
        write_ceremony_state(
            trw_dir,
            CeremonyState(session_started=True, nudge_counts={"checkpoint": 2}),
        )
        path = write_nudge_analysis(trw_dir, session_id="s1")
        assert path is not None
        assert path == trw_dir / "context" / "nudge-analysis.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["schema_version"] == 1
        assert data["session_id"] == "s1"
        assert data["applicable"] is True
        assert data["total_nudges"] == 2
        assert "resistance_by_step" in data
        assert "timing" in data
        assert data["generated_at"]  # non-empty ISO timestamp

    def test_persist_does_not_recompute(self, tmp_path: Path) -> None:
        trw_dir = _trw_dir(tmp_path)
        write_ceremony_state(trw_dir, CeremonyState(nudge_counts={"deliver": 1}))
        analysis = compute_nudge_analysis(trw_dir)
        path = persist_nudge_analysis(trw_dir, analysis)
        assert path is not None
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["total_nudges"] == 1

    def test_artifact_no_learning_content_leak(self, tmp_path: Path) -> None:
        trw_dir = _trw_dir(tmp_path)
        write_ceremony_state(trw_dir, CeremonyState(nudge_counts={"checkpoint": 1}))
        path = write_nudge_analysis(trw_dir)
        assert path is not None
        text = path.read_text(encoding="utf-8")
        # Artifact is fact-only; must not embed learning summary/detail content.
        assert "summary" not in text
        assert "detail" not in text


class TestSummary:
    def test_summary_shape(self, tmp_path: Path) -> None:
        trw_dir = _trw_dir(tmp_path)
        write_ceremony_state(
            trw_dir,
            CeremonyState(nudge_counts={"build_check": DEFAULT_RESISTANCE_THRESHOLD}),
        )
        result = compute_nudge_analysis(trw_dir)
        summary = analysis_summary(result)
        assert summary["applicable"] is True
        assert summary["total_nudges"] == DEFAULT_RESISTANCE_THRESHOLD
        assert summary["resistance_steps"] == ["build_check"]
        assert summary["resistance_flagged"] == ["build_check"]
        assert "timing_validity_rate" in summary


class TestFailOpen:
    def test_corrupt_ceremony_state_yields_not_applicable(self, tmp_path: Path) -> None:
        trw_dir = _trw_dir(tmp_path)
        ctx = trw_dir / "context"
        ctx.mkdir(parents=True)
        (ctx / "ceremony-state.json").write_text("{not json", encoding="utf-8")
        result = compute_nudge_analysis(trw_dir)  # must not raise
        assert result.applicable is False
        assert result.total_nudges == 0

    def test_missing_trw_dir_yields_not_applicable(self, tmp_path: Path) -> None:
        result = compute_nudge_analysis(tmp_path / "nonexistent" / ".trw")
        assert result.applicable is False
