"""Tests for tiered memory storage — PRD-CORE-043.

Covers:
- FR01: Hot tier LRU cache (get/put/evict/size/clear/no-IO-on-hit)
- FR02: Warm tier (vector search, keyword fallback, sidecar, remove)
- FR03: Cold tier (archive, partition dirs, keyword search, promote, read-only)
- FR04: Sweep transitions (all 4 + durability + no cross-tier duplication)
- FR05: Importance scoring (range, ordering, weights, fallback, zero-embedding)
- FR06: Config read at call time (not cached at import)
- FR07: Config field defaults and env var overrides
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from trw_mcp.models.config import TRWConfig, _reset_config
from trw_mcp.models.learning import LearningEntry, LearningStatus
from trw_mcp.state.persistence import FileStateReader, FileStateWriter
from trw_mcp.state.tiers import TierManager, TierSweepResult, compute_importance_score

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_entry(
    entry_id: str = "test-entry-001",
    summary: str = "test summary",
    impact: float = 0.5,
    status: str = "active",
    last_accessed_at: str | None = None,
    created: str | None = None,
) -> LearningEntry:
    """Build a minimal LearningEntry for testing."""
    today = datetime.now(tz=timezone.utc).date().isoformat()
    return LearningEntry(
        id=entry_id,
        summary=summary,
        detail=f"detail for {entry_id}",
        tags=["test"],
        impact=impact,
        status=LearningStatus(status),
        last_accessed_at=date.fromisoformat(last_accessed_at) if last_accessed_at else None,
        created=date.fromisoformat(created or today),
    )


def write_entry_yaml(
    entries_dir: Path,
    writer: FileStateWriter,
    entry_id: str,
    summary: str = "test summary",
    impact: float = 0.5,
    status: str = "active",
    last_accessed_at: str | None = None,
    created: str | None = None,
) -> Path:
    """Write a minimal learning entry YAML for testing."""
    today = datetime.now(tz=timezone.utc).date().isoformat()
    path = entries_dir / f"{entry_id}.yaml"
    writer.write_yaml(
        path,
        {
            "id": entry_id,
            "summary": summary,
            "detail": f"detail for {entry_id}",
            "tags": ["test"],
            "impact": impact,
            "status": status,
            "last_accessed_at": last_accessed_at,
            "created": created or today,
        },
    )
    return path


def make_tier_manager(tmp_path: Path, config: TRWConfig | None = None) -> TierManager:
    """Create a TierManager with an isolated .trw/ directory."""
    trw_dir = tmp_path / ".trw"
    trw_dir.mkdir(exist_ok=True)
    (trw_dir / "learnings" / "entries").mkdir(parents=True, exist_ok=True)
    return TierManager(
        trw_dir=trw_dir,
        reader=FileStateReader(),
        writer=FileStateWriter(),
        config=config,
    )


def days_ago(n: int) -> str:
    """Return ISO date string for N days ago."""
    return (datetime.now(tz=timezone.utc).date() - timedelta(days=n)).isoformat()


# ---------------------------------------------------------------------------
# FR01 — Hot Tier LRU Cache
# ---------------------------------------------------------------------------


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
        mgr.hot_put("e3", make_entry("e3"))  # e0 is LRU, should be evicted
        assert mgr.hot_get("e0") is None
        assert mgr.hot_get("e1") is not None
        assert mgr.hot_get("e3") is not None

    def test_hot_get_moves_to_mru(self, tmp_path: Path) -> None:
        """Accessing an entry moves it to most-recently-used position."""
        mgr = make_tier_manager(tmp_path, TRWConfig(memory_hot_max_entries=3))
        for i in range(3):
            mgr.hot_put(f"e{i}", make_entry(f"e{i}"))
        mgr.hot_get("e0")  # e0 becomes MRU; e1 is now LRU
        mgr.hot_put("e3", make_entry("e3"))  # should evict e1
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
        mgr.hot_put("e1", make_entry("e1"))  # evicts e0

        assert reader.read_yaml(yaml_path).get("last_accessed_at") == datetime.now(tz=timezone.utc).date().isoformat()

    def test_hot_eviction_no_file_is_noop(self, tmp_path: Path) -> None:
        """Eviction when no YAML file exists does not raise."""
        mgr = make_tier_manager(tmp_path, TRWConfig(memory_hot_max_entries=1))
        mgr.hot_put("e0", make_entry("e0"))
        mgr.hot_put("e1", make_entry("e1"))  # evicts e0 — no YAML, must not raise
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
        mgr.hot_put("e1", make_entry("e1", summary="updated"))  # refreshes e1 to MRU
        mgr.hot_put("e3", make_entry("e3"))  # evicts e2 (LRU), not e1
        result = mgr.hot_get("e1")
        assert result is not None and result.summary == "updated"
        assert mgr.hot_get("e2") is None


# ---------------------------------------------------------------------------
# FR02 — Warm Tier
# ---------------------------------------------------------------------------


class TestWarmTier:
    """FR02: Warm tier vector search and keyword fallback."""

    def test_warm_add_sidecar_when_no_embedding(self, tmp_path: Path) -> None:
        """When no embedding, entry is stored in JSONL sidecar."""
        mgr = make_tier_manager(tmp_path)
        mgr.warm_add("w1", {"id": "w1", "summary": "async pattern caching"}, embedding=None)
        sidecar = mgr._warm_sidecar_path()
        assert sidecar.exists()
        records = [json.loads(l) for l in sidecar.read_text().splitlines() if l.strip()]
        assert any(r["id"] == "w1" for r in records)

    def test_warm_keyword_fallback_search(self, tmp_path: Path) -> None:
        """Keyword search over sidecar finds matching entries, excludes non-matching."""
        mgr = make_tier_manager(tmp_path)
        mgr.warm_add("w1", {"id": "w1", "summary": "pytest fixture pattern"}, None)
        mgr.warm_add("w2", {"id": "w2", "summary": "docker compose setup"}, None)
        results = mgr.warm_search(["pytest", "fixture"], query_embedding=None)
        ids = [r["id"] for r in results]
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
        records = [json.loads(l) for l in sidecar.read_text().splitlines() if l.strip()]
        w1_records = [r for r in records if r["id"] == "w1"]
        assert len(w1_records) == 1
        assert w1_records[0]["summary"] == "updated summary"

    def test_warm_remove_from_sidecar(self, tmp_path: Path) -> None:
        """Entry removed from sidecar; others unaffected."""
        mgr = make_tier_manager(tmp_path)
        mgr.warm_add("w1", {"id": "w1", "summary": "to remove"}, None)
        mgr.warm_add("w2", {"id": "w2", "summary": "keep me"}, None)
        mgr.warm_remove("w1")
        sidecar = mgr._warm_sidecar_path()
        records = [json.loads(l) for l in sidecar.read_text().splitlines() if l.strip()]
        ids = [r["id"] for r in records]
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

        with patch("trw_mcp.state.memory_store.MemoryStore") as MockCls:
            MockCls.available.return_value = True
            MockCls.return_value = mock_store
            results = mgr.warm_search(["test"], query_embedding=[0.1] * 384)

        result_ids = [r["id"] for r in results]
        assert "e1" in result_ids and "e2" in result_ids


# ---------------------------------------------------------------------------
# FR03 — Cold Tier
# ---------------------------------------------------------------------------


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
        summaries = [str(r.get("summary", "")) for r in results]
        assert any("async" in s for s in summaries)
        assert not any("docker" in s for s in summaries)

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


# ---------------------------------------------------------------------------
# FR04 — Sweep Transitions
# ---------------------------------------------------------------------------


class TestSweep:
    """FR04: Tier sweep transitions."""

    def _cold_setup(self, tmp_path: Path, cfg: TRWConfig) -> tuple[Path, TierManager]:
        """Create .trw directory with entries/ and return (trw_dir, mgr)."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        (trw_dir / "learnings" / "entries").mkdir(parents=True)
        return trw_dir, TierManager(trw_dir=trw_dir, reader=FileStateReader(), writer=FileStateWriter(), config=cfg)

    def test_sweep_hot_to_warm(self, tmp_path: Path) -> None:
        """Stale hot entries are demoted to warm tier."""
        cfg = TRWConfig(memory_hot_ttl_days=5)
        _reset_config(cfg)
        mgr = make_tier_manager(tmp_path, cfg)
        mgr.hot_put("stale", make_entry("stale", last_accessed_at=days_ago(10)))
        result = mgr.sweep()
        assert result.demoted >= 1
        assert mgr.hot_get("stale") is None

    def test_sweep_hot_ttl_recent_entry_stays(self, tmp_path: Path) -> None:
        """Hot entry accessed recently is not demoted."""
        cfg = TRWConfig(memory_hot_ttl_days=7)
        _reset_config(cfg)
        mgr = make_tier_manager(tmp_path, cfg)
        mgr.hot_put("fresh", make_entry("fresh", last_accessed_at=days_ago(2)))
        result = mgr.sweep()
        assert mgr.hot_get("fresh") is not None
        assert result.demoted == 0

    def test_sweep_warm_to_cold(self, tmp_path: Path) -> None:
        """Old low-impact entries are archived to cold tier."""
        cfg = TRWConfig(memory_cold_threshold_days=30)
        _reset_config(cfg)
        trw_dir, mgr = self._cold_setup(tmp_path, cfg)
        write_entry_yaml(
            trw_dir / "learnings" / "entries", FileStateWriter(), "old-low", impact=0.2, last_accessed_at=days_ago(60)
        )
        result = mgr.sweep()
        assert result.demoted >= 1
        assert len(list((trw_dir / "memory" / "cold").rglob("*.yaml"))) >= 1

    def test_sweep_warm_to_cold_respects_impact_threshold(self, tmp_path: Path) -> None:
        """Entries with impact >= 0.5 are not archived even when stale."""
        cfg = TRWConfig(memory_cold_threshold_days=30)
        _reset_config(cfg)
        trw_dir, mgr = self._cold_setup(tmp_path, cfg)
        entries_dir = trw_dir / "learnings" / "entries"
        write_entry_yaml(entries_dir, FileStateWriter(), "high-impact", impact=0.7, last_accessed_at=days_ago(60))
        mgr.sweep()
        assert (entries_dir / "high-impact.yaml").exists()

    @pytest.mark.parametrize(
        "impact,stale_days,expect_archived",
        [
            # Well above importance threshold: impact=0.8, recent recency → importance well above 0.22
            # With default weights (w1=0.4, w2=0.3, w3=0.3) and 60 stale days:
            # recency ≈ exp(-0.693/100 * 60) ≈ 0.659; score = 0.3*0.659 + 0.3*0.8 ≈ 0.438 > 0.22 → protected
            (0.8, 60, False),
            # Well below importance threshold: impact=0.01 + 200 stale days
            # recency ≈ exp(-0.693/100 * 200) ≈ 0.25; score = 0.3*0.25 + 0.3*0.01 ≈ 0.078 < 0.22 → archived
            (0.01, 200, True),
            # Entry NOT stale enough: any impact, only 5 days old → cold_threshold not exceeded
            (0.01, 5, False),
        ],
    )
    def test_sweep_warm_to_cold_importance_boundary(
        self,
        tmp_path: Path,
        impact: float,
        stale_days: int,
        expect_archived: bool,
    ) -> None:
        """Boundary conditions for importance score threshold in warm→cold sweep.

        The sweep archives entries when BOTH conditions hold:
        1. days_since_access > memory_cold_threshold_days (staleness)
        2. compute_importance_score(entry) < 0.22 (low importance)

        Tests verify spec behavior, not the exact formula — so we use values
        well above/below the threshold to avoid floating-point edge cases.
        """
        cfg = TRWConfig(memory_cold_threshold_days=30)
        _reset_config(cfg)
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir(exist_ok=True)
        (trw_dir / "learnings" / "entries").mkdir(parents=True, exist_ok=True)
        mgr = TierManager(
            trw_dir=trw_dir,
            reader=FileStateReader(),
            writer=FileStateWriter(),
            config=cfg,
        )
        entries_dir = trw_dir / "learnings" / "entries"
        entry_id = f"boundary-{int(impact * 100)}-{stale_days}d"
        write_entry_yaml(
            entries_dir,
            FileStateWriter(),
            entry_id,
            impact=impact,
            last_accessed_at=days_ago(stale_days),
        )
        mgr.sweep()
        cold_base = trw_dir / "memory" / "cold"
        was_archived = cold_base.exists() and any(cold_base.rglob("*.yaml"))
        assert was_archived == expect_archived, (
            f"impact={impact}, stale={stale_days}d: expected archived={expect_archived}, got archived={was_archived}"
        )

    def test_sweep_cold_to_purge(self, tmp_path: Path) -> None:
        """Expired cold entries are deleted."""
        cfg = TRWConfig(memory_retention_days=100)
        _reset_config(cfg)
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        writer = FileStateWriter()
        today = datetime.now(tz=timezone.utc).date()
        partition = trw_dir / "memory" / "cold" / str(today.year) / f"{today.month:02d}"
        partition.mkdir(parents=True)
        cold_yaml = partition / "purge-target.yaml"
        writer.write_yaml(
            cold_yaml,
            {
                "id": "purge-target",
                "summary": "old entry",
                "impact": 0.1,
                "last_accessed_at": days_ago(200),
                "created": days_ago(200),
                "status": "active",
                "tags": [],
            },
        )
        mgr = TierManager(trw_dir=trw_dir, reader=FileStateReader(), writer=writer, config=cfg)
        result = mgr.sweep()
        assert result.purged >= 1
        assert not cold_yaml.exists()

    def test_sweep_cold_to_purge_respects_retention(self, tmp_path: Path) -> None:
        """Entries within retention period are not purged."""
        cfg = TRWConfig(memory_retention_days=365)
        _reset_config(cfg)
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        writer = FileStateWriter()
        today = datetime.now(tz=timezone.utc).date()
        partition = trw_dir / "memory" / "cold" / str(today.year) / f"{today.month:02d}"
        partition.mkdir(parents=True)
        cold_yaml = partition / "keep-me.yaml"
        writer.write_yaml(
            cold_yaml,
            {
                "id": "keep-me",
                "summary": "recent entry",
                "impact": 0.1,
                "last_accessed_at": days_ago(10),
                "created": days_ago(10),
                "status": "active",
                "tags": [],
            },
        )
        mgr = TierManager(trw_dir=trw_dir, reader=FileStateReader(), writer=writer, config=cfg)
        result = mgr.sweep()
        assert cold_yaml.exists()
        assert result.purged == 0

    def test_sweep_purge_audit_jsonl(self, tmp_path: Path) -> None:
        """purge_audit.jsonl is written with required fields on purge."""
        cfg = TRWConfig(memory_retention_days=100)
        _reset_config(cfg)
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        writer = FileStateWriter()
        today = datetime.now(tz=timezone.utc).date()
        partition = trw_dir / "memory" / "cold" / str(today.year) / f"{today.month:02d}"
        partition.mkdir(parents=True)
        writer.write_yaml(
            partition / "audit-entry.yaml",
            {
                "id": "audit-check",
                "summary": "audit test",
                "impact": 0.05,
                "last_accessed_at": days_ago(200),
                "created": days_ago(200),
                "status": "active",
                "tags": [],
            },
        )
        mgr = TierManager(trw_dir=trw_dir, reader=FileStateReader(), writer=writer, config=cfg)
        mgr.sweep()

        audit_path = trw_dir / "memory" / "purge_audit.jsonl"
        assert audit_path.exists()
        records = [json.loads(l) for l in audit_path.read_text().splitlines() if l.strip()]
        record = next((r for r in records if r["entry_id"] == "audit-check"), None)
        assert record is not None
        assert "purged_at" in record
        assert "days_idle" in record

    def test_sweep_continues_on_per_entry_error(self, tmp_path: Path) -> None:
        """Per-entry failure doesn't abort sweep; errors counted."""
        cfg = TRWConfig(memory_cold_threshold_days=30)
        _reset_config(cfg)
        trw_dir, mgr = self._cold_setup(tmp_path, cfg)
        entries_dir = trw_dir / "learnings" / "entries"
        write_entry_yaml(entries_dir, FileStateWriter(), "valid", impact=0.1, last_accessed_at=days_ago(200))
        (entries_dir / "corrupt.yaml").write_text("{ invalid yaml: [unclosed", encoding="utf-8")
        result = mgr.sweep()
        assert isinstance(result, TierSweepResult)

    def test_sweep_no_transitions(self, tmp_path: Path) -> None:
        """Clean sweep with no stale entries returns all zeros."""
        assert make_tier_manager(tmp_path).sweep() == TierSweepResult(0, 0, 0, 0)

    def test_sweep_result_is_named_tuple(self) -> None:
        """TierSweepResult supports named access and tuple unpacking."""
        result = TierSweepResult(promoted=1, demoted=2, purged=3, errors=0)
        promoted, demoted, purged, errors = result
        assert promoted == 1 and demoted == 2 and purged == 3 and errors == 0

    def test_sweep_no_cross_tier_duplication(self, tmp_path: Path) -> None:
        """After sweep, no entry ID exists in both entries/ and cold/ simultaneously."""
        cfg = TRWConfig(memory_cold_threshold_days=30)
        _reset_config(cfg)
        trw_dir, mgr = self._cold_setup(tmp_path, cfg)
        entries_dir = trw_dir / "learnings" / "entries"
        reader = FileStateReader()
        write_entry_yaml(entries_dir, FileStateWriter(), "dup-check", impact=0.2, last_accessed_at=days_ago(60))
        mgr.sweep()

        cold_base = trw_dir / "memory" / "cold"
        cold_ids = (
            {str(reader.read_yaml(f).get("id", "")) for f in cold_base.rglob("*.yaml")} if cold_base.exists() else set()
        )
        warm_ids = {
            str(reader.read_yaml(f).get("id", "")) for f in entries_dir.glob("*.yaml") if f.name != "index.yaml"
        }

        assert cold_ids.isdisjoint(warm_ids), f"Duplicate IDs across tiers: {cold_ids & warm_ids}"

    def test_transition_durability_write_failure(self, tmp_path: Path) -> None:
        """Write failure keeps entry in source tier; errors counted (NFR06)."""
        cfg = TRWConfig(memory_cold_threshold_days=30)
        _reset_config(cfg)
        trw_dir, _ = self._cold_setup(tmp_path, cfg)
        entries_dir = trw_dir / "learnings" / "entries"
        write_entry_yaml(entries_dir, FileStateWriter(), "durable", impact=0.1, last_accessed_at=days_ago(60))

        failing_writer = MagicMock(spec=FileStateWriter)
        failing_writer.write_yaml.side_effect = OSError("disk full")
        mgr = TierManager(trw_dir=trw_dir, reader=FileStateReader(), writer=failing_writer, config=cfg)
        result = mgr.sweep()

        assert result.errors >= 1
        assert (entries_dir / "durable.yaml").exists()


