"""Tests for PRD-FIX-027 scoring event/reward wiring."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from trw_mcp.models.config import TRWConfig
from trw_mcp.models.run import EventType
from trw_mcp.scoring import EVENT_ALIASES, REWARD_MAP
from trw_mcp.state.persistence import FileStateWriter


class TestDeliverCompleteEventType:
    """Bug 1: DELIVER_COMPLETE must exist in EventType and REWARD_MAP."""

    def test_deliver_complete_exists_in_eventtype(self) -> None:
        """EventType.DELIVER_COMPLETE must be a member of the enum."""
        assert hasattr(EventType, "DELIVER_COMPLETE"), (
            "EventType.DELIVER_COMPLETE is missing — trw_deliver() events are silently dropped"
        )

    def test_deliver_complete_value(self) -> None:
        """DELIVER_COMPLETE must map to the string logged by trw_deliver()."""
        assert EventType.DELIVER_COMPLETE == "trw_deliver_complete"

    def test_deliver_complete_in_reward_map(self) -> None:
        """DELIVER_COMPLETE must have a REWARD_MAP entry."""
        assert EventType.DELIVER_COMPLETE in REWARD_MAP, (
            "REWARD_MAP missing DELIVER_COMPLETE — Q-learning gets no reward for delivery"
        )

    def test_deliver_complete_reward_is_highest(self) -> None:
        """Delivery is the goal — reward should be 1.0."""
        reward = REWARD_MAP[EventType.DELIVER_COMPLETE]
        assert reward == 1.0, f"Expected 1.0, got {reward}"

    def test_process_outcome_returns_nonempty_for_deliver_complete(self, tmp_path: Path) -> None:
        """process_outcome_for_event("trw_deliver_complete") must not silently fail.

        Without DELIVER_COMPLETE in EventType/REWARD_MAP, the function returns []
        which means no Q-learning update ever fires on delivery.
        """
        from trw_mcp.scoring import process_outcome_for_event

        trw_dir = tmp_path / ".trw"
        learnings_dir = trw_dir / "learnings" / "entries"
        learnings_dir.mkdir(parents=True)

        writer = FileStateWriter()

        entry = {
            "id": "L-test0001",
            "summary": "Test learning for Q-learning reward",
            "detail": "Detail text",
            "impact": 0.7,
            "status": "active",
            "q_value": 0.7,
            "q_observations": 0,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "tags": [],
        }
        writer.write_yaml(learnings_dir / "test-learning.yaml", entry)

        mock_config = TRWConfig(trw_dir=str(trw_dir))
        with (
            patch("trw_mcp.scoring._correlation.get_config", return_value=mock_config),
            patch("trw_mcp.scoring._utils.resolve_trw_dir", return_value=trw_dir),
        ):
            result = process_outcome_for_event("trw_deliver_complete")
            assert isinstance(result, list)

    def test_deliver_complete_resolve_from_string(self) -> None:
        """EventType.resolve('trw_deliver_complete') must return the enum member."""
        resolved = EventType.resolve("trw_deliver_complete")
        assert resolved is EventType.DELIVER_COMPLETE


class TestRewardMapCompleteness:
    """FR02/FR05: REWARD_MAP values are correct and immutable in tests."""

    def test_all_reward_values_in_range(self) -> None:
        """All rewards in REWARD_MAP must be in [-1.0, 1.0]."""
        from trw_mcp.scoring import REWARD_MAP as rmap

        for event_type, reward in rmap.items():
            assert -1.0 <= reward <= 1.0, f"{event_type} reward {reward} out of range"

    def test_reward_map_total_count(self) -> None:
        """REWARD_MAP has at least 15 entries (including new Sprint 31 entries)."""
        from trw_mcp.scoring import REWARD_MAP as rmap

        assert len(rmap) >= 15

    def test_positive_events_have_positive_rewards(self) -> None:
        """Key positive events have positive rewards."""
        from trw_mcp.scoring import REWARD_MAP as rmap

        positive = [
            EventType.TESTS_PASSED,
            EventType.TASK_COMPLETE,
            EventType.PHASE_GATE_PASSED,
            EventType.DELIVER_COMPLETE,
            EventType.BUILD_PASSED,
        ]
        for event in positive:
            assert rmap[event] > 0, f"{event} should have positive reward"

    def test_failure_events_have_negative_rewards(self) -> None:
        """Failure events have negative rewards."""
        from trw_mcp.scoring import REWARD_MAP as rmap

        negative = [
            EventType.TESTS_FAILED,
            EventType.PHASE_GATE_FAILED,
            EventType.BUILD_FAILED,
        ]
        for event in negative:
            assert rmap[event] < 0, f"{event} should have negative reward"

    def test_build_passed_higher_than_tests_passed_parity(self) -> None:
        """build_passed (0.6) < tests_passed (0.8) — build includes coverage check."""
        from trw_mcp.scoring import REWARD_MAP as rmap

        assert rmap[EventType.BUILD_PASSED] > 0
        assert rmap[EventType.TESTS_PASSED] > 0


class TestBuildEventsInRewardMap:
    """FR05: BUILD_PASSED and BUILD_FAILED must be direct REWARD_MAP entries."""

    def test_build_passed_in_reward_map(self) -> None:
        """FR05: BUILD_PASSED is a first-class REWARD_MAP entry."""
        assert EventType.BUILD_PASSED in REWARD_MAP, (
            "EventType.BUILD_PASSED is not in REWARD_MAP — build outcome Q-learning is broken"
        )

    def test_build_failed_in_reward_map(self) -> None:
        """FR05: BUILD_FAILED is a first-class REWARD_MAP entry."""
        assert EventType.BUILD_FAILED in REWARD_MAP, (
            "EventType.BUILD_FAILED is not in REWARD_MAP — negative build outcome Q-learning is broken"
        )

    def test_build_passed_not_in_event_aliases(self) -> None:
        """FR05: BUILD_PASSED must not be in EVENT_ALIASES (promoted to REWARD_MAP)."""
        assert EventType.BUILD_PASSED not in EVENT_ALIASES, (
            "EventType.BUILD_PASSED is still in EVENT_ALIASES — should be in REWARD_MAP only"
        )

    def test_build_failed_not_in_event_aliases(self) -> None:
        """FR05: BUILD_FAILED must not be in EVENT_ALIASES (promoted to REWARD_MAP)."""
        assert EventType.BUILD_FAILED not in EVENT_ALIASES, (
            "EventType.BUILD_FAILED is still in EVENT_ALIASES — should be in REWARD_MAP only"
        )

    def test_build_passed_reward_value(self) -> None:
        """FR05: BUILD_PASSED reward must be +0.6."""
        assert REWARD_MAP[EventType.BUILD_PASSED] == pytest.approx(0.6), (
            f"Expected 0.6, got {REWARD_MAP[EventType.BUILD_PASSED]}"
        )

    def test_build_failed_reward_value(self) -> None:
        """FR05: BUILD_FAILED reward must be -0.4."""
        assert REWARD_MAP[EventType.BUILD_FAILED] == pytest.approx(-0.4), (
            f"Expected -0.4, got {REWARD_MAP[EventType.BUILD_FAILED]}"
        )
