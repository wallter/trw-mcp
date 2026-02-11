"""Tests for PRD-CORE-026: EVENT_ALIASES, signal repair, source attribution.

Covers:
- EVENT_ALIASES resolution
- Expanded REWARD_MAP entries
- process_outcome_for_event() alias + data-aware routing
- Session-scoped correlation
- access_count utility boost
- source_type utility boost
- LearningEntry source fields
"""

from __future__ import annotations

import json
import math
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from trw_mcp.models.learning import LearningEntry
from trw_mcp.scoring import (
    EVENT_ALIASES,
    REWARD_MAP,
    _resolve_event_reward,
    compute_utility_score,
    correlate_recalls,
    process_outcome_for_event,
)


# --- REWARD_MAP expansion tests ---


class TestRewardMapExpansion:
    """PRD-CORE-026-FR02: REWARD_MAP expanded from 6 to 12+ entries."""

    def test_original_entries_preserved(self) -> None:
        """All 6 original REWARD_MAP entries remain unchanged."""
        assert REWARD_MAP["tests_passed"] == 0.8
        assert REWARD_MAP["tests_failed"] == -0.3
        assert REWARD_MAP["task_complete"] == 0.5
        assert REWARD_MAP["phase_gate_passed"] == 1.0
        assert REWARD_MAP["phase_gate_failed"] == -0.5
        assert REWARD_MAP["wave_validation_passed"] == 0.7

    def test_new_entries_exist(self) -> None:
        """New entries added per PRD-CORE-026-FR02."""
        assert "shard_complete" in REWARD_MAP
        assert "reflection_complete" in REWARD_MAP
        assert "compliance_passed" in REWARD_MAP
        assert "file_modified" in REWARD_MAP
        assert "prd_approved" in REWARD_MAP
        assert "wave_complete" in REWARD_MAP

    def test_reward_map_has_at_least_12(self) -> None:
        assert len(REWARD_MAP) >= 12


# --- EVENT_ALIASES tests ---


class TestEventAliases:
    """PRD-CORE-026-FR01: EVENT_ALIASES mapping."""

    def test_shard_completed_aliases_to_shard_complete(self) -> None:
        assert EVENT_ALIASES["shard_completed"] == "shard_complete"

    def test_wave_validated_aliases_to_wave_validation_passed(self) -> None:
        assert EVENT_ALIASES["wave_validated"] == "wave_validation_passed"

    def test_wave_completed_aliases_to_wave_complete(self) -> None:
        assert EVENT_ALIASES["wave_completed"] == "wave_complete"

    def test_run_init_is_none(self) -> None:
        """run_init should be explicitly ignored (no reward)."""
        assert EVENT_ALIASES["run_init"] is None

    def test_shard_started_is_none(self) -> None:
        assert EVENT_ALIASES["shard_started"] is None

    def test_phase_revert_is_negative_float(self) -> None:
        """phase_revert maps to a direct negative reward."""
        assert EVENT_ALIASES["phase_revert"] == -0.3

    def test_checkpoint_is_small_positive(self) -> None:
        assert EVENT_ALIASES["checkpoint"] == 0.1

    def test_aliases_has_at_least_15_entries(self) -> None:
        assert len(EVENT_ALIASES) >= 15


# --- _resolve_event_reward tests ---


class TestResolveEventReward:
    """PRD-CORE-026-FR03: alias resolution + data-aware routing."""

    def test_direct_reward_map_match(self) -> None:
        reward, label = _resolve_event_reward("tests_passed")
        assert reward == 0.8
        assert label == "tests_passed"

    def test_alias_to_reward_map_key(self) -> None:
        reward, label = _resolve_event_reward("shard_completed")
        assert reward == REWARD_MAP["shard_complete"]
        assert label == "shard_complete"

    def test_alias_direct_float(self) -> None:
        reward, label = _resolve_event_reward("phase_revert")
        assert reward == -0.3
        assert label == "phase_revert"

    def test_alias_none_returns_none(self) -> None:
        """Explicit None alias = no reward."""
        reward, _label = _resolve_event_reward("run_init")
        assert reward is None

    def test_unknown_event_no_error_keywords(self) -> None:
        reward, _label = _resolve_event_reward("some_random_event")
        assert reward is None

    def test_error_keyword_fallback(self) -> None:
        reward, _label = _resolve_event_reward("shard_timeout_error")
        assert reward == -0.3

    def test_data_aware_test_run_passed(self) -> None:
        reward, label = _resolve_event_reward(
            "test_run", {"passed": True},
        )
        assert reward == 0.8
        assert label == "tests_passed"

    def test_data_aware_test_run_failed(self) -> None:
        reward, label = _resolve_event_reward(
            "test_run", {"passed": False},
        )
        assert reward == -0.3
        assert label == "tests_failed"

    def test_data_aware_prd_status_approved(self) -> None:
        reward, label = _resolve_event_reward(
            "prd_status_change", {"new_status": "approved"},
        )
        assert reward == REWARD_MAP["prd_approved"]
        assert label == "prd_approved"

    def test_data_aware_compliance_high_score(self) -> None:
        reward, label = _resolve_event_reward(
            "compliance_check", {"score": 0.9},
        )
        assert reward == REWARD_MAP["compliance_passed"]
        assert label == "compliance_passed"

    def test_data_aware_compliance_low_score(self) -> None:
        reward, _label = _resolve_event_reward(
            "compliance_check", {"score": 0.5},
        )
        assert reward is None