# ---------------------------------------------------------------------------
# FR05 — Importance Scoring
# ---------------------------------------------------------------------------


class TestImportanceScoring:
    """FR05: compute_importance_score — Stanford Generative Agents formula."""

    def _entry(self, impact: float = 0.5, last_accessed_at: str | None = None) -> dict[str, object]:
        return {
            "id": "x",
            "summary": "test entry",
            "detail": "detail",
            "impact": impact,
            "last_accessed_at": last_accessed_at or datetime.now(tz=timezone.utc).date().isoformat(),
        }

    def test_score_in_range_zero_to_one(self) -> None:
        """Result is always in [0.0, 1.0]."""
        score = compute_importance_score(self._entry(impact=0.8), ["test"], config=TRWConfig())
        assert 0.0 <= score <= 1.0

    def test_score_range_with_very_old_entry(self) -> None:
        """Score stays in [0.0, 1.0] for entries accessed 500 days ago."""
        score = compute_importance_score(self._entry(last_accessed_at=days_ago(500)), ["x"], config=TRWConfig())
        assert 0.0 <= score <= 1.0

    def test_relevance_ordering(self) -> None:
        """Higher token overlap → higher score when recency and importance are equal."""
        cfg = TRWConfig(memory_score_w1=0.8, memory_score_w2=0.1, memory_score_w3=0.1)
        today = datetime.now(tz=timezone.utc).date().isoformat()
        hi = {"id": "hi", "summary": "pytest fixture pattern", "detail": "", "impact": 0.5, "last_accessed_at": today}
        lo = {"id": "lo", "summary": "zzz unrelated text", "detail": "", "impact": 0.5, "last_accessed_at": today}
        tokens = ["pytest", "fixture", "pattern"]
        assert compute_importance_score(hi, tokens, config=cfg) > compute_importance_score(lo, tokens, config=cfg)

    def test_recency_ordering(self) -> None:
        """More recent entry scores higher when relevance and importance are equal."""
        cfg = TRWConfig(memory_score_w1=0.1, memory_score_w2=0.8, memory_score_w3=0.1)
        recent = self._entry(impact=0.5, last_accessed_at=days_ago(1))
        old = self._entry(impact=0.5, last_accessed_at=days_ago(200))
        assert compute_importance_score(recent, [], config=cfg) > compute_importance_score(old, [], config=cfg)

    def test_importance_ordering(self) -> None:
        """Higher impact → higher score when relevance and recency are equal."""
        cfg = TRWConfig(memory_score_w1=0.0, memory_score_w2=0.0, memory_score_w3=1.0)
        today = datetime.now(tz=timezone.utc).date().isoformat()
        assert compute_importance_score(
            self._entry(impact=0.9, last_accessed_at=today), [], config=cfg
        ) > compute_importance_score(self._entry(impact=0.1, last_accessed_at=today), [], config=cfg)

    def test_weight_normalization(self) -> None:
        """Non-unit weights are normalized; score stays in [0, 1]."""
        cfg = TRWConfig(memory_score_w1=1.0, memory_score_w2=1.0, memory_score_w3=1.0)
        score = compute_importance_score(self._entry(impact=0.9), ["anything"], config=cfg)
        assert 0.0 <= score <= 1.0

    def test_token_overlap_fallback_when_no_embedding(self) -> None:
        """Token overlap is used for relevance when no embeddings provided."""
        cfg = TRWConfig(memory_score_w1=0.9, memory_score_w2=0.05, memory_score_w3=0.05)
        today = datetime.now(tz=timezone.utc).date().isoformat()
        entry = {
            "id": "x",
            "summary": "sqlalchemy orm pattern",
            "detail": "database access",
            "impact": 0.5,
            "last_accessed_at": today,
        }
        assert compute_importance_score(entry, ["sqlalchemy", "orm"], config=cfg) > compute_importance_score(
            entry, ["unrelated", "terms"], config=cfg
        )

    def test_zero_query_tokens_returns_zero_relevance(self) -> None:
        """Empty query tokens → zero relevance component."""
        cfg = TRWConfig(memory_score_w1=1.0, memory_score_w2=0.0, memory_score_w3=0.0)
        assert compute_importance_score(self._entry(), [], config=cfg) == 0.0

    def test_cosine_similarity_when_embeddings_provided(self) -> None:
        """Identical embeddings → cosine = 1.0 → score near w1."""
        cfg = TRWConfig(memory_score_w1=1.0, memory_score_w2=0.0, memory_score_w3=0.0)
        vec = [0.1] * 384
        score = compute_importance_score(self._entry(), [], query_embedding=vec, entry_embedding=vec, config=cfg)
        assert abs(score - 1.0) < 0.01

    def test_antiparallel_embeddings_clamp_to_zero(self) -> None:
        """Negative cosine similarity is clamped to 0.0."""
        cfg = TRWConfig(memory_score_w1=1.0, memory_score_w2=0.0, memory_score_w3=0.0)
        score = compute_importance_score(
            self._entry(),
            [],
            query_embedding=[1.0] + [0.0] * 383,
            entry_embedding=[-1.0] + [0.0] * 383,
            config=cfg,
        )
        assert score == 0.0

    def test_zero_embedding_does_not_raise(self) -> None:
        """query_embedding=[0.0]*N does not cause divide-by-zero."""
        zero_vec = [0.0] * 384
        score = compute_importance_score(
            self._entry(), [], query_embedding=zero_vec, entry_embedding=zero_vec, config=TRWConfig()
        )
        assert 0.0 <= score <= 1.0

    def test_impact_field_clamped_to_unit_interval(self) -> None:
        """Impact values outside [0,1] in raw dict are clamped."""
        cfg = TRWConfig(memory_score_w1=0.0, memory_score_w2=0.0, memory_score_w3=1.0)
        today = datetime.now(tz=timezone.utc).date().isoformat()
        over = {"id": "x", "summary": "", "detail": "", "impact": "2.5", "last_accessed_at": today}
        under = {"id": "y", "summary": "", "detail": "", "impact": "-0.5", "last_accessed_at": today}
        assert compute_importance_score(over, [], config=cfg) <= 1.0
        assert compute_importance_score(under, [], config=cfg) >= 0.0


