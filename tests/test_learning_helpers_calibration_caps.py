"""Tests for learning helper soft-cap behavior."""

from __future__ import annotations

from tests._learning_helpers_test_support import _CFG, set_project_root  # noqa: F401
from trw_mcp.tools._learning_helpers import check_soft_cap


class TestCheckSoftCap:
    """Tests for distribution soft-cap check."""

    def test_no_cap_when_few_entries(self) -> None:
        """Below 5 active entries, no soft-cap is applied."""
        entries: list[dict[str, object]] = [{"impact": 0.9} for _ in range(3)]
        result_impact, warning = check_soft_cap(0.9, entries, _CFG)
        assert result_impact == 0.9
        assert warning is None

    def test_no_cap_when_within_threshold(self) -> None:
        """When high-impact entries are under threshold, no adjustment."""
        entries: list[dict[str, object]] = [{"impact": 0.3} for _ in range(99)]
        entries.append({"impact": 0.9})
        result_impact, warning = check_soft_cap(0.9, entries, _CFG)
        assert result_impact == 0.9
        assert warning is None

    def test_caps_impact_when_over_threshold(self) -> None:
        """When high-impact entries exceed threshold, impact is reduced."""
        entries: list[dict[str, object]] = [{"impact": 0.9} for _ in range(10)]
        result_impact, warning = check_soft_cap(0.9, entries, _CFG)
        assert result_impact < 0.9
        assert warning is not None
        assert "soft-capped" in warning

    def test_cap_does_not_go_below_05(self) -> None:
        """Floor of 0.5 prevents excessive reduction."""
        entries: list[dict[str, object]] = [{"impact": 0.9} for _ in range(100)]
        result_impact, _warning = check_soft_cap(0.85, entries, _CFG)
        assert result_impact >= 0.5

    def test_no_cap_for_low_impact(self) -> None:
        """Impact below 0.8 is never soft-capped."""
        entries: list[dict[str, object]] = [{"impact": 0.9} for _ in range(10)]
        result_impact, warning = check_soft_cap(0.5, entries, _CFG)
        assert result_impact == 0.5
        assert warning is None

    def test_warning_message_contains_details(self) -> None:
        """Warning message includes counts and threshold."""
        entries: list[dict[str, object]] = [{"impact": 0.9} for _ in range(10)]
        _result_impact, warning = check_soft_cap(0.9, entries, _CFG)
        assert warning is not None
        assert "threshold" in warning
        assert "10" in warning

    def test_fail_open_on_exception(self) -> None:
        """If an exception occurs, returns original impact with no warning."""
        entries: list[dict[str, object]] = [{"impact": "not-a-number"} for _ in range(10)]
        result_impact, warning = check_soft_cap(0.9, entries, _CFG)
        assert result_impact == 0.9
        assert warning is None

    def test_cap_floors_at_05_with_extreme_saturation(self) -> None:
        """Verify the guard exits once the adjusted score drops below 0.8."""
        cfg = _CFG.model_copy(update={"impact_high_threshold_pct": 1})
        entries: list[dict[str, object]] = [{"impact": 0.9} for _ in range(100)]
        result_impact, warning = check_soft_cap(0.81, entries, cfg)
        assert result_impact < 0.81
        assert result_impact >= 0.5
        assert warning is not None
        assert "soft-capped" in warning
