"""remove_if_hash: capture into .trw/trash, verify, keep or link back; never delete (safe-publish minimal design)."""

from __future__ import annotations

import errno
import hashlib
import json
import os
import stat
from collections.abc import Callable
from pathlib import Path

import pytest

from tests._fs_hazards import (
    assert_user_bytes_preserved,
    atomic_replace,
    edit_in_place,
    fail_nth_read,
    open_fd_writer,
    race_after,
    snapshot_user_bytes,
    swap_to_dir,
    swap_to_symlink,
    unreadable_parent,
)
from trw_mcp.bootstrap import _safe_remove, _trash
from trw_mcp.bootstrap._safe_remove import Removal, remove_if_hash, trash_dir

BODY = b"trw managed bytes\n"
GOOD = hashlib.sha256(BODY).hexdigest()
WRONG = hashlib.sha256(b"something else").hexdigest()


@pytest.fixture
def target(tmp_path: Path) -> Path:
    path = tmp_path / "sub" / "f.txt"
    path.parent.mkdir()
    path.write_bytes(BODY)
    return path


def _data_files(root: Path) -> list[Path]:
    return sorted(trash_dir(root).glob("*/data"))


def _snap_after(root: Path, holder: dict[str, dict[str, list[str]]], act: Callable[[], object]) -> Callable[[], None]:
    """Interloper that runs *act*, then records every byte that exists at that instant (racer's included)."""

    def run() -> None:
        act()
        holder["snap"] = snapshot_user_bytes(root)

    return run


def test_match_moves_bytes_into_trash(tmp_path: Path, target: Path) -> None:
    before = snapshot_user_bytes(tmp_path)
    result = remove_if_hash(target, tmp_path, GOOD, key="k1")
    assert isinstance(result, Removal)
    assert (result.status, result.key, result.path, result.published) == ("removed", "k1", target, None)
    assert not target.exists()
    assert result.retained_at is not None and result.retained_at.read_bytes() == BODY
    assert _data_files(tmp_path) == [result.retained_at]
    assert_user_bytes_preserved(before, tmp_path)


def test_mismatch_links_back_same_inode(tmp_path: Path, target: Path) -> None:
    before = snapshot_user_bytes(tmp_path)
    result = remove_if_hash(target, tmp_path, WRONG)
    assert result.status == "kept" and result.published == target
    assert result.retained_at is not None
    assert os.stat(target).st_ino == os.stat(result.retained_at).st_ino
    assert target.read_bytes() == BODY
    assert_user_bytes_preserved(before, tmp_path)


def test_absent_file_and_absent_parent(tmp_path: Path, target: Path) -> None:
    assert remove_if_hash(target.parent / "nope", tmp_path, GOOD).status == "absent"
    assert remove_if_hash(tmp_path / "no" / "such" / "f", tmp_path, GOOD).status == "absent"


@pytest.mark.parametrize("bad", ["outside", "root", "dotdot"])
def test_refuses_paths_outside_root(tmp_path: Path, target: Path, bad: str) -> None:
    other = tmp_path.parent / f"{tmp_path.name}-other.txt"
    other.write_bytes(BODY)
    arg = {"outside": other, "root": tmp_path, "dotdot": tmp_path / "sub" / ".." / "sub" / "f.txt"}[bad]
    assert remove_if_hash(arg, tmp_path, GOOD).status == "kept"
    assert other.read_bytes() == BODY and target.read_bytes() == BODY


def test_capture_folder_layout_meta_and_mode(tmp_path: Path, target: Path) -> None:
    result = remove_if_hash(target, tmp_path, GOOD, key="the-key")
    assert result.retained_at is not None
    folder = result.retained_at.parent
    assert stat.S_IMODE(folder.stat().st_mode) == 0o700
    assert stat.S_IMODE(trash_dir(tmp_path).stat().st_mode) == 0o700
    stamp, _, token = folder.name.partition("-")
    assert len(stamp) == 16 and stamp.endswith("Z") and len(token) == 32 and int(token, 16) >= 0
    meta = json.loads((folder / "meta.json").read_text())
    assert meta["v"] == 1 and meta["path"] == "sub/f.txt" and meta["key"] == "the-key"
    assert meta["captured_at"].endswith("Z")


def test_two_captures_get_distinct_folders(tmp_path: Path, target: Path) -> None:
    remove_if_hash(target, tmp_path, GOOD)
    target.write_bytes(BODY)
    remove_if_hash(target, tmp_path, GOOD)
    assert len(_data_files(tmp_path)) == 2


