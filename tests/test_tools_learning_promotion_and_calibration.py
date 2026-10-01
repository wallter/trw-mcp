"""Tests for promotion legacy behavior, distribution warnings, and calibration wiring."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests._memory_store_fake import FakeMemoryStore
from tests._tools_learning_shared import _entries_dir, _get_tools, instructions_sync_fn
from trw_mcp.state.persistence import FileStateReader, FileStateWriter


@pytest.fixture(autouse=True)
def _route_memory(fake_memory_store: FakeMemoryStore) -> FakeMemoryStore:
    """These tests only assert on trw_learn's YAML sidecar / sync status -- the fake route suffices (PRD-CORE-280 e1)."""
    return fake_memory_store


class TestClaudeMdSyncQValuePromotion:
    """Tests for PRD-CORE-004 Phase 1c — q_value-based promotion in claude_md_sync."""

    def test_immature_entry_uses_impact(self, tmp_path: Path, reader: FileStateReader, writer: FileStateWriter) -> None:
        """CORE-093: learning promotion removed — impact no longer drives CLAUDE.md content."""
        tools = _get_tools()
        tools["trw_learn"].fn(
            summary="Immature impact promotion test",
            detail="Uses impact because too few observations",
            impact=0.9,
        )

        sync_result = instructions_sync_fn(scope="root")
        # PRD-CORE-341: sync reports no promotion count; no learning reaches an instruction file
        assert "learnings_promoted" not in sync_result


class TestUnattributedCalibrationRetirement:
    """R10: unowned pooled recall statistics do not calibrate a caller."""

    def test_raw_impact_is_preserved_on_save(self, tmp_path: Path) -> None:
        """The actual registered learn caller preserves raw impact without use evidence."""
        tools = _get_tools()
        raw_impact = 0.9
        result = tools["trw_learn"].fn(
            summary="High impact learning",
            detail="Very important discovery",
            impact=raw_impact,
        )
        assert result["status"] == "recorded"

        reader = FileStateReader()
        stored = [reader.read_yaml(path) for path in _entries_dir(tmp_path).glob("*.yaml")]
        matching = [data for data in stored if data.get("id") == result["learning_id"]]
        assert len(matching) == 1
        assert float(str(matching[0]["impact"])) == raw_impact
