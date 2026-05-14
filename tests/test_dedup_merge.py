"""Tests for merge_entries behavior."""

from __future__ import annotations

from pathlib import Path

from trw_mcp.state.dedup import merge_entries
from trw_mcp.state.persistence import FileStateReader, FileStateWriter


class TestMergeEntries:
    """Tests for the merge_entries() function.

    Uses make_merge_scenario from _factories to reduce per-test boilerplate.
    """

    def test_merge_updates_tags_as_union(self, tmp_path: Path) -> None:
        """merge_entries unions the tag sets."""
        from tests._factories import make_merge_scenario

        path, new_data, reader, writer = make_merge_scenario(
            tmp_path,
            existing_tags=["python", "testing"],
            existing_evidence=["file1.py"],
            new_tags=["testing", "fixtures"],
            new_evidence=["file2.py"],
        )
        merge_entries(path, new_data, reader, writer)
        updated = reader.read_yaml(path)
        assert set(updated["tags"]) == {"python", "testing", "fixtures"}

    def test_merge_updates_evidence_as_union(self, tmp_path: Path) -> None:
        """merge_entries unions evidence lists."""
        from tests._factories import make_merge_scenario

        path, new_data, reader, writer = make_merge_scenario(
            tmp_path,
            existing_evidence=["file_a.py"],
            new_evidence=["file_b.py", "file_a.py"],
            new_impact=0.4,
            new_detail="shorter",
        )
        merge_entries(path, new_data, reader, writer)
        updated = reader.read_yaml(path)
        assert set(updated["evidence"]) == {"file_a.py", "file_b.py"}

    def test_merge_takes_max_impact(self, tmp_path: Path) -> None:
        """merge_entries uses max(existing.impact, new.impact)."""
        from tests._factories import make_merge_scenario

        path, new_data, reader, writer = make_merge_scenario(
            tmp_path,
            existing_impact=0.5,
            new_impact=0.8,
        )
        merge_entries(path, new_data, reader, writer)
        updated = reader.read_yaml(path)
        assert float(updated["impact"]) == 0.8

    def test_merge_increments_recurrence(self, tmp_path: Path) -> None:
        """merge_entries increments recurrence count."""
        from tests._factories import make_merge_scenario

        path, new_data, reader, writer = make_merge_scenario(
            tmp_path,
            existing_recurrence=2,
            new_impact=0.5,
        )
        merge_entries(path, new_data, reader, writer)
        updated = reader.read_yaml(path)
        assert int(updated["recurrence"]) == 3

    def test_merge_adds_merged_from(self, tmp_path: Path) -> None:
        """merge_entries appends new entry ID to merged_from."""
        from tests._factories import make_merge_scenario

        path, new_data, reader, writer = make_merge_scenario(
            tmp_path,
            new_id="L-newmerge05",
            new_impact=0.5,
        )
        merge_entries(path, new_data, reader, writer)
        updated = reader.read_yaml(path)
        assert "L-newmerge05" in updated["merged_from"]

    def test_merge_appends_longer_detail(self, tmp_path: Path) -> None:
        """merge_entries appends detail when new detail is longer than existing."""
        from tests._factories import make_merge_scenario

        path, new_data, reader, writer = make_merge_scenario(
            tmp_path,
            existing_detail="short detail",
            new_detail="this is a much longer and more informative detail that should be appended",
            new_impact=0.5,
        )
        merge_entries(path, new_data, reader, writer)
        updated = reader.read_yaml(path)
        assert "this is a much longer" in str(updated["detail"])

    def test_merge_returns_path(self, tmp_path: Path) -> None:
        """merge_entries returns the path of the updated entry."""
        from tests._factories import make_merge_scenario

        path, new_data, reader, writer = make_merge_scenario(
            tmp_path,
            new_impact=0.5,
        )
        returned_path = merge_entries(path, new_data, reader, writer)
        assert returned_path == path