@pytest.mark.parametrize("kind", ["dir", "symlink", "fifo"])
def test_non_regular_is_kept_untouched(tmp_path: Path, target: Path, kind: str) -> None:
    victim = target.parent / "v"
    if kind == "dir":
        victim.mkdir()
        (victim / "inner").write_bytes(BODY)
    elif kind == "symlink":
        os.symlink(target, victim)
    else:
        os.mkfifo(victim)
    # No whole-tree snapshot here: the kit's snapshot would block reading a FIFO.
    result = remove_if_hash(victim, tmp_path, GOOD)
    assert result.status == "kept" and os.path.lexists(victim)
    assert not _data_files(tmp_path)
    assert target.read_bytes() == BODY


def test_symlinked_parent_component_is_refused(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    (real / "f.txt").write_bytes(BODY)
    os.symlink(real, tmp_path / "link")
    before = snapshot_user_bytes(tmp_path)
    result = remove_if_hash(tmp_path / "link" / "f.txt", tmp_path, GOOD)
    assert result.status == "kept" and (real / "f.txt").read_bytes() == BODY
    assert not trash_dir(tmp_path).exists()
    assert_user_bytes_preserved(before, tmp_path)


def test_symlinked_trash_is_refused(tmp_path: Path, target: Path) -> None:
    elsewhere = tmp_path.parent / f"{tmp_path.name}-elsewhere"
    elsewhere.mkdir()
    (tmp_path / ".trw").mkdir()
    os.symlink(elsewhere, tmp_path / ".trw" / "trash")
    before = snapshot_user_bytes(tmp_path)
    result = remove_if_hash(target, tmp_path, GOOD)
    assert result.status == "kept" and target.read_bytes() == BODY
    assert list(elsewhere.iterdir()) == []
    assert_user_bytes_preserved(before, tmp_path)


def test_over_cap_never_matches(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, target: Path) -> None:
    monkeypatch.setattr(_trash, "_HASH_CAP", len(BODY) - 1)
    before = snapshot_user_bytes(tmp_path)
    result = remove_if_hash(target, tmp_path, GOOD)
    assert result.status == "kept" and target.read_bytes() == BODY
    assert_user_bytes_preserved(before, tmp_path)


# ---- races at each boundary ----------------------------------------------------------------------


@pytest.mark.parametrize("swap", ["atomic", "inplace"])
@pytest.mark.parametrize("when", ["before", "after"])
def test_edit_around_stat(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, target: Path, swap: str, when: str) -> None:
    """A user edit landing just before or just after our stat is never deleted: the new bytes stay named."""
    holder: dict[str, dict[str, list[str]]] = {}
    edit = atomic_replace if swap == "atomic" else edit_in_place
    probe = race_after(
        monkeypatch,
        target="f.txt",
        op="stat",
        when=when,
        interloper=_snap_after(tmp_path, holder, lambda: edit(target, b"user edit")),
    )
    result = remove_if_hash(target, tmp_path, GOOD)
    assert probe.fired
    assert result.status == "kept" and target.read_bytes() == b"user edit"
    assert_user_bytes_preserved(holder["snap"], tmp_path)


@pytest.mark.parametrize("swap", ["symlink", "dir"])
@pytest.mark.parametrize("when", ["before", "after"])
def test_kind_swap_around_stat(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, target: Path, swap: str, when: str
) -> None:
    holder: dict[str, dict[str, list[str]]] = {}
    act = (lambda: swap_to_symlink(target, "f.txt.swapped")) if swap == "symlink" else (lambda: swap_to_dir(target))
    probe = race_after(monkeypatch, target="f.txt", op="stat", when=when, interloper=_snap_after(tmp_path, holder, act))
    result = remove_if_hash(target, tmp_path, GOOD)
    assert probe.fired
    assert result.status in ("kept", "retained")
    assert (target.parent / "f.txt.swapped").read_bytes() == BODY
    assert_user_bytes_preserved(holder["snap"], tmp_path)


@pytest.mark.parametrize("swap", ["atomic", "symlink", "dir"])
def test_swap_between_stat_and_capture(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, target: Path, swap: str
) -> None:
    holder: dict[str, dict[str, list[str]]] = {}
    acts = {
        "atomic": lambda: atomic_replace(target, b"racer"),
        "symlink": lambda: swap_to_symlink(target, "f.txt.swapped"),
        "dir": lambda: swap_to_dir(target),
    }
    probe = race_after(
        monkeypatch, target="f.txt", op="rename", when="before", interloper=_snap_after(tmp_path, holder, acts[swap])
    )
    result = remove_if_hash(target, tmp_path, GOOD)
    assert probe.fired
    assert result.status in ("kept", "retained")
    if swap == "atomic":
        assert result.status == "kept" and target.read_bytes() == b"racer"
        assert result.retained_at is not None and result.retained_at.read_bytes() == b"racer"
    if swap == "dir":
        assert result.status == "retained" and result.retained_at is not None and result.retained_at.is_dir()
        assert result.reason == f"a directory replaced the file during removal; it is kept at {result.retained_at}"
        assert target.parent.joinpath("f.txt.swapped").read_bytes() == BODY
    if swap == "symlink":
        assert result.status == "kept" and os.path.islink(target)
        assert result.retained_at is not None and os.path.islink(result.retained_at)
    assert_user_bytes_preserved(holder["snap"], tmp_path)


def test_file_vanishes_before_capture_is_absent_and_no_empty_folder_stays(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, target: Path
) -> None:
    """FB-01-KI1-RACE r6: a capture folder that received nothing (only its meta file) is removed, so a
    failed capture never uses up a full disk's last space."""
    probe = race_after(monkeypatch, target="f.txt", op="rename", when="before", interloper=lambda: os.unlink(target))
    result = remove_if_hash(target, tmp_path, GOOD)
    assert probe.fired and result.status == "absent"
    assert list(trash_dir(tmp_path).iterdir()) == []


@pytest.mark.parametrize("swap", ["inplace", "atomic"])
def test_change_in_trash_during_hash_is_a_mismatch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, target: Path, swap: str
) -> None:
    holder: dict[str, dict[str, list[str]]] = {}
    edit = edit_in_place if swap == "inplace" else atomic_replace
    probe = race_after(
        monkeypatch,
        target="data",
        op="open",
        interloper=_snap_after(tmp_path, holder, lambda: edit(_data_files(tmp_path)[0], b"changed while hashing")),
    )
    result = remove_if_hash(target, tmp_path, GOOD)
    assert probe.fired and result.status == "kept"
    assert target.read_bytes() == b"changed while hashing"
    assert_user_bytes_preserved(holder["snap"], tmp_path)


