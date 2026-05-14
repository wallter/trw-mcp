"""Tests for recall search behavior and recall access tracking."""

from __future__ import annotations

import json
from pathlib import Path

from tests._tools_learning_shared import _CFG, _entries_dir, _get_tools
from trw_mcp.state.persistence import FileStateReader, FileStateWriter


class TestTrwRecall:
    """Tests for trw_recall tool."""

    def test_finds_matching_learning(self, tmp_path: Path) -> None:
        tools = _get_tools()

        # Record a learning
        tools["trw_learn"].fn(
            summary="Database connection pooling gotcha",
            detail="Always close connections in finally block",
            tags=["database", "gotcha"],
            impact=0.9,
        )

        # Search for it
        result = tools["trw_recall"].fn(query="database")
        assert result["total_matches"] >= 1
        assert len(result["learnings"]) >= 1

    def test_no_matches(self, tmp_path: Path) -> None:
        tools = _get_tools()
        result = tools["trw_recall"].fn(query="nonexistent-query-xyz")
        assert result["total_matches"] == 0

    def test_tag_filter(self, tmp_path: Path) -> None:
        tools = _get_tools()

        tools["trw_learn"].fn(
            summary="Tagged learning",
            detail="Has specific tags",
            tags=["python", "testing"],
            impact=0.7,
        )
        tools["trw_learn"].fn(
            summary="Other tagged learning",
            detail="Has different tags",
            tags=["javascript"],
            impact=0.7,
        )

        result = tools["trw_recall"].fn(query="tagged", tags=["python"])
        # Should only find the python-tagged one
        python_results = [
            entry
            for entry in result["learnings"]
            if "python" in (entry.get("tags", []) if isinstance(entry.get("tags"), list) else [])
        ]
        assert len(python_results) >= 1

    def test_min_impact_filter(self, tmp_path: Path) -> None:
        tools = _get_tools()

        tools["trw_learn"].fn(
            summary="Low impact learning filter test",
            detail="Low value",
            impact=0.2,
        )
        tools["trw_learn"].fn(
            summary="High impact learning filter test",
            detail="High value",
            impact=0.9,
        )

        result = tools["trw_recall"].fn(query="impact learning filter", min_impact=0.5)
        assert all(float(entry.get("impact", 0)) >= 0.5 for entry in result["learnings"])

    def test_multi_word_query_matches_tokens(self, tmp_path: Path) -> None:
        tools = _get_tools()

        tools["trw_learn"].fn(
            summary="Database connection pooling",
            detail="Use pool for PostgreSQL connections",
            tags=["database"],
            impact=0.8,
        )

        # Multi-word query where words appear in different fields
        result = tools["trw_recall"].fn(query="database postgresql")
        assert result["total_matches"] >= 1

        # Multi-word query where both words exist but separately
        result = tools["trw_recall"].fn(query="pooling connections")
        assert result["total_matches"] >= 1

        # Query with one matching and one missing word — union semantics
        # still matches on "database" even though "redis" matches nothing
        result = tools["trw_recall"].fn(query="database redis")
        assert result["total_matches"] >= 1

        # Query where NO words appear at all
        result = tools["trw_recall"].fn(query="kubernetes helm")
        assert result["total_matches"] == 0

