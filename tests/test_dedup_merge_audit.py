"""Tests for merge_into_survivor audit trail behavior."""

from __future__ import annotations

from pathlib import Path

from trw_mcp.state.dedup import merge_into_survivor
from trw_mcp.state.persistence import FileStateReader, FileStateWriter


class TestMergeAuditTrail:
    """Tests for FR03 — audit trail format in merge_into_survivor."""

    def test_merge_detail_uses_audit_trail_format(
        self, tmp_path: Path, reader: FileStateReader, writer: FileStateWriter
    ) -> None:
        """Merged detail uses '\\n---\\nMerged from {id} on {date}:\\n' format."""
        entries_dir = tmp_path / "entries"
        entries_dir.mkdir()

        existing_path = entries_dir / "L-audit01.yaml"
        writer.write_yaml(
            existing_path,
            {
                "id": "L-audit01",
                "summary": "s",
                "detail": "short existing",
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
            "id": "L-new-audit",
            "summary": "s",
            "detail": "this is a much longer detail that will trigger appending",
            "tags": [],
            "evidence": [],
            "impact": 0.5,
            "merged_from": [],
        }
        merge_into_survivor(existing_path, new_data, reader, writer)

        updated = reader.read_yaml(existing_path)
        detail = str(updated["detail"])
        # Must contain the audit trail marker
        assert "---" in detail
        assert "Merged from L-new-audit on" in detail
        assert "this is a much longer detail" in detail

    def test_merge_detail_audit_marker_when_new_shorter(
        self, tmp_path: Path, reader: FileStateReader, writer: FileStateWriter
    ) -> None:
        """A shorter incoming detail is still appended (PRD-CORE-042-FR03).

        Migrated: this test used to assert the opposite, pinning a length gate
        the requirement never had, which dropped the incoming detail.
        """
        entries_dir = tmp_path / "entries"
        entries_dir.mkdir()

        existing_path = entries_dir / "L-audit02.yaml"
        writer.write_yaml(
            existing_path,
            {
                "id": "L-audit02",
                "summary": "s",
                "detail": "much longer existing detail that is certainly long enough",
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
            "id": "L-short-new",
            "summary": "s",
            "detail": "short",
            "tags": [],
            "evidence": [],
            "impact": 0.5,
            "merged_from": [],
        }
        merge_into_survivor(existing_path, new_data, reader, writer)

        updated = reader.read_yaml(existing_path)
        detail = str(updated["detail"])
        assert detail.startswith("much longer existing detail that is certainly long enough\n---\n")
        assert "Merged from L-short-new on " in detail
        assert detail.endswith(":\nshort")
