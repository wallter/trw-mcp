"""Targeted coverage tests for tiers.py — bringing coverage from 88% to >=92%.

Covers previously uncovered lines:
- 240-241: _flush_last_accessed exception handler
- 288-292: warm_add with MemoryStore available + embedding
- 313, 318-319: _warm_sidecar_upsert blank line skip / JSON decode error
- 355, 360-361: warm_remove sidecar blank line skip / JSON decode error
- 422, 425-426: _warm_keyword_search blank line skip / JSON decode error
- 480-481: cold_archive inner warm_remove exception
- 515-516, 518: cold_promote YAML read error / ID mismatch
- 529-538: cold_promote write failure exception handler
- 561-562: cold_search YAML read error
- 617-619: sweep hot-to-warm exception handler
- 636: sweep warm-to-cold SQLite path skip empty entry_id
- 676, 681, 684: sweep YAML fallback skip index.yaml / empty ID / non-active
- 696-702: sweep YAML fallback warm-to-cold exception handler
- 735-741: sweep cold purge exception handler
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from trw_mcp.models.config import TRWConfig
from trw_mcp.models.learning import LearningEntry, LearningStatus
from trw_mcp.state.persistence import FileStateReader, FileStateWriter
from trw_mcp.state.tiers import TierManager

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_entry(
    entry_id: str = "test-001",
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
        source_type="agent",
        source_identity="test",
        created=date.fromisoformat(created or today),
        last_accessed_at=date.fromisoformat(last_accessed_at) if last_accessed_at else None,
    )


def _make_old_entry(entry_id: str = "old-001", days_ago: int = 60) -> LearningEntry:
    """Build a LearningEntry with an old last_accessed_at date."""
    old_date = (datetime.now(tz=timezone.utc).date() - timedelta(days=days_ago)).isoformat()
    return _make_entry(
        entry_id=entry_id,
        last_accessed_at=old_date,
        created=old_date,
        impact=0.1,
    )


def _setup_entries_dir(trw_dir: Path) -> Path:
    """Create standard learnings/entries dir structure."""
    entries_dir = trw_dir / "learnings" / "entries"
    entries_dir.mkdir(parents=True, exist_ok=True)
    return entries_dir


def _write_yaml_entry(
    entries_dir: Path,
    entry_id: str,
    *,
    summary: str = "test summary",
    impact: float = 0.5,
    status: str = "active",
    last_accessed_at: str | None = None,
    q_observations: int = 0,
) -> Path:
    """Write a minimal YAML entry file to disk."""
    today = datetime.now(tz=timezone.utc).date().isoformat()
    data = {
        "id": entry_id,
        "summary": summary,
        "detail": f"detail for {entry_id}",
        "tags": ["test"],
        "impact": impact,
        "status": status,
        "source_type": "agent",
        "created": last_accessed_at or today,
        "last_accessed_at": last_accessed_at or today,
        "q_observations": q_observations,
    }
    writer = FileStateWriter()
    path = entries_dir / f"{entry_id}.yaml"
    writer.write_yaml(path, data)
    return path


# ---------------------------------------------------------------------------
# _flush_last_accessed exception handler (lines 240-241)
# ---------------------------------------------------------------------------


class TestFlushLastAccessedException:
    """Test _flush_last_accessed when read/write raises."""

    def test_flush_last_accessed_exception_logged(self, tmp_path: Path) -> None:
        """Lines 240-241: exception during YAML read/write is logged, not raised."""
        trw_dir = tmp_path / ".trw"
        entries_dir = _setup_entries_dir(trw_dir)

        # Create a real YAML file so the exists() check passes
        entry_path = entries_dir / "bad-entry.yaml"
        entry_path.write_text("id: bad-entry\n", encoding="utf-8")

        reader = MagicMock(spec=FileStateReader)
        reader.read_yaml.side_effect = Exception("disk failure")

        mgr = TierManager(trw_dir, reader=reader)
        # Should NOT raise — the exception is caught and logged
        mgr._flush_last_accessed("bad-entry")
        reader.read_yaml.assert_called_once()


# ---------------------------------------------------------------------------
# warm_add with MemoryStore available + embedding (lines 288-292)
# ---------------------------------------------------------------------------


class TestWarmAddWithMemoryStore:
    """Test warm_add branch when MemoryStore is available and embedding provided."""

    def test_warm_add_memory_store_available_with_embedding(self, tmp_path: Path) -> None:
        """Lines 288-292: MemoryStore.available() is True and embedding is not None."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir(parents=True, exist_ok=True)
        mgr = TierManager(trw_dir)

        mock_store = MagicMock()
        mock_store_cls = MagicMock(return_value=mock_store)
        mock_store_cls.available.return_value = True

        with patch("trw_mcp.state.tiers.MemoryStore", mock_store_cls, create=True):
            # Patch the local import
            original_import = __builtins__.__import__ if hasattr(__builtins__, "__import__") else __import__

            # Use a simpler approach — patch at the module function level
            with patch.dict("sys.modules", {}):
                pass  # Need different approach

        # Use monkeypatch on the actual import
        mock_ms = MagicMock()
        mock_ms.available.return_value = True
        mock_instance = MagicMock()
        mock_ms.return_value = mock_instance

        with patch("trw_mcp.state.memory_store.MemoryStore", mock_ms):
            mgr.warm_add("entry-1", {"summary": "test"}, [0.1, 0.2, 0.3])

        mock_instance.upsert.assert_called_once_with("entry-1", [0.1, 0.2, 0.3], {"source": "warm_tier"})
        # FIX-046: singleton pattern — no per-op close() call
        mock_instance.close.assert_not_called()

    def test_warm_add_memory_store_upsert_exception_propagates(self, tmp_path: Path) -> None:
        """Lines 288-292: MemoryStore.upsert raises — exception propagates."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir(parents=True, exist_ok=True)
        mgr = TierManager(trw_dir)

        mock_ms = MagicMock()
        mock_ms.available.return_value = True
        mock_instance = MagicMock()
        mock_instance.upsert.side_effect = RuntimeError("vec error")
        mock_ms.return_value = mock_instance

        with patch("trw_mcp.state.memory_store.MemoryStore", mock_ms):
            with pytest.raises(RuntimeError, match="vec error"):
                mgr.warm_add("entry-1", {"summary": "test"}, [0.1, 0.2, 0.3])


# ---------------------------------------------------------------------------
# _warm_sidecar_upsert blank line / JSON decode error (lines 313, 318-319)
# ---------------------------------------------------------------------------


class TestWarmSidecarUpsertEdgeCases:
    """Test blank lines and corrupt JSON in sidecar file."""

    def test_sidecar_upsert_skips_blank_lines(self, tmp_path: Path) -> None:
        """Line 313: blank lines in existing sidecar are skipped."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir(parents=True, exist_ok=True)
        mgr = TierManager(trw_dir)

        sidecar = mgr._warm_sidecar_path()
        sidecar.parent.mkdir(parents=True, exist_ok=True)
        # Write sidecar with blank lines interspersed
        sidecar.write_text(
            json.dumps({"id": "keep-me", "summary": "keep"})
            + "\n"
            + "\n"  # blank line
            + "   \n"  # whitespace-only line
            + json.dumps({"id": "also-keep", "summary": "also"})
            + "\n",
            encoding="utf-8",
        )

        mgr._warm_sidecar_upsert("new-entry", {"summary": "new"})

        # Read back — should have keep-me, also-keep, new-entry
        lines = [l.strip() for l in sidecar.read_text(encoding="utf-8").splitlines() if l.strip()]
        ids = [json.loads(l)["id"] for l in lines]
        assert "keep-me" in ids
        assert "also-keep" in ids
        assert "new-entry" in ids

    def test_sidecar_upsert_skips_corrupt_json(self, tmp_path: Path) -> None:
        """Lines 318-319: corrupt JSON lines in sidecar are silently skipped."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir(parents=True, exist_ok=True)
        mgr = TierManager(trw_dir)

        sidecar = mgr._warm_sidecar_path()
        sidecar.parent.mkdir(parents=True, exist_ok=True)
        sidecar.write_text(
            json.dumps({"id": "good", "summary": "good"})
            + "\n"
            + "{not valid json\n"
            + json.dumps({"id": "also-good", "summary": "also"})
            + "\n",
            encoding="utf-8",
        )

        mgr._warm_sidecar_upsert("new-entry", {"summary": "new"})

        lines = [l.strip() for l in sidecar.read_text(encoding="utf-8").splitlines() if l.strip()]
        ids = [json.loads(l)["id"] for l in lines]
        assert "good" in ids
        assert "also-good" in ids
        assert "new-entry" in ids


# ---------------------------------------------------------------------------
# warm_remove sidecar blank line / JSON decode error (lines 355, 360-361)
# ---------------------------------------------------------------------------


class TestWarmRemoveSidecarEdgeCases:
    """Test warm_remove handles corrupt sidecar gracefully."""

    def test_warm_remove_sidecar_skips_blank_lines(self, tmp_path: Path) -> None:
        """Line 355: blank lines in sidecar during warm_remove are skipped."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir(parents=True, exist_ok=True)
        mgr = TierManager(trw_dir)

        sidecar = mgr._warm_sidecar_path()
        sidecar.parent.mkdir(parents=True, exist_ok=True)
        sidecar.write_text(
            json.dumps({"id": "remove-me", "summary": "bye"})
            + "\n"
            + "\n"  # blank line
            + json.dumps({"id": "keep-me", "summary": "stay"})
            + "\n",
            encoding="utf-8",
        )

        with patch("trw_mcp.state.memory_store.MemoryStore") as mock_ms:
            mock_ms.available.return_value = False
            mgr.warm_remove("remove-me")

        lines = [l.strip() for l in sidecar.read_text(encoding="utf-8").splitlines() if l.strip()]
        ids = [json.loads(l)["id"] for l in lines]
        assert "remove-me" not in ids
        assert "keep-me" in ids

    def test_warm_remove_sidecar_skips_corrupt_json(self, tmp_path: Path) -> None:
        """Lines 360-361: corrupt JSON lines in sidecar during remove are skipped."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir(parents=True, exist_ok=True)
        mgr = TierManager(trw_dir)

        sidecar = mgr._warm_sidecar_path()
        sidecar.parent.mkdir(parents=True, exist_ok=True)
        sidecar.write_text(
            json.dumps({"id": "keep", "summary": "stay"})
            + "\n"
            + "{broken json\n"
            + json.dumps({"id": "remove-me", "summary": "bye"})
            + "\n",
            encoding="utf-8",
        )

        with patch("trw_mcp.state.memory_store.MemoryStore") as mock_ms:
            mock_ms.available.return_value = False
            mgr.warm_remove("remove-me")

        lines = [l.strip() for l in sidecar.read_text(encoding="utf-8").splitlines() if l.strip()]
        ids = [json.loads(l)["id"] for l in lines]
        assert "keep" in ids
        assert "remove-me" not in ids


# ---------------------------------------------------------------------------
# _warm_keyword_search blank line / JSON decode error (lines 422, 425-426)
# ---------------------------------------------------------------------------


class TestWarmKeywordSearchEdgeCases:
    """Test _warm_keyword_search handles corrupt sidecar gracefully."""

    def test_keyword_search_skips_blank_lines(self, tmp_path: Path) -> None:
        """Line 422: blank lines in sidecar during keyword search are skipped."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir(parents=True, exist_ok=True)
        mgr = TierManager(trw_dir)

        sidecar = mgr._warm_sidecar_path()
        sidecar.parent.mkdir(parents=True, exist_ok=True)
        sidecar.write_text(
            json.dumps({"id": "entry-1", "summary": "testing coverage", "tags": []})
            + "\n"
            + "\n"  # blank line
            + "  \n"  # whitespace-only
            + json.dumps({"id": "entry-2", "summary": "another test", "tags": ["foo"]})
            + "\n",
            encoding="utf-8",
        )

        results = mgr._warm_keyword_search(["testing"], top_k=10)
        assert len(results) == 1
        assert results[0]["id"] == "entry-1"

    def test_keyword_search_skips_corrupt_json(self, tmp_path: Path) -> None:
        """Lines 425-426: corrupt JSON in sidecar during keyword search is skipped."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir(parents=True, exist_ok=True)
        mgr = TierManager(trw_dir)

        sidecar = mgr._warm_sidecar_path()
        sidecar.parent.mkdir(parents=True, exist_ok=True)
        sidecar.write_text(
            json.dumps({"id": "entry-1", "summary": "testing coverage", "tags": []})
            + "\n"
            + "{corrupt json here\n"
            + json.dumps({"id": "entry-2", "summary": "other topic", "tags": []})
            + "\n",
            encoding="utf-8",
        )

        results = mgr._warm_keyword_search(["testing"], top_k=10)
        assert len(results) == 1
        assert results[0]["id"] == "entry-1"


# ---------------------------------------------------------------------------
# cold_archive inner warm_remove exception (lines 480-481)
# ---------------------------------------------------------------------------


class TestColdArchiveWarmRemoveException:
    """Test cold_archive when inner warm_remove fails."""

    def test_cold_archive_warm_remove_exception_caught(self, tmp_path: Path) -> None:
        """Lines 480-481: warm_remove exception inside cold_archive is swallowed."""
        trw_dir = tmp_path / ".trw"
        entries_dir = _setup_entries_dir(trw_dir)

        entry_path = _write_yaml_entry(entries_dir, "archive-me", impact=0.1)

        mgr = TierManager(trw_dir)

        # Make warm_remove raise
        with patch.object(mgr, "warm_remove", side_effect=RuntimeError("warm db gone")):
            mgr.cold_archive("archive-me", entry_path)

        # Should still succeed — entry archived, original deleted
        assert not entry_path.exists()
        cold_base = trw_dir / "memory" / "cold"
        cold_files = list(cold_base.rglob("*.yaml"))
        assert len(cold_files) == 1


# ---------------------------------------------------------------------------
# cold_promote YAML read error / ID mismatch / write failure (lines 515-538)
# ---------------------------------------------------------------------------


class TestColdPromoteEdgeCases:
    """Test cold_promote error handling branches."""

    def test_cold_promote_yaml_read_error_skips_file(self, tmp_path: Path) -> None:
        """Lines 515-516: unreadable YAML in cold archive is skipped."""
        trw_dir = tmp_path / ".trw"
        cold_dir = trw_dir / "memory" / "cold" / "2026" / "01"
        cold_dir.mkdir(parents=True, exist_ok=True)

        # Write a valid file with the target ID
        writer = FileStateWriter()
        writer.write_yaml(
            cold_dir / "target-entry.yaml",
            {"id": "target-entry", "summary": "target", "last_accessed_at": "2026-01-01"},
        )

        # Write a corrupt file that will fail to read
        (cold_dir / "corrupt.yaml").write_text("{invalid yaml[", encoding="utf-8")

        reader = MagicMock(spec=FileStateReader)
        # First call (corrupt) raises, second call (target) succeeds
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

        reader = FileStateReader()
        real_writer = FileStateWriter()
        real_writer.write_yaml(
            cold_dir / "target.yaml",
            {"id": "target-id", "summary": "target", "last_accessed_at": "2026-01-01"},
        )

        mgr = TierManager(trw_dir, reader=reader, writer=writer_mock)
        result = mgr.cold_promote("target-id")
        assert result is None


# ---------------------------------------------------------------------------
# cold_search YAML read error (lines 561-562)
# ---------------------------------------------------------------------------


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
        # Write corrupt file
        (cold_dir / "bad.yaml").write_text("{corrupt yaml[", encoding="utf-8")

        reader = MagicMock(spec=FileStateReader)
        reader.read_yaml.side_effect = [
            Exception("parse error"),  # bad.yaml
            {"id": "good", "summary": "testing keywords", "tags": ["test"]},  # good.yaml
        ]

        mgr = TierManager(trw_dir, reader=reader)
        results = mgr.cold_search(["testing"])
        assert len(results) == 1
        assert results[0]["id"] == "good"


# ---------------------------------------------------------------------------
# sweep: hot-to-warm exception (lines 617-619)
# ---------------------------------------------------------------------------


class TestSweepHotToWarmException:
    """Test sweep hot-to-warm transition error handling."""

    def test_sweep_hot_to_warm_exception_counted(self, tmp_path: Path) -> None:
        """Lines 617-619: exception during hot-to-warm transition increments errors."""
        trw_dir = tmp_path / ".trw"
        _setup_entries_dir(trw_dir)

        mgr = TierManager(trw_dir)

        # Put a stale entry in hot cache
        stale_entry = _make_old_entry("stale-1", days_ago=100)
        mgr._hot["stale-1"] = stale_entry

        # Make warm_add raise
        with patch.object(mgr, "warm_add", side_effect=RuntimeError("warm failure")):
            with patch("trw_mcp.state.tiers.get_config") as mock_cfg:
                cfg = TRWConfig()
                object.__setattr__(cfg, "memory_hot_ttl_days", 1)
                mock_cfg.return_value = cfg
                # Also need to prevent the SQLite import from running
                with patch(
                    "trw_mcp.state.memory_adapter.list_active_learnings",
                    side_effect=ImportError("no sqlite"),
                ):
                    result = mgr.sweep()

        assert result.errors >= 1


# ---------------------------------------------------------------------------
# sweep: warm-to-cold SQLite path skip empty entry_id (line 636)
# ---------------------------------------------------------------------------


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
                    {"id": "valid-id", "summary": "valid entry", "last_accessed_at": datetime.now(tz=timezone.utc).date().isoformat()},
                ]
            )
            with patch(
                "trw_mcp.state.memory_adapter.list_active_learnings",
                mock_list,
            ):
                result = mgr.sweep()

        # No errors — empty ID entries are silently skipped
        assert result.errors == 0


# ---------------------------------------------------------------------------
# sweep: YAML fallback — skip index.yaml, empty ID, non-active (lines 676, 681, 684)
# ---------------------------------------------------------------------------


class TestSweepYamlFallbackSkips:
    """Test sweep YAML fallback filters out irrelevant entries."""

    def test_sweep_yaml_fallback_skips_index_and_empty_id_and_non_active(self, tmp_path: Path) -> None:
        """Lines 676, 681, 684: YAML fallback skips index.yaml, empty ID, non-active."""
        trw_dir = tmp_path / ".trw"
        entries_dir = _setup_entries_dir(trw_dir)

        writer = FileStateWriter()

        # index.yaml — should be skipped (line 676)
        writer.write_yaml(entries_dir / "index.yaml", {"entries": []})

        # Entry without ID — should be skipped (line 681)
        writer.write_yaml(
            entries_dir / "no-id.yaml",
            {"summary": "no id entry", "status": "active", "last_accessed_at": datetime.now(tz=timezone.utc).date().isoformat()},
        )

        # Non-active entry — should be skipped (line 684)
        writer.write_yaml(
            entries_dir / "resolved.yaml",
            {
                "id": "resolved-1",
                "summary": "resolved entry",
                "status": "resolved",
                "last_accessed_at": datetime.now(tz=timezone.utc).date().isoformat(),
            },
        )

        # Valid active entry (recent, won't be demoted)
        _write_yaml_entry(entries_dir, "active-recent")

        mgr = TierManager(trw_dir)

        with patch("trw_mcp.state.tiers.get_config") as mock_cfg:
            cfg = TRWConfig()
            object.__setattr__(cfg, "memory_hot_ttl_days", 999)
            mock_cfg.return_value = cfg

            # Force SQLite path to fail so YAML fallback is used
            with patch(
                "trw_mcp.state.memory_adapter.list_active_learnings",
                side_effect=ImportError("no sqlite"),
            ):
                result = mgr.sweep()

        # No demotions — only the recent active entry exists
        assert result.demoted == 0
        assert result.errors == 0


# ---------------------------------------------------------------------------
# sweep: YAML fallback warm-to-cold exception (lines 696-702)
# ---------------------------------------------------------------------------


class TestSweepYamlFallbackWarmToColdException:
    """Test sweep YAML fallback error handling during warm-to-cold."""

    def test_sweep_yaml_warm_to_cold_exception_counted(self, tmp_path: Path) -> None:
        """Lines 696-702: exception during YAML warm-to-cold increments errors."""
        trw_dir = tmp_path / ".trw"
        entries_dir = _setup_entries_dir(trw_dir)

        # Write an old, low-impact entry that would trigger cold archival
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

            # Force SQLite to fail (use YAML fallback)
            with patch(
                "trw_mcp.state.memory_adapter.list_active_learnings",
                side_effect=ImportError("no sqlite"),
            ):
                # Make cold_archive raise
                with patch.object(mgr, "cold_archive", side_effect=RuntimeError("archive fail")):
                    result = mgr.sweep()

        assert result.errors >= 1


# ---------------------------------------------------------------------------
# sweep: cold purge exception (lines 735-741)
# ---------------------------------------------------------------------------


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

        # Use a reader that fails on the cold file
        reader = MagicMock(spec=FileStateReader)
        reader.read_yaml.side_effect = Exception("corrupt cold yaml")

        mgr = TierManager(trw_dir, reader=reader)

        with patch("trw_mcp.state.tiers.get_config") as mock_cfg:
            cfg = TRWConfig()
            object.__setattr__(cfg, "memory_hot_ttl_days", 999)
            object.__setattr__(cfg, "memory_retention_days", 90)
            mock_cfg.return_value = cfg

            # Force SQLite to fail
            with patch(
                "trw_mcp.state.memory_adapter.list_active_learnings",
                side_effect=ImportError("no sqlite"),
            ):
                result = mgr.sweep()

        assert result.errors >= 1


# ---------------------------------------------------------------------------
# compute_importance_score edge cases
# ---------------------------------------------------------------------------


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
        # With w1=0.5, full token match, recent entry, impact=1.0
        score = compute_importance_score(entry, ["test", "keyword"], config=cfg)
        # relevance=1.0, recency~1.0, importance=1.0 => score ~= 0.5+0.3+0.2 = 1.0
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
        # decay_rate=0 => exp(0)=1.0, so recency=1.0 regardless of age
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


# ---------------------------------------------------------------------------
# Cold tier edge cases
# ---------------------------------------------------------------------------


class TestColdPartitionExplicitTimestamp:
    """Test _cold_partition with an explicit timestamp argument."""

    def test_cold_partition_with_explicit_timestamp(self, tmp_path: Path) -> None:
        """_cold_partition returns correct path for a specific datetime."""
        from datetime import datetime, timezone

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
        # Deliberately do NOT create memory/cold/
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


# ---------------------------------------------------------------------------
# Warm tier edge cases
# ---------------------------------------------------------------------------


class TestWarmKeywordSearchTopK:
    """Test _warm_keyword_search respects top_k limit."""

    def test_keyword_search_respects_top_k(self, tmp_path: Path) -> None:
        """Only top_k results returned when more matches exist."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir(parents=True, exist_ok=True)
        mgr = TierManager(trw_dir)

        # Add 5 entries all matching "test"
        for i in range(5):
            mgr._warm_sidecar_upsert(f"e{i}", {"summary": f"test entry {i}", "tags": []})

        results = mgr._warm_keyword_search(["test"], top_k=2)
        assert len(results) == 2

    def test_keyword_search_empty_tokens_returns_empty(self, tmp_path: Path) -> None:
        """Empty query_tokens returns [] even when sidecar has entries."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir(parents=True, exist_ok=True)
        mgr = TierManager(trw_dir)
        mgr._warm_sidecar_upsert("e1", {"summary": "test entry", "tags": []})
        assert mgr._warm_keyword_search([], top_k=10) == []


class TestWarmSearchTagMatching:
    """Test that warm keyword search matches on tags, not just summary."""

    def test_keyword_search_matches_tags(self, tmp_path: Path) -> None:
        """Entries matched by tag content alone are returned."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir(parents=True, exist_ok=True)
        mgr = TierManager(trw_dir)

        # Entry with matching tag but no matching summary text
        mgr._warm_sidecar_upsert(
            "tagged",
            {"summary": "unrelated summary", "tags": ["pytest", "fixture"]},
        )
        results = mgr._warm_keyword_search(["pytest"], top_k=10)
        assert len(results) == 1
        assert results[0]["id"] == "tagged"


