"""Analytics YAML fallback tests for mutation/update flows."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from tests._analytics_yaml_paths_support import _setup_trw, _write_entry
from trw_mcp.state.analytics import (
    auto_prune_excess_entries,
    mark_promoted,
    update_analytics_extended,
)
from trw_mcp.state.persistence import FileStateReader


class TestUpdateAnalyticsExtendedYamlFallback:
    """Test YAML fallback in update_analytics_extended for q-learning scan."""

    def test_yaml_fallback_counts_q_activations_and_high_impact(self, tmp_path: Path) -> None:
        """Lines 727-735: YAML fallback scans entries for q_observations and impact."""
        trw_dir = _setup_trw(tmp_path)
        entries_dir = trw_dir / "learnings" / "entries"

        _write_entry(entries_dir, "q-active", q_observations=3, impact=0.5)
        _write_entry(entries_dir, "high-impact", q_observations=0, impact=0.8)
        _write_entry(entries_dir, "both", q_observations=2, impact=0.9)

        with patch(
            "trw_mcp.state.memory_adapter.list_active_learnings",
            side_effect=ImportError("no sqlite"),
        ):
            update_analytics_extended(
                trw_dir,
                new_learnings_count=1,
                is_reflection=True,
                is_success=True,
            )

        data = FileStateReader().read_yaml(trw_dir / "context" / "analytics.yaml")
        assert data["q_learning_activations"] == 2
        assert data["high_impact_learnings"] == 2


class TestMarkPromotedSqliteException:
    """Test mark_promoted when SQLite fails."""

    def test_sqlite_exception_falls_through_to_yaml(self, tmp_path: Path) -> None:
        """Lines 764-765: SQLite exception is caught, YAML update still happens."""
        trw_dir = _setup_trw(tmp_path)
        entries_dir = trw_dir / "learnings" / "entries"

        _write_entry(entries_dir, "promote-me", summary="promote target")

        with patch(
            "trw_mcp.state.memory_adapter.get_backend",
            side_effect=RuntimeError("sqlite broken"),
        ):
            mark_promoted(trw_dir, "promote-me")

        data = FileStateReader().read_yaml(entries_dir / "promote-me.yaml")
        assert data.get("promoted_to_claude_md") is True


class TestAutoPruneYamlPath:
    """Test auto_prune_excess_entries YAML fallback path."""

    def test_yaml_path_early_return_under_threshold(self, tmp_path: Path) -> None:
        """Line 1094: YAML path returns early when active_count <= max_entries."""
        trw_dir = _setup_trw(tmp_path)
        entries_dir = trw_dir / "learnings" / "entries"

        _write_entry(entries_dir, "entry-1")
        _write_entry(entries_dir, "entry-2")

        with patch(
            "trw_mcp.state.memory_adapter.list_entries_by_status",
            side_effect=ImportError("no sqlite"),
        ):
            result = auto_prune_excess_entries(
                trw_dir,
                max_entries=10,
            )

        assert result["actions_taken"] == 0
        assert result["active_count"] == 2

    def test_yaml_path_dedup_and_utility_prune(self, tmp_path: Path) -> None:
        """Lines 1108-1110, 1116-1125: YAML path performs dedup + utility pruning."""
        trw_dir = _setup_trw(tmp_path)
        entries_dir = trw_dir / "learnings" / "entries"

        old_date = (datetime.now(tz=timezone.utc).date() - timedelta(days=60)).isoformat()
        for i in range(5):
            _write_entry(
                entries_dir,
                f"entry-{i}",
                summary=f"testing coverage gap in module {i % 2}",
                impact=0.1,
                last_accessed_at=old_date,
            )

        with patch(
            "trw_mcp.state.memory_adapter.list_entries_by_status",
            side_effect=ImportError("no sqlite"),
        ):
            with patch(
                "trw_mcp.state.analytics.dedup.find_duplicate_learnings",
                return_value=[("entry-0", "entry-1", 0.85)],
            ):
                with patch(
                    "trw_mcp.scoring.utility_based_prune_candidates",
                    return_value=[
                        {"id": "entry-2", "suggested_status": "obsolete"},
                        {"id": "entry-3", "suggested_status": "resolved"},
                        {"id": "entry-0", "suggested_status": "obsolete"},
                        {"id": "entry-4", "suggested_status": "active"},
                    ],
                ):
                    with patch("trw_mcp.state.analytics.dedup.apply_status_update"):
                        with patch("trw_mcp.state.analytics.dedup.resync_learning_index") as mock_resync:
                            result = auto_prune_excess_entries(
                                trw_dir,
                                max_entries=2,
                                dry_run=False,
                            )

        assert result["actions_taken"] == 3
        mock_resync.assert_called_once()

    def test_yaml_path_dry_run_no_actions(self, tmp_path: Path) -> None:
        """Lines 1108-1110: dry_run=True does not apply any changes."""
        trw_dir = _setup_trw(tmp_path)
        entries_dir = trw_dir / "learnings" / "entries"

        old_date = (datetime.now(tz=timezone.utc).date() - timedelta(days=60)).isoformat()
        for i in range(5):
            _write_entry(
                entries_dir,
                f"entry-{i}",
                summary=f"testing coverage gap {i}",
                impact=0.1,
                last_accessed_at=old_date,
            )

        with patch(
            "trw_mcp.state.memory_adapter.list_entries_by_status",
            side_effect=ImportError("no sqlite"),
        ):
            with patch(
                "trw_mcp.state.analytics.dedup.find_duplicate_learnings",
                return_value=[("entry-0", "entry-1", 0.9)],
            ):
                with patch(
                    "trw_mcp.scoring.utility_based_prune_candidates",
                    return_value=[],
                ):
                    result = auto_prune_excess_entries(
                        trw_dir,
                        max_entries=2,
                        dry_run=True,
                    )

        assert result["actions_taken"] == 0
        candidates = result["dedup_candidates"]
        assert isinstance(candidates, list)
        assert len(candidates) == 1
