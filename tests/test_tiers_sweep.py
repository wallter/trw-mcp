"""Tests for tiered memory storage sweep transitions — PRD-CORE-043 FR04."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from trw_mcp.models.config import TRWConfig, _reset_config
from trw_mcp.state.persistence import FileStateReader, FileStateWriter
from trw_mcp.state.tiers import TierManager, TierSweepResult

from tests._tiers_test_support import days_ago, make_entry, make_tier_manager, write_entry_yaml


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
        write_entry_yaml(trw_dir / "learnings" / "entries", FileStateWriter(), "old-low", impact=0.2, last_accessed_at=days_ago(60))
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
        [(0.8, 60, False), (0.01, 200, True), (0.01, 5, False)],
    )
    def test_sweep_warm_to_cold_importance_boundary(
        self,
        tmp_path: Path,
        impact: float,
        stale_days: int,
        expect_archived: bool,
    ) -> None:
        """Boundary conditions for importance score threshold in warm→cold sweep."""
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
        records = [json.loads(line) for line in audit_path.read_text().splitlines() if line.strip()]
        record = next((item for item in records if item["entry_id"] == "audit-check"), None)
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
        cold_ids = {str(reader.read_yaml(path).get("id", "")) for path in cold_base.rglob("*.yaml")} if cold_base.exists() else set()
        warm_ids = {
            str(reader.read_yaml(path).get("id", ""))
            for path in entries_dir.glob("*.yaml")
            if path.name != "index.yaml"
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
