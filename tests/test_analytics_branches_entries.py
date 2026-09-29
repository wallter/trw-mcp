"""Targeted analytics entry iteration and lookup branch tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests._analytics_branches_support import _write_entry
from trw_mcp.state.analytics import (
    _iter_entry_files,
    find_entry_by_id,
    has_existing_mechanical_learning,
)

from ._analytics_branches_support import trw_dir  # noqa: F401


class TestIterEntryFilesIndexYamlSkipped:
    """Line 66: index.yaml is skipped by _iter_entry_files."""

    def test_index_yaml_is_skipped(self, trw_dir: Path) -> None:
        """index.yaml file is silently skipped — line 66 (continue)."""
        entries_dir = trw_dir / "learnings" / "entries"
        _write_entry(entries_dir, "valid_entry", summary="real learning")
        (entries_dir / "index.yaml").write_text("entries: []\ntotal_count: 0\n", encoding="utf-8")

        results = list(_iter_entry_files(entries_dir))
        filenames = [p.name for p, _ in results]
        assert "index.yaml" not in filenames
        assert "valid_entry.yaml" in filenames

    def test_index_yaml_skipped_sorted_order(self, trw_dir: Path) -> None:
        """index.yaml skipped even in sorted_order=True mode — line 66."""
        entries_dir = trw_dir / "learnings" / "entries"
        _write_entry(entries_dir, "aaa_entry", summary="first learning")
        (entries_dir / "index.yaml").write_text("entries: []\ntotal_count: 0\n", encoding="utf-8")

        results = list(_iter_entry_files(entries_dir, sorted_order=True))
        filenames = [p.name for p, _ in results]
        assert "index.yaml" not in filenames
        assert "aaa_entry.yaml" in filenames


class TestIterEntryFilesExceptionHandling:
    """Line 71: corrupt files are skipped with continue."""

    def test_corrupt_yaml_is_skipped(self, trw_dir: Path) -> None:
        """Unparseable YAML file is silently skipped — line 71 (continue)."""
        entries_dir = trw_dir / "learnings" / "entries"
        _write_entry(entries_dir, "good_entry", summary="valid entry")
        (entries_dir / "bad_entry.yaml").write_bytes(b"\xff\xfe\x00INVALID\x00")

        results = list(_iter_entry_files(entries_dir))
        filenames = [p.name for p, _ in results]
        assert "good_entry.yaml" in filenames
        assert "bad_entry.yaml" not in filenames

    def test_corrupt_yaml_skipped_sorted(self, trw_dir: Path) -> None:
        """Corrupt file skipped in sorted_order=True path — line 71."""
        entries_dir = trw_dir / "learnings" / "entries"
        _write_entry(entries_dir, "valid_entry", summary="ok")
        (entries_dir / "zzz_corrupt.yaml").write_bytes(b"\x00\x01INVALID\xff")

        results = list(_iter_entry_files(entries_dir, sorted_order=True))
        filenames = [p.name for p, _ in results]
        assert "valid_entry.yaml" in filenames
        assert "zzz_corrupt.yaml" not in filenames


class TestFindEntryByIdExceptionHandling:
    """Lines 144-145: exception handling in find_entry_by_id."""

    def test_corrupt_entry_skipped_returns_none(self, trw_dir: Path) -> None:
        """Corrupt YAML during ID scan is skipped — lines 144-145."""
        entries_dir = trw_dir / "learnings" / "entries"
        (entries_dir / "corrupt.yaml").write_bytes(b"\xff\xfe INVALID \x00")

        result = find_entry_by_id(entries_dir, "L-nonexistent")
        assert result is None

    def test_corrupt_entry_skipped_valid_found(self, trw_dir: Path) -> None:
        """Corrupt file skipped; subsequent valid file with matching ID found."""
        entries_dir = trw_dir / "learnings" / "entries"
        (entries_dir / "aaa_corrupt.yaml").write_bytes(b"\xff\xfe INVALID \x00")
        _write_entry(entries_dir, "zzz_valid", summary="target", learning_id="L-target")

        result = find_entry_by_id(entries_dir, "L-target")
        assert result is not None
        path, data = result
        assert data["id"] == "L-target"
        assert path.suffix == ".yaml"
        assert "summary" in data


class TestHasExistingMechanicalLearning:
    """Lines 376, 381: has_existing_mechanical_learning."""

    def test_nonexistent_entries_dir_returns_false(self, tmp_path: Path) -> None:
        """Returns False when entries_dir doesn't exist — line 376."""
        fake_trw = tmp_path / ".trw_nonexistent"
        result = has_existing_mechanical_learning(fake_trw, "Repeated operation: build")
        assert result is False

    def test_no_match_returns_false(self, trw_dir: Path) -> None:
        """Returns False when no matching prefix exists — final return False (line 385)."""
        entries_dir = trw_dir / "learnings" / "entries"
        _write_entry(entries_dir, "other", summary="Error pattern: timeout in api")
        result = has_existing_mechanical_learning(trw_dir, "Repeated operation: build")
        assert result is False

    def test_finds_matching_active_entry(self, trw_dir: Path) -> None:
        """Returns True when active entry with matching prefix found — line 381."""
        entries_dir = trw_dir / "learnings" / "entries"
        _write_entry(
            entries_dir,
            "repeated_op",
            summary="repeated operation: file_modified (12x)",
            status="active",
        )
        result = has_existing_mechanical_learning(trw_dir, "Repeated operation: file_modified")
        assert result is True

    def test_non_active_entry_ignored(self, trw_dir: Path) -> None:
        """Non-active entries are ignored even if prefix matches — lines 379-380."""
        entries_dir = trw_dir / "learnings" / "entries"
        _write_entry(
            entries_dir,
            "resolved_op",
            summary="repeated operation: build_step (8x)",
            status="resolved",
        )
        result = has_existing_mechanical_learning(trw_dir, "Repeated operation: build_step")
        assert result is False


