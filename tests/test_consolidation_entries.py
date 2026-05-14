"""Consolidated entry creation tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from trw_mcp.state.consolidation import _create_consolidated_entry
from trw_mcp.state.persistence import FileStateReader, FileStateWriter

from ._consolidation_test_helpers import make_cluster


class TestCreateConsolidatedEntry:
    """FR03: _create_consolidated_entry aggregates fields and writes atomically."""

    def test_entry_id_has_L_prefix(self, tmp_path: Path, writer: FileStateWriter) -> None:
        """Generated entry ID starts with 'L-'."""
        cluster = make_cluster(3)
        entries_dir = tmp_path / "entries"
        entries_dir.mkdir()

        entry = _create_consolidated_entry(cluster, "summary", "detail", entries_dir, writer)
        assert str(entry["id"]).startswith("L-")

    def test_impact_is_max_of_cluster(self, tmp_path: Path, writer: FileStateWriter) -> None:
        """impact = max(cluster impacts)."""
        cluster = [
            {"id": "e1", "impact": 0.3},
            {"id": "e2", "impact": 0.7},
            {"id": "e3", "impact": 0.5},
        ]
        entries_dir = tmp_path / "entries"
        entries_dir.mkdir()

        entry = _create_consolidated_entry(cluster, "s", "d", entries_dir, writer)
        assert entry["impact"] == pytest.approx(0.7)

    def test_tags_sorted_union(self, tmp_path: Path, writer: FileStateWriter) -> None:
        """tags = sorted union of all cluster tags (deduplicated)."""
        cluster = [
            {"id": "e1", "tags": ["beta", "alpha"]},
            {"id": "e2", "tags": ["alpha", "gamma"]},
            {"id": "e3", "tags": ["delta"]},
        ]
        entries_dir = tmp_path / "entries"
        entries_dir.mkdir()

        entry = _create_consolidated_entry(cluster, "s", "d", entries_dir, writer)
        assert entry["tags"] == ["alpha", "beta", "delta", "gamma"]

    def test_evidence_deduplicated_union(self, tmp_path: Path, writer: FileStateWriter) -> None:
        """evidence = union of all cluster evidence (deduplicated)."""
        cluster = [
            {"id": "e1", "evidence": ["ev1", "ev2"]},
            {"id": "e2", "evidence": ["ev2", "ev3"]},
            {"id": "e3", "evidence": ["ev4"]},
        ]
        entries_dir = tmp_path / "entries"
        entries_dir.mkdir()

        entry = _create_consolidated_entry(cluster, "s", "d", entries_dir, writer)
        evidence = list(entry["evidence"])  # type: ignore[arg-type]
        assert sorted(evidence) == ["ev1", "ev2", "ev3", "ev4"]

    def test_recurrence_is_cluster_size(self, tmp_path: Path, writer: FileStateWriter) -> None:
        """FIX-071-FR06: recurrence = len(cluster), not sum."""
        cluster = [
            {"id": "e1", "recurrence": 2},
            {"id": "e2", "recurrence": 3},
            {"id": "e3", "recurrence": 1},
        ]
        entries_dir = tmp_path / "entries"
        entries_dir.mkdir()

        entry = _create_consolidated_entry(cluster, "s", "d", entries_dir, writer)
        assert entry["recurrence"] == 3  # len(cluster), not sum(2+3+1)

    def test_q_value_is_max(self, tmp_path: Path, writer: FileStateWriter) -> None:
        """q_value = max of cluster q_values."""
        cluster = [
            {"id": "e1", "q_value": 0.2},
            {"id": "e2", "q_value": 0.8},
            {"id": "e3", "q_value": 0.5},
        ]
        entries_dir = tmp_path / "entries"
        entries_dir.mkdir()

        entry = _create_consolidated_entry(cluster, "s", "d", entries_dir, writer)
        assert entry["q_value"] == pytest.approx(0.8)

    def test_source_type_is_consolidated(self, tmp_path: Path, writer: FileStateWriter) -> None:
        """source_type = 'consolidated'."""
        cluster = make_cluster(3)
        entries_dir = tmp_path / "entries"
        entries_dir.mkdir()

        entry = _create_consolidated_entry(cluster, "s", "d", entries_dir, writer)
        assert entry["source_type"] == "consolidated"

    def test_consolidated_from_contains_cluster_ids(self, tmp_path: Path, writer: FileStateWriter) -> None:
        """consolidated_from contains IDs of all cluster entries."""
        cluster = make_cluster(3)
        entries_dir = tmp_path / "entries"
        entries_dir.mkdir()

        entry = _create_consolidated_entry(cluster, "s", "d", entries_dir, writer)
        consolidated_from = list(entry["consolidated_from"])  # type: ignore[arg-type]
        assert "L-entry000" in consolidated_from
        assert "L-entry001" in consolidated_from
        assert "L-entry002" in consolidated_from

    def test_entry_written_to_disk(self, tmp_path: Path, writer: FileStateWriter, reader: FileStateReader) -> None:
        """Entry is written atomically to entries_dir as a YAML file."""
        cluster = make_cluster(3)
        entries_dir = tmp_path / "entries"
        entries_dir.mkdir()

        entry = _create_consolidated_entry(cluster, "s", "d", entries_dir, writer)
        entry_id = str(entry["id"])
        slug = entry_id.replace("/", "-")
        written_path = entries_dir / f"{slug}.yaml"

        assert written_path.exists()
        data = reader.read_yaml(written_path)
        assert data["id"] == entry_id

    def test_missing_fields_use_defaults(self, tmp_path: Path, writer: FileStateWriter) -> None:
        """Cluster entries missing fields fall back to defaults."""
        cluster = [
            {"id": "e1"},  # no impact, tags, evidence, recurrence, q_value
            {"id": "e2"},
            {"id": "e3"},
        ]
        entries_dir = tmp_path / "entries"
        entries_dir.mkdir()

        # Should not raise
        entry = _create_consolidated_entry(cluster, "s", "d", entries_dir, writer)
        assert float(str(entry["impact"])) == pytest.approx(0.5)
        assert entry["tags"] == []
        assert entry["recurrence"] == 3

    def test_status_is_active(self, tmp_path: Path, writer: FileStateWriter) -> None:
        """New consolidated entry has status='active'."""
        cluster = make_cluster(3)
        entries_dir = tmp_path / "entries"
        entries_dir.mkdir()

        entry = _create_consolidated_entry(cluster, "s", "d", entries_dir, writer)
        assert entry["status"] == "active"

class TestCreateConsolidatedEntryEdgeCases:
    """Edge cases for _create_consolidated_entry field aggregation."""

    def test_entries_without_id_excluded_from_consolidated_from(self, tmp_path: Path, writer: FileStateWriter) -> None:
        """Entries missing 'id' field are excluded from consolidated_from list."""
        cluster = [
            {"id": "e1", "summary": "s1"},
            {"summary": "no id entry"},
            {"id": "e3", "summary": "s3"},
        ]
        entries_dir = tmp_path / "entries"
        entries_dir.mkdir()

        entry = _create_consolidated_entry(cluster, "s", "d", entries_dir, writer)
        consolidated_from = list(entry["consolidated_from"])  # type: ignore[arg-type]
        assert "e1" in consolidated_from
        assert "e3" in consolidated_from
        assert len(consolidated_from) == 2

    def test_evidence_preserves_insertion_order(self, tmp_path: Path, writer: FileStateWriter) -> None:
        """Deduplicated evidence preserves original insertion order."""
        cluster = [
            {"id": "e1", "evidence": ["third", "first"]},
            {"id": "e2", "evidence": ["first", "second"]},
        ]
        entries_dir = tmp_path / "entries"
        entries_dir.mkdir()

        entry = _create_consolidated_entry(cluster, "s", "d", entries_dir, writer)
        evidence = list(entry["evidence"])  # type: ignore[arg-type]
        # dict.fromkeys preserves insertion order: third, first, second
        assert evidence == ["third", "first", "second"]

    def test_tags_none_treated_as_empty(self, tmp_path: Path, writer: FileStateWriter) -> None:
        """Entries with tags=None produce empty tag union."""
        cluster = [
            {"id": "e1", "tags": None},
            {"id": "e2", "tags": None},
            {"id": "e3"},
        ]
        entries_dir = tmp_path / "entries"
        entries_dir.mkdir()

        entry = _create_consolidated_entry(cluster, "s", "d", entries_dir, writer)
        assert entry["tags"] == []

    def test_evidence_none_treated_as_empty(self, tmp_path: Path, writer: FileStateWriter) -> None:
        """Entries with evidence=None produce empty evidence list."""
        cluster = [
            {"id": "e1", "evidence": None},
            {"id": "e2"},
        ]
        entries_dir = tmp_path / "entries"
        entries_dir.mkdir()

        entry = _create_consolidated_entry(cluster, "s", "d", entries_dir, writer)
        assert list(entry["evidence"]) == []  # type: ignore[arg-type]

    def test_single_entry_cluster_aggregation(self, tmp_path: Path, writer: FileStateWriter) -> None:
        """Single-entry cluster uses that entry's values directly."""
        cluster = [
            {
                "id": "only",
                "impact": 0.9,
                "tags": ["solo"],
                "evidence": ["proof"],
                "recurrence": 5,
                "q_value": 0.7,
            }
        ]
        entries_dir = tmp_path / "entries"
        entries_dir.mkdir()

        entry = _create_consolidated_entry(cluster, "summary", "detail", entries_dir, writer)
        assert entry["impact"] == pytest.approx(0.9)
        assert entry["tags"] == ["solo"]
        assert list(entry["evidence"]) == ["proof"]  # type: ignore[arg-type]
        assert entry["recurrence"] == 1  # FIX-071-FR06: len(cluster), not original recurrence
        assert entry["q_value"] == pytest.approx(0.7)

    def test_date_fields_set_to_today(self, tmp_path: Path, writer: FileStateWriter) -> None:
        """created, updated, last_accessed_at are set to today's date."""
        from datetime import datetime, timezone

        cluster = make_cluster(2)
        entries_dir = tmp_path / "entries"
        entries_dir.mkdir()

        entry = _create_consolidated_entry(cluster, "s", "d", entries_dir, writer)
        today = datetime.now(tz=timezone.utc).date().isoformat()
        assert entry["created"] == today
        assert entry["updated"] == today
        assert entry["last_accessed_at"] == today

    def test_string_impact_values_parsed_correctly(self, tmp_path: Path, writer: FileStateWriter) -> None:
        """Impact values stored as strings are parsed to floats for max()."""
        cluster = [
            {"id": "e1", "impact": "0.3"},
            {"id": "e2", "impact": "0.9"},
        ]
        entries_dir = tmp_path / "entries"
        entries_dir.mkdir()

        entry = _create_consolidated_entry(cluster, "s", "d", entries_dir, writer)
        assert float(str(entry["impact"])) == pytest.approx(0.9)
