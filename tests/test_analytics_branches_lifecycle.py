"""Targeted analytics lifecycle, pruning, and backfill branch tests."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from tests._analytics_branches_support import _reader, _write_entry
from trw_mcp.models.learning import LearningStatus
from trw_mcp.state.analytics import (
    apply_status_update,
    auto_prune_excess_entries,
)

from ._analytics_branches_support import trw_dir  # noqa: F401


class TestApplyStatusUpdateEdgeCases:
    """Lines 626, 634: apply_status_update edge cases."""

    def test_nonexistent_entries_dir_returns_silently(self, tmp_path: Path) -> None:
        """apply_status_update returns early when entries_dir missing — line 626."""
        fake_trw = tmp_path / ".trw_no_entries"
        apply_status_update(fake_trw, "L-nonexistent", "resolved")
        assert not (fake_trw / "learnings" / "entries").exists()

    def test_resolved_status_adds_resolved_at(self, trw_dir: Path) -> None:
        """Resolved status adds resolved_at field — line 634."""
        entries_dir = trw_dir / "learnings" / "entries"
        _write_entry(entries_dir, "resolve_me", learning_id="L-resolve-me")

        apply_status_update(trw_dir, "L-resolve-me", LearningStatus.RESOLVED.value)

        data = _reader.read_yaml(entries_dir / "resolve_me.yaml")
        assert data["status"] == "resolved"
        assert "resolved_at" in data
        assert data["resolved_at"] is not None

    def test_obsolete_status_no_resolved_at(self, trw_dir: Path) -> None:
        """Obsolete status does not add resolved_at — confirms line 634 branch not taken."""
        entries_dir = trw_dir / "learnings" / "entries"
        _write_entry(entries_dir, "obsolete_me", learning_id="L-obsolete-me")

        apply_status_update(trw_dir, "L-obsolete-me", "obsolete")

        data = _reader.read_yaml(entries_dir / "obsolete_me.yaml")
        assert data["status"] == "obsolete"
        assert "resolved_at" not in data


class TestAutoPruneNonexistentDir:
    """Line 840: auto_prune_excess_entries when entries_dir doesn't exist."""

    def test_nonexistent_entries_dir_returns_empty(self, tmp_path: Path) -> None:
        """Returns empty result when entries_dir doesn't exist — line 840."""
        fake_trw = tmp_path / ".trw_no_entries"
        result = auto_prune_excess_entries(fake_trw, max_entries=100)
        assert result["actions_taken"] == 0
        assert result["dedup_candidates"] == []
        assert result["utility_candidates"] == []


class TestAutoPruneUtilityCandidates:
    """Lines 873-878: utility candidate pruning in auto_prune_excess_entries."""

    def test_utility_candidates_with_suggested_status_applied(self, trw_dir: Path) -> None:
        """Utility candidates with suggested_status are applied — lines 873-878."""
        entries_dir = trw_dir / "learnings" / "entries"
        for i in range(6):
            _write_entry(
                entries_dir,
                f"entry_{i:02d}",
                summary=f"Unique learning topic {i} about subject {i}",
                status="active",
                impact=0.1 + i * 0.05,
                learning_id=f"L-entry_{i:02d}",
            )

        fake_candidates = [
            {"id": "L-entry_00", "suggested_status": "obsolete"},
            {"id": "L-entry_01", "suggested_status": "resolved"},
            {"id": "", "suggested_status": "obsolete"},
        ]

        with patch(
            "trw_mcp.scoring.utility_based_prune_candidates",
            return_value=fake_candidates,
        ):
            result = auto_prune_excess_entries(trw_dir, max_entries=3, dry_run=False)

        assert result["actions_taken"] > 0

    def test_utility_candidate_invalid_status_skipped(self, trw_dir: Path) -> None:
        """Utility candidates with invalid suggested_status are skipped — line 876."""
        entries_dir = trw_dir / "learnings" / "entries"
        for i in range(5):
            _write_entry(
                entries_dir,
                f"e_{i:02d}",
                summary=f"Topic {i} about something entirely different",
                status="active",
                learning_id=f"L-e_{i:02d}",
            )

        fake_candidates = [
            {"id": "L-e_00", "suggested_status": "invalid_status"},
            {"id": "L-e_01", "suggested_status": ""},
        ]

        with patch(
            "trw_mcp.scoring.utility_based_prune_candidates",
            return_value=fake_candidates,
        ):
            result = auto_prune_excess_entries(trw_dir, max_entries=3, dry_run=False)

        assert result is not None
        assert isinstance(result, dict)
        assert "actions_taken" in result
        assert result["actions_taken"] == 0
