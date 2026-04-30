"""Status and persistence wiring ceremony feedback tests."""

from __future__ import annotations

import pytest

from trw_mcp.state.ceremony_feedback import (
    _pending_proposals,
    generate_reduction_proposal,
    get_ceremony_status,
    read_feedback_data,
)

from tests._ceremony_feedback_support import FeedbackEnv, feedback_env, record_sessions


class TestCeremonyStatus:
    """FR08: trw_ceremony_status."""

    def test_status_single_class(self, feedback_env: FeedbackEnv) -> None:
        trw_dir, _ = feedback_env
        record_sessions(trw_dir, [80.0] * 5)
        result = get_ceremony_status(trw_dir, "feature")
        assert len(result["task_classes"]) == 1
        tc_list = result["task_classes"]
        assert isinstance(tc_list, list)
        assert tc_list[0]["session_count"] == 5

    def test_status_all_classes(self, feedback_env: FeedbackEnv) -> None:
        trw_dir, _ = feedback_env
        result = get_ceremony_status(trw_dir)
        assert len(result["task_classes"]) == 5

    def test_status_invalid_class(self, feedback_env: FeedbackEnv) -> None:
        trw_dir, _ = feedback_env
        with pytest.raises(ValueError, match="Invalid task_class"):
            get_ceremony_status(trw_dir, "unknown")


class TestDeEscalationWiring:
    """FIX-051-FR03: Proposals persisted to disk; trw_ceremony_status reads them."""

    def test_ceremony_status_reads_disk_proposals(self, feedback_env: FeedbackEnv) -> None:
        """Proposals written to ceremony-overrides.yaml should appear in get_ceremony_status."""
        trw_dir, _ = feedback_env
        from trw_mcp.state.ceremony_feedback import _overrides_path
        from trw_mcp.state.persistence import FileStateWriter

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
        overrides: dict[str, object] = {"_pending_proposals": {"prop-disk001": fake_proposal}}
        FileStateWriter().write_yaml(_overrides_path(trw_dir), overrides)

        _pending_proposals.clear()

        status = get_ceremony_status(trw_dir, "feature")
        tc_list = status["task_classes"]
        assert isinstance(tc_list, list)
        proposals = tc_list[0]["proposals"]
        assert isinstance(proposals, list)
        proposal_ids = [str(p.get("proposal_id")) for p in proposals]
        assert "prop-disk001" in proposal_ids

    def test_generate_reduction_proposal_with_good_scores(self, feedback_env: FeedbackEnv) -> None:
        """5 sessions with score=85, quality=0.95 at COMPREHENSIVE should yield a proposal."""
        trw_dir, config = feedback_env
        record_sessions(trw_dir, [85.0] * 15, ceremony_tier="COMPREHENSIVE")
        data = read_feedback_data(trw_dir)
        proposal = generate_reduction_proposal("feature", data, config)
        assert proposal is not None
        assert proposal["from_tier"] == "COMPREHENSIVE"
        assert proposal["to_tier"] == "STANDARD"