class TestWarmRemoveEmptySidecar:
    """Test warm_remove when sidecar becomes empty after removal."""

    def test_warm_remove_last_entry_empties_sidecar(self, tmp_path: Path) -> None:
        """Removing the only entry leaves sidecar empty (not stale data)."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir(parents=True, exist_ok=True)
        mgr = TierManager(trw_dir)

        mgr._warm_sidecar_upsert("only-one", {"summary": "sole entry", "tags": []})
        sidecar = mgr._warm_sidecar_path()
        assert sidecar.exists()

        with patch("trw_mcp.state.memory_store.MemoryStore") as mock_ms:
            mock_ms.available.return_value = False
            mgr.warm_remove("only-one")

        # Sidecar should be empty or contain no records
        content = sidecar.read_text(encoding="utf-8").strip()
        assert content == ""

    def test_warm_remove_no_sidecar_no_error(self, tmp_path: Path) -> None:
        """Removing when no sidecar file exists does not raise."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir(parents=True, exist_ok=True)
        mgr = TierManager(trw_dir)

        sidecar = mgr._warm_sidecar_path()
        assert not sidecar.exists()

        with patch("trw_mcp.state.memory_store.MemoryStore") as mock_ms:
            mock_ms.available.return_value = False
            mgr.warm_remove("nonexistent")  # should not raise


