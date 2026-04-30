"""Branch coverage tests for hot-tier and misc tier utilities in tiers.py."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from trw_mcp.models.config import TRWConfig
from trw_mcp.state.persistence import FileStateReader, FileStateWriter
from trw_mcp.state.tiers import TierManager

from tests._tiers_branches_support import _make_entry, _setup_entries_dir


class TestFlushLastAccessedException:
    """Test _flush_last_accessed when read/write raises."""

    def test_flush_last_accessed_exception_logged(self, tmp_path: Path) -> None:
        """Lines 240-241: exception during YAML read/write is logged, not raised."""
        trw_dir = tmp_path / ".trw"
        entries_dir = _setup_entries_dir(trw_dir)

        entry_path = entries_dir / "bad-entry.yaml"
        entry_path.write_text("id: bad-entry\n", encoding="utf-8")

        reader = MagicMock(spec=FileStateReader)
        reader.read_yaml.side_effect = Exception("disk failure")

        mgr = TierManager(trw_dir, reader=reader)
        mgr._flush_last_accessed("bad-entry")
        reader.read_yaml.assert_called_once()


class TestImportanceScoreEdgeCases:
    """Edge cases for compute_importance_score not covered by test_tiers.py."""

    def test_zero_weights_returns_zero(self) -> None:
        """All weights zero means total_w == 0; normalization skipped, score == 0."""
        from trw_mcp.state.tiers import compute_importance_score

        cfg = TRWConfig(memory_score_w1=0.0, memory_score_w2=0.0, memory_score_w3=0.0)
        entry: dict[str, object] = {
            "id": "z",
            "summary": "test",
            "detail": "",
            "impact": 0.9,
            "last_accessed_at": datetime.now(tz=timezone.utc).date().isoformat(),
        }
        score = compute_importance_score(entry, ["test"], config=cfg)
        assert score == 0.0

    def test_weights_exactly_one_no_normalization(self) -> None:
        """Weights summing to exactly 1.0 skip normalization; score unchanged."""
        from trw_mcp.state.tiers import compute_importance_score

        cfg = TRWConfig(memory_score_w1=0.5, memory_score_w2=0.3, memory_score_w3=0.2)
        today = datetime.now(tz=timezone.utc).date().isoformat()
        entry: dict[str, object] = {
            "id": "x",
            "summary": "test keyword",
            "detail": "",
            "impact": 1.0,
            "last_accessed_at": today,
        }
        score = compute_importance_score(entry, ["test", "keyword"], config=cfg)
        assert 0.9 <= score <= 1.0

    def test_zero_half_life_recency_is_one(self) -> None:
        """When half_life is 0, decay_rate is 0 and recency always == 1.0."""
        from trw_mcp.state.tiers import compute_importance_score

        cfg = TRWConfig(
            memory_score_w1=0.0,
            memory_score_w2=1.0,
            memory_score_w3=0.0,
            learning_decay_half_life_days=0,
        )
        old_entry: dict[str, object] = {
            "id": "old",
            "summary": "",
            "detail": "",
            "impact": 0.5,
            "last_accessed_at": (datetime.now(tz=timezone.utc).date() - timedelta(days=500)).isoformat(),
        }
        score = compute_importance_score(old_entry, [], config=cfg)
        assert score == pytest.approx(1.0, abs=0.01)

    def test_missing_impact_defaults_to_half(self) -> None:
        """Entry without 'impact' field defaults to 0.5."""
        from trw_mcp.state.tiers import compute_importance_score

        cfg = TRWConfig(memory_score_w1=0.0, memory_score_w2=0.0, memory_score_w3=1.0)
        entry: dict[str, object] = {
            "id": "no-impact",
            "summary": "",
            "detail": "",
            "last_accessed_at": datetime.now(tz=timezone.utc).date().isoformat(),
        }
        score = compute_importance_score(entry, [], config=cfg)
        assert score == pytest.approx(0.5, abs=0.01)

    def test_config_none_uses_get_config(self) -> None:
        """config=None falls back to get_config() singleton."""
        from trw_mcp.models.config import _reset_config
        from trw_mcp.state.tiers import compute_importance_score

        cfg = TRWConfig(memory_score_w1=0.0, memory_score_w2=0.0, memory_score_w3=1.0)
        _reset_config(cfg)
        entry: dict[str, object] = {
            "id": "x",
            "summary": "",
            "detail": "",
            "impact": 0.8,
            "last_accessed_at": datetime.now(tz=timezone.utc).date().isoformat(),
        }
        score = compute_importance_score(entry, [], config=None)
        assert score == pytest.approx(0.8, abs=0.01)

    def test_impact_string_parsed_as_float(self) -> None:
        """Impact stored as string (from raw YAML) is parsed correctly."""
        from trw_mcp.state.tiers import compute_importance_score

        cfg = TRWConfig(memory_score_w1=0.0, memory_score_w2=0.0, memory_score_w3=1.0)
        entry: dict[str, object] = {
            "id": "x",
            "summary": "",
            "detail": "",
            "impact": "0.7",
            "last_accessed_at": datetime.now(tz=timezone.utc).date().isoformat(),
        }
        score = compute_importance_score(entry, [], config=cfg)
        assert score == pytest.approx(0.7, abs=0.01)


class TestFlushLastAccessedSanitization:
    """Test _flush_last_accessed filename sanitization with special chars."""

    def test_flush_sanitizes_special_chars_in_id(self, tmp_path: Path) -> None:
        """Entry IDs with special characters are sanitized to dashes for filename."""
        trw_dir = tmp_path / ".trw"
        entries_dir = trw_dir / "learnings" / "entries"
        entries_dir.mkdir(parents=True, exist_ok=True)

        sanitized_path = entries_dir / "entry-with-spaces-.yaml"
        writer = FileStateWriter()
        writer.write_yaml(sanitized_path, {"id": "entry with spaces!", "summary": "test"})

        mgr = TierManager(trw_dir, writer=writer)
        mgr._flush_last_accessed("entry with spaces!")

        reader = FileStateReader()
        data = reader.read_yaml(sanitized_path)
        assert data["last_accessed_at"] == datetime.now(tz=timezone.utc).date().isoformat()

    def test_flush_nonexistent_file_is_noop(self, tmp_path: Path) -> None:
        """When YAML file for entry doesn't exist, flush silently returns."""
        trw_dir = tmp_path / ".trw"
        entries_dir = trw_dir / "learnings" / "entries"
        entries_dir.mkdir(parents=True, exist_ok=True)

        mgr = TierManager(trw_dir)
        mgr._flush_last_accessed("nonexistent-entry")


