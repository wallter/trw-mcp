"""Tests for extracted learning helpers — calibrate, soft-cap, dedup, distribution.

Each helper is a pure function that takes explicit parameters, making these
tests independent of the MCP tool registration machinery.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from trw_mcp.models.config import TRWConfig
from trw_mcp.state.persistence import FileStateReader, FileStateWriter
from trw_mcp.tools._learning_helpers import (
    LearningParams,
    calibrate_impact,
    check_and_handle_dedup,
    check_soft_cap,
    enforce_distribution,
)

_CFG = TRWConfig()


@pytest.fixture(autouse=True)
def set_project_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Set TRW_PROJECT_ROOT to temp directory for all tests."""
    monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
    return tmp_path


# ── calibrate_impact ─────────────────────────────────────────────────────


class TestCalibrateImpact:
    """Tests for Bayesian calibration helper."""

    def test_returns_calibrated_impact_with_default_stats(self) -> None:
        """With no recall history, calibration pulls toward org mean."""
        result = calibrate_impact(0.9, _CFG)
        # bayesian_calibrate(0.9, org_mean=0.5, user_weight=1.0, org_weight=0.5)
        # = (0.9*1 + 0.5*0.5) / (1+0.5) = 1.15/1.5 ≈ 0.7667
        assert result < 0.9
        assert result > 0.5

    def test_low_impact_still_calibrated(self) -> None:
        """Low impact is pulled up toward org mean."""
        result = calibrate_impact(0.1, _CFG)
        # Should be pulled toward 0.5
        assert result > 0.1

    def test_mid_impact_stays_near_mid(self) -> None:
        """Impact at org mean stays near org mean."""
        result = calibrate_impact(0.5, _CFG)
        assert abs(result - 0.5) < 0.01

    def test_fail_open_on_exception(self) -> None:
        """When calibration throws, raw impact is returned."""
        with patch(
            "trw_mcp.tools._learning_helpers.calibrate_impact.__module__",
        ):
            # Patch get_recall_stats to raise
            with patch(
                "trw_mcp.state.recall_tracking.get_recall_stats",
                side_effect=RuntimeError("tracking boom"),
            ):
                result = calibrate_impact(0.8, _CFG)
                assert result == 0.8

    def test_calibration_with_high_accuracy_user(self) -> None:
        """User with high accuracy gets higher weight (closer to raw)."""
        # Mock recall stats that produce high accuracy weight (2.0)
        mock_stats: dict[str, Any] = {
            "total_recalls": 100,
            "positive_outcomes": 80,
        }
        with patch(
            "trw_mcp.state.recall_tracking.get_recall_stats",
            return_value=mock_stats,
        ):
            result = calibrate_impact(0.9, _CFG)
            # user_weight=2.0 (75%+ positive)
            # = (0.9*2 + 0.5*0.5) / (2+0.5) = 2.05/2.5 = 0.82
            assert result > 0.75
            assert result < 0.9


# ── check_soft_cap ───────────────────────────────────────────────────────


class TestCheckSoftCap:
    """Tests for distribution soft-cap check."""

    def test_no_cap_when_few_entries(self) -> None:
        """Below 5 active entries, no soft-cap is applied."""
        entries: list[dict[str, object]] = [
            {"impact": 0.9} for _ in range(3)
        ]
        result_impact, warning = check_soft_cap(0.9, entries, _CFG)
        assert result_impact == 0.9
        assert warning is None

    def test_no_cap_when_within_threshold(self) -> None:
        """When high-impact entries are under threshold, no adjustment."""
        entries: list[dict[str, object]] = [
            {"impact": 0.3} for _ in range(99)
        ]
        entries.append({"impact": 0.9})
        result_impact, warning = check_soft_cap(0.9, entries, _CFG)
        assert result_impact == 0.9
        assert warning is None

    def test_caps_impact_when_over_threshold(self) -> None:
        """When high-impact entries exceed threshold, impact is reduced."""
        entries: list[dict[str, object]] = [
            {"impact": 0.9} for _ in range(10)
        ]
        result_impact, warning = check_soft_cap(0.9, entries, _CFG)
        assert result_impact < 0.9
        assert warning is not None
        assert "soft-capped" in warning

    def test_cap_does_not_go_below_05(self) -> None:
        """Floor of 0.5 prevents excessive reduction."""
        entries: list[dict[str, object]] = [
            {"impact": 0.9} for _ in range(100)
        ]
        result_impact, _warning = check_soft_cap(0.85, entries, _CFG)
        assert result_impact >= 0.5

    def test_no_cap_for_low_impact(self) -> None:
        """Impact below 0.8 is never soft-capped."""
        entries: list[dict[str, object]] = [
            {"impact": 0.9} for _ in range(10)
        ]
        result_impact, warning = check_soft_cap(0.5, entries, _CFG)
        assert result_impact == 0.5
        assert warning is None

    def test_warning_message_contains_details(self) -> None:
        """Warning message includes counts and threshold."""
        entries: list[dict[str, object]] = [
            {"impact": 0.9} for _ in range(10)
        ]
        _result_impact, warning = check_soft_cap(0.9, entries, _CFG)
        assert warning is not None
        assert "threshold" in warning
        assert "10" in warning  # count of high-impact entries

    def test_fail_open_on_exception(self) -> None:
        """If an exception occurs, returns original impact with no warning."""
        # Entries with bad data that would cause float() to fail
        entries: list[dict[str, object]] = [
            {"impact": "not-a-number"} for _ in range(10)
        ]
        result_impact, warning = check_soft_cap(0.9, entries, _CFG)
        assert result_impact == 0.9
        assert warning is None

    def test_cap_floors_at_05_with_extreme_saturation(self) -> None:
        """Lines 93-94: The 0.5 floor guard is defensive — the while loop
        condition (adjusted >= 0.8) exits before adjusted can reach 0.5.
        Verify the while loop exits correctly when adjusted drops below 0.8.
        """
        # With 1% threshold, all entries are high-impact, so soft-cap triggers.
        # adjusted starts at 0.81 -> 0.81*0.9=0.729 -> exits while (< 0.8)
        cfg = _CFG.model_copy(update={"impact_high_threshold_pct": 1})
        entries: list[dict[str, object]] = [
            {"impact": 0.9} for _ in range(100)
        ]
        result_impact, warning = check_soft_cap(0.81, entries, cfg)
        # adjusted = 0.729 after one iteration, then while exits
        assert result_impact < 0.81
        assert result_impact >= 0.5
        assert warning is not None
        assert "soft-capped" in warning


