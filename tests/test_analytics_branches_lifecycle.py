"""Targeted analytics lifecycle, pruning, and backfill branch tests."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from tests._analytics_branches_support import _reader, _write_entry
from trw_mcp.models.learning import LearningStatus
from trw_mcp.state.analytics import (
    apply_status_update,
    auto_prune_excess_entries,
    backfill_source_attribution,
    mark_promoted,
)
from ._analytics_branches_support import trw_dir  # noqa: F401

from ._analytics_branches_support import trw_dir  # noqa: F401

from ._analytics_branches_support import trw_dir  # noqa: F401


class TestMarkPromotedNoEntriesDir:
    """Line 607: mark_promoted returns early when entries_dir doesn't exist."""

    def test_nonexistent_entries_dir_returns_silently(self, tmp_path: Path) -> None:
        """mark_promoted returns without error when entries_dir missing — line 607."""
        fake_trw = tmp_path / ".trw_no_entries"
        mark_promoted(fake_trw, "L-nonexistent")
        assert not (fake_trw / "learnings" / "entries").exists()

    def test_mark_promoted_sets_flag(self, trw_dir: Path) -> None:
        """mark_promoted writes promoted_to_claude_md=True to entry file."""
        entries_dir = trw_dir / "learnings" / "entries"
        _write_entry(entries_dir, "promote_me", learning_id="L-promote-me")

        mark_promoted(trw_dir, "L-promote-me")

        data = _reader.read_yaml(entries_dir / "promote_me.yaml")
        assert data["promoted_to_claude_md"] is True

    def test_mark_promoted_missing_id_no_error(self, trw_dir: Path) -> None:
        """mark_promoted with non-existent ID does nothing silently."""
        entries_dir = trw_dir / "learnings" / "entries"
        _write_entry(entries_dir, "some_entry", learning_id="L-some-entry")
        mark_promoted(trw_dir, "L-nonexistent-id")
        data = _reader.read_yaml(entries_dir / "some_entry.yaml")
        assert data.get("promoted_to_claude_md") is not True


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
                q_observations=0,
                q_value=0.1,
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


class TestBackfillSourceAttribution:
    """Lines 1030-1052: backfill_source_attribution function."""

    def test_nonexistent_entries_dir_returns_zeros(self, tmp_path: Path) -> None:
        """Returns zero counts when entries_dir doesn't exist — line 1031-1032."""
        fake_trw = tmp_path / ".trw_no_entries"
        result = backfill_source_attribution(fake_trw)
        assert result["updated_count"] == 0
        assert result["skipped_count"] == 0
        assert result["total_scanned"] == 0

    def test_entries_with_valid_source_type_skipped(self, trw_dir: Path) -> None:
        """Entries with valid source_type ('human' or 'agent') are skipped — lines 1042-1044."""
        entries_dir = trw_dir / "learnings" / "entries"
        _write_entry(entries_dir, "agent_entry", source_type="agent")
        _write_entry(entries_dir, "human_entry", source_type="human")

        result = backfill_source_attribution(trw_dir)
        assert result["updated_count"] == 0
        assert result["skipped_count"] == 2
        assert result["total_scanned"] == 2

    def test_entries_missing_source_type_are_updated(self, trw_dir: Path) -> None:
        """Entries with missing/invalid source_type get backfilled — lines 1046-1050."""
        entries_dir = trw_dir / "learnings" / "entries"
        (entries_dir / "no_source.yaml").write_text(
            "id: L-no-source\nsummary: Old entry without source\ndetail: detail\n"
            "status: active\nimpact: 0.5\ntags: []\ncreated: '2026-01-01'\n",
            encoding="utf-8",
        )
        (entries_dir / "bad_source.yaml").write_text(
            "id: L-bad-source\nsummary: Old entry with bad source\ndetail: detail\n"
            "status: active\nimpact: 0.5\nsource_type: unknown_type\n"
            "tags: []\ncreated: '2026-01-01'\n",
            encoding="utf-8",
        )

        result = backfill_source_attribution(trw_dir, dry_run=False)
        assert result["updated_count"] == 2
        assert result["skipped_count"] == 0
        assert result["total_scanned"] == 2
        assert result["dry_run"] is False

        data1 = _reader.read_yaml(entries_dir / "no_source.yaml")
        assert data1["source_type"] == "agent"
        assert data1["source_identity"] == ""
        assert "updated" in data1

        data2 = _reader.read_yaml(entries_dir / "bad_source.yaml")
        assert data2["source_type"] == "agent"

    def test_dry_run_does_not_modify_files(self, trw_dir: Path) -> None:
        """dry_run=True reports count without modifying files — line 1045."""
        entries_dir = trw_dir / "learnings" / "entries"
        (entries_dir / "no_source.yaml").write_text(
            "id: L-no-source\nsummary: Old entry\ndetail: detail\n"
            "status: active\nimpact: 0.5\ntags: []\ncreated: '2026-01-01'\n",
            encoding="utf-8",
        )
        original_content = (entries_dir / "no_source.yaml").read_text()

        result = backfill_source_attribution(trw_dir, dry_run=True)
        assert result["updated_count"] == 1
        assert result["dry_run"] is True

        assert (entries_dir / "no_source.yaml").read_text() == original_content

    def test_mixed_entries_counted_correctly(self, trw_dir: Path) -> None:
        """Mix of valid and invalid source_types counted correctly."""
        entries_dir = trw_dir / "learnings" / "entries"
        _write_entry(entries_dir, "valid_agent", source_type="agent")
        _write_entry(entries_dir, "valid_human", source_type="human")
        (entries_dir / "no_src.yaml").write_text(
            "id: L-no-src\nsummary: Entry without source\ndetail: d\n"
            "status: active\nimpact: 0.5\ntags: []\ncreated: '2026-01-01'\n",
            encoding="utf-8",
        )

        result = backfill_source_attribution(trw_dir, dry_run=False)
        assert result["total_scanned"] == 3
        assert result["skipped_count"] == 2
        assert result["updated_count"] == 1
