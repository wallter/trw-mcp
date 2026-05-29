"""Tests for learning helper distribution enforcement."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import structlog

from tests._learning_helpers_test_support import _CFG, set_project_root  # noqa: F401
from trw_mcp.tools._learning_helpers import enforce_distribution


class TestEnforceDistribution:
    """Tests for forced distribution enforcement helper."""

    def test_no_warning_when_disabled(self) -> None:
        """When impact_forced_distribution_enabled=False, returns empty warning."""
        cfg = _CFG.model_copy(update={"impact_forced_distribution_enabled": False})
        warning, demoted_ids = enforce_distribution(
            0.95,
            0.75,
            "L-new001",
            [],
            Path(".trw"),
            cfg,
        )
        assert warning == ""
        assert demoted_ids == []

    def test_no_warning_below_07_threshold(self) -> None:
        """When raw impact < 0.7, distribution is not checked."""
        warning, demoted_ids = enforce_distribution(
            0.5,
            0.45,
            "L-new002",
            [],
            Path(".trw"),
            _CFG,
        )
        assert warning == ""
        assert demoted_ids == []

    def test_warning_when_demotions_occur(self, tmp_path: Path) -> None:
        """When enforce_tier_distribution demotes entries, returns warning."""
        active_entries: list[dict[str, object]] = [{"id": f"L-{i:03d}", "impact": 0.95} for i in range(20)]

        with (
            patch(
                "trw_mcp.scoring.enforce_tier_distribution",
                return_value=[("L-000", 0.69)],
            ),
            patch(
                "trw_mcp.state.memory_adapter.update_learning",
            ) as mock_update,
        ):
            warning, demoted_ids = enforce_distribution(
                0.95,
                0.75,
                "L-new003",
                active_entries,
                tmp_path,
                _CFG,
            )
            assert "critical" in warning
            assert "cap" in warning
            assert "L-000" in demoted_ids
            mock_update.assert_called_once()

    def test_high_tier_name_for_impact_below_09(self, tmp_path: Path) -> None:
        """When raw impact is 0.7-0.89, tier name is 'high'."""
        active_entries: list[dict[str, object]] = [{"id": f"L-{i:03d}", "impact": 0.75} for i in range(20)]

        with (
            patch(
                "trw_mcp.scoring.enforce_tier_distribution",
                return_value=[("L-005", 0.69)],
            ),
            patch(
                "trw_mcp.state.memory_adapter.update_learning",
            ),
        ):
            warning, _demoted_ids = enforce_distribution(
                0.75,
                0.65,
                "L-new004",
                active_entries,
                tmp_path,
                _CFG,
            )
            assert "high" in warning

    def test_fail_open_on_exception(self, tmp_path: Path) -> None:
        """When enforce_tier_distribution throws, returns empty warning."""
        with patch(
            "trw_mcp.scoring.enforce_tier_distribution",
            side_effect=RuntimeError("distribution boom"),
        ):
            warning, demoted_ids = enforce_distribution(
                0.95,
                0.75,
                "L-new005",
                [],
                tmp_path,
                _CFG,
            )
            assert warning == ""
            assert demoted_ids == []

    def test_appends_new_entry_to_active(self, tmp_path: Path) -> None:
        """The newly stored entry is appended to active_entries."""
        active_entries: list[dict[str, object]] = []

        with patch(
            "trw_mcp.scoring.enforce_tier_distribution",
            return_value=[],
        ):
            enforce_distribution(
                0.95,
                0.75,
                "L-new006",
                active_entries,
                tmp_path,
                _CFG,
            )
            assert len(active_entries) == 1
            assert active_entries[0]["id"] == "L-new006"
            assert active_entries[0]["impact"] == 0.75

    def test_multiple_demotions(self, tmp_path: Path) -> None:
        """When multiple entries are demoted, all IDs appear in result."""
        active_entries: list[dict[str, object]] = [{"id": f"L-{i:03d}", "impact": 0.95} for i in range(20)]

        with (
            patch(
                "trw_mcp.scoring.enforce_tier_distribution",
                return_value=[("L-000", 0.69), ("L-001", 0.69), ("L-002", 0.69)],
            ),
            patch(
                "trw_mcp.state.memory_adapter.update_learning",
            ),
        ):
            warning, demoted_ids = enforce_distribution(
                0.95,
                0.75,
                "L-new007",
                active_entries,
                tmp_path,
                _CFG,
            )
            assert len(demoted_ids) == 3
            assert "entries" in warning

    def test_adapter_update_failopen_on_individual_demotion(
        self,
        tmp_path: Path,
    ) -> None:
        """Lines 268-269: adapter_update exception on individual demotion is swallowed."""
        active_entries: list[dict[str, object]] = [{"id": f"L-{i:03d}", "impact": 0.95} for i in range(20)]

        with (
            patch(
                "trw_mcp.scoring.enforce_tier_distribution",
                return_value=[("L-000", 0.69), ("L-001", 0.69)],
            ),
            patch(
                "trw_mcp.state.memory_adapter.update_learning",
                side_effect=RuntimeError("adapter write failure"),
            ),
        ):
            warning, demoted_ids = enforce_distribution(
                0.95,
                0.75,
                "L-new008",
                active_entries,
                tmp_path,
                _CFG,
            )
            assert len(demoted_ids) == 2
            assert "L-000" in demoted_ids
            assert "L-001" in demoted_ids
            assert "critical" in warning

    def test_emits_structured_event_on_demotion(self, tmp_path: Path) -> None:
        """FU-OBS-06: demotion emits a learn_distribution_demoted structlog event
        carrying the actual demoted IDs, count, and tier name."""
        active_entries: list[dict[str, object]] = [{"id": f"L-{i:03d}", "impact": 0.95} for i in range(20)]

        with (
            patch(
                "trw_mcp.scoring.enforce_tier_distribution",
                return_value=[("L-000", 0.69), ("L-001", 0.69)],
            ),
            patch("trw_mcp.state.memory_adapter.update_learning"),
            structlog.testing.capture_logs() as logs,
        ):
            enforce_distribution(0.95, 0.75, "L-new009", active_entries, tmp_path, _CFG)

        events = [e for e in logs if e.get("event") == "learn_distribution_demoted"]
        assert len(events) == 1
        ev = events[0]
        assert ev["log_level"] == "warning"
        assert ev["n_demoted"] == 2
        assert ev["demoted_ids"] == ["L-000", "L-001"]
        assert ev["tier"] == "critical"

    def test_no_structured_event_when_no_demotion(self, tmp_path: Path) -> None:
        """FU-OBS-06: no learn_distribution_demoted event when nothing is demoted."""
        active_entries: list[dict[str, object]] = []

        with (
            patch("trw_mcp.scoring.enforce_tier_distribution", return_value=[]),
            structlog.testing.capture_logs() as logs,
        ):
            enforce_distribution(0.95, 0.75, "L-new010", active_entries, tmp_path, _CFG)

        assert not [e for e in logs if e.get("event") == "learn_distribution_demoted"]