def test_write_between_two_fstats_is_a_mismatch(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, target: Path) -> None:
    real_read = os.read
    fired = {"n": 0}

    def read_then_write(fd: int, n: int) -> bytes:
        out = real_read(fd, n)
        if out and not fired["n"]:
            fired["n"] = 1
            edit_in_place(_data_files(tmp_path)[0], BODY)  # same bytes, new mtime/ctime
        return out

    monkeypatch.setattr(os, "read", read_then_write)
    before = snapshot_user_bytes(tmp_path)
    result = remove_if_hash(target, tmp_path, GOOD)
    assert fired["n"] == 1 and result.status == "kept" and target.read_bytes() == BODY
    assert_user_bytes_preserved(before, tmp_path)


def test_path_recreated_before_link_back_retains_both(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, target: Path
) -> None:
    holder: dict[str, dict[str, list[str]]] = {}
    probe = race_after(
        monkeypatch,
        target="f.txt",
        op="link",
        when="before",
        interloper=_snap_after(tmp_path, holder, lambda: atomic_replace(target, b"new file")),
    )
    result = remove_if_hash(target, tmp_path, WRONG)
    assert probe.fired and result.status == "retained"
    assert target.read_bytes() == b"new file"
    assert result.retained_at is not None and result.retained_at.read_bytes() == BODY
    assert_user_bytes_preserved(holder["snap"], tmp_path)


def test_open_fd_writer_bytes_written_after_match_land_in_trash(tmp_path: Path, target: Path) -> None:
    with open_fd_writer(target) as writer:
        result = remove_if_hash(target, tmp_path, GOOD)
        assert result.status == "removed"
        writer.write(b"late write through an open fd")
    assert result.retained_at is not None
    assert result.retained_at.read_bytes() == b"late write through an open fd"
    assert_user_bytes_preserved(snapshot_user_bytes(tmp_path), tmp_path)


# ---- faults --------------------------------------------------------------------------------------


def test_link_unavailable_retains_in_trash(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, target: Path) -> None:
    def no_link(*_a: object, **_k: object) -> None:
        raise PermissionError(errno.EPERM, "link not permitted")

    monkeypatch.setattr(os, "link", no_link)
    before = snapshot_user_bytes(tmp_path)
    result = remove_if_hash(target, tmp_path, WRONG)
    assert result.status == "retained" and result.published is None
    assert result.retained_at is not None and result.retained_at.read_bytes() == BODY
    assert not target.exists()
    assert_user_bytes_preserved(before, tmp_path)