# --- access_count utility boost tests ---


class TestAccessCountBoost:
    """PRD-CORE-026-FR05: access_count utility boost."""

    def test_zero_access_no_boost(self) -> None:
        score_zero = compute_utility_score(
            q_value=0.5, days_since_last_access=0,
            recurrence_count=1, base_impact=0.5, q_observations=5,
            access_count=0,
        )
        score_some = compute_utility_score(
            q_value=0.5, days_since_last_access=0,
            recurrence_count=1, base_impact=0.5, q_observations=5,
            access_count=10,
        )
        assert score_some > score_zero

    def test_boost_is_sub_linear(self) -> None:
        """Boost from 10->100 should be less than 10x boost from 1->10."""
        score_1 = compute_utility_score(
            q_value=0.5, days_since_last_access=0,
            recurrence_count=1, base_impact=0.5, q_observations=5,
            access_count=1,
        )
        score_10 = compute_utility_score(
            q_value=0.5, days_since_last_access=0,
            recurrence_count=1, base_impact=0.5, q_observations=5,
            access_count=10,
        )
        score_100 = compute_utility_score(
            q_value=0.5, days_since_last_access=0,
            recurrence_count=1, base_impact=0.5, q_observations=5,
            access_count=100,
        )
        delta_1_10 = score_10 - score_1
        delta_10_100 = score_100 - score_10
        assert delta_10_100 < delta_1_10

    def test_boost_caps_at_015(self) -> None:
        """Even with very high access_count, boost should not exceed 0.15."""
        score_base = compute_utility_score(
            q_value=0.5, days_since_last_access=0,
            recurrence_count=1, base_impact=0.5, q_observations=5,
            access_count=0, access_count_boost_cap=0.15,
        )
        score_huge = compute_utility_score(
            q_value=0.5, days_since_last_access=0,
            recurrence_count=1, base_impact=0.5, q_observations=5,
            access_count=1_000_000, access_count_boost_cap=0.15,
        )
        assert (score_huge - score_base) <= 0.15 + 1e-9


# --- source_type utility boost tests ---


class TestSourceTypeBoost:
    """PRD-CORE-026-FR06: source_type utility boost."""

    def test_human_gets_boost(self) -> None:
        agent_score = compute_utility_score(
            q_value=0.5, days_since_last_access=0,
            recurrence_count=1, base_impact=0.5, q_observations=5,
            source_type="agent",
        )
        human_score = compute_utility_score(
            q_value=0.5, days_since_last_access=0,
            recurrence_count=1, base_impact=0.5, q_observations=5,
            source_type="human",
        )
        assert human_score > agent_score
        assert abs(human_score - agent_score - 0.1) < 1e-9

    def test_agent_no_boost(self) -> None:
        score_default = compute_utility_score(
            q_value=0.5, days_since_last_access=0,
            recurrence_count=1, base_impact=0.5, q_observations=5,
        )
        score_agent = compute_utility_score(
            q_value=0.5, days_since_last_access=0,
            recurrence_count=1, base_impact=0.5, q_observations=5,
            source_type="agent",
        )
        assert score_default == score_agent


# --- LearningEntry source attribution tests ---


class TestLearningEntrySourceFields:
    """PRD-CORE-026-FR07: source_type and source_identity on LearningEntry."""

    def test_defaults(self) -> None:
        entry = LearningEntry(
            id="L-test001",
            summary="test",
            detail="test detail",
        )
        assert entry.source_type == "agent"
        assert entry.source_identity == ""

    def test_human_source(self) -> None:
        entry = LearningEntry(
            id="L-test002",
            summary="test",
            detail="test detail",
            source_type="human",
            source_identity="Tyler",
        )
        assert entry.source_type == "human"
        assert entry.source_identity == "Tyler"

    def test_serialization_roundtrip(self) -> None:
        entry = LearningEntry(
            id="L-test003",
            summary="roundtrip",
            detail="test",
            source_type="human",
            source_identity="claude-opus-4-6",
        )
        data = json.loads(entry.model_dump_json())
        assert data["source_type"] == "human"
        assert data["source_identity"] == "claude-opus-4-6"


