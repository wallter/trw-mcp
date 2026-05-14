"""Tests for tier integration and SQLite-backed sweep paths."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from tests._tiers_test_support import days_ago, make_entry, write_entry_yaml
from trw_mcp.models.config import TRWConfig, _reset_config
from trw_mcp.state.persistence import FileStateReader, FileStateWriter
from trw_mcp.state.tiers import TierManager


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

        writer = FileStateWriter()
        write_entry_yaml(entries_dir, writer, "sqlite-old", impact=0.2, last_accessed_at=days_ago(60))

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
            patch("trw_mcp.state.memory_adapter.list_active_learnings", return_value=fake_entries) as mock_sqlite,
            patch("trw_mcp.state.memory_adapter.find_yaml_path_for_entry", return_value=entries_dir / "sqlite-old.yaml"),
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
        write_entry_yaml(entries_dir, writer, "yaml-old", impact=0.2, last_accessed_at=days_ago(60))

        with patch("trw_mcp.state.memory_adapter.list_active_learnings", side_effect=RuntimeError("SQLite unavailable")):
            mgr = TierManager(
                trw_dir=trw_dir,
                reader=FileStateReader(),
                writer=writer,
                config=cfg,
            )
            result = mgr.sweep()

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
            patch("trw_mcp.state.memory_adapter.list_active_learnings", return_value=fake_entries),
            patch("trw_mcp.state.memory_adapter.find_yaml_path_for_entry", return_value=None),
        ):
            mgr = TierManager(
                trw_dir=trw_dir,
                reader=FileStateReader(),
                writer=FileStateWriter(),
                config=cfg,
            )
            result = mgr.sweep()

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

        with patch("trw_mcp.state.memory_adapter.list_active_learnings", side_effect=RuntimeError("SQLite unavailable")):
            mgr = TierManager(
                trw_dir=trw_dir,
                reader=FileStateReader(),
                writer=writer,
                config=cfg,
            )
            result = mgr.sweep()

        assert result.purged >= 1
        assert not cold_yaml.exists()
