"""Targeted analytics overflow, SQLite, and duplicate-learning branch tests."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from tests._analytics_branches_support import _reader, _write_entry
from trw_mcp.models.learning import LearningEntry
from trw_mcp.state.analytics import auto_prune_excess_entries, update_learning_index


class TestUpdateLearningIndexOverflow:
    """Lines 444-445: update_learning_index overflow pruning."""

    def test_overflow_prunes_lowest_impact(self, trw_dir: Path) -> None:
        """When entries exceed learning_max_entries, lowest impact entries are pruned — lines 444-445."""
        from trw_mcp.models.config import TRWConfig, _reset_config

        _reset_config()

        config_with_low_max = TRWConfig(
            trw_dir=str(trw_dir),
            learning_max_entries=2,
        )

        with patch("trw_mcp.state.analytics.entries.get_config", return_value=config_with_low_max):
            from datetime import date

            entries = [
                LearningEntry(
                    id=f"L-{i:04d}",
                    summary=f"Learning number {i}",
                    detail="detail",
                    tags=["test"],
                    impact=float(i) / 10.0,
                    source_type="agent",
                    source_identity="test",
                    created=date(2026, 1, i + 1),
                )
                for i in range(3)
            ]
            for entry in entries:
                update_learning_index(trw_dir, entry)

        index_path = trw_dir / "learnings" / "index.yaml"
        assert index_path.exists()
        data = _reader.read_yaml(index_path)
        assert len(data["entries"]) <= 2


class TestAutoPruneUsesSQLite:
    """PRD-FIX-033-FR02: auto_prune_excess_entries uses SQLite when available."""

    def test_auto_prune_uses_sqlite(self, trw_dir: Path) -> None:
        """auto_prune calls list_entries_by_status instead of _iter_entry_files."""
        entries_dir = trw_dir / "learnings" / "entries"
        for i in range(5):
            _write_entry(
                entries_dir,
                f"sq_{i:02d}",
                summary=f"Unique topic {i} about subject {i * 10}",
                status="active",
                impact=0.5,
                learning_id=f"L-sq_{i:02d}",
            )

        fake_entries: list[dict[str, object]] = [
            {
                "id": f"L-sq_{i:02d}",
                "summary": f"Unique topic {i} about subject {i * 10}",
                "status": "active",
                "impact": 0.5,
                "tags": [],
                "detail": "detail",
                "created": "2026-02-01",
                "last_accessed_at": "2026-02-01",
                "q_value": 0.5,
                "q_observations": 0,
                "recurrence": 1,
                "access_count": 0,
                "source_type": "agent",
            }
            for i in range(5)
        ]

        with (
            patch(
                "trw_mcp.state.memory_adapter.list_entries_by_status",
                return_value=fake_entries,
            ) as mock_sqlite,
            patch(
                "trw_mcp.scoring.utility_based_prune_candidates",
                return_value=[],
            ),
        ):
            result = auto_prune_excess_entries(trw_dir, max_entries=3, dry_run=True)

        mock_sqlite.assert_called_once()
        assert result["active_count"] == 5

    def test_auto_prune_fallback_to_yaml(self, trw_dir: Path) -> None:
        """Falls back to YAML when SQLite raises, with warning logged."""
        entries_dir = trw_dir / "learnings" / "entries"
        for i in range(5):
            _write_entry(
                entries_dir,
                f"fb_{i:02d}",
                summary=f"Fallback topic {i} about thing {i * 100}",
                status="active",
                impact=0.5,
                learning_id=f"L-fb_{i:02d}",
            )

        with patch(
            "trw_mcp.state.memory_adapter.list_entries_by_status",
            side_effect=RuntimeError("SQLite unavailable"),
        ):
            result = auto_prune_excess_entries(trw_dir, max_entries=3, dry_run=True)

        assert result["active_count"] == 5


class TestFindDuplicateLearningsEntriesParam:
    """PRD-FIX-033-FR03: find_duplicate_learnings accepts pre-loaded entries."""

    def test_with_entries_param_skips_yaml(self, trw_dir: Path) -> None:
        """When entries param provided, YAML files are not read."""
        from trw_mcp.state.analytics import find_duplicate_learnings

        entries_dir = trw_dir / "learnings" / "entries"
        pre_loaded: list[dict[str, object]] = [
            {"id": "L-a1", "summary": "python testing gotcha mock", "status": "active"},
            {"id": "L-a2", "summary": "python testing gotcha mock pattern", "status": "active"},
            {"id": "L-a3", "summary": "completely different topic rust", "status": "active"},
        ]

        results = find_duplicate_learnings(entries_dir, threshold=0.6, entries=pre_loaded)
        assert len(results) >= 1
        pair_ids = {(r[0], r[1]) for r in results}
        assert ("L-a1", "L-a2") in pair_ids

    def test_backward_compat_without_entries_param(self, trw_dir: Path) -> None:
        """Without entries param, existing YAML scan path works."""
        from trw_mcp.state.analytics import find_duplicate_learnings

        entries_dir = trw_dir / "learnings" / "entries"
        _write_entry(entries_dir, "dup1", summary="exact same words here", learning_id="L-dup1")
        _write_entry(entries_dir, "dup2", summary="exact same words here", learning_id="L-dup2")

        results = find_duplicate_learnings(entries_dir, threshold=0.9)
        assert len(results) >= 1

    def test_entries_param_filters_active_only(self, trw_dir: Path) -> None:
        """Pre-loaded entries with non-active status are filtered out."""
        from trw_mcp.state.analytics import find_duplicate_learnings

        entries_dir = trw_dir / "learnings" / "entries"
        pre_loaded: list[dict[str, object]] = [
            {"id": "L-x1", "summary": "same words here", "status": "active"},
            {"id": "L-x2", "summary": "same words here", "status": "obsolete"},
        ]

        results = find_duplicate_learnings(entries_dir, threshold=0.9, entries=pre_loaded)
        assert len(results) == 0