def test_exdev_on_capture_moves_nothing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, target: Path) -> None:
    def cross_device(*_a: object, **_k: object) -> None:
        raise OSError(errno.EXDEV, "Invalid cross-device link")

    monkeypatch.setattr(os, "rename", cross_device)
    before = snapshot_user_bytes(tmp_path)
    result = remove_if_hash(target, tmp_path, GOOD)
    assert result.status == "kept" and "another device" in result.reason
    assert target.read_bytes() == BODY and not _data_files(tmp_path)
    assert_user_bytes_preserved(before, tmp_path)


def test_other_rename_error_moves_nothing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, target: Path) -> None:
    def denied(*_a: object, **_k: object) -> None:
        raise PermissionError(errno.EACCES, "denied")

    monkeypatch.setattr(os, "rename", denied)
    result = remove_if_hash(target, tmp_path, GOOD)
    assert result.status == "kept" and target.read_bytes() == BODY


def test_unreadable_parent_keeps(tmp_path: Path, target: Path) -> None:
    before = snapshot_user_bytes(tmp_path)
    with unreadable_parent(target):
        result = remove_if_hash(target, tmp_path, GOOD)
    assert result.status == "kept"
    assert target.read_bytes() == BODY
    assert_user_bytes_preserved(before, tmp_path)


def test_read_failure_of_captured_data_links_back(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, target: Path
) -> None:
    counter = fail_nth_read(monkeypatch, "data", nth=1)
    before = snapshot_user_bytes(tmp_path)
    result = remove_if_hash(target, tmp_path, GOOD)
    assert counter.raised == 1
    assert result.status == "kept" and target.read_bytes() == BODY
    assert_user_bytes_preserved(before, tmp_path)


def test_eio_while_hashing_links_back(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, target: Path) -> None:
    def boom(*_a: object, **_k: object) -> bytes:
        raise OSError(errno.EIO, "io error")

    monkeypatch.setattr(os, "read", boom)
    before = snapshot_user_bytes(tmp_path)
    result = remove_if_hash(target, tmp_path, GOOD)
    assert result.status == "kept" and target.read_bytes() == BODY
    assert_user_bytes_preserved(before, tmp_path)


def test_trash_creation_failure_keeps_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, target: Path) -> None:
    (tmp_path / ".trw").write_bytes(b"a file where the dir should be")
    before = snapshot_user_bytes(tmp_path)
    result = remove_if_hash(target, tmp_path, GOOD)
    assert result.status == "kept" and target.read_bytes() == BODY
    assert_user_bytes_preserved(before, tmp_path)


# ---- fix round: validation, lazy trash, interrupts, reporting ------------------------------------


@pytest.mark.parametrize("bad", ["", "abc", GOOD.upper(), GOOD[:-1], GOOD + "0", "g" * 64, None, 5])
def test_invalid_expected_hash_raises_before_any_mutation(tmp_path: Path, target: Path, bad: object) -> None:
    with pytest.raises(ValueError):
        remove_if_hash(target, tmp_path, bad)  # type: ignore[arg-type]
    assert target.read_bytes() == BODY and not (tmp_path / ".trw").exists()


@pytest.mark.parametrize("exc", [RuntimeError, KeyboardInterrupt])
def test_exception_after_capture_restores_the_name_and_propagates(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, target: Path, exc: type[BaseException]
) -> None:
    def boom(*_a: object, **_k: object) -> bool:
        raise exc("boom")

    monkeypatch.setattr(_trash, "_sha256_stable", boom)
    before = snapshot_user_bytes(tmp_path)
    with pytest.raises(exc):
        remove_if_hash(target, tmp_path, GOOD)
    assert target.read_bytes() == BODY
    assert os.stat(target).st_ino == os.stat(_data_files(tmp_path)[0]).st_ino
    assert_user_bytes_preserved(before, tmp_path)


@pytest.mark.parametrize("case", ["absent", "no_parent", "dir", "symlink", "outside"])
def test_no_trash_is_created_unless_a_regular_file_is_captured(tmp_path: Path, target: Path, case: str) -> None:
    victim = {
        "absent": target.parent / "nope",
        "no_parent": tmp_path / "x" / "y",
        "dir": target.parent,
        "symlink": target.parent / "ln",
        "outside": tmp_path.parent / "elsewhere",
    }[case]
    if case == "symlink":
        os.symlink(target, victim)
    remove_if_hash(victim, tmp_path, GOOD)
    assert not (tmp_path / ".trw").exists()


