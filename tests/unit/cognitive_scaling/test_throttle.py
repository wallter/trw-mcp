"""Unit tests for PRD-SCALE-001 Scout anti-inflation throttle (FR14)."""

from __future__ import annotations

from trw_mcp.cognitive_scaling.scout import evaluate_throttle
from trw_mcp.models.cognitive_scaling import PlanningMode


def test_empty_stream_never_over_cap() -> None:
    """FR14: zero samples cannot infer inflation."""
    d = evaluate_throttle([], cap=0.15)
    assert d.over_cap is False
    assert d.sample_size == 0
    assert d.mode3_rate == 0.0


def test_rate_below_cap_not_over() -> None:
    """FR14: 1/10 mode-3 = 0.10 <= 0.15 cap -> not over."""
    modes = [PlanningMode.DIRECT] * 9 + [PlanningMode.TRIANGULATED_WITH_PROBE]
    d = evaluate_throttle(modes, cap=0.15)
    assert d.mode3_rate == 0.1
    assert d.over_cap is False


def test_rate_above_cap_is_over() -> None:
    """FR14: 3/10 mode-3 = 0.30 > 0.15 cap -> over (warning trigger)."""
    modes = [PlanningMode.DIRECT] * 7 + [PlanningMode.TRIANGULATED_WITH_PROBE] * 3
    d = evaluate_throttle(modes, cap=0.15)
    assert d.mode3_rate == 0.3
    assert d.over_cap is True
    assert d.enforcement_active is False  # Sprint-97 scaffold: warn only


def test_accepts_int_modes() -> None:
    """FR14: stream may carry raw ints (telemetry codes)."""
    d = evaluate_throttle([0, 0, 3, 3], cap=0.15)
    assert d.mode3_rate == 0.5
    assert d.over_cap is True
