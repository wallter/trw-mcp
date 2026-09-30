"""The pre-restore raw copy is streamed a chunk at a time and never left half-written (E2E-BACKUP-RAW-COPY-STREAM)."""

from __future__ import annotations

from pathlib import Path

import pytest

from trw_mcp.server import _subcommands_backup as sb

pytestmark = pytest.mark.integration


def _archive_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    from trw_memory.storage import _backup_archive

    def boom(*_a: object, **_k: object) -> object:
        raise _backup_archive.BackupArchiveError("corrupt")

    monkeypatch.setattr(_backup_archive, "create_backup_archive", boom)


def _raw_dirs(base: Path) -> list[Path]:
    from trw_memory.storage._backup_archive import backups_base_dir

    root = backups_base_dir(base)
    return sorted(root.glob("pre-restore-raw-*")) if root.exists() else []


def test_multi_chunk_store_and_wal_copy_byte_identical(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sb, "_RAW_COPY_CHUNK", 1000)
    db = tmp_path / "memory.db"
    db.write_bytes(bytes(range(256)) * 20 + b"tail")
    (tmp_path / "memory.db-wal").write_bytes(b"w" * 2500)
    (tmp_path / "memory.db-shm").write_bytes(b"")
    _archive_fails(monkeypatch)

    sb._keep_current_store(tmp_path, db)

    (raw,) = _raw_dirs(tmp_path)
    assert (raw / "memory.db").read_bytes() == db.read_bytes()
    assert (raw / "memory.db-wal").read_bytes() == b"w" * 2500
    assert (raw / "memory.db-shm").read_bytes() == b""


def test_no_single_read_exceeds_the_chunk(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sb, "_RAW_COPY_CHUNK", 512)
    db = tmp_path / "memory.db"
    db.write_bytes(b"x" * 5000)
    sizes: list[int] = []
    real_open = Path.open

    def spy(self: Path, mode: str = "r", *a: object, **k: object) -> object:
        handle = real_open(self, mode, *a, **k)  # type: ignore[arg-type]
        if self == db and "b" in mode:
            real_read = handle.read
            handle.read = lambda n=-1: (sizes.append(n), real_read(n))[1]  # type: ignore[method-assign]
        return handle

    monkeypatch.setattr(Path, "open", spy)
    _archive_fails(monkeypatch)
    sb._keep_current_store(tmp_path, db)

    assert sizes and all(0 < n <= 512 for n in sizes)


def test_a_failure_mid_stream_leaves_no_partial_copy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import trw_memory.safe_fs as safe_fs

    monkeypatch.setattr(sb, "_RAW_COPY_CHUNK", 100)
    db = tmp_path / "memory.db"
    db.write_bytes(b"d" * 1000)
    real_append = safe_fs.append_beneath
    calls: list[int] = []

    def flaky(*a: object, **k: object) -> None:
        calls.append(1)
        if len(calls) == 3:
            raise OSError(28, "no space left")
        real_append(*a, **k)  # type: ignore[arg-type]

    monkeypatch.setattr(safe_fs, "append_beneath", flaky)
    _archive_fails(monkeypatch)

    with pytest.raises(SystemExit) as exit_info:
        sb._keep_current_store(tmp_path, db)

    assert exit_info.value.code == 1
    assert _raw_dirs(tmp_path) == []
    assert db.read_bytes() == b"d" * 1000


def test_a_symlinked_raw_directory_component_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from trw_memory.storage._backup_archive import backups_base_dir

    outside = tmp_path / "outside"
    outside.mkdir()
    base = tmp_path / "store"
    base.mkdir()
    db = base / "memory.db"
    db.write_bytes(b"data")
    backups = backups_base_dir(base)
    backups.parent.mkdir(parents=True, exist_ok=True)
    backups.symlink_to(outside, target_is_directory=True)
    _archive_fails(monkeypatch)

    with pytest.raises(SystemExit):
        sb._keep_current_store(base, db)

    assert list(outside.iterdir()) == []


def test_only_the_last_chunk_is_synced_and_a_single_chunk_needs_no_append(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import trw_memory.safe_fs as safe_fs

    monkeypatch.setattr(sb, "_RAW_COPY_CHUNK", 100)
    flags: list[bool] = []
    real_append = safe_fs.append_beneath

    def spy(*a: object, **k: object) -> None:
        flags.append(bool(k.get("sync")))
        real_append(*a, **k)  # type: ignore[arg-type]

    monkeypatch.setattr(safe_fs, "append_beneath", spy)
    (tmp_path / "many.db").write_bytes(b"m" * 350)
    (tmp_path / "one.db").write_bytes(b"o" * 50)

    sb._copy_streamed(tmp_path / "many.db", tmp_path, Path("out/many.db"))
    assert flags == [False, False, True]
    flags.clear()
    sb._copy_streamed(tmp_path / "one.db", tmp_path, Path("out/one.db"))

    assert flags == [] and (tmp_path / "out" / "one.db").read_bytes() == b"o" * 50


def test_cleanup_after_a_failed_archive_does_not_follow_a_swapped_backups_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from trw_memory.storage._backup_archive import backups_base_dir

    base = tmp_path / "store"
    base.mkdir()
    db = base / "memory.db"
    db.write_bytes(b"data")
    outside = tmp_path / "outside"
    _archive_fails(monkeypatch)
    real_copy = sb._copy_streamed

    def copy_then_swap(source: Path, b: Path, rel: Path) -> None:
        real_copy(source, b, rel)
        # After the copy, the backups directory is swapped for a link whose target holds a same-named tree.
        backups = backups_base_dir(b)
        (outside / rel.parent.name).mkdir(parents=True)
        (outside / rel.parent.name / "keep").write_text("x")
        backups.rename(tmp_path / "moved")
        backups.symlink_to(outside, target_is_directory=True)
        raise OSError(28, "no space left")

    monkeypatch.setattr(sb, "_copy_streamed", copy_then_swap)
    with pytest.raises(SystemExit):
        sb._keep_current_store(base, db)

    assert [p.name for p in outside.rglob("keep")] == ["keep"], "cleanup deleted through the swapped link"