class TestWarmSearchFallbackPath:
    """Test warm_search falls back to keyword search when MemoryStore unavailable."""

    def test_warm_search_no_memorystore_uses_keyword_fallback(self, tmp_path: Path) -> None:
        """When MemoryStore.available() is False, keyword search is used."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir(parents=True, exist_ok=True)
        mgr = TierManager(trw_dir)

        # Populate sidecar
        mgr._warm_sidecar_upsert("w1", {"summary": "pytest patterns", "tags": []})
        mgr._warm_sidecar_upsert("w2", {"summary": "docker setup", "tags": []})

        with patch("trw_mcp.state.memory_store.MemoryStore") as mock_ms:
            mock_ms.available.return_value = False
            results = mgr.warm_search(["pytest"], query_embedding=[0.1] * 384)

        # Despite providing an embedding, keyword fallback is used
        assert len(results) == 1
        assert results[0]["id"] == "w1"

    def test_warm_search_no_embedding_uses_keyword_fallback(self, tmp_path: Path) -> None:
        """When query_embedding is None, keyword search is used even if store available."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir(parents=True, exist_ok=True)
        mgr = TierManager(trw_dir)

        mgr._warm_sidecar_upsert("w1", {"summary": "testing patterns", "tags": []})

        with patch("trw_mcp.state.memory_store.MemoryStore") as mock_ms:
            mock_ms.available.return_value = True
            results = mgr.warm_search(["testing"], query_embedding=None)

        assert len(results) == 1
        assert results[0]["id"] == "w1"


