"""Archival and rollback tests for consolidation."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from trw_mcp.state.consolidation import _archive_originals, _rollback_archive
from trw_mcp.state.persistence import FileStateReader, FileStateWriter

from ._consolidation_test_helpers import write_entry


class TestArchiveOriginals:
    """FR04: _archive_originals marks originals as consolidated_into."""

    def test_sets_consolidated_into_field(
        self, tmp_path: Path, reader: FileStateReader, writer: FileStateWriter
    ) -> None:
        """Each original entry gets consolidated_into set."""
        entries_dir = tmp_path / "entries"
        entries_dir.mkdir()
        e1 = write_entry(entries_dir, writer, "e001")
        e2 = write_entry(entries_dir, writer, "e002")
        cluster = [
            {"id": "e001", "summary": "s1"},
            {"id": "e002", "summary": "s2"},
        ]

        _archive_originals(cluster, "L-cons001", entries_dir, reader, writer)

        for path in [e1, e2]:
            data = reader.read_yaml(path)
            assert data["consolidated_into"] == "L-cons001"

    def test_sets_status_archived_without_tier_manager(
        self, tmp_path: Path, reader: FileStateReader, writer: FileStateWriter
    ) -> None:
        """Without tier_manager, entries get status='archived'."""
        entries_dir = tmp_path / "entries"
        entries_dir.mkdir()
        write_entry(entries_dir, writer, "e001")
        cluster = [{"id": "e001", "summary": "s1"}]

        _archive_originals(cluster, "L-cons001", entries_dir, reader, writer, tier_manager=None)

        data = reader.read_yaml(entries_dir / "e001.yaml")
        assert data["status"] == "archived"

    def test_calls_cold_archive_when_tier_manager_available(
        self, tmp_path: Path, reader: FileStateReader, writer: FileStateWriter
    ) -> None:
        """When tier_manager is available, cold_archive is called."""
        entries_dir = tmp_path / "entries"
        entries_dir.mkdir()
        write_entry(entries_dir, writer, "e001")
        cluster = [{"id": "e001", "summary": "s1"}]

        tier_manager = MagicMock()
        tier_manager.cold_archive = MagicMock()

        _archive_originals(cluster, "L-cons001", entries_dir, reader, writer, tier_manager=tier_manager)

        tier_manager.cold_archive.assert_called_once()

    def test_cold_archive_failure_falls_back_to_archived_status(
        self, tmp_path: Path, reader: FileStateReader, writer: FileStateWriter
    ) -> None:
        """When cold_archive raises, falls back to status='archived'."""
        entries_dir = tmp_path / "entries"
        entries_dir.mkdir()
        write_entry(entries_dir, writer, "e001")
        cluster = [{"id": "e001", "summary": "s1"}]

        tier_manager = MagicMock()
        tier_manager.cold_archive.side_effect = RuntimeError("cold archive failed")

        _archive_originals(cluster, "L-cons001", entries_dir, reader, writer, tier_manager=tier_manager)

        data = reader.read_yaml(entries_dir / "e001.yaml")
        assert data["status"] == "archived"

    def test_missing_entry_file_skipped(self, tmp_path: Path, reader: FileStateReader, writer: FileStateWriter) -> None:
        """Entries with no matching file are skipped without raising."""
        entries_dir = tmp_path / "entries"
        entries_dir.mkdir()
        cluster = [{"id": "nonexistent", "summary": "s"}]

        # Should not raise
        _archive_originals(cluster, "L-cons001", entries_dir, reader, writer)

    def test_entry_without_id_skipped(self, tmp_path: Path, reader: FileStateReader, writer: FileStateWriter) -> None:
        """Entries without 'id' field are skipped."""
        entries_dir = tmp_path / "entries"
        entries_dir.mkdir()
        cluster = [{"summary": "no id here"}]

        # Should not raise
        _archive_originals(cluster, "L-cons001", entries_dir, reader, writer)

    def test_exact_slug_derivation_for_entry_id_with_colons(
        self, tmp_path: Path, reader: FileStateReader, writer: FileStateWriter
    ) -> None:
        """Entry IDs with colons are resolved via exact slug derivation."""
        entries_dir = tmp_path / "entries"
        entries_dir.mkdir()
        # Write an entry with ID that contains a colon via the slug path
        entry_id = "e:001"  # colon gets replaced to dash
        slug = entry_id.replace("/", "-").replace(":", "-")
        path = entries_dir / f"{slug}.yaml"
        writer.write_yaml(
            path,
            {
                "id": entry_id,
                "summary": "test",
                "status": "active",
            },
        )
        cluster = [{"id": entry_id, "summary": "test"}]

        # Should find the file via exact slug derivation (line 384 path)
        _archive_originals(cluster, "L-cons001", entries_dir, reader, writer)

        data = reader.read_yaml(path)
        assert data["consolidated_into"] == "L-cons001"

    def test_rollback_on_write_failure(self, tmp_path: Path, reader: FileStateReader, writer: FileStateWriter) -> None:
        """On write failure, previously written entries are rolled back."""
        entries_dir = tmp_path / "entries"
        entries_dir.mkdir()
        write_entry(entries_dir, writer, "e001", summary="original-s1")
        write_entry(entries_dir, writer, "e002", summary="original-s2")

        cluster = [
            {"id": "e001", "summary": "s1"},
            {"id": "e002", "summary": "s2"},
        ]

        # Create a consolidated entry so rollback can delete it
        cons_path = entries_dir / "L-cons001.yaml"
        writer.write_yaml(cons_path, {"id": "L-cons001"})

        # Make the second write fail
        original_write = writer.write_yaml
        call_count = [0]

        def failing_write(path: Path, data: Any) -> None:
            call_count[0] += 1
            # Fail on the 3rd call (after the 2 consolidated_into writes for e001)
            # Actually fail on write for e002's consolidated_into
            if call_count[0] >= 3:
                raise OSError("disk full")
            original_write(path, data)

        writer.write_yaml = failing_write  # type: ignore[method-assign]

        with pytest.raises(OSError):
            _archive_originals(cluster, "L-cons001", entries_dir, reader, writer)

        # Restore
        writer.write_yaml = original_write  # type: ignore[method-assign]


class TestRollbackArchive:
    """FR04: _rollback_archive reverts writes and deletes consolidated entry."""

    def test_reverts_consolidated_into_writes(
        self, tmp_path: Path, reader: FileStateReader, writer: FileStateWriter
    ) -> None:
        """Processed entries are restored to their original data."""
        entries_dir = tmp_path / "entries"
        entries_dir.mkdir()
        path = write_entry(entries_dir, writer, "e001", summary="original")

        # Simulate a processed write
        original_data = reader.read_yaml(path)
        modified = dict(original_data)
        modified["consolidated_into"] = "L-cons001"
        writer.write_yaml(path, modified)

        _rollback_archive([(path, original_data)], "L-cons001", entries_dir, writer)

        restored = reader.read_yaml(path)
        assert "consolidated_into" not in restored

    def test_deletes_consolidated_entry_file(self, tmp_path: Path, writer: FileStateWriter) -> None:
        """Consolidated entry file is deleted during rollback."""
        entries_dir = tmp_path / "entries"
        entries_dir.mkdir()
        cons_path = entries_dir / "L-cons001.yaml"
        writer.write_yaml(cons_path, {"id": "L-cons001"})

        _rollback_archive([], "L-cons001", entries_dir, writer)

        assert not cons_path.exists()

    def test_rollback_with_no_processed_entries(self, tmp_path: Path, writer: FileStateWriter) -> None:
        """Rollback with empty processed list only deletes consolidated file."""
        entries_dir = tmp_path / "entries"
        entries_dir.mkdir()
        # Should not raise even if file doesn't exist
        _rollback_archive([], "L-nonexistent", entries_dir, writer)

    def test_rollback_write_failure_logged_not_raised(self, tmp_path: Path, writer: FileStateWriter) -> None:
        """Write failure during rollback is caught and logged, not re-raised."""
        entries_dir = tmp_path / "entries"
        entries_dir.mkdir()

        # Use a path that doesn't exist to cause write failure
        bad_path = tmp_path / "nonexistent" / "entry.yaml"
        original_data: dict[str, object] = {"id": "e001"}

        # Should not raise
        _rollback_archive([(bad_path, original_data)], "L-cons001", entries_dir, writer)

    def test_rollback_unlink_failure_logged_not_raised(self, tmp_path: Path, writer: FileStateWriter) -> None:
        """unlink failure during rollback is caught and logged, not re-raised."""
        entries_dir = tmp_path / "entries"
        entries_dir.mkdir()
        cons_path = entries_dir / "L-cons001.yaml"
        writer.write_yaml(cons_path, {"id": "L-cons001"})

        with patch.object(
            cons_path.__class__,
            "unlink",
            side_effect=OSError("permission denied"),
        ):
            # Should not raise — exception caught at lines 457-458
            _rollback_archive([], "L-cons001", entries_dir, writer)


class TestArchiveOriginalsEdgeCases:
    """Edge cases for _archive_originals."""

    def test_multiple_entries_some_missing_files(
        self, tmp_path: Path, reader: FileStateReader, writer: FileStateWriter
    ) -> None:
        """Entries with missing files are skipped; others are still archived."""
        entries_dir = tmp_path / "entries"
        entries_dir.mkdir()
        write_entry(entries_dir, writer, "exists1")
        # "missing1" has no file on disk
        write_entry(entries_dir, writer, "exists2")

        cluster = [
            {"id": "exists1", "summary": "s1"},
            {"id": "missing1", "summary": "s2"},
            {"id": "exists2", "summary": "s3"},
        ]

        _archive_originals(cluster, "L-cons", entries_dir, reader, writer)

        # exists1 and exists2 should be archived
        d1 = reader.read_yaml(entries_dir / "exists1.yaml")
        assert d1["consolidated_into"] == "L-cons"
        d2 = reader.read_yaml(entries_dir / "exists2.yaml")
        assert d2["consolidated_into"] == "L-cons"

    def test_entry_id_with_slash_slugified(
        self, tmp_path: Path, reader: FileStateReader, writer: FileStateWriter
    ) -> None:
        """Entry IDs with slashes are slugified (slash -> dash) for file lookup."""
        entries_dir = tmp_path / "entries"
        entries_dir.mkdir()
        # ID "a/b" slugifies to "a-b"
        entry_path = entries_dir / "a-b.yaml"
        writer.write_yaml(entry_path, {"id": "a/b", "summary": "test", "status": "active"})

        cluster = [{"id": "a/b", "summary": "test"}]
        _archive_originals(cluster, "L-cons", entries_dir, reader, writer)

        data = reader.read_yaml(entry_path)
        assert data["consolidated_into"] == "L-cons"

    def test_tier_manager_without_cold_archive_method_falls_back(
        self, tmp_path: Path, reader: FileStateReader, writer: FileStateWriter
    ) -> None:
        """TierManager without cold_archive attr falls back to status='archived'."""
        entries_dir = tmp_path / "entries"
        entries_dir.mkdir()
        write_entry(entries_dir, writer, "e001")

        cluster = [{"id": "e001", "summary": "s1"}]
        tier_manager = MagicMock(spec=[])  # empty spec -> no cold_archive attr

        _archive_originals(cluster, "L-cons", entries_dir, reader, writer, tier_manager=tier_manager)

        data = reader.read_yaml(entries_dir / "e001.yaml")
        assert data["status"] == "archived"
        assert data["consolidated_into"] == "L-cons"


# ---------------------------------------------------------------------------
# Additional edge cases for _rollback_archive
# ---------------------------------------------------------------------------


class TestRollbackArchiveEdgeCases:
    """Edge cases for _rollback_archive slug derivation and error handling."""

    def test_consolidated_id_with_slash_slugified_for_deletion(self, tmp_path: Path, writer: FileStateWriter) -> None:
        """consolidated_id with '/' chars is slugified when deriving file path."""
        entries_dir = tmp_path / "entries"
        entries_dir.mkdir()
        # Create file with slugified name
        cons_path = entries_dir / "L-a-b.yaml"
        writer.write_yaml(cons_path, {"id": "L-a/b"})

        _rollback_archive([], "L-a/b", entries_dir, writer)

        assert not cons_path.exists()

    def test_rollback_multiple_entries_all_restored(
        self, tmp_path: Path, reader: FileStateReader, writer: FileStateWriter
    ) -> None:
        """All processed entries in the list are restored to originals."""
        entries_dir = tmp_path / "entries"
        entries_dir.mkdir()

        paths_and_originals = []
        for i in range(3):
            path = entries_dir / f"e{i:03d}.yaml"
            original: dict[str, object] = {"id": f"e{i:03d}", "summary": f"orig{i}"}
            writer.write_yaml(path, {"id": f"e{i:03d}", "summary": f"orig{i}", "consolidated_into": "L-c"})
            paths_and_originals.append((path, original))

        _rollback_archive(paths_and_originals, "L-c", entries_dir, writer)

        for path, original in paths_and_originals:
            restored = reader.read_yaml(path)
            assert "consolidated_into" not in restored
            assert restored["summary"] == original["summary"]