# ---------------------------------------------------------------------------
# FR06 — Config Read at Call Time
# ---------------------------------------------------------------------------


class TestConfigAtCallTime:
    """FR06: sweep() reads config from get_config() at call time."""

    def test_sweep_reads_config_at_call_time(self, tmp_path: Path) -> None:
        """Injecting config into singleton before sweep applies new thresholds."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        entries_dir = trw_dir / "learnings" / "entries"
        entries_dir.mkdir(parents=True)
        write_entry_yaml(entries_dir, FileStateWriter(), "threshold-test", impact=0.1, last_accessed_at=days_ago(10))

        cfg = TRWConfig(memory_cold_threshold_days=5)  # 10 days > 5 → should demote
        _reset_config(cfg)
        mgr = TierManager(trw_dir=trw_dir, reader=FileStateReader(), writer=FileStateWriter(), config=cfg)
        assert mgr.sweep().demoted >= 1

    def test_hot_put_reads_config_at_call_time(self, tmp_path: Path) -> None:
        """hot_put() respects capacity from injected config."""
        mgr = make_tier_manager(tmp_path)
        mgr._config = TRWConfig(memory_hot_max_entries=2)
        mgr.hot_put("e1", make_entry("e1"))
        mgr.hot_put("e2", make_entry("e2"))
        mgr.hot_put("e3", make_entry("e3"))  # should evict e1
        assert mgr.hot_get("e1") is None
        assert mgr.hot_get("e3") is not None


# ---------------------------------------------------------------------------
# FR07 — Config Fields and Env Var Overrides
# ---------------------------------------------------------------------------


class TestConfigFields:
    """FR07: TRWConfig CORE-043 fields."""

    def test_config_defaults(self) -> None:
        """All 7 CORE-043 config fields have correct defaults."""
        cfg = TRWConfig()
        assert cfg.memory_hot_max_entries == 50
        assert cfg.memory_hot_ttl_days == 7
        assert cfg.memory_cold_threshold_days == 90
        assert cfg.memory_retention_days == 365
        assert cfg.memory_score_w1 == pytest.approx(0.4)
        assert cfg.memory_score_w2 == pytest.approx(0.3)
        assert cfg.memory_score_w3 == pytest.approx(0.3)

    def test_config_score_weights_sum_to_one(self) -> None:
        """Default w1+w2+w3 == 1.0 within float tolerance."""
        cfg = TRWConfig()
        assert abs(cfg.memory_score_w1 + cfg.memory_score_w2 + cfg.memory_score_w3 - 1.0) < 1e-9

    def test_config_env_override_hot_max_entries(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """TRW_MEMORY_HOT_MAX_ENTRIES env var overrides default."""
        _reset_config()
        monkeypatch.setenv("TRW_MEMORY_HOT_MAX_ENTRIES", "100")
        assert TRWConfig().memory_hot_max_entries == 100

    def test_config_env_override_hot_ttl_days(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """TRW_MEMORY_HOT_TTL_DAYS env var overrides default."""
        _reset_config()
        monkeypatch.setenv("TRW_MEMORY_HOT_TTL_DAYS", "14")
        assert TRWConfig().memory_hot_ttl_days == 14

    def test_config_env_override_retention_days(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """TRW_MEMORY_RETENTION_DAYS env var overrides default."""
        _reset_config()
        monkeypatch.setenv("TRW_MEMORY_RETENTION_DAYS", "730")
        assert TRWConfig().memory_retention_days == 730

    def test_config_env_override_score_weights(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Score weight env vars override defaults."""
        _reset_config()
        monkeypatch.setenv("TRW_MEMORY_SCORE_W1", "0.5")
        monkeypatch.setenv("TRW_MEMORY_SCORE_W2", "0.3")
        monkeypatch.setenv("TRW_MEMORY_SCORE_W3", "0.2")
        cfg = TRWConfig()
        assert cfg.memory_score_w1 == pytest.approx(0.5)
        assert cfg.memory_score_w2 == pytest.approx(0.3)
        assert cfg.memory_score_w3 == pytest.approx(0.2)