class TestMergeEntriesEdgeCases:
    """Additional edge cases for merge_entries coverage."""

    def test_merge_empty_existing_detail_uses_new_directly(
        self, tmp_path: Path, reader: FileStateReader, writer: FileStateWriter
    ) -> None:
        """When existing detail is empty and new detail is longer, use new detail directly."""
        entries_dir = tmp_path / "entries"
        entries_dir.mkdir()

        existing_path = entries_dir / "L-empty-det.yaml"
        writer.write_yaml(
            existing_path,
            {
                "id": "L-empty-det",
                "summary": "s",
                "detail": "",  # Empty existing detail
                "tags": [],
                "evidence": [],
                "impact": 0.5,
                "status": "active",
                "recurrence": 1,
                "created": "2026-01-01",
                "updated": "2026-01-01",
                "merged_from": [],
            },
        )

        new_data = {
            "id": "L-new-det",
            "summary": "s",
            "detail": "this is new detail that should replace the empty existing",
            "tags": [],
            "evidence": [],
            "impact": 0.5,
            "merged_from": [],
        }
        merge_entries(existing_path, new_data, reader, writer)

        updated = reader.read_yaml(existing_path)
        # When existing detail is empty, new detail replaces it directly (no \n\n separator)
        assert "this is new detail" in str(updated["detail"])
        assert "\n\n" not in str(updated["detail"])

    def test_merge_same_length_detail_unchanged(
        self, tmp_path: Path, reader: FileStateReader, writer: FileStateWriter
    ) -> None:
        """When new detail is not longer than existing, detail is unchanged."""
        entries_dir = tmp_path / "entries"
        entries_dir.mkdir()

        existing_path = entries_dir / "L-same-len.yaml"
        writer.write_yaml(
            existing_path,
            {
                "id": "L-same-len",
                "summary": "s",
                "detail": "existing detail is long enough already",
                "tags": [],
                "evidence": [],
                "impact": 0.5,
                "status": "active",
                "recurrence": 1,
                "created": "2026-01-01",
                "updated": "2026-01-01",
                "merged_from": [],
            },
        )

        new_data = {
            "id": "L-new-same",
            "summary": "s",
            "detail": "short",  # Shorter than existing
            "tags": [],
            "evidence": [],
            "impact": 0.5,
            "merged_from": [],
        }
        merge_entries(existing_path, new_data, reader, writer)

        updated = reader.read_yaml(existing_path)
        assert str(updated["detail"]) == "existing detail is long enough already"

    def test_merge_duplicate_merged_from_not_added_twice(
        self, tmp_path: Path, reader: FileStateReader, writer: FileStateWriter
    ) -> None:
        """When new_id is already in merged_from, it should not be duplicated."""
        entries_dir = tmp_path / "entries"
        entries_dir.mkdir()

        existing_path = entries_dir / "L-dedup-mf.yaml"
        writer.write_yaml(
            existing_path,
            {
                "id": "L-dedup-mf",
                "summary": "s",
                "detail": "d",
                "tags": [],
                "evidence": [],
                "impact": 0.5,
                "status": "active",
                "recurrence": 1,
                "created": "2026-01-01",
                "updated": "2026-01-01",
                "merged_from": ["L-already-there"],  # Pre-existing merged_from
            },
        )

        new_data = {
            "id": "L-already-there",  # Same as existing merged_from entry
            "summary": "s",
            "detail": "d",
            "tags": [],
            "evidence": [],
            "impact": 0.5,
            "merged_from": [],
        }
        merge_entries(existing_path, new_data, reader, writer)

        updated = reader.read_yaml(existing_path)
        # L-already-there should appear only once
        assert updated["merged_from"].count("L-already-there") == 1

    def test_merge_empty_new_id_not_added_to_merged_from(
        self, tmp_path: Path, reader: FileStateReader, writer: FileStateWriter
    ) -> None:
        """When new entry id is empty string, it is not added to merged_from."""
        entries_dir = tmp_path / "entries"
        entries_dir.mkdir()

        existing_path = entries_dir / "L-noid.yaml"
        writer.write_yaml(
            existing_path,
            {
                "id": "L-noid",
                "summary": "s",
                "detail": "d",
                "tags": [],
                "evidence": [],
                "impact": 0.5,
                "status": "active",
                "recurrence": 1,
                "created": "2026-01-01",
                "updated": "2026-01-01",
                "merged_from": [],
            },
        )

        new_data = {
            "id": "",  # Empty id
            "summary": "s",
            "detail": "d",
            "tags": [],
            "evidence": [],
            "impact": 0.5,
            "merged_from": [],
        }
        merge_entries(existing_path, new_data, reader, writer)

        updated = reader.read_yaml(existing_path)
        # Empty id should not be added
        assert "" not in updated["merged_from"]
