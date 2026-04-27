"""PRD-CORE-144 FR04: capture learning_ids in session_metrics.learning_exposure."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from trw_mcp.state.surface_tracking import log_surface_event
from trw_mcp.tools._deferred_steps_learning import _step_delivery_metrics


class TestLearningIdsCapture:
    def test_captures_unique_ids_in_exposure_block(self, tmp_path: Path) -> None:
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        # Three distinct learnings surfaced in the current session
        for lid in ("L-a", "L-b", "L-c"):
            log_surface_event(trw_dir, learning_id=lid, surface_type="recall", session_id="my-session")

        with patch.dict("os.environ", {"TRW_SESSION_ID": "my-session"}):
            result = _step_delivery_metrics(trw_dir, None)

        exposure = result.get("learning_exposure")
        assert isinstance(exposure, dict)
        assert "ids" in exposure
        ids = exposure["ids"]
        assert isinstance(ids, list)
        assert set(ids) == {"L-a", "L-b", "L-c"}

    def test_deduplicates_repeated_surfaces(self, tmp_path: Path) -> None:
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        # Same learning surfaced twice
        log_surface_event(trw_dir, learning_id="L-dup", surface_type="nudge", session_id="my-session")
        log_surface_event(trw_dir, learning_id="L-dup", surface_type="recall", session_id="my-session")

        with patch.dict("os.environ", {"TRW_SESSION_ID": "my-session"}):
            result = _step_delivery_metrics(trw_dir, None)

        exposure = result.get("learning_exposure")
        assert isinstance(exposure, dict)
        assert exposure["ids"] == ["L-dup"]

    def test_empty_session_produces_empty_ids_list(self, tmp_path: Path) -> None:
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        # No surface events at all
        with patch.dict("os.environ", {"TRW_SESSION_ID": "empty-session"}):
            result = _step_delivery_metrics(trw_dir, None)

        exposure = result.get("learning_exposure")
        assert isinstance(exposure, dict)
        # ids key MUST be present (not missing), as an empty list
        assert "ids" in exposure
        assert exposure["ids"] == []

    def test_ids_scoped_to_current_session_only(self, tmp_path: Path) -> None:
        """Other sessions' learnings must not leak into this session's ids."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        log_surface_event(trw_dir, learning_id="mine", surface_type="recall", session_id="me")
        log_surface_event(trw_dir, learning_id="theirs", surface_type="recall", session_id="someone-else")

        with patch.dict("os.environ", {"TRW_SESSION_ID": "me"}):
            result = _step_delivery_metrics(trw_dir, None)

        exposure = result.get("learning_exposure")
        assert isinstance(exposure, dict)
        assert exposure["ids"] == ["mine"]
