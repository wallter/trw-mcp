"""End-to-end nudge -> behavior compliance loop (nudge-deep-dive #3).

Proves the loop actually closes: a nudge FIRES for a pending ceremony step,
its content reaches the tool response, the agent ACTS (calls trw_checkpoint),
and the live analysis flips that step from resistant to responded.

Unit tests elsewhere cover selection/state/telemetry in isolation; this is the
missing integration that proves nudge-fires -> agent-reads -> agent-completes.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from tests._ceremony_helpers import make_ceremony_server as _make_ceremony_server
from trw_mcp.state._ceremony_progress_state import (
    CeremonyState,
    increment_nudge_count,
    mark_checkpoint,
    read_ceremony_state,
    write_ceremony_state,
)
from trw_mcp.state.nudge_analysis import compute_nudge_analysis
from trw_mcp.tools._ceremony_status import append_ceremony_status


def _checkpoint_pending_workspace(tmp_path: Path) -> Path:
    """A .trw workspace where session_start is done but checkpoint is pending."""
    trw_dir = tmp_path / ".trw"
    (trw_dir / "context").mkdir(parents=True)
    # Deterministic, recordable nudges via the minimal messenger.
    (trw_dir / "config.yaml").write_text(
        "nudge_enabled: true\nnudge_messenger: minimal\n",
        encoding="utf-8",
    )
    write_ceremony_state(
        trw_dir,
        CeremonyState(
            session_started=True,
            checkpoint_count=0,
            files_modified_since_checkpoint=5,
            phase="implement",
        ),
    )
    return trw_dir


@pytest.mark.integration
class TestNudgeComplianceLoop:
    def test_nudge_fires_for_pending_checkpoint(self, tmp_path: Path) -> None:
        """append_ceremony_status surfaces nudge_content and records the step."""
        trw_dir = _checkpoint_pending_workspace(tmp_path)
        response = append_ceremony_status({}, trw_dir)
        assert "ceremony_status" in response
        assert response.get("nudge_content")  # a nudge fired
        # The emitted nudge was counted against the highest-priority pending
        # step, which is checkpoint (session_start already done).
        state = read_ceremony_state(trw_dir)
        assert state.nudge_counts.get("checkpoint", 0) >= 1

    def test_immediate_compliance_marks_step_responded(self, tmp_path: Path) -> None:
        """Nudge fires -> agent checkpoints immediately -> analysis = responded."""
        trw_dir = _checkpoint_pending_workspace(tmp_path)

        # Nudge fires.
        append_ceremony_status({}, trw_dir)
        before = compute_nudge_analysis(trw_dir)
        assert before.nudge_counts_by_step.get("checkpoint", 0) >= 1
        assert before.nudge_step_completed.get("checkpoint") is False
        assert before.nudge_responsiveness == 0.0
        assert "checkpoint" in before.resistance_by_step

        # Agent reads nudge_content and calls trw_checkpoint().
        mark_checkpoint(trw_dir)

        after = compute_nudge_analysis(trw_dir)
        assert read_ceremony_state(trw_dir).checkpoint_count == 1
        assert after.nudge_step_completed.get("checkpoint") is True
        assert after.nudge_responsiveness == 1.0
        assert "checkpoint" not in after.resistance_by_step

    def test_eventual_compliance_after_repeated_nudges(self, tmp_path: Path) -> None:
        """Resistance flags a repeatedly-nudged-but-pending step, then clears.

        Eventual compliance: responsiveness keys off step completion, not the
        number of nudge impressions (learning L-be92b5d7).
        """
        trw_dir = _checkpoint_pending_workspace(tmp_path)

        # Three checkpoint nudges, agent ignores all of them.
        for _ in range(3):
            increment_nudge_count(trw_dir, "checkpoint")
        resistant = compute_nudge_analysis(trw_dir)
        assert resistant.nudge_counts_by_step["checkpoint"] == 3
        assert resistant.nudge_responsiveness == 0.0
        assert any(f["step"] == "checkpoint" for f in resistant.resistance_flags)

        # Agent eventually complies.
        mark_checkpoint(trw_dir)
        complied = compute_nudge_analysis(trw_dir)
        assert complied.nudge_responsiveness == 1.0
        assert complied.resistance_flags == []
        assert complied.resistance_by_step == {}


@pytest.mark.integration
class TestDeliverWritesNudgeAnalysis:
    """Wiring: trw_deliver must actually emit the live artifact + summary."""

    def test_deliver_writes_nudge_analysis_artifact(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        tools = _make_ceremony_server(monkeypatch, tmp_path)
        trw_dir = tmp_path / ".trw"
        (trw_dir / "learnings" / "entries").mkdir(parents=True)
        (trw_dir / "context").mkdir(parents=True)
        # A resistant build_check (nudged 3x, never ran) should be flagged.
        write_ceremony_state(
            trw_dir,
            CeremonyState(session_started=True, nudge_counts={"build_check": 3}),
        )

        with (
            patch("trw_mcp.tools.ceremony.resolve_trw_dir", return_value=trw_dir),
            patch("trw_mcp.tools.ceremony.find_active_run", return_value=None),
            patch(
                "trw_mcp.tools.ceremony._do_instruction_sync",
                return_value={"status": "success", "learnings_promoted": 0, "path": "", "total_lines": 0},
            ),
            patch(
                "trw_mcp.tools._deferred_delivery._do_index_sync",
                return_value={"status": "success", "index": {}, "roadmap": {}},
            ),
            patch("trw_mcp.state._paths.resolve_project_root", return_value=tmp_path),
        ):
            # allow_unverified clears the unpinned build gate so deliver reaches
            # the post-mark_deliver artifact step (mirrors the resilience tests).
            result = tools["trw_deliver"].fn(
                skip_reflect=True,
                allow_unverified=True,
                unverified_reason="test fixture: no build_check recorded for this synthetic run",
            )

        # Summary surfaced on the deliver result.
        assert "nudge_analysis" in result
        summary = result["nudge_analysis"]
        assert summary["applicable"] is True
        assert summary["total_nudges"] == 3
        assert "build_check" in summary["resistance_flagged"]

        # Artifact written to disk.
        artifact = trw_dir / "context" / "nudge-analysis.json"
        assert artifact.exists()
        data = json.loads(artifact.read_text(encoding="utf-8"))
        assert data["schema_version"] == 1
        # deliver_called flips True during deliver, so deliver is NOT resistant
        # even though build_check is.
        assert data["resistance_by_step"].get("build_check") == 3