def test_capture_location_unknown_when_data_vanishes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, target: Path
) -> None:
    probe = race_after(
        monkeypatch, target="data", op="open", when="before", interloper=lambda: os.unlink(_data_files(tmp_path)[0])
    )
    result = remove_if_hash(target, tmp_path, GOOD)  # the test itself deleted the only copy; no bytes to preserve
    assert probe.fired and result.status == "retained"
    assert result.retained_at is None and "capture location unknown" in result.reason


def test_published_is_dropped_when_the_folder_moves_during_removal(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, target: Path
) -> None:
    moved = tmp_path / "moved"
    probe = race_after(
        monkeypatch, target="f.txt", op="link", when="after", interloper=lambda: os.rename(target.parent, moved)
    )
    result = remove_if_hash(target, tmp_path, WRONG)
    assert probe.fired and result.status == "kept" and result.published is None
    assert "the folder moved during removal" in result.reason
    assert (moved / "f.txt").read_bytes() == BODY
    assert_user_bytes_preserved(snapshot_user_bytes(tmp_path), tmp_path)


def test_published_is_reported_when_the_folder_is_stable(tmp_path: Path, target: Path) -> None:
    assert remove_if_hash(target, tmp_path, WRONG).published == target


def test_unsupported_platform_flag_keeps_without_touching(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, target: Path
) -> None:
    monkeypatch.setattr(_trash, "_UNSUPPORTED", "platform lacks something")  # read where the helper lives
    result = remove_if_hash(target, tmp_path, GOOD)
    assert (result.status, result.reason) == ("kept", "platform lacks something")
    assert target.read_bytes() == BODY and not (tmp_path / ".trw").exists()


@pytest.mark.parametrize("attr", ["supports_dir_fd", "supports_follow_symlinks"])
def test_capability_check_reports_missing_support(monkeypatch: pytest.MonkeyPatch, attr: str) -> None:
    assert _safe_remove._capable() is None
    monkeypatch.setattr(os, attr, set())
    assert "platform lacks" in (_safe_remove._capable() or "")


def test_capability_check_requires_nofollow_stat(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(os, "supports_follow_symlinks", {os.link})
    assert _safe_remove._capable() == "platform lacks no-follow support for stat"


def test_meta_write_failure_keeps_and_moves_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, target: Path
) -> None:
    real_open = os.open

    def open_meta_fails(file: str, *args: object, **kwargs: object) -> int:
        if file == "meta.json":
            raise FileExistsError(errno.EEXIST, "meta exists")
        return real_open(file, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(os, "open", open_meta_fails)
    result = remove_if_hash(target, tmp_path, GOOD)
    assert result.status == "kept" and target.read_bytes() == BODY and not _data_files(tmp_path)


def test_mkdir_collision_retries_with_a_new_folder(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, target: Path
) -> None:
    real_mkdir = os.mkdir
    calls: list[str] = []

    def mkdir_collides_once(file: str, *args: object, **kwargs: object) -> None:
        if "-" in file:
            calls.append(file)
            if len(calls) == 1:
                raise FileExistsError(errno.EEXIST, "exists")
        real_mkdir(file, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(os, "mkdir", mkdir_collides_once)
    result = remove_if_hash(target, tmp_path, GOOD)
    assert result.status == "removed" and len(calls) == 2 and calls[0] != calls[1]
    assert result.retained_at is not None and result.retained_at.parent.name == calls[1]


def _open_fds() -> int:
    return len(os.listdir("/dev/fd"))


@pytest.mark.parametrize("outcome", ["removed", "mismatch", "absent", "not_regular", "unsupported_parent", "exdev"])
def test_no_fd_leaks(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, target: Path, outcome: str) -> None:
    victim, digest = target, GOOD
    if outcome == "mismatch":
        digest = WRONG
    elif outcome == "absent":
        victim = target.parent / "nope"
    elif outcome == "not_regular":
        victim = target.parent
    elif outcome == "unsupported_parent":
        victim = tmp_path / "missing" / "f"
    elif outcome == "exdev":

        def cross(*_a: object, **_k: object) -> None:
            raise OSError(errno.EXDEV, "xdev")

        monkeypatch.setattr(os, "rename", cross)
    baseline = _open_fds()
    remove_if_hash(victim, tmp_path, digest)
    assert _open_fds() == baseline
