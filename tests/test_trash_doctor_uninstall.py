"""``.trw/trash`` in ``trw-mcp doctor`` (read-only row) and in uninstall (never removed recursively)."""

from __future__ import annotations

import argparse
import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from tests._fs_hazards import assert_user_bytes_preserved, snapshot_user_bytes, unreadable
from trw_mcp.models.config import TRWConfig
from trw_mcp.server import _doctor_trash, _uninstall_corpus
from trw_mcp.server._subcommands import _run_uninstall
from trw_mcp.server._subcommands_doctor import _CHECKS, _check_trw_trash


def _capture(root: Path, name: str, payload: bytes) -> Path:
    folder = root / ".trw" / "trash" / name
    folder.mkdir(parents=True)
    (folder / "data").write_bytes(payload)
    (folder / "meta.json").write_text("{}", encoding="utf-8")
    return folder


def _row(root: Path) -> tuple[str, str]:
    res = _check_trw_trash(root, TRWConfig())
    return res.status, res.message


# ---- doctor row --------------------------------------------------------------------------------


def test_row_is_registered() -> None:
    assert ("trw_trash", "_check_trw_trash") in _CHECKS


def test_absent_passes(tmp_path: Path) -> None:
    status, msg = _row(tmp_path)
    assert status == "PASS"
    assert "absent" in msg
    assert not (tmp_path / ".trw").exists()  # read-only: nothing created


def test_empty_passes(tmp_path: Path) -> None:
    (tmp_path / ".trw" / "trash").mkdir(parents=True)
    status, msg = _row(tmp_path)
    assert (status, "empty" in msg) == ("PASS", True)


def test_small_reports_count_and_size(tmp_path: Path) -> None:
    _capture(tmp_path, "a", b"x" * 2048)
    _capture(tmp_path, "b", b"y" * 10)
    status, msg = _row(tmp_path)
    assert status == "PASS"
    assert "2 backup(s)" in msg
    assert str(tmp_path / ".trw" / "trash") in msg
    assert "KB" in msg


def test_over_threshold_warns_with_exact_wording(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_doctor_trash, "WARN_BYTES", 1000)
    folder = _capture(tmp_path, "a", b"")
    with (folder / "data").open("wb") as fh:
        fh.truncate(5 * 1024 * 1024)  # sparse
    status, msg = _row(tmp_path)
    assert status == "WARN"
    assert msg == "`.trw/trash` holds 5.0 MB in 1 backups; delete it when you no longer need them"


def test_exactly_50mb_default_threshold(tmp_path: Path) -> None:
    folder = _capture(tmp_path, "a", b"")
    with (folder / "data").open("wb") as fh:
        fh.truncate(50 * 1024 * 1024 + 1)
    assert _row(tmp_path)[0] == "WARN"
    with (folder / "data").open("wb") as fh:
        fh.truncate(50 * 1024 * 1024 - 10)  # + 2-byte meta.json
    assert _row(tmp_path)[0] == "PASS"


def test_scan_is_bounded_and_says_at_least(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_doctor_trash, "MAX_SCANNED", 3)
    for i in range(6):
        _capture(tmp_path, f"c{i}", b"z")
    status, msg = _row(tmp_path)
    assert status == "PASS"
    assert "≥" in msg


def test_unreadable_trash_warns_not_crashes(tmp_path: Path) -> None:
    _capture(tmp_path, "a", b"x")
    with unreadable(tmp_path / ".trw" / "trash"):
        status, msg = _row(tmp_path)
    assert status == "WARN"
    assert "cannot read" in msg


def test_symlinked_trash_not_followed(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "big").write_bytes(b"q" * 4096)
    (tmp_path / ".trw").mkdir()
    (tmp_path / ".trw" / "trash").symlink_to(outside, target_is_directory=True)
    status, msg = _row(tmp_path)
    assert status == "WARN"
    assert "symlink" in msg
    assert (outside / "big").read_bytes() == b"q" * 4096


def test_symlink_inside_trash_not_followed_or_counted(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "big").write_bytes(b"q" * 4096)
    folder = _capture(tmp_path, "a", b"x")
    (folder / "link").symlink_to(outside / "big")
    (folder / "dirlink").symlink_to(outside, target_is_directory=True)
    status, msg = _row(tmp_path)
    assert status == "PASS"
    assert "1 backup(s)" in msg
    assert "KB" not in msg  # 1 + 2 bytes only: the 4096-byte targets were not counted


