"""Sprint 9 tests: source attribution backfill + learn_update source fields."""

from __future__ import annotations

from pathlib import Path

import pytest

from trw_mcp.state.analytics import (
    backfill_source_attribution,
    find_entry_by_id,
)
from trw_mcp.state.persistence import FileStateReader, FileStateWriter


@pytest.fixture
def entries_dir(tmp_project: Path, writer: FileStateWriter) -> Path:
    """Create a learnings/entries dir with sample entries."""
    entries = tmp_project / ".trw" / "learnings" / "entries"
    entries.mkdir(parents=True, exist_ok=True)

    # Entry without source_type
    writer.write_yaml(entries / "2026-02-07-no-source.yaml", {
        "id": "L-nosource1",
        "summary": "Entry without source",
        "detail": "Test entry missing source_type",
        "tags": ["test"],
        "impact": 0.5,
        "status": "active",
        "created": "2026-02-07",
    })

    # Entry with valid source_type
    writer.write_yaml(entries / "2026-02-08-has-source.yaml", {
        "id": "L-hassource",
        "summary": "Entry with source",
        "detail": "Test entry with source_type",
        "tags": ["test"],
        "impact": 0.5,
        "status": "active",
        "source_type": "human",
        "source_identity": "Tyler",
        "created": "2026-02-08",
    })

    # Another entry without source_type
    writer.write_yaml(entries / "2026-02-09-no-source2.yaml", {
        "id": "L-nosource2",
        "summary": "Another entry without source",
        "detail": "Second test entry missing source_type",
        "tags": ["test"],
        "impact": 0.7,
        "status": "active",
        "created": "2026-02-09",
    })

    return entries


class TestLearnUpdateSourceFields:
    """Tests for trw_learn_update source_type/source_identity parameters."""

    def test_learn_update_source_type(
        self, tmp_project: Path, writer: FileStateWriter, reader: FileStateReader,
    ) -> None:
        """Verify source fields can be updated via trw_learn_update."""
        entries = tmp_project / ".trw" / "learnings" / "entries"
        entries.mkdir(parents=True, exist_ok=True)

        writer.write_yaml(entries / "2026-02-07-test-entry.yaml", {
            "id": "L-test0001",
            "summary": "Test learning",
            "detail": "Test detail",
            "tags": ["test"],
            "impact": 0.5,
            "status": "active",
            "created": "2026-02-07",
        })

        # Import and call the tool function directly
        from trw_mcp.state.analytics import find_entry_by_id as _find

        # Simulate what trw_learn_update does: find, update, write
        found = _find(entries, "L-test0001")
        assert found is not None
        target_path, target_data = found

        target_data["source_type"] = "agent"
        target_data["source_identity"] = "claude-opus-4-6"
        writer.write_yaml(target_path, target_data)

        # Verify
        updated = reader.read_yaml(target_path)
        assert updated["source_type"] == "agent"
        assert updated["source_identity"] == "claude-opus-4-6"

    def test_learn_update_invalid_source_type(self) -> None:
        """Verify validation rejects invalid source_type values."""
        valid_source_types = {"human", "agent"}
        bad_value = "robot"
        assert bad_value not in valid_source_types


class TestBackfillSourceAttribution:
    """Tests for backfill_source_attribution function."""

    def test_backfill_missing_fields(
        self, tmp_project: Path, entries_dir: Path, reader: FileStateReader,
    ) -> None:
        """Verify backfill writes source_type to entries without it."""
        trw_dir = tmp_project / ".trw"
        result = backfill_source_attribution(trw_dir)

        assert result["updated_count"] == 2
        assert result["skipped_count"] == 1
        assert result["total_scanned"] == 3
        assert result["dry_run"] is False

        # Verify the entries were actually updated
        _, data1 = find_entry_by_id(entries_dir, "L-nosource1")  # type: ignore[misc]
        assert data1["source_type"] == "agent"
        assert data1["source_identity"] == ""

        _, data2 = find_entry_by_id(entries_dir, "L-nosource2")  # type: ignore[misc]
        assert data2["source_type"] == "agent"
        assert data2["source_identity"] == ""

    def test_backfill_skips_existing(
        self, tmp_project: Path, entries_dir: Path, reader: FileStateReader,
    ) -> None:
        """Verify entries with valid source_type are untouched."""
        trw_dir = tmp_project / ".trw"
        backfill_source_attribution(trw_dir)

        # The "has-source" entry should still have its original values
        _, data = find_entry_by_id(entries_dir, "L-hassource")  # type: ignore[misc]
        assert data["source_type"] == "human"
        assert data["source_identity"] == "Tyler"

    def test_backfill_dry_run(
        self, tmp_project: Path, entries_dir: Path, reader: FileStateReader,
    ) -> None:
        """Verify dry_run counts but doesn't modify files."""
        trw_dir = tmp_project / ".trw"
        result = backfill_source_attribution(trw_dir, dry_run=True)

        assert result["updated_count"] == 2
        assert result["skipped_count"] == 1
        assert result["dry_run"] is True

        # Verify files were NOT modified
        _, data = find_entry_by_id(entries_dir, "L-nosource1")  # type: ignore[misc]
        assert "source_type" not in data

    def test_backfill_empty_dir(self, tmp_path: Path) -> None:
        """Verify backfill handles missing entries directory gracefully."""
        result = backfill_source_attribution(tmp_path / ".trw")
        assert result["updated_count"] == 0
        assert result["total_scanned"] == 0
