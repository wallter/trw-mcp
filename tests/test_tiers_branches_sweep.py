"""Branch coverage tests for tier sweep behavior in tiers.py."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

from trw_mcp.models.config import TRWConfig
from trw_mcp.state.persistence import FileStateReader, FileStateWriter
from trw_mcp.state.tiers import TierManager

from tests._tiers_branches_support import _make_old_entry, _setup_entries_dir, _write_yaml_entry


class TestSweepHotToWarmException:
    """Test sweep hot-to-warm transition error handling."""

    def test_sweep_hot_to_warm_exception_counted(self, tmp_path: Path) -> None:
        """Lines 617-619: exception during hot-to-warm transition increments errors."""
        trw_dir = tmp_path / ".trw"
        _setup_entries_dir(trw_dir)

        mgr = TierManager(trw_dir)
        mgr._hot["stale-1"] = _make_old_entry("stale-1", days_ago=100)

        with patch.object(mgr, "warm_add", side_effect=RuntimeError("warm failure")):
            with patch("trw_mcp.state.tiers.get_config") as mock_cfg:
                cfg = TRWConfig()
                object.__setattr__(cfg, "memory_hot_ttl_days", 1)
                mock_cfg.return_value = cfg
                with patch(
                    "trw_mcp.state.memory_adapter.list_active_learnings",
                    side_effect=ImportError("no sqlite"),
                ):
                    result = mgr.sweep()

        assert result.errors >= 1


class TestSweepWarmToColdSkipEmpty:
    """Test sweep warm-to-cold SQLite path skips entries without ID."""

    def test_sweep_sqlite_path_skips_empty_entry_id(self, tmp_path: Path) -> None:
        """Line 636: entry with empty id is skipped during SQLite warm-to-cold."""
        trw_dir = tmp_path / ".trw"
        _setup_entries_dir(trw_dir)

        mgr = TierManager(trw_dir)

        with patch("trw_mcp.state.tiers.get_config") as mock_cfg:
            cfg = TRWConfig()
            object.__setattr__(cfg, "memory_hot_ttl_days", 999)
            mock_cfg.return_value = cfg

            mock_list = MagicMock(
                return_value=[
                    {"id": "", "summary": "empty id entry"},
                    {
                        "id": "valid-id",
                        "summary": "valid entry",
                        "last_accessed_at": datetime.now(tz=timezone.utc).date().isoformat(),
                    },
                ]
            )
            with patch(
                "trw_mcp.state.memory_adapter.list_active_learnings",
                mock_list,
            ):
                result = mgr.sweep()

        assert result.errors == 0


class TestSweepYamlFallbackSkips:
    """Test sweep YAML fallback filters out irrelevant entries."""

    def test_sweep_yaml_fallback_skips_index_and_empty_id_and_non_active(self, tmp_path: Path) -> None:
        """Lines 676, 681, 684: YAML fallback skips index.yaml, empty ID, non-active."""
        trw_dir = tmp_path / ".trw"
        entries_dir = _setup_entries_dir(trw_dir)

        writer = FileStateWriter()
        writer.write_yaml(entries_dir / "index.yaml", {"entries": []})
        writer.write_yaml(
            entries_dir / "no-id.yaml",
            {
                "summary": "no id entry",
                "status": "active",
                "last_accessed_at": datetime.now(tz=timezone.utc).date().isoformat(),
            },
        )
        writer.write_yaml(
            entries_dir / "resolved.yaml",
            {
                "id": "resolved-1",
                "summary": "resolved entry",
                "status": "resolved",
                "last_accessed_at": datetime.now(tz=timezone.utc).date().isoformat(),
            },
        )
        _write_yaml_entry(entries_dir, "active-recent")

        mgr = TierManager(trw_dir)

        with patch("trw_mcp.state.tiers.get_config") as mock_cfg:
            cfg = TRWConfig()
            object.__setattr__(cfg, "memory_hot_ttl_days", 999)
            mock_cfg.return_value = cfg

            with patch(
                "trw_mcp.state.memory_adapter.list_active_learnings",
                side_effect=ImportError("no sqlite"),
            ):
                result = mgr.sweep()

        assert result.demoted == 0
        assert result.errors == 0


class TestSweepYamlFallbackWarmToColdException:
    """Test sweep YAML fallback error handling during warm-to-cold."""

    def test_sweep_yaml_warm_to_cold_exception_counted(self, tmp_path: Path) -> None:
        """Lines 696-702: exception during YAML warm-to-cold increments errors."""
        trw_dir = tmp_path / ".trw"
        entries_dir = _setup_entries_dir(trw_dir)

        old_date = (datetime.now(tz=timezone.utc).date() - timedelta(days=200)).isoformat()
        _write_yaml_entry(
            entries_dir,
            "old-entry",
            impact=0.05,
            last_accessed_at=old_date,
        )

        mgr = TierManager(trw_dir)

        with patch("trw_mcp.state.tiers.get_config") as mock_cfg:
            cfg = TRWConfig()
            object.__setattr__(cfg, "memory_hot_ttl_days", 999)
            object.__setattr__(cfg, "memory_cold_threshold_days", 30)
            mock_cfg.return_value = cfg

            with patch(
                "trw_mcp.state.memory_adapter.list_active_learnings",
                side_effect=ImportError("no sqlite"),
            ):
                with patch.object(mgr, "cold_archive", side_effect=RuntimeError("archive fail")):
                    result = mgr.sweep()

        assert result.errors >= 1


class TestSweepColdPurgeException:
    """Test sweep cold purge exception handling."""

    def test_sweep_cold_purge_exception_counted(self, tmp_path: Path) -> None:
        """Lines 735-741: exception during cold purge increments errors."""
        trw_dir = tmp_path / ".trw"
        _setup_entries_dir(trw_dir)

        cold_dir = trw_dir / "memory" / "cold" / "2026" / "01"
        cold_dir.mkdir(parents=True, exist_ok=True)

        writer = FileStateWriter()
        old_date = (datetime.now(tz=timezone.utc).date() - timedelta(days=500)).isoformat()
        writer.write_yaml(
            cold_dir / "ancient.yaml",
            {"id": "ancient", "summary": "very old", "impact": 0.01, "last_accessed_at": old_date, "created": old_date},
        )

        reader = MagicMock(spec=FileStateReader)
        reader.read_yaml.side_effect = Exception("corrupt cold yaml")

        mgr = TierManager(trw_dir, reader=reader)

        with patch("trw_mcp.state.tiers.get_config") as mock_cfg:
            cfg = TRWConfig()
            object.__setattr__(cfg, "memory_hot_ttl_days", 999)
            object.__setattr__(cfg, "memory_retention_days", 90)
            mock_cfg.return_value = cfg

            with patch(
                "trw_mcp.state.memory_adapter.list_active_learnings",
                side_effect=ImportError("no sqlite"),
            ):
                result = mgr.sweep()

        assert result.errors >= 1


class TestSweepSQLitePerEntryException:
    """Test sweep warm-to-cold SQLite path handles per-entry exceptions."""

    def test_sweep_sqlite_per_entry_exception_counted(self, tmp_path: Path) -> None:
        """Exception on a single entry during SQLite warm-to-cold increments errors."""
        trw_dir = tmp_path / ".trw"
        entries_dir = trw_dir / "learnings" / "entries"
        entries_dir.mkdir(parents=True, exist_ok=True)

        old_date = (datetime.now(tz=timezone.utc).date() - timedelta(days=200)).isoformat()

        mgr = TierManager(trw_dir)

        with patch("trw_mcp.state.tiers.get_config") as mock_cfg:
            cfg = TRWConfig()
            object.__setattr__(cfg, "memory_hot_ttl_days", 999)
            object.__setattr__(cfg, "memory_cold_threshold_days", 30)
            mock_cfg.return_value = cfg

            mock_list = MagicMock(
                return_value=[
                    {
                        "id": "problematic",
                        "summary": "will fail",
                        "detail": "",
                        "status": "active",
                        "impact": 0.05,
                        "tags": [],
                        "last_accessed_at": old_date,
                        "created": old_date,
                    },
                ]
            )
            mock_find = MagicMock(return_value=entries_dir / "problematic.yaml")

            with patch("trw_mcp.state.memory_adapter.list_active_learnings", mock_list):
                with patch("trw_mcp.state.memory_adapter.find_yaml_path_for_entry", mock_find):
                    with patch.object(mgr, "cold_archive", side_effect=RuntimeError("archive boom")):
                        result = mgr.sweep()

        assert result.errors >= 1
        assert result.demoted == 0