# ---- uninstall ---------------------------------------------------------------------------------


def _ns(root: Path, **kw: object) -> argparse.Namespace:
    base: dict[str, object] = {
        "target_dir": str(root),
        "dry_run": False,
        "yes": True,
        "delete_memory": False,
        "keep_memory": False,
    }
    base.update(kw)
    return argparse.Namespace(**base)


def _project(root: Path, *, corpus: bool) -> None:
    trw = root / ".trw"
    trw.mkdir()
    (trw / "config.yaml").write_text("x: 1\n", encoding="utf-8")
    (trw / "sessions").mkdir()
    (trw / "sessions" / "s.json").write_text("{}", encoding="utf-8")
    if corpus:
        (trw / "memory").mkdir()
        (trw / "memory" / "memory.db").write_bytes(b"db")


@pytest.mark.usefixtures("no_memory_daemon")
@pytest.mark.parametrize("keep_memory", [False, True])
def test_nonempty_trash_survives_whole_and_is_reported(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], keep_memory: bool
) -> None:
    _project(tmp_path, corpus=keep_memory)
    _capture(tmp_path, "20260101T000000Z-" + "a" * 32, b"user bytes one")
    _capture(tmp_path, "20260102T000000Z-" + "b" * 32, b"user bytes two")
    before = snapshot_user_bytes(tmp_path / ".trw" / "trash")
    _run_uninstall(_ns(tmp_path, keep_memory=keep_memory))
    out = capsys.readouterr().out
    assert_user_bytes_preserved(before, tmp_path)
    trash = tmp_path / ".trw" / "trash"
    assert (trash / ("20260101T000000Z-" + "a" * 32) / "data").read_bytes() == b"user bytes one"
    assert (trash / ("20260102T000000Z-" + "b" * 32) / "meta.json").is_file()
    assert "Kept .trw/trash: it holds 2 backup(s) TRW could not remove automatically (see `trw-mcp doctor`)" in out
    assert not (tmp_path / ".trw" / "config.yaml").exists()
    assert not (tmp_path / ".trw" / "sessions").exists()


@pytest.mark.usefixtures("no_memory_daemon")
def test_empty_trash_and_trw_are_rmdired(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _project(tmp_path, corpus=False)
    (tmp_path / ".trw" / "trash").mkdir()
    _run_uninstall(_ns(tmp_path))
    assert not (tmp_path / ".trw").exists()
    assert "Kept .trw/trash" not in capsys.readouterr().out


@pytest.mark.usefixtures("no_memory_daemon")
def test_empty_trash_in_keep_memory_mode_is_rmdired_and_corpus_kept(tmp_path: Path) -> None:
    _project(tmp_path, corpus=True)
    (tmp_path / ".trw" / "trash").mkdir()
    _run_uninstall(_ns(tmp_path, keep_memory=True))
    assert not (tmp_path / ".trw" / "trash").exists()
    assert (tmp_path / ".trw" / "memory" / "memory.db").read_bytes() == b"db"


@pytest.mark.usefixtures("no_memory_daemon")
@pytest.mark.parametrize("keep_memory", [False, True])
def test_capture_created_mid_uninstall_survives(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], keep_memory: bool
) -> None:
    _project(tmp_path, corpus=keep_memory)
    (tmp_path / ".trw" / "trash").mkdir()  # empty at the exclusion scan
    fired: list[bool] = []

    def late_capture() -> None:
        if not fired:  # a concurrent capture lands after the first child is removed
            fired.append(True)
            _capture(tmp_path, "20260103T000000Z-" + "c" * 32, b"late capture")

    if keep_memory:  # --keep-memory removes children path-by-path via safe_remove
        real_remove = _uninstall_corpus.safe_remove

        def racing_remove(path: Path, root: Path, **kw: object) -> str | None:
            result = real_remove(path, root, **kw)  # type: ignore[arg-type]
            late_capture()
            return result

        monkeypatch.setattr(_uninstall_corpus, "safe_remove", racing_remove)
    else:  # whole-.trw removal goes through the dir-fd-anchored walk
        real_walk = _uninstall_corpus._remove_children

        def racing_walk(trw_dir: Path, keep: object) -> Iterator[tuple[str, str | None]]:
            for item in real_walk(trw_dir, keep):  # type: ignore[arg-type]
                yield item
                late_capture()

        monkeypatch.setattr(_uninstall_corpus, "_remove_children", racing_walk)
    _run_uninstall(_ns(tmp_path, keep_memory=keep_memory))
    assert fired == [True]
    data = tmp_path / ".trw" / "trash" / ("20260103T000000Z-" + "c" * 32) / "data"
    assert data.read_bytes() == b"late capture"
    assert "Kept .trw/trash: it holds 1 backup(s)" in capsys.readouterr().out


