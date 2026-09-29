from __future__ import annotations

import pytest

from trw_mcp.models.config import TRWConfig
from trw_mcp.tools._orchestration_phase import _compute_reversion_metrics


class TestComputeReversionMetrics:
    """Direct tests for _compute_reversion_metrics (lines 415-430)."""

    def test_no_events_healthy_zero_rate(self) -> None:
        """Empty events yields healthy classification with zero rate."""
        result = _compute_reversion_metrics([])
        assert result["count"] == 0
        assert result["rate"] == 0.0
        assert result["classification"] == "healthy"
        assert result["latest"] is None
        assert result["by_trigger"] == {}

    def test_trigger_classified_key_used_over_trigger(self) -> None:
        """trigger_classified key takes precedence over trigger in by_trigger."""
        events: list[dict[str, object]] = [
            {
                "event": "phase_revert",
                "trigger_classified": "refactor",
                "trigger": "raw-trigger",
            }
        ]
        result = _compute_reversion_metrics(events)
        assert "refactor" in result["by_trigger"]
        assert "raw-trigger" not in result["by_trigger"]

    def test_trigger_fallback_when_no_trigger_classified(self) -> None:
        """Falls back to 'trigger' when 'trigger_classified' absent."""
        events: list[dict[str, object]] = [
            {"event": "phase_revert", "trigger": "scope-creep"},
        ]
        result = _compute_reversion_metrics(events)
        assert "scope-creep" in result["by_trigger"]

    def test_trigger_defaults_to_other_when_both_absent(self) -> None:
        """Falls back to 'other' when neither trigger key present."""
        events: list[dict[str, object]] = [
            {"event": "phase_revert"},
        ]
        result = _compute_reversion_metrics(events)
        assert "other" in result["by_trigger"]

    def test_concerning_classification(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """High reversion rate classified as 'concerning'."""
        cfg = TRWConfig(reversion_rate_concerning=0.0, reversion_rate_elevated=0.0)
        monkeypatch.setattr("trw_mcp.tools._orchestration_phase.get_config", lambda: cfg)

        events: list[dict[str, object]] = [
            {"event": "phase_revert"},
        ]
        result = _compute_reversion_metrics(events)
        assert result["classification"] == "concerning"

    def test_elevated_classification(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Moderate reversion rate classified as 'elevated'."""
        cfg = TRWConfig(reversion_rate_concerning=0.9, reversion_rate_elevated=0.1)
        monkeypatch.setattr("trw_mcp.tools._orchestration_phase.get_config", lambda: cfg)

        events: list[dict[str, object]] = [
            {"event": "phase_revert"},
            {"event": "phase_enter"},
        ]
        result = _compute_reversion_metrics(events)
        assert result["classification"] == "elevated"

    def test_healthy_classification_default(self) -> None:
        """Zero reverts with default thresholds is 'healthy'."""
        events: list[dict[str, object]] = [
            {"event": "phase_enter"},
            {"event": "phase_enter"},
        ]
        result = _compute_reversion_metrics(events)
        assert result["classification"] == "healthy"

    def test_latest_reversion_populated(self) -> None:
        """latest field contains info from most recent phase_revert event."""
        events: list[dict[str, object]] = [
            {
                "event": "phase_revert",
                "from_phase": "implement",
                "to_phase": "plan",
                "trigger_classified": "refactor",
                "reason": "Found bigger issue",
                "ts": "2026-01-01T00:00:00Z",
            },
            {
                "event": "phase_revert",
                "from_phase": "validate",
                "to_phase": "implement",
                "trigger": "test_failure",
                "reason": "Tests broke",
                "ts": "2026-01-02T00:00:00Z",
            },
        ]
        result = _compute_reversion_metrics(events)
        latest = result["latest"]
        assert latest is not None
        assert latest["from_phase"] == "validate"
        assert latest["to_phase"] == "implement"
        assert latest["reason"] == "Tests broke"

    def test_multiple_triggers_counted(self) -> None:
        """Multiple reverts with same trigger are accumulated."""
        events: list[dict[str, object]] = [
            {"event": "phase_revert", "trigger": "scope"},
            {"event": "phase_revert", "trigger": "scope"},
            {"event": "phase_revert", "trigger": "blocker"},
        ]
        result = _compute_reversion_metrics(events)
        assert result["by_trigger"]["scope"] == 2
        assert result["by_trigger"]["blocker"] == 1
        assert result["count"] == 3

    def test_rate_calculation_with_mixed_events(self) -> None:
        """Rate = revert_count / (revert_count + phase_enter_count)."""
        events: list[dict[str, object]] = [
            {"event": "phase_revert"},
            {"event": "phase_revert"},
            {"event": "phase_enter"},
            {"event": "phase_enter"},
            {"event": "phase_enter"},
        ]
        result = _compute_reversion_metrics(events)
        assert result["rate"] == pytest.approx(0.4, abs=0.001)
        assert result["count"] == 2