# ---------------------------------------------------------------------------
# Hot tier edge cases
# ---------------------------------------------------------------------------


class TestFlushLastAccessedSanitization:
    """Test _flush_last_accessed filename sanitization with special chars."""

    def test_flush_sanitizes_special_chars_in_id(self, tmp_path: Path) -> None:
        """Entry IDs with special characters are sanitized to dashes for filename."""
        trw_dir = tmp_path / ".trw"
        entries_dir = trw_dir / "learnings" / "entries"
        entries_dir.mkdir(parents=True, exist_ok=True)

        # Create file with sanitized name (what the code would produce)
        sanitized_path = entries_dir / "entry-with-spaces-.yaml"
        writer = FileStateWriter()
        writer.write_yaml(sanitized_path, {"id": "entry with spaces!", "summary": "test"})

        mgr = TierManager(trw_dir, writer=writer)
        # Should not raise — finds the sanitized filename
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
        # No file exists for this ID — should silently return
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

        mgr.hot_put("e3", _make_entry("e3"))  # evicts e1
        assert mgr.hot_get("e1") is None
        assert mgr.hot_size == 2

        mgr.hot_put("e4", _make_entry("e4"))  # evicts e2
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


# ---------------------------------------------------------------------------
# Sweep SQLite warm-to-cold exception on individual entry
# ---------------------------------------------------------------------------


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
        assert result.demoted == 0  # Failed to demote
