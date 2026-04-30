"""Tests for tiered memory storage warm and cold tiers — PRD-CORE-043 FR02/FR03."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

from trw_mcp.state.persistence import FileStateReader, FileStateWriter
from trw_mcp.state.tiers import TierManager

from tests._tiers_test_support import days_ago, make_tier_manager, write_entry_yaml


class TestWarmTier:
    """FR02: Warm tier vector search and keyword fallback."""

    def test_warm_add_sidecar_when_no_embedding(self, tmp_path: Path) -> None:
        """When no embedding, entry is stored in JSONL sidecar."""
        mgr = make_tier_manager(tmp_path)
        mgr.warm_add("w1", {"id": "w1", "summary": "async pattern caching"}, embedding=None)
        sidecar = mgr._warm_sidecar_path()
        assert sidecar.exists()
        records = [json.loads(line) for line in sidecar.read_text().splitlines() if line.strip()]
        assert any(record["id"] == "w1" for record in records)

    def test_warm_keyword_fallback_search(self, tmp_path: Path) -> None:
        """Keyword search over sidecar finds matching entries, excludes non-matching."""
        mgr = make_tier_manager(tmp_path)
        mgr.warm_add("w1", {"id": "w1", "summary": "pytest fixture pattern"}, None)
        mgr.warm_add("w2", {"id": "w2", "summary": "docker compose setup"}, None)
        results = mgr.warm_search(["pytest", "fixture"], query_embedding=None)
        ids = [result["id"] for result in results]
        assert "w1" in ids
        assert "w2" not in ids

    def test_warm_search_keyword_score_is_match_fraction(self, tmp_path: Path) -> None:
        """Score is ratio of matched tokens to query tokens."""
        mgr = make_tier_manager(tmp_path)
        mgr.warm_add("w1", {"id": "w1", "summary": "alpha beta gamma"}, None)
        results = mgr.warm_search(["alpha", "gamma", "delta"], query_embedding=None)
        assert len(results) == 1
        assert abs(float(str(results[0]["score"])) - 2 / 3) < 0.01

    def test_warm_sidecar_upsert_dedup(self, tmp_path: Path) -> None:
        """Upserting existing entry replaces it — no duplicates."""
        mgr = make_tier_manager(tmp_path)
        mgr.warm_add("w1", {"id": "w1", "summary": "original"}, None)
        mgr.warm_add("w1", {"id": "w1", "summary": "updated summary"}, None)
        sidecar = mgr._warm_sidecar_path()
        records = [json.loads(line) for line in sidecar.read_text().splitlines() if line.strip()]
        w1_records = [record for record in records if record["id"] == "w1"]
        assert len(w1_records) == 1
        assert w1_records[0]["summary"] == "updated summary"

    def test_warm_remove_from_sidecar(self, tmp_path: Path) -> None:
        """Entry removed from sidecar; others unaffected."""
        mgr = make_tier_manager(tmp_path)
        mgr.warm_add("w1", {"id": "w1", "summary": "to remove"}, None)
        mgr.warm_add("w2", {"id": "w2", "summary": "keep me"}, None)
        mgr.warm_remove("w1")
        sidecar = mgr._warm_sidecar_path()
        records = [json.loads(line) for line in sidecar.read_text().splitlines() if line.strip()]
        ids = [record["id"] for record in records]
        assert "w1" not in ids and "w2" in ids

    def test_warm_search_empty_returns_empty(self, tmp_path: Path) -> None:
        """Returns empty list when no sidecar exists."""
        assert make_tier_manager(tmp_path).warm_search(["nonexistent"], query_embedding=None) == []

    def test_warm_auto_creates_memory_directory(self, tmp_path: Path) -> None:
        """warm.db parent directory is auto-created on first warm_add."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        mgr = TierManager(trw_dir=trw_dir)
        mgr.warm_add("e1", {"id": "e1", "summary": "test"}, None)
        assert (trw_dir / "memory").exists()

    def test_warm_remove_nonexistent_is_noop(self, tmp_path: Path) -> None:
        """Removing an entry that doesn't exist does not raise."""
        make_tier_manager(tmp_path).warm_remove("nonexistent-id")

    def test_warm_search_with_embedding_uses_memory_store(self, tmp_path: Path) -> None:
        """Vector search via MemoryStore is called when embedding provided."""
        mgr = make_tier_manager(tmp_path)
        mock_store = MagicMock()
        mock_store.search.return_value = [("e1", 0.1), ("e2", 0.3)]
        mock_store.close = MagicMock()

        with patch("trw_mcp.state.memory_store.MemoryStore") as mock_cls:
            mock_cls.available.return_value = True
            mock_cls.return_value = mock_store
            results = mgr.warm_search(["test"], query_embedding=[0.1] * 384)

        result_ids = [result["id"] for result in results]
        assert "e1" in result_ids and "e2" in result_ids