class TestTrwRecallAccessTracking:
    """Tests for PRD-CORE-004 Phase 1a — access tracking in trw_recall."""

    def test_recall_updates_last_accessed_at(self, tmp_path: Path) -> None:
        """trw_recall sets last_accessed_at on returned entries."""
        from datetime import datetime, timezone

        from trw_mcp.state.memory_adapter import find_entry_by_id as adapter_find

        # Capture UTC date before and after to handle midnight boundary
        utc_date_before = datetime.now(timezone.utc).date().isoformat()

        tools = _get_tools()
        result = tools["trw_learn"].fn(
            summary="Access tracking date test",
            detail="Should have last_accessed_at updated",
            impact=0.8,
        )
        lid = result["learning_id"]

        # Recall should update access tracking
        tools["trw_recall"].fn(query="access tracking date")

        utc_date_after = datetime.now(timezone.utc).date().isoformat()

        # Verify via SQLite that last_accessed_at was set (adapter uses UTC)
        trw_dir = tmp_path / _CFG.trw_dir
        data = adapter_find(trw_dir, lid)
        assert data is not None, "Entry not found in SQLite"
        accessed = data.get("last_accessed_at")
        assert accessed in (utc_date_before, utc_date_after), (
            f"last_accessed_at={accessed} not in [{utc_date_before}, {utc_date_after}]"
        )

    def test_recall_increments_access_count(self, tmp_path: Path) -> None:
        """trw_recall increments access_count on each matching recall."""
        from trw_mcp.state.memory_adapter import find_entry_by_id as adapter_find

        tools = _get_tools()
        result = tools["trw_learn"].fn(
            summary="Access count increment test",
            detail="Should increment access_count",
            impact=0.8,
        )
        lid = result["learning_id"]

        # Recall multiple times
        tools["trw_recall"].fn(query="access count increment")
        tools["trw_recall"].fn(query="access count increment")
        tools["trw_recall"].fn(query="access count increment")

        # Verify via SQLite that access_count == 3
        trw_dir = tmp_path / _CFG.trw_dir
        data = adapter_find(trw_dir, lid)
        assert data is not None, "Entry not found in SQLite"
        assert int(str(data.get("access_count", 0))) == 3

    def test_recall_only_updates_matched_entries(self, tmp_path: Path, reader: FileStateReader) -> None:
        """trw_recall does not touch entries that don't match the query."""
        tools = _get_tools()

        tools["trw_learn"].fn(
            summary="Database pooling gotcha xray",
            detail="This should be accessed",
            impact=0.8,
        )
        r2 = tools["trw_learn"].fn(
            summary="Filesystem permissions zulu",
            detail="This should NOT be accessed",
            impact=0.8,
        )

        tools["trw_recall"].fn(query="database pooling xray")

        entries_dir = _entries_dir(tmp_path)
        for entry_file in entries_dir.glob("*.yaml"):
            data = reader.read_yaml(entry_file)
            if data.get("id") == r2["learning_id"]:
                # Unmatched entry should have access_count 0 and no last_accessed_at
                assert int(str(data.get("access_count", 0))) == 0
                assert data.get("last_accessed_at") is None
                break

    def test_recall_no_match_no_access_update(self, tmp_path: Path) -> None:
        """When query has no matches, no access tracking updates occur."""
        tools = _get_tools()
        tools["trw_learn"].fn(
            summary="No match access test",
            detail="Should not be accessed",
            impact=0.8,
        )

        tools["trw_recall"].fn(query="zzz_nonexistent_xyz")

        receipt_path = tmp_path / _CFG.trw_dir / _CFG.learnings_dir / _CFG.receipts_dir / "recall_log.jsonl"
        # Receipt should still be logged (with empty matched_ids)
        if receipt_path.exists():
            lines = receipt_path.read_text(encoding="utf-8").strip().split("\n")
            record = json.loads(lines[-1])
            assert len(record["matched_ids"]) == 0

    def test_new_fields_default_for_existing_entries(self, tmp_path: Path, writer: FileStateWriter) -> None:
        """Entries created without new fields get defaults (lazy migration)."""
        from trw_mcp.state.memory_adapter import find_entry_by_id as adapter_find
        from trw_mcp.state.memory_adapter import store_learning

        # Simulate an old entry without last_accessed_at or access_count
        entries_dir = _entries_dir(tmp_path)
        writer.ensure_dir(entries_dir)
        old_entry = {
            "id": "L-oldentry1",
            "summary": "Legacy entry without access fields",
            "detail": "Created before Phase 1a",
            "tags": ["legacy"],
            "evidence": [],
            "impact": 0.7,
            "status": "active",
            "recurrence": 1,
            "created": "2026-01-01",
            "updated": "2026-01-01",
            "resolved_at": None,
            "promoted_to_claude_md": False,
            # Deliberately missing: last_accessed_at, access_count
        }
        writer.write_yaml(entries_dir / "2026-01-01-legacy-entry.yaml", old_entry)

        # Update the index
        index_path = tmp_path / _CFG.trw_dir / _CFG.learnings_dir / "index.yaml"
        writer.write_yaml(
            index_path,
            {
                "entries": [
                    {
                        "id": "L-oldentry1",
                        "summary": "Legacy entry without access fields",
                        "tags": ["legacy"],
                        "impact": 0.7,
                        "created": "2026-01-01",
                    }
                ],
                "total_count": 1,
            },
        )

        # Also store the legacy entry in SQLite so the adapter can find and track it
        trw_dir = tmp_path / _CFG.trw_dir
        store_learning(
            trw_dir,
            "L-oldentry1",
            "Legacy entry without access fields",
            "Created before Phase 1a",
            tags=["legacy"],
            impact=0.7,
        )

        tools = _get_tools()
        result = tools["trw_recall"].fn(query="legacy entry")
        assert result["total_matches"] == 1

        # After recall, the SQLite entry should have access tracking fields updated
        data = adapter_find(trw_dir, "L-oldentry1")
        assert data is not None, "Entry not found in SQLite"
        assert int(str(data.get("access_count", 0))) == 1
        assert data.get("last_accessed_at") is not None

    def test_wildcard_recall_updates_all_entries(self, tmp_path: Path) -> None:
        """Wildcard '*' recall updates access tracking for all returned entries."""
        from trw_mcp.state.memory_adapter import find_entry_by_id as adapter_find

        tools = _get_tools()
        r1 = tools["trw_learn"].fn(
            summary="Wildcard access test one",
            detail="First",
            impact=0.8,
        )
        r2 = tools["trw_learn"].fn(
            summary="Wildcard access test two",
            detail="Second",
            impact=0.8,
        )

        tools["trw_recall"].fn(query="*")

        # Verify via SQLite that access tracking was updated for both entries
        trw_dir = tmp_path / _CFG.trw_dir
        for lid in (r1["learning_id"], r2["learning_id"]):
            data = adapter_find(trw_dir, lid)
            assert data is not None, f"Entry {lid} not found in SQLite"
            assert int(str(data.get("access_count", 0))) == 1
