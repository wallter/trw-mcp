"""At the pinned-fd cap, entries whose path no longer exists are reclaimed (E2E-INC-103).

Live descriptors keep the B71-08 "never evict" policy: a path that still exists (even one swapped to a
new inode) or an inode another path still names is never evicted, and an all-live cache still refuses.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from tests._checkout_access_state import holding, reset_pinned_reads
from trw_mcp import _checkout_access
from trw_mcp._checkout_access import PinnedReadCapacityExceeded, read_at


@pytest.fixture(autouse=True)
def _clean_state() -> None:
    reset_pinned_reads()
    yield
    reset_pinned_reads()


def _make(directory: Path, name: str, data: bytes = b"data") -> Path:
    path = directory / name
    path.write_bytes(data)
    return path


def _fd_is_open(fd: int, key: tuple[int, int] | None = None) -> bool:
    """Whether *fd* is open (and, given *key*, still on that inode: a closed fd number is quickly reused)."""
    try:
        st = os.fstat(fd)
    except OSError:  # trw-fail-silent-allow: False IS the answer to "is this fd open?"
        return False
    return key is None or (st.st_dev, st.st_ino) == key


def test_deleted_paths_are_reclaimed_past_the_real_cap(tmp_path: Path) -> None:
    cap = _checkout_access._MAX_PINNED_FDS
    for index in range(cap + 1 + 5):
        path = _make(tmp_path, f"box{index}")
        assert read_at(path, 4) == b"data"
        path.unlink()
    fresh = _make(tmp_path, "fresh", b"fresh")
    assert read_at(fresh, 5) == b"fresh"
    assert len(_checkout_access._fds) <= cap


def test_swept_descriptors_are_actually_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_checkout_access, "_MAX_PINNED_FDS", 2)
    paths = [_make(tmp_path, f"p{i}") for i in range(2)]
    for path in paths:
        read_at(path, 4)
    old = dict(_checkout_access._fds)
    for path in paths:
        path.unlink()
    read_at(_make(tmp_path, "new"), 4)
    assert not any(_fd_is_open(fd, key) for key, fd in old.items())
    assert set(_checkout_access._path_inode) == {tmp_path / "new"}
    assert len(_checkout_access._inode_locks) == 1


def test_all_live_cache_refuses_and_keeps_every_descriptor(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_checkout_access, "_MAX_PINNED_FDS", 3)
    live = [_make(tmp_path, f"live{i}") for i in range(3)]
    for path in live:
        read_at(path, 4)
    pinned = dict(_checkout_access._fds)
    with holding(*live), pytest.raises(PinnedReadCapacityExceeded):  # live AND held: never evicted
        read_at(_make(tmp_path, "extra"), 4)
    assert _checkout_access._fds == pinned
    assert all(_fd_is_open(fd) for fd in pinned.values())


def test_swapped_path_at_cap_still_refuses(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A path that exists but names a new inode is the swap the pin detects: never treated as dead."""
    monkeypatch.setattr(_checkout_access, "_MAX_PINNED_FDS", 2)
    first = _make(tmp_path, "first")
    linked = tmp_path / "linked"
    os.link(first, linked)  # one inode, two tracked paths: swapping `linked` frees no slot
    read_at(first, 4)
    read_at(linked, 4)
    other = _make(tmp_path, "other")
    read_at(other, 4)
    pinned = dict(_checkout_access._fds)
    replacement = _make(tmp_path, "linked.new", b"evil")
    with holding(first, other):  # held at open, so the old inode stays protected after the swap
        os.replace(replacement, linked)
        with pytest.raises(PinnedReadCapacityExceeded):
            read_at(linked, 4)
    assert _checkout_access._fds == pinned
    assert all(_fd_is_open(fd) for fd in pinned.values())
    assert _checkout_access._path_inode[linked] == _checkout_access._path_inode[first]


def test_hard_linked_inode_with_a_live_sibling_is_not_evicted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_checkout_access, "_MAX_PINNED_FDS", 2)
    first = _make(tmp_path, "first")
    sibling = tmp_path / "sibling"
    os.link(first, sibling)
    other = _make(tmp_path, "other")
    read_at(first, 4)
    read_at(sibling, 4)
    read_at(other, 4)
    linked_key = _checkout_access._path_inode[first]
    linked_fd = _checkout_access._fds[linked_key]
    first.unlink()
    other.unlink()
    read_at(_make(tmp_path, "fresh"), 4)  # reclaims `other` (fully dead) and only unlinks `first`'s bookkeeping
    assert _checkout_access._fds[linked_key] == linked_fd
    assert _fd_is_open(linked_fd)
    assert read_at(sibling, 4) == b"data"
    assert first not in _checkout_access._path_inode
    assert _checkout_access._inode_paths[linked_key] == {sibling}


def test_hard_link_is_evicted_only_when_every_path_is_gone(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_checkout_access, "_MAX_PINNED_FDS", 1)
    first = _make(tmp_path, "first")
    sibling = tmp_path / "sibling"
    os.link(first, sibling)
    read_at(first, 4)
    read_at(sibling, 4)
    key = _checkout_access._path_inode[first]
    fd = _checkout_access._fds[key]
    first.unlink()
    blocked = _make(tmp_path, "blocked")
    with holding(sibling), pytest.raises(PinnedReadCapacityExceeded):
        read_at(blocked, 4)  # sibling still live and held: refusal stands
    assert _fd_is_open(fd, key)
    sibling.unlink()
    assert read_at(blocked, 4) == b"data"
    assert not _fd_is_open(fd, key)