class TestColdTier:
    """FR03: Cold tier archive, search, and promote."""

    def test_cold_archive_moves_entry_to_partition(self, tmp_path: Path) -> None:
        """Entry moved to .trw/memory/cold/YYYY/MM/; original deleted."""
        mgr = make_tier_manager(tmp_path)
        writer = FileStateWriter()
        entries_dir = tmp_path / ".trw" / "learnings" / "entries"
        src = write_entry_yaml(entries_dir, writer, "c1")
        mgr.cold_archive("c1", src)

        assert not src.exists()
        today = datetime.now(tz=timezone.utc).date()
        partition = tmp_path / ".trw" / "memory" / "cold" / str(today.year) / f"{today.month:02d}"
        assert len(list(partition.glob("*.yaml"))) == 1

    def test_cold_archive_creates_partition_dirs(self, tmp_path: Path) -> None:
        """Partition directories are auto-created."""
        mgr = make_tier_manager(tmp_path)
        writer = FileStateWriter()
        entries_dir = tmp_path / ".trw" / "learnings" / "entries"
        src = write_entry_yaml(entries_dir, writer, "c2")

        today = datetime.now(tz=timezone.utc).date()
        partition = tmp_path / ".trw" / "memory" / "cold" / str(today.year) / f"{today.month:02d}"
        assert not partition.exists()
        mgr.cold_archive("c2", src)
        assert partition.exists()

    def test_cold_archive_preserves_yaml_format(self, tmp_path: Path) -> None:
        """Archived YAML is identical in format — no schema change (NFR07)."""
        mgr = make_tier_manager(tmp_path)
        writer, reader = FileStateWriter(), FileStateReader()
        entries_dir = tmp_path / ".trw" / "learnings" / "entries"
        src = write_entry_yaml(entries_dir, writer, "c3", summary="format check")
        src_data = reader.read_yaml(src)
        mgr.cold_archive("c3", src)

        today = datetime.now(tz=timezone.utc).date()
        partition = tmp_path / ".trw" / "memory" / "cold" / str(today.year) / f"{today.month:02d}"
        archived_data = reader.read_yaml(list(partition.glob("*.yaml"))[0])
        assert archived_data["id"] == src_data["id"]
        assert archived_data["summary"] == src_data["summary"]

    def test_cold_search_keyword_match(self, tmp_path: Path) -> None:
        """Linear scan finds matching entries, excludes non-matching."""
        mgr = make_tier_manager(tmp_path)
        writer = FileStateWriter()
        entries_dir = tmp_path / ".trw" / "learnings" / "entries"
        src1 = write_entry_yaml(entries_dir, writer, "s1", summary="async pattern testing")
        src2 = write_entry_yaml(entries_dir, writer, "s2", summary="docker configuration")
        mgr.cold_archive("s1", src1)
        mgr.cold_archive("s2", src2)

        results = mgr.cold_search(["async", "pattern"])
        summaries = [str(result.get("summary", "")) for result in results]
        assert any("async" in summary for summary in summaries)
        assert not any("docker" in summary for summary in summaries)

    def test_cold_search_no_match_returns_empty(self, tmp_path: Path) -> None:
        """Returns empty for non-matching query."""
        mgr = make_tier_manager(tmp_path)
        writer = FileStateWriter()
        entries_dir = tmp_path / ".trw" / "learnings" / "entries"
        src = write_entry_yaml(entries_dir, writer, "nm", summary="something unrelated")
        mgr.cold_archive("nm", src)
        assert mgr.cold_search(["zzz_nonexistent_term"]) == []

    def test_cold_search_empty_dir_returns_empty(self, tmp_path: Path) -> None:
        """Returns empty list when cold directory doesn't exist."""
        assert make_tier_manager(tmp_path).cold_search(["anything"]) == []

    def test_cold_search_creates_no_files(self, tmp_path: Path) -> None:
        """cold_search is read-only — creates no new files."""
        mgr = make_tier_manager(tmp_path)
        mgr.cold_search(["test"])
        assert not (tmp_path / ".trw" / "memory" / "cold").exists()

    def test_cold_promote_returns_entry_data(self, tmp_path: Path) -> None:
        """Promoted entry data is returned."""
        mgr = make_tier_manager(tmp_path)
        writer = FileStateWriter()
        entries_dir = tmp_path / ".trw" / "learnings" / "entries"
        src = write_entry_yaml(entries_dir, writer, "p1", summary="promote me back")
        mgr.cold_archive("p1", src)

        result = mgr.cold_promote("p1")
        assert result is not None
        assert isinstance(result, dict)
        assert str(result.get("id", "")) == "p1"
        assert "summary" in result

    def test_cold_promote_updates_last_accessed(self, tmp_path: Path) -> None:
        """Promoted entry has last_accessed_at set to today."""
        mgr = make_tier_manager(tmp_path)
        writer = FileStateWriter()
        entries_dir = tmp_path / ".trw" / "learnings" / "entries"
        src = write_entry_yaml(entries_dir, writer, "p2", last_accessed_at=days_ago(30))
        mgr.cold_archive("p2", src)

        result = mgr.cold_promote("p2")
        assert result is not None
        assert isinstance(result, dict)
        assert str(result.get("id", "")) == "p2"
        assert str(result.get("last_accessed_at", "")) == datetime.now(tz=timezone.utc).date().isoformat()

    def test_cold_promote_removes_entry_from_cold(self, tmp_path: Path) -> None:
        """After promotion, entry is no longer in cold archive."""
        mgr = make_tier_manager(tmp_path)
        writer = FileStateWriter()
        entries_dir = tmp_path / ".trw" / "learnings" / "entries"
        src = write_entry_yaml(entries_dir, writer, "p3", summary="remove from cold")
        mgr.cold_archive("p3", src)
        mgr.cold_promote("p3")
        assert mgr.cold_search(["remove"]) == []

    def test_cold_promote_nonexistent_returns_none(self, tmp_path: Path) -> None:
        """Returns None for unknown ID."""
        assert make_tier_manager(tmp_path).cold_promote("does-not-exist") is None