# ---------------------------------------------------------------------------
# PRD-FIX-130-FR03: one index read-modify-write per sweep, not one per record
# ---------------------------------------------------------------------------


def _entry(index: int, impact: float) -> object:
    from trw_mcp.models.learning import LearningEntry

    return LearningEntry(
        id=f"L-batch{index:03d}",
        summary=f"batched index probe entry number {index} with a sufficiently long summary",
        detail=f"detail body for batched index probe {index}",
        tags=["batch", "index"],
        impact=impact,
    )


def _index_entries(trw_dir: Path) -> list[dict[str, object]]:
    from trw_mcp.state.persistence import FileStateReader

    data = FileStateReader().read_yaml(trw_dir / "learnings" / "index.yaml")
    raw = data.get("entries", [])
    return [e for e in raw if isinstance(e, dict)] if isinstance(raw, list) else []


class TestBatchLearningIndex:
    """FR03: the whole-file index rewrite becomes a per-SWEEP cost.

    NON-VACUITY: the lock/write counters assert 1, and the pre-change per-record
    path produces 5. Route ``update_learning_index_batch`` back through N
    sequential ``update_learning_index`` calls and the first test fails on 5 != 1.
    """

    def test_batch_index_update_writes_once_and_matches_sequential_content(
        self, trw_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from trw_mcp.state.analytics import entries as entries_mod

        locks: list[int] = []
        writes: list[Path] = []
        real_lock = entries_mod.lock_for_rmw
        real_write = entries_mod.FileStateWriter.write_yaml

        def _counting_lock(path: Path):  # type: ignore[no-untyped-def]
            locks.append(1)
            return real_lock(path)

        def _counting_write(self, path: Path, data: object) -> None:  # type: ignore[no-untyped-def]
            writes.append(path)
            real_write(self, path, data)

        monkeypatch.setattr(entries_mod, "lock_for_rmw", _counting_lock)
        monkeypatch.setattr(entries_mod.FileStateWriter, "write_yaml", _counting_write)

        batch = [_entry(i, 0.5) for i in range(5)]
        entries_mod.update_learning_index_batch(trw_dir, batch)  # type: ignore[arg-type]

        assert locks == [1], f"expected exactly one lock acquisition, got {len(locks)}"
        index_writes = [p for p in writes if p.name == "index.yaml"]
        assert len(index_writes) == 1, index_writes
        batched = _index_entries(trw_dir)

        # Same five entries, applied one at a time, must produce the same content.
        sequential_dir = trw_dir.parent / "sequential" / ".trw"
        (sequential_dir / "learnings").mkdir(parents=True)
        for entry in batch:
            entries_mod.update_learning_index(sequential_dir, entry)  # type: ignore[arg-type]
        assert batched == _index_entries(sequential_dir)

    def test_batch_index_trim_retains_highest_impact(self, trw_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """The DOCUMENTED divergence: a batch that crosses the cap keeps the top-impact set."""
        from trw_mcp.models.config import TRWConfig
        from trw_mcp.state.analytics import entries as entries_mod

        monkeypatch.setattr(entries_mod, "get_config", lambda: TRWConfig(learning_max_entries=3))
        batch = [_entry(i, impact=i / 10.0) for i in range(6)]  # impacts 0.0 .. 0.5

        entries_mod.update_learning_index_batch(trw_dir, batch)  # type: ignore[arg-type]

        kept = {str(e["id"]) for e in _index_entries(trw_dir)}
        assert kept == {"L-batch003", "L-batch004", "L-batch005"}, kept

    def test_batch_index_write_failure_leaves_index_untouched(
        self, trw_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A failed batch write releases the lock and changes nothing on disk.

        The backend rows are already durable when the sweep reaches the index, so
        an index failure must be a missed projection, never a lost learning.
        """
        from trw_mcp.state.analytics import entries as entries_mod

        entries_mod.update_learning_index_batch(trw_dir, [_entry(0, 0.9)])  # type: ignore[arg-type]
        before = (trw_dir / "learnings" / "index.yaml").read_bytes()

        real_write = entries_mod.FileStateWriter.write_yaml

        def _boom(self, path: Path, data: object) -> None:  # type: ignore[no-untyped-def]
            if path.name == "index.yaml":
                raise OSError("simulated index write failure")
            real_write(self, path, data)

        monkeypatch.setattr(entries_mod.FileStateWriter, "write_yaml", _boom)
        with pytest.raises(OSError):
            entries_mod.update_learning_index_batch(trw_dir, [_entry(1, 0.9)])  # type: ignore[arg-type]

        assert (trw_dir / "learnings" / "index.yaml").read_bytes() == before
        # The lock was released, so the next writer is not blocked.
        monkeypatch.setattr(entries_mod.FileStateWriter, "write_yaml", real_write)
        entries_mod.update_learning_index_batch(trw_dir, [_entry(2, 0.9)])  # type: ignore[arg-type]
        assert {str(e["id"]) for e in _index_entries(trw_dir)} == {"L-batch000", "L-batch002"}

    def test_save_learning_entry_without_a_sink_is_unchanged(self, trw_dir: Path) -> None:
        """The interactive trw_learn path keeps its per-entry index update."""
        from trw_mcp.state.analytics import entries as entries_mod

        entries_mod.save_learning_entry(trw_dir, _entry(7, 0.6))  # type: ignore[arg-type]
        assert {str(e["id"]) for e in _index_entries(trw_dir)} == {"L-batch007"}

    def test_save_learning_entry_with_a_sink_defers_the_index_write(self, trw_dir: Path) -> None:
        """With a sink the YAML sidecar still lands; only the index write is deferred."""
        from trw_mcp.state.analytics import entries as entries_mod

        sink: list[object] = []
        path = entries_mod.save_learning_entry(trw_dir, _entry(8, 0.6), index_sink=sink)  # type: ignore[arg-type]
        assert path.is_file()
        assert not (trw_dir / "learnings" / "index.yaml").exists()
        assert len(sink) == 1

        entries_mod.update_learning_index_batch(trw_dir, sink)  # type: ignore[arg-type]
        assert {str(e["id"]) for e in _index_entries(trw_dir)} == {"L-batch008"}