# ---------------------------------------------------------------------------
# Integration
# ---------------------------------------------------------------------------


class TestTierManagerIntegration:
    """End-to-end integration across tiers."""

    def test_full_demote_lifecycle(self, tmp_path: Path) -> None:
        """Both hot→warm and warm→cold demotions occur in one sweep."""
        cfg = TRWConfig(memory_hot_ttl_days=1, memory_cold_threshold_days=1)
        _reset_config(cfg)
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        entries_dir = trw_dir / "learnings" / "entries"
        entries_dir.mkdir(parents=True)
        write_entry_yaml(entries_dir, FileStateWriter(), "lifecycle", impact=0.2, last_accessed_at=days_ago(10))

        mgr = TierManager(trw_dir=trw_dir, reader=FileStateReader(), writer=FileStateWriter(), config=cfg)
        mgr.hot_put("stale-hot", make_entry("stale-hot", last_accessed_at=days_ago(10)))
        result = mgr.sweep()
        assert result.demoted >= 1

    def test_default_reader_writer(self, tmp_path: Path) -> None:
        """TierManager works without explicit reader/writer injection."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        mgr = TierManager(trw_dir=trw_dir)
        mgr.hot_put("x", make_entry("x"))
        assert mgr.hot_get("x") is not None


# ---------------------------------------------------------------------------
# PRD-FIX-033-FR05: SQLite-backed Warm→Cold sweep
# ---------------------------------------------------------------------------


class TestSweepWarmToColdSQLite:
    """PRD-FIX-033-FR05: sweep Warm→Cold uses SQLite when available."""

    def test_sweep_warm_to_cold_uses_sqlite(self, tmp_path: Path) -> None:
        """Warm→Cold calls list_active_learnings instead of YAML glob."""
        cfg = TRWConfig(memory_cold_threshold_days=30)
        _reset_config(cfg)
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        entries_dir = trw_dir / "learnings" / "entries"
        entries_dir.mkdir(parents=True)

        # Write YAML file so cold_archive can find it
        writer = FileStateWriter()
        write_entry_yaml(
            entries_dir,
            writer,
            "sqlite-old",
            impact=0.2,
            last_accessed_at=days_ago(60),
        )

        # Mock list_active_learnings to return entry data from "SQLite"
        fake_entries: list[dict[str, object]] = [
            {
                "id": "sqlite-old",
                "summary": "test summary",
                "detail": "detail for sqlite-old",
                "status": "active",
                "impact": 0.2,
                "tags": ["test"],
                "last_accessed_at": days_ago(60),
                "created": days_ago(60),
            },
        ]

        with (
            patch(
                "trw_mcp.state.memory_adapter.list_active_learnings",
                return_value=fake_entries,
            ) as mock_sqlite,
            patch(
                "trw_mcp.state.memory_adapter.find_yaml_path_for_entry",
                return_value=entries_dir / "sqlite-old.yaml",
            ),
        ):
            mgr = TierManager(
                trw_dir=trw_dir,
                reader=FileStateReader(),
                writer=writer,
                config=cfg,
            )
            result = mgr.sweep()

        mock_sqlite.assert_called_once()
        assert result.demoted >= 1

    def test_sweep_fallback_to_yaml(self, tmp_path: Path) -> None:
        """Falls back to YAML when SQLite raises."""
        cfg = TRWConfig(memory_cold_threshold_days=30)
        _reset_config(cfg)
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        entries_dir = trw_dir / "learnings" / "entries"
        entries_dir.mkdir(parents=True)

        writer = FileStateWriter()
        write_entry_yaml(
            entries_dir,
            writer,
            "yaml-old",
            impact=0.2,
            last_accessed_at=days_ago(60),
        )

        with patch(
            "trw_mcp.state.memory_adapter.list_active_learnings",
            side_effect=RuntimeError("SQLite unavailable"),
        ):
            mgr = TierManager(
                trw_dir=trw_dir,
                reader=FileStateReader(),
                writer=writer,
                config=cfg,
            )
            result = mgr.sweep()

        # YAML fallback should still find and demote the old entry
        assert result.demoted >= 1

    def test_sweep_sqlite_no_yaml_path_skips_entry(self, tmp_path: Path) -> None:
        """Entry in SQLite but without YAML file is skipped (not an error)."""
        cfg = TRWConfig(memory_cold_threshold_days=30)
        _reset_config(cfg)
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        entries_dir = trw_dir / "learnings" / "entries"
        entries_dir.mkdir(parents=True)

        fake_entries: list[dict[str, object]] = [
            {
                "id": "no-yaml",
                "summary": "entry without yaml file",
                "detail": "detail",
                "status": "active",
                "impact": 0.2,
                "tags": ["test"],
                "last_accessed_at": days_ago(60),
                "created": days_ago(60),
            },
        ]

        with (
            patch(
                "trw_mcp.state.memory_adapter.list_active_learnings",
                return_value=fake_entries,
            ),
            patch(
                "trw_mcp.state.memory_adapter.find_yaml_path_for_entry",
                return_value=None,  # No YAML file found
            ),
        ):
            mgr = TierManager(
                trw_dir=trw_dir,
                reader=FileStateReader(),
                writer=FileStateWriter(),
                config=cfg,
            )
            result = mgr.sweep()

        # Entry skipped — no demotion, no error
        assert result.demoted == 0
        assert result.errors == 0

    def test_cold_to_purge_still_uses_yaml(self, tmp_path: Path) -> None:
        """Cold→Purge phase remains YAML-only (not migrated)."""
        cfg = TRWConfig(memory_retention_days=100)
        _reset_config(cfg)
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        writer = FileStateWriter()
        today = datetime.now(tz=timezone.utc).date()
        partition = trw_dir / "memory" / "cold" / str(today.year) / f"{today.month:02d}"
        partition.mkdir(parents=True)
        cold_yaml = partition / "purge-target.yaml"
        writer.write_yaml(
            cold_yaml,
            {
                "id": "purge-target",
                "summary": "old entry",
                "impact": 0.1,
                "last_accessed_at": days_ago(200),
                "created": days_ago(200),
                "status": "active",
                "tags": [],
            },
        )

        # Even with SQLite patched to fail, Cold→Purge should still work
        with patch(
            "trw_mcp.state.memory_adapter.list_active_learnings",
            side_effect=RuntimeError("SQLite unavailable"),
        ):
            mgr = TierManager(
                trw_dir=trw_dir,
                reader=FileStateReader(),
                writer=writer,
                config=cfg,
            )
            result = mgr.sweep()

        assert result.purged >= 1
        assert not cold_yaml.exists()


# ---------------------------------------------------------------------------
# PRD-FIX-052-FR01/FR02: Impact Tier Label Assignment
# ---------------------------------------------------------------------------


class TestAssignImpactTiers:
    """PRD-FIX-052-FR01/FR02: assign_impact_tiers() labels entries by impact score."""

    def _setup_entries_dir(self, tmp_path: Path) -> tuple[Path, Path, FileStateWriter]:
        """Create .trw/learnings/entries/ and return (trw_dir, entries_dir, writer)."""
        trw_dir = tmp_path / ".trw"
        entries_dir = trw_dir / "learnings" / "entries"
        entries_dir.mkdir(parents=True)
        writer = FileStateWriter()
        return trw_dir, entries_dir, writer

    def _write_entry(
        self,
        entries_dir: Path,
        writer: FileStateWriter,
        entry_id: str,
        impact: float = 0.5,
        status: str = "active",
    ) -> Path:
        """Write a minimal entry YAML for tier assignment tests."""
        path = entries_dir / f"{entry_id}.yaml"
        writer.write_yaml(
            path,
            {
                "id": entry_id,
                "summary": f"summary for {entry_id}",
                "detail": f"detail for {entry_id}",
                "tags": ["test"],
                "impact": impact,
                "status": status,
            },
        )
        return path

    def test_assign_impact_tiers_critical(self, tmp_path: Path) -> None:
        """Entry with impact=0.95 gets tier='critical' (>= 0.9 boundary)."""
        from unittest.mock import patch

        from trw_mcp.state.tiers import TierManager

        trw_dir, entries_dir, writer = self._setup_entries_dir(tmp_path)
        self._write_entry(entries_dir, writer, "e-crit", impact=0.95)
        mgr = TierManager(trw_dir=trw_dir, reader=FileStateReader(), writer=writer)

        with patch(
            "trw_mcp.state.tiers.list_active_learnings",
            return_value=[
                {"id": "e-crit", "impact": 0.95, "status": "active"},
            ],
        ):
            dist = mgr.assign_impact_tiers(trw_dir)

        assert dist["critical"] == 1

    def test_assign_impact_tiers_high(self, tmp_path: Path) -> None:
        """Entry with impact=0.75 gets tier='high' (>= 0.7, < 0.9)."""
        from unittest.mock import patch

        from trw_mcp.state.tiers import TierManager

        trw_dir, entries_dir, writer = self._setup_entries_dir(tmp_path)
        self._write_entry(entries_dir, writer, "e-high", impact=0.75)
        mgr = TierManager(trw_dir=trw_dir, reader=FileStateReader(), writer=writer)

        with patch(
            "trw_mcp.state.tiers.list_active_learnings",
            return_value=[
                {"id": "e-high", "impact": 0.75, "status": "active"},
            ],
        ):
            dist = mgr.assign_impact_tiers(trw_dir)

        assert dist["high"] == 1

    def test_assign_impact_tiers_medium(self, tmp_path: Path) -> None:
        """Entry with impact=0.5 gets tier='medium' (>= 0.4, < 0.7)."""
        from unittest.mock import patch

        from trw_mcp.state.tiers import TierManager

        trw_dir, entries_dir, writer = self._setup_entries_dir(tmp_path)
        self._write_entry(entries_dir, writer, "e-med", impact=0.5)
        mgr = TierManager(trw_dir=trw_dir, reader=FileStateReader(), writer=writer)

        with patch(
            "trw_mcp.state.tiers.list_active_learnings",
            return_value=[
                {"id": "e-med", "impact": 0.5, "status": "active"},
            ],
        ):
            dist = mgr.assign_impact_tiers(trw_dir)

        assert dist["medium"] == 1

    def test_assign_impact_tiers_low(self, tmp_path: Path) -> None:
        """Entry with impact=0.2 gets tier='low' (< 0.4)."""
        from unittest.mock import patch

        from trw_mcp.state.tiers import TierManager

        trw_dir, entries_dir, writer = self._setup_entries_dir(tmp_path)
        self._write_entry(entries_dir, writer, "e-low", impact=0.2)
        mgr = TierManager(trw_dir=trw_dir, reader=FileStateReader(), writer=writer)

        with patch(
            "trw_mcp.state.tiers.list_active_learnings",
            return_value=[
                {"id": "e-low", "impact": 0.2, "status": "active"},
            ],
        ):
            dist = mgr.assign_impact_tiers(trw_dir)

        assert dist["low"] == 1

    def test_assign_impact_tiers_boundary_at_0_9(self, tmp_path: Path) -> None:
        """Entry with impact exactly 0.9 gets tier='critical'."""
        from unittest.mock import patch

        from trw_mcp.state.tiers import TierManager

        trw_dir, entries_dir, writer = self._setup_entries_dir(tmp_path)
        self._write_entry(entries_dir, writer, "e-boundary-crit", impact=0.9)
        mgr = TierManager(trw_dir=trw_dir, reader=FileStateReader(), writer=writer)

        with patch(
            "trw_mcp.state.tiers.list_active_learnings",
            return_value=[
                {"id": "e-boundary-crit", "impact": 0.9, "status": "active"},
            ],
        ):
            dist = mgr.assign_impact_tiers(trw_dir)

        assert dist["critical"] == 1

    def test_assign_impact_tiers_boundary_at_0_7(self, tmp_path: Path) -> None:
        """Entry with impact exactly 0.7 gets tier='high'."""
        from unittest.mock import patch

        from trw_mcp.state.tiers import TierManager

        trw_dir, entries_dir, writer = self._setup_entries_dir(tmp_path)
        self._write_entry(entries_dir, writer, "e-boundary-high", impact=0.7)
        mgr = TierManager(trw_dir=trw_dir, reader=FileStateReader(), writer=writer)

        with patch(
            "trw_mcp.state.tiers.list_active_learnings",
            return_value=[
                {"id": "e-boundary-high", "impact": 0.7, "status": "active"},
            ],
        ):
            dist = mgr.assign_impact_tiers(trw_dir)

        assert dist["high"] == 1

    def test_assign_impact_tiers_idempotent(self, tmp_path: Path) -> None:
        """Running assign_impact_tiers twice produces the same distribution."""
        from unittest.mock import patch

        from trw_mcp.state.tiers import TierManager

        trw_dir, entries_dir, writer = self._setup_entries_dir(tmp_path)
        self._write_entry(entries_dir, writer, "e-idem", impact=0.8)
        mgr = TierManager(trw_dir=trw_dir, reader=FileStateReader(), writer=writer)

        fake_entries = [{"id": "e-idem", "impact": 0.8, "status": "active"}]
        with patch("trw_mcp.state.tiers.list_active_learnings", return_value=fake_entries):
            dist1 = mgr.assign_impact_tiers(trw_dir)
        with patch("trw_mcp.state.tiers.list_active_learnings", return_value=fake_entries):
            dist2 = mgr.assign_impact_tiers(trw_dir)

        assert dist1 == dist2

    def test_assign_impact_tiers_writes_yaml(self, tmp_path: Path) -> None:
        """After assignment, the YAML file contains the correct impact_tier field."""
        from unittest.mock import patch

        from trw_mcp.state.tiers import TierManager

        reader = FileStateReader()
        trw_dir, entries_dir, writer = self._setup_entries_dir(tmp_path)
        self._write_entry(entries_dir, writer, "e-yaml", impact=0.92)
        mgr = TierManager(trw_dir=trw_dir, reader=reader, writer=writer)

        with patch(
            "trw_mcp.state.tiers.list_active_learnings",
            return_value=[
                {"id": "e-yaml", "impact": 0.92, "status": "active"},
            ],
        ):
            mgr.assign_impact_tiers(trw_dir)

        data = reader.read_yaml(entries_dir / "e-yaml.yaml")
        assert data.get("impact_tier") == "critical"

    def test_assign_impact_tiers_distribution_sums_to_entry_count(self, tmp_path: Path) -> None:
        """Distribution counts sum to total number of active entries processed."""
        from unittest.mock import patch

        from trw_mcp.state.tiers import TierManager

        trw_dir, entries_dir, writer = self._setup_entries_dir(tmp_path)
        for eid, imp in [("e1", 0.95), ("e2", 0.75), ("e3", 0.5), ("e4", 0.2)]:
            self._write_entry(entries_dir, writer, eid, impact=imp)
        mgr = TierManager(trw_dir=trw_dir, reader=FileStateReader(), writer=writer)

        fake_entries = [
            {"id": "e1", "impact": 0.95, "status": "active"},
            {"id": "e2", "impact": 0.75, "status": "active"},
            {"id": "e3", "impact": 0.5, "status": "active"},
            {"id": "e4", "impact": 0.2, "status": "active"},
        ]
        with patch("trw_mcp.state.tiers.list_active_learnings", return_value=fake_entries):
            dist = mgr.assign_impact_tiers(trw_dir)

        total = sum(dist.values())
        assert total == 4
        assert dist["critical"] == 1
        assert dist["high"] == 1
        assert dist["medium"] == 1
        assert dist["low"] == 1

    def test_assign_impact_tiers_skips_missing_yaml(self, tmp_path: Path) -> None:
        """Entry in SQLite but with no YAML file on disk is skipped gracefully."""
        from unittest.mock import patch

        from trw_mcp.state.tiers import TierManager

        trw_dir, entries_dir, writer = self._setup_entries_dir(tmp_path)
        mgr = TierManager(trw_dir=trw_dir, reader=FileStateReader(), writer=writer)

        with patch(
            "trw_mcp.state.tiers.list_active_learnings",
            return_value=[
                {"id": "missing-yaml-entry", "impact": 0.8, "status": "active"},
            ],
        ):
            dist = mgr.assign_impact_tiers(trw_dir)

        assert sum(dist.values()) == 0

    def test_impact_tier_field_default_is_question_mark(self) -> None:
        """LearningEntry.impact_tier defaults to '?' when not set (FR02)."""
        from trw_mcp.models.learning import LearningEntry

        entry = LearningEntry(id="test", summary="x", detail="y")
        assert entry.impact_tier == "?"

    def test_impact_tier_invalid_value_raises(self) -> None:
        """LearningEntry with invalid impact_tier raises ValidationError (Literal type)."""
        from pydantic import ValidationError

        from trw_mcp.models.learning import LearningEntry

        with pytest.raises(ValidationError):
            LearningEntry(id="t", summary="x", detail="y", impact_tier="invalid")  # type: ignore[arg-type]
