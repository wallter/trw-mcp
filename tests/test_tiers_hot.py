"""Tests for tiered memory storage hot tier — PRD-CORE-043 FR01."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

from trw_mcp.models.config import TRWConfig
from trw_mcp.state.persistence import FileStateReader, FileStateWriter
from trw_mcp.state.tiers import TierManager

from tests._tiers_test_support import make_entry, make_tier_manager


class TestHotTier:
    """FR01: Hot tier LRU cache."""

    def test_hot_get_returns_none_on_miss(self, tmp_path: Path) -> None:
        """Cache miss returns None without raising."""
        assert make_tier_manager(tmp_path).hot_get("nonexistent-id") is None

    def test_hot_put_and_get(self, tmp_path: Path) -> None:
        """Put then get returns the entry."""
        mgr = make_tier_manager(tmp_path)
        mgr.hot_put("e1", make_entry("e1"))
        result = mgr.hot_get("e1")
        assert result is not None
        assert result.id == "e1"
        assert result.summary == "test summary"

    def test_hot_get_cache_hit_no_file_io(self, tmp_path: Path) -> None:
        """Cache hit must not perform any file I/O (FR01 acceptance criterion)."""
        mgr = make_tier_manager(tmp_path)
        mgr.hot_put("e1", make_entry("e1"))
        mgr._reader = MagicMock(spec=FileStateReader)
        mgr._reader.read_yaml.side_effect = AssertionError("unexpected file I/O on cache hit")
        result = mgr.hot_get("e1")
        assert result is not None
        assert result.id == "e1"
        mgr._reader.read_yaml.assert_not_called()

    def test_hot_lru_eviction(self, tmp_path: Path) -> None:
        """When capacity exceeded, LRU entry is evicted."""
        mgr = make_tier_manager(tmp_path, TRWConfig(memory_hot_max_entries=3))
        for i in range(3):
            mgr.hot_put(f"e{i}", make_entry(f"e{i}"))
        mgr.hot_put("e3", make_entry("e3"))
        assert mgr.hot_get("e0") is None
        assert mgr.hot_get("e1") is not None
        assert mgr.hot_get("e3") is not None

    def test_hot_get_moves_to_mru(self, tmp_path: Path) -> None:
        """Accessing an entry moves it to most-recently-used position."""
        mgr = make_tier_manager(tmp_path, TRWConfig(memory_hot_max_entries=3))
        for i in range(3):
            mgr.hot_put(f"e{i}", make_entry(f"e{i}"))
        mgr.hot_get("e0")
        mgr.hot_put("e3", make_entry("e3"))
        assert mgr.hot_get("e0") is not None
        assert mgr.hot_get("e1") is None
        assert mgr.hot_get("e3") is not None

    def test_hot_eviction_writes_last_accessed(self, tmp_path: Path) -> None:
        """Evicted entry has last_accessed_at written to its YAML file."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        entries_dir = trw_dir / "learnings" / "entries"
        entries_dir.mkdir(parents=True)
        writer, reader = FileStateWriter(), FileStateReader()
        yaml_path = entries_dir / "e0.yaml"
        writer.write_yaml(yaml_path, {"id": "e0", "summary": "evict me", "last_accessed_at": None})

        mgr = TierManager(trw_dir=trw_dir, reader=reader, writer=writer, config=TRWConfig(memory_hot_max_entries=1))
        mgr.hot_put("e0", make_entry("e0"))
        mgr.hot_put("e1", make_entry("e1"))

        assert reader.read_yaml(yaml_path).get("last_accessed_at") == datetime.now(tz=timezone.utc).date().isoformat()

    def test_hot_eviction_no_file_is_noop(self, tmp_path: Path) -> None:
        """Eviction when no YAML file exists does not raise."""
        mgr = make_tier_manager(tmp_path, TRWConfig(memory_hot_max_entries=1))
        mgr.hot_put("e0", make_entry("e0"))
        mgr.hot_put("e1", make_entry("e1"))
        assert mgr.hot_get("e0") is None

    def test_hot_clear(self, tmp_path: Path) -> None:
        """clear() empties the cache."""
        mgr = make_tier_manager(tmp_path)
        for i in range(5):
            mgr.hot_put(f"e{i}", make_entry(f"e{i}"))
        mgr.hot_clear()
        assert mgr.hot_size == 0

    def test_hot_size_property(self, tmp_path: Path) -> None:
        """hot_size reflects current count."""
        mgr = make_tier_manager(tmp_path)
        assert mgr.hot_size == 0
        mgr.hot_put("e1", make_entry("e1"))
        assert mgr.hot_size == 1

    def test_hot_put_refresh_existing(self, tmp_path: Path) -> None:
        """Putting same ID twice updates entry and moves it to MRU."""
        mgr = make_tier_manager(tmp_path, TRWConfig(memory_hot_max_entries=2))
        mgr.hot_put("e1", make_entry("e1", summary="original"))
        mgr.hot_put("e2", make_entry("e2"))
        mgr.hot_put("e1", make_entry("e1", summary="updated"))
        mgr.hot_put("e3", make_entry("e3"))
        result = mgr.hot_get("e1")
        assert result is not None and result.summary == "updated"
        assert mgr.hot_get("e2") is None
