"""Behavior tests: merge_entries preserves typed protection fields (PRD-CORE-110).

Before the fix, merging a high-tier/verified/incident entry into a
normal/unverified/pattern survivor silently dropped the stronger protection
because merge_entries never folded the typed fields. These tests assert the
stronger tier/confidence/type wins.
"""

from __future__ import annotations

from pathlib import Path

from trw_mcp.state.dedup import merge_entries
from trw_mcp.state.persistence import FileStateReader, FileStateWriter


def _write_existing(
    entries_dir: Path,
    writer: FileStateWriter,
    *,
    protection_tier: str,
    confidence: str,
    learning_type: str,
) -> Path:
    path = entries_dir / "L-survivor.yaml"
    writer.write_yaml(
        path,
        {
            "id": "L-survivor",
            "summary": "shared summary",
            "detail": "existing detail",
            "tags": [],
            "evidence": [],
            "impact": 0.5,
            "status": "active",
            "recurrence": 1,
            "created": "2026-01-01",
            "updated": "2026-01-01",
            "merged_from": [],
            "protection_tier": protection_tier,
            "confidence": confidence,
            "type": learning_type,
        },
    )
    return path


class TestMergePreservesProtection:
    def test_incident_high_verified_new_entry_preserves_protection(
        self, tmp_path: Path, reader: FileStateReader, writer: FileStateWriter
    ) -> None:
        """A stronger new entry merged into a weak survivor upgrades all 3 fields."""
        entries_dir = tmp_path / "entries"
        entries_dir.mkdir()
        path = _write_existing(
            entries_dir,
            writer,
            protection_tier="normal",
            confidence="unverified",
            learning_type="pattern",
        )

        new_data: dict[str, object] = {
            "id": "L-incoming",
            "summary": "shared summary",
            "detail": "much longer incoming detail with operational weight",
            "tags": [],
            "evidence": [],
            "impact": 0.9,
            "protection_tier": "critical",
            "confidence": "verified",
            "type": "incident",
        }

        merge_entries(path, new_data, reader, writer)
        updated = reader.read_yaml(path)

        assert updated["protection_tier"] == "critical"
        assert updated["confidence"] == "verified"
        assert updated["type"] == "incident"

    def test_weaker_new_entry_does_not_downgrade_survivor(
        self, tmp_path: Path, reader: FileStateReader, writer: FileStateWriter
    ) -> None:
        """Merging a weak new entry into a strong survivor keeps the survivor strong."""
        entries_dir = tmp_path / "entries"
        entries_dir.mkdir()
        path = _write_existing(
            entries_dir,
            writer,
            protection_tier="critical",
            confidence="verified",
            learning_type="incident",
        )

        new_data: dict[str, object] = {
            "id": "L-weak",
            "summary": "shared summary",
            "detail": "short",
            "tags": [],
            "evidence": [],
            "impact": 0.3,
            "protection_tier": "normal",
            "confidence": "unverified",
            "type": "pattern",
        }

        merge_entries(path, new_data, reader, writer)
        updated = reader.read_yaml(path)

        assert updated["protection_tier"] == "critical"
        assert updated["confidence"] == "verified"
        assert updated["type"] == "incident"
