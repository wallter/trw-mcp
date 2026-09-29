"""Self-tests: each filesystem-hazard helper in ``tests/_fs_hazards.py`` really does its thing."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from tests._fs_hazards import (
    assert_user_bytes_preserved,
    atomic_replace,
    edit_in_place,
    fail_nth_read,
    name_collision_corpus,
    open_fd_writer,
    race_after,
    snapshot_user_bytes,
    swap_to_dir,
    swap_to_symlink,
    unreadable,
    unreadable_parent,
)

pytestmark = pytest.mark.unit


@pytest.fixture
def f(tmp_path: Path) -> Path:
    p = tmp_path / "f.txt"
    p.write_bytes(b"orig")
    return p


@pytest.mark.parametrize("op", ["stat", "lstat", "open", "read_bytes", "read_text", "exists", "is_file"])
def test_probe_fires_on_exactly_the_nth_call(monkeypatch: pytest.MonkeyPatch, f: Path, op: str) -> None:
    calls: list[int] = []
    probe = race_after(monkeypatch, target=f, op=op, interloper=lambda: calls.append(1), nth=2)
    other = f.with_name("other")
    other.write_bytes(b"o")

    def do(p: Path) -> None:
        {
            "stat": lambda: os.stat(p),
            "lstat": lambda: os.lstat(p),
            "open": lambda: os.close(os.open(p, os.O_RDONLY)),
            "read_bytes": p.read_bytes,
            "read_text": p.read_text,
            "exists": p.exists,
            "is_file": p.is_file,
        }[op]()

    do(other)  # different path: never counts
    do(f)
    assert not probe.fired and calls == []
    do(f)
    assert probe.fired and calls == [1]
    do(f)
    assert calls == [1], "interloper runs once"


@pytest.mark.parametrize("op", ["unlink", "replace", "rename", "link", "mkdir"])
def test_probe_mutating_ops(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, op: str) -> None:
    src = tmp_path / "src"
    src.write_bytes(b"s")
    dst = tmp_path / "dst"
    tgt = tmp_path / "d" if op == "mkdir" else (src if op in ("unlink",) else dst)
    probe = race_after(monkeypatch, target=tgt, op=op, interloper=lambda: None)
    {
        "unlink": lambda: os.unlink(src),
        "replace": lambda: os.replace(src, dst),
        "rename": lambda: os.rename(src, dst),
        "link": lambda: os.link(src, dst),
        "mkdir": lambda: os.mkdir(tgt),
    }[op]()
    assert probe.fired


def test_when_before_vs_after_ordering(monkeypatch: pytest.MonkeyPatch, f: Path) -> None:
    seen: list[int] = []
    race_after(monkeypatch, target=f, op="stat", interloper=lambda: atomic_replace(f, b"12345"), when="before")
    seen.append(os.stat(f).st_size)
    assert seen == [5], "stat sees the state after a 'before' interloper"

    g = f.with_name("g")
    g.write_bytes(b"orig")
    race_after(monkeypatch, target=g, op="stat", interloper=lambda: atomic_replace(g, b"12345"))
    assert os.stat(g).st_size == 4, "an 'after' interloper leaves the stale answer"
    assert g.read_bytes() == b"12345"


def test_interloper_own_calls_are_not_counted(monkeypatch: pytest.MonkeyPatch, f: Path) -> None:
    probe = race_after(monkeypatch, target=f, op="replace", interloper=lambda: atomic_replace(f, b"x"), nth=1)
    os.replace(f, f.with_name("moved"))
    assert probe.fired and probe.matches == 1


def test_probe_unfired_when_op_not_reached(monkeypatch: pytest.MonkeyPatch, f: Path) -> None:
    probe = race_after(monkeypatch, target=f, op="unlink", interloper=lambda: None)
    f.read_bytes()
    assert not probe.fired


def test_race_after_rejects_bad_arguments(monkeypatch: pytest.MonkeyPatch, f: Path) -> None:
    with pytest.raises(ValueError):
        race_after(monkeypatch, target=f, op="chmod", interloper=lambda: None)
    with pytest.raises(ValueError):
        race_after(monkeypatch, target=f, op="stat", interloper=lambda: None, when="during")
    with pytest.raises(ValueError):
        race_after(monkeypatch, target=f, op="stat", interloper=lambda: None, nth=0)


def test_swap_to_symlink(tmp_path: Path, f: Path) -> None:
    dest = tmp_path / "dest"
    dest.mkdir()
    swap_to_symlink(f, dest)
    assert f.is_symlink() and os.readlink(f) == str(dest)
    assert (tmp_path / "f.txt.swapped").read_bytes() == b"orig"


def test_swap_to_dir(tmp_path: Path, f: Path) -> None:
    swap_to_dir(f)
    assert stat.S_ISDIR(os.lstat(f).st_mode)
    assert (tmp_path / "f.txt.swapped").read_bytes() == b"orig"


def test_atomic_replace_changes_inode_edit_in_place_keeps_it(f: Path) -> None:
    ino = os.stat(f).st_ino
    edit_in_place(f, b"in place")
    assert f.read_bytes() == b"in place" and os.stat(f).st_ino == ino
    atomic_replace(f, b"atomic")
    assert f.read_bytes() == b"atomic" and os.stat(f).st_ino != ino
    assert [p.name for p in f.parent.iterdir()] == ["f.txt"], "no tmp litter"


def test_open_fd_writer_survives_unlink_by_name(f: Path) -> None:
    with open_fd_writer(f) as w:
        f.unlink()
        w.write(b"late edit")  # lands in the detached inode
        assert not f.exists()
    w.close()  # idempotent


def test_open_fd_writer_writes_through_the_existing_inode(f: Path) -> None:
    with open_fd_writer(f) as w:
        w.write(b"through fd")
    assert f.read_bytes() == b"through fd"


def test_fail_nth_read_raises_only_on_nth(monkeypatch: pytest.MonkeyPatch, f: Path) -> None:
    c = fail_nth_read(monkeypatch, f, nth=2)
    assert f.read_bytes() == b"orig"
    with pytest.raises(PermissionError):
        f.read_text()
    assert f.read_bytes() == b"orig"
    assert (c.calls, c.raised) == (3, 1)


@pytest.mark.parametrize("via", ["open_rb", "open_r", "read_bytes", "os_open"])
def test_fail_nth_read_covers_each_read_path(monkeypatch: pytest.MonkeyPatch, f: Path, via: str) -> None:
    fail_nth_read(monkeypatch, f, nth=1, exc=OSError)
    reader = {
        "open_rb": lambda: open(f, "rb").close(),
        "open_r": lambda: open(f).close(),
        "read_bytes": f.read_bytes,
        "os_open": lambda: os.close(os.open(f, os.O_RDONLY)),
    }[via]
    with pytest.raises(OSError):
        reader()


def test_fail_nth_read_ignores_writes_and_other_paths(monkeypatch: pytest.MonkeyPatch, f: Path) -> None:
    other = f.with_name("o")
    other.write_bytes(b"o")
    c = fail_nth_read(monkeypatch, f, nth=1)
    other.read_bytes()
    f.write_bytes(b"new")
    assert c.calls == 0 and c.raised == 0


def test_unreadable_blocks_reads_and_restores_mode(f: Path) -> None:
    before = stat.S_IMODE(os.stat(f).st_mode)
    with unreadable(f):
        with pytest.raises(PermissionError):
            f.read_bytes()
    assert stat.S_IMODE(os.stat(f).st_mode) == before
    assert f.read_bytes() == b"orig"


def test_unreadable_restores_mode_on_error(f: Path) -> None:
    before = stat.S_IMODE(os.stat(f).st_mode)
    with pytest.raises(RuntimeError), unreadable(f):
        raise RuntimeError("boom")
    assert stat.S_IMODE(os.stat(f).st_mode) == before


def test_unreadable_parent_blocks_stat_and_restores_mode(tmp_path: Path) -> None:
    d = tmp_path / "d"
    d.mkdir()
    p = d / "f"
    p.write_bytes(b"x")
    before = stat.S_IMODE(os.stat(d).st_mode)
    with unreadable_parent(p):
        with pytest.raises(PermissionError):
            os.lstat(p)
        # Path.exists() masks EACCES as False on 3.12+ but raises PermissionError on 3.11.
        try:
            masked = p.exists()
        except PermissionError:
            masked = False
        assert not masked
    assert stat.S_IMODE(os.stat(d).st_mode) == before
    assert p.read_bytes() == b"x"


def test_unreadable_skips_as_root(monkeypatch: pytest.MonkeyPatch, f: Path) -> None:
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    with pytest.raises(pytest.skip.Exception), unreadable(f):
        pass


def test_name_collision_corpus_contains_defaults_and_given_patterns() -> None:
    corpus = name_collision_corpus([".x.{name}.mine", "literal.stage"])
    for expected in (".x.tmp", "x~", "x.retired", ".x.seeding-123", ".x.{name}.mine", ".x.x.mine", "literal.stage"):
        assert expected in corpus
    assert len(corpus) == len(set(corpus))
    assert name_collision_corpus([]) == name_collision_corpus([])


def test_snapshot_groups_hardlinks_and_counts_inner_symlinks(tmp_path: Path) -> None:
    (tmp_path / "a").write_bytes(b"A")
    os.link(tmp_path / "a", tmp_path / "a2")
    (tmp_path / "s").symlink_to(tmp_path / "a")
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.write_bytes(b"OUT")
    (tmp_path / "out").symlink_to(outside)
    (tmp_path / "broken").symlink_to(tmp_path / "nope")
    snap = snapshot_user_bytes(tmp_path)
    assert len(snap) == 1, "outside-root and broken links contribute nothing"
    assert sorted(next(iter(snap.values()))) == ["a", "a2", "s"]


def test_preserved_passes_on_rename_and_extra_links(tmp_path: Path) -> None:
    (tmp_path / "u").write_bytes(b"unique")
    before = snapshot_user_bytes(tmp_path)
    os.replace(tmp_path / "u", tmp_path / "moved")
    assert_user_bytes_preserved(before, tmp_path)


def test_preserved_passes_when_one_of_two_hardlinks_is_deleted(tmp_path: Path) -> None:
    (tmp_path / "u").write_bytes(b"unique")
    os.link(tmp_path / "u", tmp_path / "v")
    before = snapshot_user_bytes(tmp_path)
    (tmp_path / "u").unlink()
    assert_user_bytes_preserved(before, tmp_path)


def test_preserved_fails_when_last_link_deleted(tmp_path: Path) -> None:
    (tmp_path / "u").write_bytes(b"unique")
    (tmp_path / "keep").write_bytes(b"other")
    before = snapshot_user_bytes(tmp_path)
    (tmp_path / "u").unlink()
    with pytest.raises(AssertionError, match="user bytes lost"):
        assert_user_bytes_preserved(before, tmp_path)


def test_preserved_fails_when_bytes_edited_in_place(tmp_path: Path) -> None:
    (tmp_path / "u").write_bytes(b"unique")
    before = snapshot_user_bytes(tmp_path)
    edit_in_place(tmp_path / "u", b"changed")
    with pytest.raises(AssertionError):
        assert_user_bytes_preserved(before, tmp_path)


def test_preserved_fails_when_only_a_dangling_symlink_remains(tmp_path: Path) -> None:
    (tmp_path / "u").write_bytes(b"unique")
    (tmp_path / "s").symlink_to(tmp_path / "u")
    before = snapshot_user_bytes(tmp_path)
    (tmp_path / "u").unlink()
    with pytest.raises(AssertionError):
        assert_user_bytes_preserved(before, tmp_path)


def test_unreadable_skips_on_a_non_posix_host_instead_of_crashing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Windows has neither chmod 000 nor os.geteuid: the helper skips cleanly, the target is untouched."""
    from tests._fs_hazards import unreadable

    target = tmp_path / "d"
    target.mkdir()
    mode = target.stat().st_mode
    monkeypatch.setattr(os, "name", "nt")
    monkeypatch.delattr(os, "geteuid", raising=False)
    with pytest.raises(pytest.skip.Exception, match="POSIX"), unreadable(target):
        pass
    assert target.stat().st_mode == mode