# ── check_and_handle_dedup ───────────────────────────────────────────────


class TestCheckAndHandleDedup:
    """Tests for semantic dedup check helper."""

    def test_returns_none_when_disabled(self, tmp_path: Path) -> None:
        """When dedup is disabled, returns None (proceed to store)."""
        cfg = _CFG.model_copy(update={"dedup_enabled": False})
        result = check_and_handle_dedup(
            LearningParams(
                summary="summary", detail="detail", learning_id="L-test001",
                tags=["tag"], evidence=["evidence"], impact=0.8,
                source_type="agent", source_identity="",
            ),
            tmp_path / "entries", FileStateReader(), FileStateWriter(), cfg,
        )
        assert result is None

    def test_returns_none_when_no_duplicate(self, tmp_path: Path) -> None:
        """When no duplicate found, returns None."""
        entries_dir = tmp_path / "entries"
        entries_dir.mkdir(parents=True)

        mock_result = MagicMock()
        mock_result.action = "store"
        mock_result.existing_id = None
        mock_result.similarity = 0.1

        with patch(
            "trw_mcp.state.dedup.check_duplicate",
            return_value=mock_result,
        ):
            result = check_and_handle_dedup(
                LearningParams(
                    summary="new summary", detail="new detail", learning_id="L-test002",
                    tags=[], evidence=[], impact=0.5,
                    source_type="agent", source_identity="",
                ),
                entries_dir, FileStateReader(), FileStateWriter(), _CFG,
            )
            assert result is None

    def test_returns_skip_result_on_exact_duplicate(self, tmp_path: Path) -> None:
        """When dedup says skip, returns a skip result dict."""
        entries_dir = tmp_path / "entries"
        entries_dir.mkdir(parents=True)

        mock_result = MagicMock()
        mock_result.action = "skip"
        mock_result.existing_id = "L-existing001"
        mock_result.similarity = 0.98

        with patch(
            "trw_mcp.state.dedup.check_duplicate",
            return_value=mock_result,
        ):
            result = check_and_handle_dedup(
                LearningParams(
                    summary="duplicate summary", detail="duplicate detail",
                    learning_id="L-test003", tags=[], evidence=[], impact=0.5,
                    source_type="agent", source_identity="",
                ),
                entries_dir, FileStateReader(), FileStateWriter(), _CFG,
            )
            assert result is not None
            assert result["status"] == "skipped"
            assert result["duplicate_of"] == "L-existing001"
            assert result["similarity"] == 0.98

    def test_returns_merge_result_on_near_duplicate(self, tmp_path: Path) -> None:
        """When dedup says merge, merges and returns a merge result dict."""
        entries_dir = tmp_path / "entries"
        entries_dir.mkdir(parents=True)

        # Write an existing entry file
        writer = FileStateWriter()
        reader = FileStateReader()
        existing_data = {
            "id": "L-existing002",
            "summary": "Existing learning",
            "detail": "Existing detail",
            "tags": [],
            "evidence": [],
            "impact": 0.7,
        }
        writer.write_yaml(entries_dir / "existing.yaml", existing_data)

        mock_dedup = MagicMock()
        mock_dedup.action = "merge"
        mock_dedup.existing_id = "L-existing002"
        mock_dedup.similarity = 0.88

        with patch(
            "trw_mcp.state.dedup.check_duplicate",
            return_value=mock_dedup,
        ), patch(
            "trw_mcp.state.dedup.merge_entries",
        ) as mock_merge:
            result = check_and_handle_dedup(
                LearningParams(
                    summary="near-duplicate summary", detail="near-duplicate detail",
                    learning_id="L-test004", tags=["tag"], evidence=["evidence"],
                    impact=0.8, source_type="agent", source_identity="",
                ),
                entries_dir, reader, writer, _CFG,
            )
            assert result is not None
            assert result["status"] == "merged"
            assert result["merged_into"] == "L-existing002"
            assert mock_merge.called

    def test_merge_skips_index_yaml(self, tmp_path: Path) -> None:
        """Line 178: index.yaml is skipped when scanning for merge target."""
        entries_dir = tmp_path / "entries"
        entries_dir.mkdir(parents=True)

        writer = FileStateWriter()
        reader = FileStateReader()

        # Write index.yaml with the target id — should be skipped
        writer.write_yaml(entries_dir / "index.yaml", {
            "id": "L-existing010",
            "summary": "Index entry",
        })
        # Write actual entry that should be found
        writer.write_yaml(entries_dir / "real-entry.yaml", {
            "id": "L-existing010",
            "summary": "Real entry",
            "detail": "Detail",
            "tags": [],
            "evidence": [],
            "impact": 0.7,
        })

        mock_dedup = MagicMock()
        mock_dedup.action = "merge"
        mock_dedup.existing_id = "L-existing010"
        mock_dedup.similarity = 0.85

        with patch(
            "trw_mcp.state.dedup.check_duplicate",
            return_value=mock_dedup,
        ), patch(
            "trw_mcp.state.dedup.merge_entries",
        ) as mock_merge:
            result = check_and_handle_dedup(
                LearningParams(
                    summary="near-dup summary", detail="near-dup detail",
                    learning_id="L-test010", tags=["tag"], evidence=["evidence"],
                    impact=0.8, source_type="agent", source_identity="",
                ),
                entries_dir, reader, writer, _CFG,
            )

        assert result is not None
        assert result["status"] == "merged"
        # merge_entries should have been called with real-entry.yaml, not index.yaml
        assert mock_merge.called
        actual_path = mock_merge.call_args[0][0]
        assert actual_path.name == "real-entry.yaml"

    def test_merge_inner_read_exception_continues(self, tmp_path: Path) -> None:
        """Lines 210-211: Exception reading a yaml file during merge scan continues."""
        entries_dir = tmp_path / "entries"
        entries_dir.mkdir(parents=True)

        writer = FileStateWriter()

        # Write a corrupt entry and a valid entry
        (entries_dir / "corrupt.yaml").write_text("{{invalid", encoding="utf-8")
        writer.write_yaml(entries_dir / "valid.yaml", {
            "id": "L-existing020",
            "summary": "Valid",
            "detail": "Detail",
            "tags": [],
            "evidence": [],
            "impact": 0.7,
        })

        mock_dedup = MagicMock()
        mock_dedup.action = "merge"
        mock_dedup.existing_id = "L-existing020"
        mock_dedup.similarity = 0.85

        reader = FileStateReader()

        with patch(
            "trw_mcp.state.dedup.check_duplicate",
            return_value=mock_dedup,
        ), patch(
            "trw_mcp.state.dedup.merge_entries",
        ) as mock_merge:
            result = check_and_handle_dedup(
                LearningParams(
                    summary="near-dup summary", detail="near-dup detail",
                    learning_id="L-test020", tags=["tag"], evidence=["evidence"],
                    impact=0.8, source_type="agent", source_identity="",
                ),
                entries_dir, reader, writer, _CFG,
            )

        # Should succeed despite the corrupt file — continues past it
        assert result is not None
        assert result["status"] == "merged"
        assert mock_merge.called

    def test_fail_open_on_dedup_exception(self, tmp_path: Path) -> None:
        """When dedup check throws, returns None (proceed to store)."""
        entries_dir = tmp_path / "entries"
        entries_dir.mkdir(parents=True)

        with patch(
            "trw_mcp.state.dedup.check_duplicate",
            side_effect=RuntimeError("dedup boom"),
        ):
            result = check_and_handle_dedup(
                LearningParams(
                    summary="summary", detail="detail", learning_id="L-test005",
                    tags=[], evidence=[], impact=0.5,
                    source_type="agent", source_identity="",
                ),
                entries_dir, FileStateReader(), FileStateWriter(), _CFG,
            )
            assert result is None