# --- Session-scoped correlation tests ---


class TestSessionScopedCorrelation:
    """PRD-CORE-026-FR04: session-scoped correlation."""

    def test_window_scope_filters_by_minutes(
        self, tmp_path: Path,
    ) -> None:
        """Window scope should only find receipts within N minutes."""
        trw_dir = tmp_path / ".trw"
        receipt_dir = trw_dir / "learnings" / "receipts"
        receipt_dir.mkdir(parents=True)

        now = datetime.now(timezone.utc)
        old_ts = (now - timedelta(hours=5)).isoformat()
        recent_ts = (now - timedelta(minutes=10)).isoformat()

        receipt_path = receipt_dir / "recall_log.jsonl"
        with receipt_path.open("w") as f:
            f.write(json.dumps({
                "ts": old_ts, "matched_ids": ["L-old"],
            }) + "\n")
            f.write(json.dumps({
                "ts": recent_ts, "matched_ids": ["L-recent"],
            }) + "\n")

        results = correlate_recalls(trw_dir, window_minutes=30, scope="window")
        ids = [lid for lid, _ in results]
        assert "L-recent" in ids
        assert "L-old" not in ids

    def test_session_scope_finds_all_since_start(
        self, tmp_path: Path,
    ) -> None:
        """Session scope should find all receipts since last run_init."""
        trw_dir = tmp_path / ".trw"
        receipt_dir = trw_dir / "learnings" / "receipts"
        receipt_dir.mkdir(parents=True)

        # Create a run with run_init 2 hours ago
        now = datetime.now(timezone.utc)
        run_init_ts = (now - timedelta(hours=2)).isoformat()
        run_dir = tmp_path / "docs" / "task" / "runs" / "20260210T100000Z-test"
        meta_dir = run_dir / "meta"
        meta_dir.mkdir(parents=True)
        events_path = meta_dir / "events.jsonl"
        with events_path.open("w") as f:
            f.write(json.dumps({
                "ts": run_init_ts, "event": "run_init",
            }) + "\n")

        # Receipt from 90 min ago (within session, outside 30-min window)
        receipt_ts = (now - timedelta(minutes=90)).isoformat()
        receipt_path = receipt_dir / "recall_log.jsonl"
        with receipt_path.open("w") as f:
            f.write(json.dumps({
                "ts": receipt_ts, "matched_ids": ["L-session"],
            }) + "\n")

        # With window scope, 30 min window would miss this
        window_results = correlate_recalls(
            trw_dir, window_minutes=30, scope="window",
        )
        window_ids = [lid for lid, _ in window_results]
        assert "L-session" not in window_ids

        # With session scope, should find it
        session_results = correlate_recalls(
            trw_dir, window_minutes=30, scope="session",
        )
        session_ids = [lid for lid, _ in session_results]
        assert "L-session" in session_ids

    def test_session_scope_fallback_to_window(
        self, tmp_path: Path,
    ) -> None:
        """When no session boundary found, fall back to window."""
        trw_dir = tmp_path / ".trw"
        receipt_dir = trw_dir / "learnings" / "receipts"
        receipt_dir.mkdir(parents=True)

        now = datetime.now(timezone.utc)
        recent_ts = (now - timedelta(minutes=5)).isoformat()

        receipt_path = receipt_dir / "recall_log.jsonl"
        with receipt_path.open("w") as f:
            f.write(json.dumps({
                "ts": recent_ts, "matched_ids": ["L-fallback"],
            }) + "\n")

        results = correlate_recalls(
            trw_dir, window_minutes=240, scope="session",
        )
        ids = [lid for lid, _ in results]
        assert "L-fallback" in ids


# --- Backward compatibility tests ---


class TestBackwardCompatibility:
    """Verify existing behavior is preserved."""

    def test_compute_utility_score_without_new_params(self) -> None:
        """Calling without access_count/source_type still works."""
        score = compute_utility_score(
            q_value=0.5,
            days_since_last_access=0,
            recurrence_count=1,
            base_impact=0.5,
            q_observations=5,
        )
        assert 0.0 <= score <= 1.0

    def test_process_outcome_for_event_without_data(self) -> None:
        """Calling without event_data still works (backward compat)."""
        # Should not raise even without event_data
        result = process_outcome_for_event("some_unknown_event")
        assert isinstance(result, list)

    def test_existing_reward_map_events_still_work(self) -> None:
        """Direct REWARD_MAP events still resolve correctly."""
        reward, label = _resolve_event_reward("tests_passed")
        assert reward == 0.8

    def test_error_keyword_fallback_still_works(self) -> None:
        reward, _label = _resolve_event_reward("unknown_failure")
        assert reward == -0.3
