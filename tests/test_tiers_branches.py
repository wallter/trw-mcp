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
from datetime import date, timedelta
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
    today = date.today().isoformat()
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
    old_date = (date.today() - timedelta(days=days_ago)).isoformat()
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
    today = date.today().isoformat()
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
            original_import = __builtins__.__import__ if hasattr(__builtins__, '__import__') else __import__

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

        mock_instance.upsert.assert_called_once_with(
            "entry-1", [0.1, 0.2, 0.3], {"source": "warm_tier"}
        )
        mock_instance.close.assert_called_once()

    def test_warm_add_memory_store_upsert_exception_still_closes(self, tmp_path: Path) -> None:
        """Lines 288-292: MemoryStore.upsert raises, but close() still called (finally)."""
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

        # close() must still be called despite exception (finally block)
        mock_instance.close.assert_called_once()


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
            json.dumps({"id": "keep-me", "summary": "keep"}) + "\n"
            + "\n"  # blank line
            + "   \n"  # whitespace-only line
            + json.dumps({"id": "also-keep", "summary": "also"}) + "\n",
            encoding="utf-8",
        )

        mgr._warm_sidecar_upsert("new-entry", {"summary": "new"})

        # Read back — should have keep-me, also-keep, new-entry
        lines = [
            l.strip() for l in sidecar.read_text(encoding="utf-8").splitlines()
            if l.strip()
        ]
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
            json.dumps({"id": "good", "summary": "good"}) + "\n"
            + "{not valid json\n"
            + json.dumps({"id": "also-good", "summary": "also"}) + "\n",
            encoding="utf-8",
        )

        mgr._warm_sidecar_upsert("new-entry", {"summary": "new"})

        lines = [
            l.strip() for l in sidecar.read_text(encoding="utf-8").splitlines()
            if l.strip()
        ]
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
            json.dumps({"id": "remove-me", "summary": "bye"}) + "\n"
            + "\n"  # blank line
            + json.dumps({"id": "keep-me", "summary": "stay"}) + "\n",
            encoding="utf-8",
        )

        with patch("trw_mcp.state.memory_store.MemoryStore") as mock_ms:
            mock_ms.available.return_value = False
            mgr.warm_remove("remove-me")

        lines = [
            l.strip() for l in sidecar.read_text(encoding="utf-8").splitlines()
            if l.strip()
        ]
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
            json.dumps({"id": "keep", "summary": "stay"}) + "\n"
            + "{broken json\n"
            + json.dumps({"id": "remove-me", "summary": "bye"}) + "\n",
            encoding="utf-8",
        )

        with patch("trw_mcp.state.memory_store.MemoryStore") as mock_ms:
            mock_ms.available.return_value = False
            mgr.warm_remove("remove-me")

        lines = [
            l.strip() for l in sidecar.read_text(encoding="utf-8").splitlines()
            if l.strip()
        ]
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
            json.dumps({"id": "entry-1", "summary": "testing coverage", "tags": []}) + "\n"
            + "\n"  # blank line
            + "  \n"  # whitespace-only
            + json.dumps({"id": "entry-2", "summary": "another test", "tags": ["foo"]}) + "\n",
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
            json.dumps({"id": "entry-1", "summary": "testing coverage", "tags": []}) + "\n"
            + "{corrupt json here\n"
            + json.dumps({"id": "entry-2", "summary": "other topic", "tags": []}) + "\n",
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
        assert result["id"] == "target-entry"

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
        assert result["id"] == "wanted-id"

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

            mock_list = MagicMock(return_value=[
                {"id": "", "summary": "empty id entry"},
                {"id": "valid-id", "summary": "valid entry", "last_accessed_at": date.today().isoformat()},
            ])
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

    def test_sweep_yaml_fallback_skips_index_and_empty_id_and_non_active(
        self, tmp_path: Path
    ) -> None:
        """Lines 676, 681, 684: YAML fallback skips index.yaml, empty ID, non-active."""
        trw_dir = tmp_path / ".trw"
        entries_dir = _setup_entries_dir(trw_dir)

        writer = FileStateWriter()

        # index.yaml — should be skipped (line 676)
        writer.write_yaml(entries_dir / "index.yaml", {"entries": []})

        # Entry without ID — should be skipped (line 681)
        writer.write_yaml(
            entries_dir / "no-id.yaml",
            {"summary": "no id entry", "status": "active", "last_accessed_at": date.today().isoformat()},
        )

        # Non-active entry — should be skipped (line 684)
        writer.write_yaml(
            entries_dir / "resolved.yaml",
            {"id": "resolved-1", "summary": "resolved entry", "status": "resolved",
             "last_accessed_at": date.today().isoformat()},
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
        old_date = (date.today() - timedelta(days=200)).isoformat()
        _write_yaml_entry(
            entries_dir, "old-entry",
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
        old_date = (date.today() - timedelta(days=500)).isoformat()
        writer.write_yaml(
            cold_dir / "ancient.yaml",
            {"id": "ancient", "summary": "very old", "impact": 0.01,
             "last_accessed_at": old_date, "created": old_date},
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