class TestHotTierMultipleEvictions:
    """Test hot tier behavior with multiple sequential evictions."""

    def test_hot_multiple_evictions_maintain_lru_order(self, tmp_path: Path) -> None:
        """When capacity is 2 and we add 4 entries, the first 2 are evicted in LRU order."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir(parents=True, exist_ok=True)
        (trw_dir / "learnings" / "entries").mkdir(parents=True, exist_ok=True)

        cfg = TRWConfig(memory_hot_max_entries=2)
        mgr = TierManager(trw_dir, config=cfg)

        mgr.hot_put("e1", _make_entry("e1"))
        mgr.hot_put("e2", _make_entry("e2"))
        assert mgr.hot_size == 2

        mgr.hot_put("e3", _make_entry("e3"))
        assert mgr.hot_get("e1") is None
        assert mgr.hot_size == 2

        mgr.hot_put("e4", _make_entry("e4"))
        assert mgr.hot_get("e2") is None
        assert mgr.hot_size == 2
        assert mgr.hot_get("e3") is not None
        assert mgr.hot_get("e4") is not None

    def test_hot_put_same_id_does_not_increase_size(self, tmp_path: Path) -> None:
        """Putting the same entry ID repeatedly does not grow the cache."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir(parents=True, exist_ok=True)
        mgr = TierManager(trw_dir)

        for i in range(10):
            mgr.hot_put("same-id", _make_entry("same-id", summary=f"version {i}"))

        assert mgr.hot_size == 1
        entry = mgr.hot_get("same-id")
        assert entry is not None
        assert entry.summary == "version 9"
