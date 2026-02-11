"""Sprint 8: Signal Path Repair tests.

Covers EventType enum completeness, TRWConfig scoring field defaults,
REWARD_MAP/EVENT_ALIASES structure, _resolve_event_reward config-driven
routing, _days_since_access fallback, source attribution in LLM extraction,
and _auto_sync_index error handling.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from unittest.mock import patch

import pytest

from trw_mcp.models.config import TRWConfig
from trw_mcp.models.run import EventType
from trw_mcp.scoring import (
    EVENT_ALIASES,
    REWARD_MAP,
    _days_since_access,
    _resolve_event_reward,
)


class TestEventType:
    """EventType enum completeness and resolve() method."""

    def test_all_reward_map_keys_are_event_types(self) -> None:
        for key in REWARD_MAP:
            assert EventType.resolve(key) is not None, (
                f"REWARD_MAP key {key!r} is not a valid EventType member"
            )

    def test_all_event_alias_keys_are_event_types(self) -> None:
        for key in EVENT_ALIASES:
            assert EventType.resolve(key) is not None, (
                f"EVENT_ALIASES key {key!r} is not a valid EventType member"
            )

    def test_resolve_known_event(self) -> None:
        assert EventType.resolve("run_init") == EventType.RUN_INIT

    def test_resolve_unknown_event(self) -> None:
        assert EventType.resolve("nonexistent_event_xyz") is None

    def test_event_type_is_str_enum(self) -> None:
        """EventType members must be usable as strings for YAML serialization."""
        assert EventType.RUN_INIT == "run_init"
        assert EventType.PHASE_GATE_PASSED == "phase_gate_passed"

    def test_phase_enter_event_exists(self) -> None:
        """PHASE_ENTER is required by orchestration tools."""
        assert EventType.PHASE_ENTER == "phase_enter"

    def test_data_aware_event_types_exist(self) -> None:
        """Events used by data-aware routing must exist in the enum."""
        assert EventType.TEST_RUN == "test_run"
        assert EventType.PRD_STATUS_CHANGE == "prd_status_change"
        assert EventType.COMPLIANCE_CHECK == "compliance_check"

    def test_alias_targets_exist_in_reward_map(self) -> None:
        """Every string alias target must resolve to a REWARD_MAP key."""
        for key, alias in EVENT_ALIASES.items():
            if isinstance(alias, str):
                assert alias in REWARD_MAP, (
                    f"EVENT_ALIASES[{key!r}] -> {alias!r} not in REWARD_MAP"
                )


class TestConfigScoringFields:
    """TRWConfig scoring fields have correct defaults."""

    @pytest.fixture(autouse=True)
    def _config(self) -> None:
        self.cfg = TRWConfig()

    def test_scoring_default_days_unused(self) -> None:
        assert self.cfg.scoring_default_days_unused == 30

    def test_scoring_error_keywords(self) -> None:
        expected = {"error", "fail", "exception", "crash", "timeout"}
        assert expected.issubset(set(self.cfg.scoring_error_keywords))

    def test_scoring_recency_discount_floor(self) -> None:
        assert self.cfg.scoring_recency_discount_floor == 0.5

    def test_scoring_error_fallback_reward(self) -> None:
        assert self.cfg.scoring_error_fallback_reward == -0.3

    def test_index_auto_sync_enabled_by_default(self) -> None:
        assert self.cfg.index_auto_sync_on_status_change is True


class TestRewardMaps:
    """REWARD_MAP and EVENT_ALIASES structural invariants."""

    def test_reward_map_values_are_numeric(self) -> None:
        for key, val in REWARD_MAP.items():
            assert isinstance(val, (int, float)), (
                f"REWARD_MAP[{key!r}] = {val!r} is not numeric"
            )

    def test_event_aliases_values_are_valid(self) -> None:
        """Alias values must be str, float, or None."""
        for key, val in EVENT_ALIASES.items():
            assert val is None or isinstance(val, (str, int, float)), (
                f"EVENT_ALIASES[{key!r}] = {val!r} has invalid type"
            )

    def test_reward_map_has_minimum_entries(self) -> None:
        """PRD-CORE-026 requires at least 12 REWARD_MAP entries."""
        assert len(REWARD_MAP) >= 12

    def test_event_aliases_has_minimum_entries(self) -> None:
        """EVENT_ALIASES must cover all known lifecycle events (>= 18)."""
        assert len(EVENT_ALIASES) >= 18


class TestResolveEventReward:
    """_resolve_event_reward uses config values instead of magic numbers."""

    def test_direct_reward_map_hit(self) -> None:
        reward, label = _resolve_event_reward("tests_passed")
        assert reward == 0.8
        assert label == EventType.TESTS_PASSED

    def test_alias_to_reward_map(self) -> None:
        reward, label = _resolve_event_reward("shard_completed")
        assert reward == 0.6  # shard_complete reward
        assert label == EventType.SHARD_COMPLETE

    def test_explicit_none_alias(self) -> None:
        """Events with explicit None alias return (None, event_type)."""
        reward, _label = _resolve_event_reward("run_init")
        assert reward is None

    def test_direct_float_alias(self) -> None:
        reward, label = _resolve_event_reward("phase_revert")
        assert reward == -0.3
        assert label == EventType.PHASE_REVERT

    def test_data_aware_test_run_passed(self) -> None:
        reward, label = _resolve_event_reward("test_run", {"passed": True})
        assert reward == 0.8
        assert label == EventType.TESTS_PASSED

    def test_data_aware_test_run_failed(self) -> None:
        reward, label = _resolve_event_reward("test_run", {"passed": False})
        assert reward == -0.3
        assert label == EventType.TESTS_FAILED

    def test_data_aware_prd_approved(self) -> None:
        reward, _label = _resolve_event_reward(
            "prd_status_change", {"new_status": "approved"},
        )
        assert reward == 0.7

    def test_data_aware_compliance_passed(self) -> None:
        reward, _label = _resolve_event_reward(
            "compliance_check", {"score": 0.9},
        )
        assert reward == 0.5

    def test_error_keyword_fallback_uses_config(self) -> None:
        """Unknown events containing error keywords use the config fallback reward."""
        reward, label = _resolve_event_reward("custom_error_event")
        assert reward == -0.3  # scoring_error_fallback_reward default
        assert label == "custom_error_event"

    def test_completely_unknown_event_returns_none(self) -> None:
        """Events with no match and no error keywords return None."""
        reward, _label = _resolve_event_reward("happy_custom_event_xyz")
        assert reward is None


class TestDaysSinceAccess:
    """_days_since_access uses config for default fallback."""

    def test_fallback_uses_config_default(self) -> None:
        """When no dates are available, falls back to config default (30)."""
        result = _days_since_access({}, date.today())
        assert result == 30

    def test_explicit_fallback_overrides_config(self) -> None:
        result = _days_since_access({}, date.today(), fallback_days=7)
        assert result == 7

    def test_last_accessed_at_takes_priority(self) -> None:
        entry: dict[str, object] = {"last_accessed_at": "2026-02-05"}
        result = _days_since_access(entry, date(2026, 2, 10))
        assert result == 5


class TestSourceAttribution:
    """Source identity is populated in the LLM extraction path."""

    def test_llm_extraction_sets_source_identity(self, tmp_project: Path) -> None:
        from trw_mcp.state.analytics import extract_learnings_from_llm, find_entry_by_id

        trw_dir = tmp_project / ".trw"
        llm_items = [
            {"summary": "Test learning", "detail": "Detail text", "impact": 0.7},
        ]
        result = extract_learnings_from_llm(llm_items, trw_dir)
        assert len(result) == 1

        entries_dir = trw_dir / "learnings" / "entries"
        found = find_entry_by_id(entries_dir, result[0]["id"])
        assert found is not None
        _, data = found
        assert data["source_type"] == "agent"
        assert data["source_identity"] == "trw_reflect:llm"


class TestAutoSyncIndex:
    """_auto_sync_index handles errors gracefully."""

    def test_returns_false_on_error(self) -> None:
        """When project root resolution fails, returns False without raising."""
        from trw_mcp.tools.requirements import _auto_sync_index

        with patch(
            "trw_mcp.tools.requirements.resolve_project_root",
            side_effect=Exception("no project"),
        ):
            assert _auto_sync_index() is False
