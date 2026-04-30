"""Coverage-targeted reversion metrics tests for trw_mcp/tools/orchestration.py."""

from __future__ import annotations

from trw_mcp.tools.orchestration import _compute_reversion_metrics


class TestComputeReversionMetrics:
    """Lines 415-416, 420, 422, 429-430: reversion classification and latest fields."""

    def test_empty_events_returns_healthy(self) -> None:
        """No events → rate=0 → classification=healthy, latest=None."""
        result = _compute_reversion_metrics([])

        assert result["count"] == 0
        assert result["rate"] == 0.0
        assert result["classification"] == "healthy"
        assert result["latest"] is None
        assert result["by_trigger"] == {}

    def test_healthy_classification_below_elevated(self) -> None:
        """Rate below elevated threshold gives classification=healthy."""
        revert_event: dict[str, object] = {
            "event": "phase_revert",
            "trigger_classified": "blocker",
            "from_phase": "implement",
            "to_phase": "plan",
            "reason": "found bug",
            "ts": "2026-01-01T00:00:00Z",
        }
        enter_event: dict[str, object] = {"event": "phase_enter"}
        events: list[dict[str, object]] = [revert_event] + [enter_event for _ in range(9)]
        result = _compute_reversion_metrics(events)

        assert result["classification"] == "healthy"
        assert result["count"] == 1

    def test_elevated_classification_between_thresholds(self) -> None:
        """Rate between elevated (0.15) and concerning (0.30) → classification=elevated."""
        revert1: dict[str, object] = {
            "event": "phase_revert",
            "trigger_classified": "scope_change",
            "from_phase": "implement",
            "to_phase": "plan",
            "reason": "r1",
            "ts": "2026-01-01T00:00:00Z",
        }
        revert2: dict[str, object] = {
            "event": "phase_revert",
            "trigger_classified": "blocker",
            "from_phase": "validate",
            "to_phase": "implement",
            "reason": "r2",
            "ts": "2026-01-02T00:00:00Z",
        }
        enter_event: dict[str, object] = {"event": "phase_enter"}
        events: list[dict[str, object]] = [revert1, revert2] + [enter_event for _ in range(10)]
        result = _compute_reversion_metrics(events)

        assert result["classification"] == "elevated"
        assert result["count"] == 2

    def test_concerning_classification_above_threshold(self) -> None:
        """Rate >= concerning threshold (0.30) → classification=concerning (line 420)."""
        revert: dict[str, object] = {
            "event": "phase_revert",
            "trigger_classified": "scope_change",
            "from_phase": "implement",
            "to_phase": "plan",
            "reason": "oops",
            "ts": "2026-01-01T00:00:00Z",
        }
        enter: dict[str, object] = {"event": "phase_enter"}
        events: list[dict[str, object]] = [dict(revert)] * 4 + [dict(enter)] * 8
        result = _compute_reversion_metrics(events)

        assert result["classification"] == "concerning"

    def test_by_trigger_grouping(self) -> None:
        """trigger_classified values are counted per-trigger (lines 415-416)."""
        events: list[dict[str, object]] = [
            {
                "event": "phase_revert",
                "trigger_classified": "scope_change",
                "from_phase": "implement",
                "to_phase": "plan",
                "reason": "r",
                "ts": "2026-01-01T00:00:00Z",
            },
            {
                "event": "phase_revert",
                "trigger_classified": "scope_change",
                "from_phase": "validate",
                "to_phase": "implement",
                "reason": "r",
                "ts": "2026-01-02T00:00:00Z",
            },
            {
                "event": "phase_revert",
                "trigger_classified": "blocker",
                "from_phase": "plan",
                "to_phase": "research",
                "reason": "r",
                "ts": "2026-01-03T00:00:00Z",
            },
        ]
        result = _compute_reversion_metrics(events)

        by_trigger = result["by_trigger"]
        assert isinstance(by_trigger, dict)
        assert by_trigger.get("scope_change") == 2
        assert by_trigger.get("blocker") == 1

    def test_by_trigger_falls_back_to_trigger_key(self) -> None:
        """When trigger_classified is absent, 'trigger' key is used (line 415)."""
        events: list[dict[str, object]] = [
            {
                "event": "phase_revert",
                "trigger": "manual",
                "from_phase": "implement",
                "to_phase": "plan",
                "reason": "r",
                "ts": "2026-01-01T00:00:00Z",
            },
        ]
        result = _compute_reversion_metrics(events)

        by_trigger = result["by_trigger"]
        assert isinstance(by_trigger, dict)
        assert by_trigger.get("manual") == 1

    def test_by_trigger_defaults_to_other(self) -> None:
        """When neither trigger_classified nor trigger exists, key is 'other'."""
        events: list[dict[str, object]] = [
            {
                "event": "phase_revert",
                "from_phase": "implement",
                "to_phase": "plan",
                "reason": "r",
                "ts": "2026-01-01T00:00:00Z",
            },
        ]
        result = _compute_reversion_metrics(events)

        by_trigger = result["by_trigger"]
        assert isinstance(by_trigger, dict)
        assert by_trigger.get("other") == 1

    def test_latest_populated_from_last_revert_event(self) -> None:
        """latest dict is populated from the last phase_revert event (lines 429-430)."""
        events: list[dict[str, object]] = [
            {
                "event": "phase_revert",
                "trigger_classified": "first_trigger",
                "from_phase": "implement",
                "to_phase": "plan",
                "reason": "reason-A",
                "ts": "2026-01-01T00:00:00Z",
            },
            {
                "event": "phase_revert",
                "trigger_classified": "second_trigger",
                "from_phase": "validate",
                "to_phase": "implement",
                "reason": "reason-B",
                "ts": "2026-01-02T00:00:00Z",
            },
        ]
        result = _compute_reversion_metrics(events)

        latest = result["latest"]
        assert latest is not None
        assert isinstance(latest, dict)
        assert latest["from_phase"] == "validate"
        assert latest["to_phase"] == "implement"
        assert latest["trigger"] == "second_trigger"
        assert latest["reason"] == "reason-B"

    def test_latest_trigger_falls_back_to_trigger_key(self) -> None:
        """latest['trigger'] uses 'trigger' key when trigger_classified absent (line 433)."""
        events: list[dict[str, object]] = [
            {
                "event": "phase_revert",
                "trigger": "manual_fallback",
                "from_phase": "plan",
                "to_phase": "research",
                "reason": "changed mind",
                "ts": "2026-01-01T00:00:00Z",
            },
        ]
        result = _compute_reversion_metrics(events)

        latest = result["latest"]
        assert latest is not None
        assert isinstance(latest, dict)
        assert latest["trigger"] == "manual_fallback"

    def test_rate_computed_correctly(self) -> None:
        """Rate is revert_count / (revert_count + phase_enter_count)."""
        events: list[dict[str, object]] = [
            {
                "event": "phase_revert",
                "trigger_classified": "x",
                "from_phase": "a",
                "to_phase": "b",
                "reason": "r",
                "ts": "2026-01-01T00:00:00Z",
            },
            {"event": "phase_enter"},
            {"event": "phase_enter"},
            {"event": "phase_enter"},
        ]
        result = _compute_reversion_metrics(events)

        assert result["rate"] == round(1 / 4, 4)
