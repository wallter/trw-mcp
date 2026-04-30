"""Branch coverage tests for cold-tier behavior in tiers.py."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from trw_mcp.state.persistence import FileStateReader, FileStateWriter
from trw_mcp.state.tiers import TierManager

from tests._tiers_branches_support import _setup_entries_dir, _write_yaml_entry


class TestColdArchiveWarmRemoveException:
    """Test cold_archive when inner warm_remove fails."""

    def test_cold_archive_warm_remove_exception_caught(self, tmp_path: Path) -> None:
        """Lines 480-481: warm_remove exception inside cold_archive is swallowed."""
        trw_dir = tmp_path / ".trw"
        entries_dir = _setup_entries_dir(trw_dir)

        entry_path = _write_yaml_entry(entries_dir, "archive-me", impact=0.1)

        mgr = TierManager(trw_dir)

        with patch.object(mgr, "warm_remove", side_effect=RuntimeError("warm db gone")):
            mgr.cold_archive("archive-me", entry_path)

        assert not entry_path.exists()
        cold_base = trw_dir / "memory" / "cold"
        cold_files = list(cold_base.rglob("*.yaml"))
        assert len(cold_files) == 1


class TestColdPromoteEdgeCases:
    """Test cold_promote error handling branches."""

    def test_cold_promote_yaml_read_error_skips_file(self, tmp_path: Path) -> None:
        """Lines 515-516: unreadable YAML in cold archive is skipped."""
        trw_dir = tmp_path / ".trw"
        cold_dir = trw_dir / "memory" / "cold" / "2026" / "01"
        cold_dir.mkdir(parents=True, exist_ok=True)

        writer = FileStateWriter()
        writer.write_yaml(
            cold_dir / "target-entry.yaml",
            {"id": "target-entry", "summary": "target", "last_accessed_at": "2026-01-01"},
        )
        (cold_dir / "corrupt.yaml").write_text("{invalid yaml[", encoding="utf-8")

        reader = MagicMock(spec=FileStateReader)
        reader.read_yaml.side_effect = [
            Exception("parse error"),
            {"id": "target-entry", "summary": "target", "last_accessed_at": "2026-01-01"},
        ]

        mgr = TierManager(trw_dir, reader=reader, writer=FileStateWriter())
        result = mgr.cold_promote("target-entry")
        assert result is not None
        assert isinstance(result, dict)
        assert result["id"] == "target-entry"
        assert "summary" in result

    def test_cold_promote_id_mismatch_skips_file(self, tmp_path: Path) -> None:
        """Line 518: entry whose ID does not match is skipped."""
        trw_dir = tmp_path / ".trw"
        cold_dir = trw_dir / "memory" / "cold" / "2026" / "01"
        cold_dir.mkdir(parents=True, exist_ok=True)

        writer = FileStateWriter()
        writer.write_yaml(
            cold_dir / "other.yaml",
            {"id": "other-id", "summary": "not the one"},
        )
        writer.write_yaml(
            cold_dir / "target.yaml",
            {"id": "wanted-id", "summary": "the target", "last_accessed_at": "2026-01-01"},
        )

        mgr = TierManager(trw_dir)
        result = mgr.cold_promote("wanted-id")
        assert result is not None
        assert isinstance(result, dict)
        assert result["id"] == "wanted-id"
        assert result.get("summary") == "the target"

    def test_cold_promote_not_found_returns_none(self, tmp_path: Path) -> None:
        """Line 538: no matching entry in cold archive returns None."""
        trw_dir = tmp_path / ".trw"
        cold_dir = trw_dir / "memory" / "cold" / "2026" / "01"
        cold_dir.mkdir(parents=True, exist_ok=True)

        writer = FileStateWriter()
        writer.write_yaml(
            cold_dir / "other.yaml",
            {"id": "some-other-id", "summary": "not the one"},
        )

        mgr = TierManager(trw_dir)
        result = mgr.cold_promote("nonexistent-id")
        assert result is None

    def test_cold_promote_write_failure_returns_none(self, tmp_path: Path) -> None:
        """Lines 529-538: write failure during cold promote returns None."""
        trw_dir = tmp_path / ".trw"
        cold_dir = trw_dir / "memory" / "cold" / "2026" / "01"
        cold_dir.mkdir(parents=True, exist_ok=True)

        writer_mock = MagicMock(spec=FileStateWriter)
        writer_mock.write_yaml.side_effect = OSError("disk full")

        real_writer = FileStateWriter()
        real_writer.write_yaml(
            cold_dir / "target.yaml",
            {"id": "target-id", "summary": "target", "last_accessed_at": "2026-01-01"},
        )

        mgr = TierManager(trw_dir, reader=FileStateReader(), writer=writer_mock)
        result = mgr.cold_promote("target-id")
        assert result is None


class TestColdSearchReadError:
    """Test cold_search when a YAML file is unreadable."""

    def test_cold_search_skips_unreadable_files(self, tmp_path: Path) -> None:
        """Lines 561-562: unreadable YAML in cold archive is skipped."""
        trw_dir = tmp_path / ".trw"
        cold_dir = trw_dir / "memory" / "cold" / "2026" / "01"
        cold_dir.mkdir(parents=True, exist_ok=True)

        writer = FileStateWriter()
        writer.write_yaml(
            cold_dir / "good.yaml",
            {"id": "good", "summary": "testing keywords", "tags": ["test"]},
        )
        (cold_dir / "bad.yaml").write_text("{corrupt yaml[", encoding="utf-8")

        reader = MagicMock(spec=FileStateReader)
        reader.read_yaml.side_effect = [
            Exception("parse error"),
            {"id": "good", "summary": "testing keywords", "tags": ["test"]},
        ]

        mgr = TierManager(trw_dir, reader=reader)
        results = mgr.cold_search(["testing"])
        assert len(results) == 1
        assert results[0]["id"] == "good"


class TestColdPartitionExplicitTimestamp:
    """Test _cold_partition with an explicit timestamp argument."""

    def test_cold_partition_with_explicit_timestamp(self, tmp_path: Path) -> None:
        """_cold_partition returns correct path for a specific datetime."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir(parents=True, exist_ok=True)
        mgr = TierManager(trw_dir)
        ts = datetime(2025, 3, 15, 12, 0, 0, tzinfo=timezone.utc)
        result = mgr._cold_partition(ts)
        assert result == trw_dir / "memory" / "cold" / "2025" / "03"

    def test_cold_partition_none_uses_now(self, tmp_path: Path) -> None:
        """_cold_partition with None defaults to current UTC time."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir(parents=True, exist_ok=True)
        mgr = TierManager(trw_dir)
        result = mgr._cold_partition(None)
        today = datetime.now(tz=timezone.utc).date()
        assert str(today.year) in str(result)
        assert f"{today.month:02d}" in str(result)


class TestColdSearchEmptyQuery:
    """Test cold_search with empty query tokens."""

    def test_cold_search_empty_tokens_returns_empty(self, tmp_path: Path) -> None:
        """Empty query_tokens returns [] even when cold entries exist."""
        trw_dir = tmp_path / ".trw"
        cold_dir = trw_dir / "memory" / "cold" / "2026" / "01"
        cold_dir.mkdir(parents=True, exist_ok=True)

        writer = FileStateWriter()
        writer.write_yaml(
            cold_dir / "entry.yaml",
            {"id": "entry-1", "summary": "some content", "tags": ["tag"]},
        )

        mgr = TierManager(trw_dir)
        assert mgr.cold_search([]) == []


class TestColdPromoteNoColdDir:
    """Test cold_promote when cold directory doesn't exist at all."""

    def test_cold_promote_no_cold_dir_returns_none(self, tmp_path: Path) -> None:
        """No cold directory means immediate None return."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir(parents=True, exist_ok=True)
        mgr = TierManager(trw_dir)
        assert mgr.cold_promote("any-id") is None


class TestColdArchiveReadFailureRaises:
    """Test that cold_archive re-raises on read failure."""

    def test_cold_archive_read_failure_raises(self, tmp_path: Path) -> None:
        """When reader.read_yaml fails, cold_archive raises the exception."""
        trw_dir = tmp_path / ".trw"
        entries_dir = trw_dir / "learnings" / "entries"
        entries_dir.mkdir(parents=True, exist_ok=True)

        entry_path = entries_dir / "fail-entry.yaml"
        entry_path.write_text("id: fail-entry\n", encoding="utf-8")

        reader = MagicMock(spec=FileStateReader)
        reader.read_yaml.side_effect = OSError("disk read failure")

        mgr = TierManager(trw_dir, reader=reader, writer=FileStateWriter())
        with pytest.raises(OSError, match="disk read failure"):
            mgr.cold_archive("fail-entry", entry_path)