@pytest.mark.usefixtures("no_memory_daemon")
def test_symlinked_trash_is_never_followed_or_removed(tmp_path: Path) -> None:
    outside = tmp_path.parent / (tmp_path.name + "-outside")
    outside.mkdir()
    (outside / "keep").write_bytes(b"outside bytes")
    _project(tmp_path, corpus=False)
    (tmp_path / ".trw" / "trash").symlink_to(outside, target_is_directory=True)
    _run_uninstall(_ns(tmp_path))
    assert (outside / "keep").read_bytes() == b"outside bytes"
    assert os.path.islink(tmp_path / ".trw" / "trash")


def test_remove_trw_dir_case_variant_trash_is_kept(tmp_path, capsys):
    from trw_mcp.server._uninstall_corpus import remove_trw_dir

    trw = tmp_path / ".trw"
    (trw / "Trash" / "cap").mkdir(parents=True)
    (trw / "Trash" / "cap" / "data").write_bytes(b"user")
    (trw / "sessions").mkdir()
    remove_trw_dir(trw, tmp_path, lambda p, t: str(p))
    assert (trw / "Trash" / "cap" / "data").read_bytes() == b"user"
    assert not (trw / "sessions").exists()


def test_remove_trw_dir_trash_regular_file_kept_with_message(tmp_path, capsys):
    from trw_mcp.server._uninstall_corpus import remove_trw_dir

    trw = tmp_path / ".trw"
    trw.mkdir()
    (trw / "trash").write_bytes(b"not a dir")
    removed, errors = remove_trw_dir(trw, tmp_path, lambda p, t: str(p))
    assert (removed, errors) == (0, 0)
    assert (trw / "trash").read_bytes() == b"not a dir"
    assert "not a directory" in capsys.readouterr().out


def test_remove_trw_dir_symlinked_trw_refused_target_untouched(tmp_path, capsys):
    from trw_mcp.server._uninstall_corpus import remove_trw_dir

    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_bytes(b"user")
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / ".trw").symlink_to(outside, target_is_directory=True)
    assert remove_trw_dir(proj / ".trw", proj, lambda p, t: str(p)) == (0, 1)
    assert (outside / "keep.txt").read_bytes() == b"user"
    assert (proj / ".trw").is_symlink()


def test_remove_trw_dir_swap_to_symlink_mid_walk_stays_in_project(tmp_path, monkeypatch):
    import os

    from trw_mcp.server import _uninstall_corpus as uc

    outside = tmp_path / "outside"
    (outside / "b").mkdir(parents=True)
    (outside / "b" / "keep.txt").write_bytes(b"user")
    proj = tmp_path / "proj"
    trw = proj / ".trw"
    (trw / "a").mkdir(parents=True)
    (trw / "b").mkdir()
    real_rmtree = uc.shutil.rmtree
    swapped = []

    def rmtree_then_swap(name, *a, **kw):
        real_rmtree(name, *a, **kw)
        if not swapped:  # after the first child goes, a racer replaces .trw with a symlink
            swapped.append(1)
            os.rename(trw, proj / ".trw-moved")
            trw.symlink_to(outside, target_is_directory=True)

    monkeypatch.setattr(uc.shutil, "rmtree", rmtree_then_swap)
    uc.remove_trw_dir(trw, proj, lambda p, t: str(p))
    assert (outside / "b" / "keep.txt").read_bytes() == b"user"
    assert not (proj / ".trw-moved" / "b").exists()  # the fd stayed on the original directory
