"""PRD-CORE-144 FR03: wall-clock cap on nudge pool cooldown."""

from __future__ import annotations

import datetime as dt

import pytest

from trw_mcp.models.config._client_profile import NudgePoolWeights
from trw_mcp.state._ceremony_progress_state import CeremonyState
from trw_mcp.state._nudge_rules import (
    _select_nudge_pool,
    apply_pool_cooldown,
    is_pool_in_cooldown,
)


def _state_with_cooldown(pool: str, hours_ago: float) -> CeremonyState:
    state = CeremonyState()
    state.tool_call_counter = 5
    state.pool_cooldown_until[pool] = 100  # still in tool-call cooldown
    entered = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=hours_ago)
    state.pool_cooldown_set_at[pool] = entered.isoformat()
    return state


class TestWallClockCap:
    def test_pool_still_cooled_within_cap(self) -> None:
        state = _state_with_cooldown("learnings", hours_ago=3.0)
        assert is_pool_in_cooldown(state, "learnings", wall_clock_max_hours=24) is True

    def test_pool_force_expired_after_cap(self) -> None:
        state = _state_with_cooldown("learnings", hours_ago=25.0)
        assert is_pool_in_cooldown(state, "learnings", wall_clock_max_hours=24) is False
        # State was reset in place
        assert state.pool_cooldown_until.get("learnings", 0) == 0
        assert "learnings" not in state.pool_cooldown_set_at

    def test_missing_set_at_treated_as_never_cooled(self) -> None:
        """NFR03: missing pool_cooldown_set_at must NOT be treated as 'always cooled'."""
        state = CeremonyState()
        state.tool_call_counter = 5
        state.pool_cooldown_until["learnings"] = 100
        # no pool_cooldown_set_at entry
        # Falls through to legacy tool-call check (still in cooldown).
        assert is_pool_in_cooldown(state, "learnings", wall_clock_max_hours=24) is True

    def test_corrupt_timestamp_treated_as_expired(self) -> None:
        state = CeremonyState()
        state.tool_call_counter = 5
        state.pool_cooldown_until["learnings"] = 100
        state.pool_cooldown_set_at["learnings"] = "not-a-real-timestamp"
        assert is_pool_in_cooldown(state, "learnings", wall_clock_max_hours=24) is False
        assert "learnings" not in state.pool_cooldown_set_at

    def test_apply_pool_cooldown_stamps_set_at(self) -> None:
        state = CeremonyState()
        state.tool_call_counter = 0
        state.pool_ignore_counts["learnings"] = 5
        activated = apply_pool_cooldown(state, "learnings", cooldown_after=3, cooldown_calls=10)
        assert activated is True
        assert "learnings" in state.pool_cooldown_set_at
        # Stamp parses as ISO-8601 with tz
        parsed = dt.datetime.fromisoformat(state.pool_cooldown_set_at["learnings"])
        assert parsed.tzinfo is not None

    def test_rotation_still_eligible_for_non_cooled_pools(self) -> None:
        """Normal rotation behavior preserved for pools not in cooldown."""
        state = CeremonyState()
        state.tool_call_counter = 0
        weights = NudgePoolWeights(workflow=40, learnings=30, ceremony=20, context=10)
        selected = _select_nudge_pool(state, weights)
        assert selected in {"workflow", "learnings", "ceremony", "context"}


class TestDefaultConfig:
    def test_config_default_is_24_hours(self) -> None:
        from trw_mcp.models.config import TRWConfig

        cfg = TRWConfig()
        assert cfg.nudge_pool_cooldown_wall_clock_max_hours == 24

    @pytest.mark.parametrize("hours", [1, 6, 720])
    def test_config_range(self, hours: int) -> None:
        from trw_mcp.models.config import TRWConfig

        cfg = TRWConfig(nudge_pool_cooldown_wall_clock_max_hours=hours)
        assert cfg.nudge_pool_cooldown_wall_clock_max_hours == hours