# ── enforce_distribution ─────────────────────────────────────────────────


class TestEnforceDistribution:
    """Tests for forced distribution enforcement helper."""

    def test_no_warning_when_disabled(self) -> None:
        """When impact_forced_distribution_enabled=False, returns empty warning."""
        cfg = _CFG.model_copy(update={"impact_forced_distribution_enabled": False})
        warning, demoted_ids = enforce_distribution(
            0.95, 0.75, "L-new001", [], Path(".trw"), cfg,
        )
        assert warning == ""
        assert demoted_ids == []

    def test_no_warning_below_07_threshold(self) -> None:
        """When raw impact < 0.7, distribution is not checked."""
        warning, demoted_ids = enforce_distribution(
            0.5, 0.45, "L-new002", [], Path(".trw"), _CFG,
        )
        assert warning == ""
        assert demoted_ids == []

    def test_warning_when_demotions_occur(self, tmp_path: Path) -> None:
        """When enforce_tier_distribution demotes entries, returns warning."""
        active_entries: list[dict[str, object]] = [
            {"id": f"L-{i:03d}", "impact": 0.95}
            for i in range(20)
        ]

        with patch(
            "trw_mcp.scoring.enforce_tier_distribution",
            return_value=[("L-000", 0.69)],
        ), patch(
            "trw_mcp.state.memory_adapter.update_learning",
        ) as mock_update:
            warning, demoted_ids = enforce_distribution(
                0.95, 0.75, "L-new003", active_entries, tmp_path, _CFG,
            )
            assert "critical" in warning
            assert "cap" in warning
            assert "L-000" in demoted_ids
            mock_update.assert_called_once()

    def test_high_tier_name_for_impact_below_09(self, tmp_path: Path) -> None:
        """When raw impact is 0.7-0.89, tier name is 'high'."""
        active_entries: list[dict[str, object]] = [
            {"id": f"L-{i:03d}", "impact": 0.75}
            for i in range(20)
        ]

        with patch(
            "trw_mcp.scoring.enforce_tier_distribution",
            return_value=[("L-005", 0.69)],
        ), patch(
            "trw_mcp.state.memory_adapter.update_learning",
        ):
            warning, _demoted_ids = enforce_distribution(
                0.75, 0.65, "L-new004", active_entries, tmp_path, _CFG,
            )
            assert "high" in warning

    def test_fail_open_on_exception(self, tmp_path: Path) -> None:
        """When enforce_tier_distribution throws, returns empty warning."""
        with patch(
            "trw_mcp.scoring.enforce_tier_distribution",
            side_effect=RuntimeError("distribution boom"),
        ):
            warning, demoted_ids = enforce_distribution(
                0.95, 0.75, "L-new005", [], tmp_path, _CFG,
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
                0.95, 0.75, "L-new006", active_entries, tmp_path, _CFG,
            )
            # active_entries is mutated in-place
            assert len(active_entries) == 1
            assert active_entries[0]["id"] == "L-new006"
            assert active_entries[0]["impact"] == 0.75

    def test_multiple_demotions(self, tmp_path: Path) -> None:
        """When multiple entries are demoted, all IDs appear in result."""
        active_entries: list[dict[str, object]] = [
            {"id": f"L-{i:03d}", "impact": 0.95}
            for i in range(20)
        ]

        with patch(
            "trw_mcp.scoring.enforce_tier_distribution",
            return_value=[("L-000", 0.69), ("L-001", 0.69), ("L-002", 0.69)],
        ), patch(
            "trw_mcp.state.memory_adapter.update_learning",
        ):
            warning, demoted_ids = enforce_distribution(
                0.95, 0.75, "L-new007", active_entries, tmp_path, _CFG,
            )
            assert len(demoted_ids) == 3
            assert "entries" in warning  # plural

    def test_adapter_update_failopen_on_individual_demotion(
        self, tmp_path: Path,
    ) -> None:
        """Lines 268-269: adapter_update exception on individual demotion is swallowed."""
        active_entries: list[dict[str, object]] = [
            {"id": f"L-{i:03d}", "impact": 0.95}
            for i in range(20)
        ]

        with patch(
            "trw_mcp.scoring.enforce_tier_distribution",
            return_value=[("L-000", 0.69), ("L-001", 0.69)],
        ), patch(
            "trw_mcp.state.memory_adapter.update_learning",
            side_effect=RuntimeError("adapter write failure"),
        ):
            warning, demoted_ids = enforce_distribution(
                0.95, 0.75, "L-new008", active_entries, tmp_path, _CFG,
            )
            # Demotions are still recorded despite adapter failures
            assert len(demoted_ids) == 2
            assert "L-000" in demoted_ids
            assert "L-001" in demoted_ids
            assert "critical" in warning
