"""Repeatedly replacing a pinned-read target must not grow the process's open-fd count (P1 rc9).

Before the fix, ``trw_mcp._checkout_access`` cached one descriptor per inode
forever: replacing a status database's file (a new inode each time) and
reading its header pinned a new descriptor every call, eventually exhausting
the process's file-descriptor table (EMFILE) and taking down unrelated MCP
work. The fix retires a path's previously-pinned descriptor once that path
resolves to a different inode -- see ``_checkout_access``'s module docstring for
why that close can never drop a live lock.
"""

from __future__ import annotations

import gc
import os
import threading
from pathlib import Path

import pytest

from tests._checkout_access_state import holding, reset_pinned_reads
from trw_mcp import _checkout_access


@pytest.fixture(autouse=True)
def _clean_checkout_access_state() -> None:
    """Isolate the module-level fd cache between tests (it is process-global by design)."""
    reset_pinned_reads()
    yield
    reset_pinned_reads()


def _replace_with_new_inode(path: Path, content: bytes) -> None:
    """Atomically swap *path* for a brand-new inode (like a store reset/rewrite would)."""
    tmp = path.with_suffix(path.suffix + ".new")
    tmp.write_bytes(content)
    os.replace(tmp, path)


def test_repeated_inode_replacement_retires_the_stale_descriptor(tmp_path: Path) -> None:
    """The discriminating case: this failed against the pre-fix cache, which never retired."""
    target = tmp_path / "operations.sqlite3"
    target.write_bytes(b"0" * 32)

    for i in range(40):
        _replace_with_new_inode(target, str(i).encode().ljust(32, b"0"))
        _checkout_access.read_at(target, 32)
        # At most one descriptor is ever pinned for a single, repeatedly-replaced path.
        assert len(_checkout_access._fds) == 1, (
            f"iteration {i}: expected exactly 1 pinned fd for one churned path, "
            f"got {len(_checkout_access._fds)} -- stale descriptors are leaking"
        )


def test_repeated_inode_replacement_does_not_grow_process_fd_count(tmp_path: Path) -> None:
    """Same scenario, verified against the OS's real fd table rather than the cache's own bookkeeping."""
    target = tmp_path / "operations.sqlite3"
    target.write_bytes(b"0" * 32)
    _checkout_access.read_at(target, 32)
    baseline = len(os.listdir("/dev/fd"))

    for i in range(40):
        _replace_with_new_inode(target, str(i).encode().ljust(32, b"0"))
        _checkout_access.read_at(target, 32)

    assert len(os.listdir("/dev/fd")) <= baseline + 1


def test_same_inode_rewrite_never_retires(tmp_path: Path) -> None:
    """A write-in-place (no replacement) keeps tracking the live file, unlike a real replace."""
    target = tmp_path / "operations.sqlite3"
    target.write_bytes(b"a" * 32)
    first = _checkout_access.read_at(target, 32)
    assert first == b"a" * 32

    with target.open("r+b") as handle:
        handle.write(b"b" * 32)

    second = _checkout_access.read_at(target, 32)
    assert second == b"b" * 32
    assert len(_checkout_access._fds) == 1


def test_distinct_paths_each_get_their_own_pinned_descriptor(tmp_path: Path) -> None:
    a = tmp_path / "a.sqlite3"
    b = tmp_path / "b.sqlite3"
    a.write_bytes(b"a" * 16)
    b.write_bytes(b"b" * 16)

    _checkout_access.read_at(a, 16)
    _checkout_access.read_at(b, 16)

    assert len(_checkout_access._fds) == 2


def test_capacity_cap_raises_instead_of_growing_without_bound(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_checkout_access, "_MAX_PINNED_FDS", 2)
    paths = [tmp_path / f"db-{i}.sqlite3" for i in range(3)]
    for path in paths[:2]:
        path.write_bytes(b"x" * 8)
        _checkout_access.read_at(path, 8)

    paths[2].write_bytes(b"x" * 8)
    with holding(*paths[:2]), pytest.raises(_checkout_access.PinnedReadCapacityExceeded):  # a HELD fd is never evicted
        _checkout_access.read_at(paths[2], 8)
    assert len(_checkout_access._fds) == 2


def test_concurrent_retire_and_read_never_ebadf_or_cross_file_bytes(tmp_path: Path) -> None:
    """The discriminating case for the C12 race (P1 rc9).

    Thread A hammers ``read_at`` on a fixed path while thread B repeatedly
    replaces that path with a brand-new inode (each generation carrying a
    distinct marker) and also reads it. Before the fix, ``_pinned_fd`` handed
    out an fd and released the lock *before* ``os.pread`` ran, so a retire on
    thread B could close that exact fd while thread A's ``pread`` was in
    flight -- either an ``OSError`` (EBADF) or, if the closed fd number was
    reused by thread B's concurrent ``os.open``, bytes belonging to a
    different generation's file than the one the read call names. Every
    successful read here must return exactly one generation's 32-byte marker,
    never a mix, and no thread may ever see EBADF.
    """
    target = tmp_path / "mailbox.sqlite3"
    target.write_bytes(b"g".ljust(32, b"0"))

    iterations = 400
    errors: list[BaseException] = []
    bad_reads: list[bytes] = []
    stop = threading.Event()

    def _marker(generation: int) -> bytes:
        return f"g{generation}".encode().ljust(32, b"0")

    valid_markers = {_marker(i) for i in range(iterations + 1)} | {b"g".ljust(32, b"0")}

    def _replace_with_new_inode(path: Path, content: bytes) -> None:
        tmp = path.with_suffix(path.suffix + f".new{threading.get_ident()}")
        tmp.write_bytes(content)
        os.replace(tmp, path)

    def _reader() -> None:
        while not stop.is_set():
            try:
                data = _checkout_access.read_at(target, 32)
            except OSError as exc:  # EBADF (or any other close-underneath failure) is the bug
                errors.append(exc)
                return
            if data not in valid_markers:
                bad_reads.append(data)

    def _replacer() -> None:
        for i in range(iterations):
            try:
                _replace_with_new_inode(target, _marker(i))
                _checkout_access.read_at(target, 32)
            except OSError as exc:
                errors.append(exc)
                return
        stop.set()

    reader_threads = [threading.Thread(target=_reader) for _ in range(3)]
    replacer_thread = threading.Thread(target=_replacer)

    for t in reader_threads:
        t.start()
    replacer_thread.start()

    replacer_thread.join(timeout=30)
    stop.set()
    for t in reader_threads:
        t.join(timeout=30)

    assert not errors, f"reader/replacer hit OSError under concurrent retire+read: {errors!r}"
    assert not bad_reads, f"read returned bytes not matching any known generation marker: {bad_reads!r}"


class _LockThatIsOrphanedBeforeAcquire:
    """Wraps a per-inode lock; on acquire, first replays "retired, then re-pinned under the SAME (key, fd)".

    That replay is what Linux produces when a retirement closes fd N and pops the inode's lock, the
    freed inode number is handed to the next new file, and the next ``os.open`` gets fd N back: the
    maps again say ``_fds[key] == N``, but ``_inode_locks[key]`` is a NEW lock object.
    """

    def __init__(self, real: threading.Lock, key: tuple[int, int]) -> None:
        self._real = real
        self._key = key

    def acquire(self) -> bool:
        with _checkout_access._map_lock:
            _checkout_access._inode_locks[self._key] = threading.Lock()
        return self._real.acquire()

    def release(self) -> None:
        self._real.release()


@pytest.mark.parametrize("orphaned", [True, False], ids=["aba-repin-while-waiting", "no-race"])
def test_io_lock_is_the_inodes_current_lock_even_after_an_aba_repin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, orphaned: bool
) -> None:
    """Deterministic guard for the Linux-only EBADF race (8.0.0 release check, Linux leg).

    The fd-only revalidation in ``_pinned_fd_for_io`` accepted a reader whose lock had been orphaned
    by a retire + inode/fd-number reuse re-pin: it then read with a lock no future ``_close_stale``
    waits on, so a later retirement could close the fd mid-``pread``. The lock handed back for I/O
    must always be the one ``_inode_locks`` holds for that key -- the one a closer acquires.
    """
    target = tmp_path / "mailbox.sqlite3"
    target.write_bytes(b"m" * 16)
    _checkout_access.read_at(target, 16)
    real_get_lock = _checkout_access._inode_lock_locked
    calls = 0

    def _get_lock(key: tuple[int, int]) -> object:
        nonlocal calls
        calls += 1
        real = real_get_lock(key)
        return _LockThatIsOrphanedBeforeAcquire(real, key) if orphaned and calls == 1 else real

    monkeypatch.setattr(_checkout_access, "_inode_lock_locked", _get_lock)
    fd, lock = _checkout_access._pinned_fd_for_io(target)
    try:
        key = _checkout_access._path_inode[target]
        assert _checkout_access._fds[key] == fd
        assert lock is _checkout_access._inode_locks[key], (
            "I/O would run under an orphaned lock that no retirement waits on (EBADF window)"
        )
        assert lock.locked()
        assert os.pread(fd, 16, 0) == b"m" * 16
    finally:
        lock.release()
    assert calls == (2 if orphaned else 1), "the ABA case must re-resolve exactly once"


def test_capacity_exceeded_for_an_unrelated_path_never_leaks_the_previous_descriptor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed open at the cap (a genuinely unrelated new path) must not retire-then-leak anything.

    Retiring *before* the new open/cap-check could succeed would pop a previous fd out of every dict
    that could close it, and then raise -- leaking it. The fix retires only after the new fd is
    safely resolved, so a capacity failure leaves every existing path's descriptor exactly as it was:
    valid, open, and still tracked. (The P2 fix below covers the DIFFERENT case -- a path refreshing
    its OWN, exclusively-owned previous inode -- which is now allowed even at the cap.)
    """
    monkeypatch.setattr(_checkout_access, "_MAX_PINNED_FDS", 1)
    pinned = tmp_path / "first.sqlite3"
    pinned.write_bytes(b"a" * 8)
    _checkout_access.read_at(pinned, 8)
    key1 = _checkout_access._path_inode[pinned]
    fd1 = _checkout_access._fds[key1]

    unrelated = tmp_path / "second.sqlite3"  # a path this module has never seen before
    unrelated.write_bytes(b"b" * 8)

    with holding(pinned), pytest.raises(_checkout_access.PinnedReadCapacityExceeded):
        _checkout_access.read_at(unrelated, 8)

    assert _checkout_access._fds.get(key1) == fd1, "the existing descriptor must still be tracked"
    os.fstat(fd1)  # raises OSError (EBADF) if it had been closed/leaked out of the map


def test_replacing_the_only_pinned_path_at_capacity_still_refreshes_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PRD-CORE-316 P2 fix (worker-3 review, core-316-sA round 3; NFR04/B71-08 regression guard).

    At the cap, a path that exclusively owns its own previous inode must still be refreshable:
    retiring that association frees a slot net-zero, so this is not the "never evict a live
    descriptor another path still needs" policy B71-08 protects -- it is the SAME path reclaiming
    its own slot. Before this fix, a replaced mailbox or delivery-journal file became permanently
    unrefreshable once the cache reached its cap.
    """
    monkeypatch.setattr(_checkout_access, "_MAX_PINNED_FDS", 1)
    target = tmp_path / "solo.sqlite3"
    target.write_bytes(b"a" * 8)
    _checkout_access.read_at(target, 8)
    key1 = _checkout_access._path_inode[target]
    fd1 = _checkout_access._fds[key1]

    tmp = target.with_suffix(".new")
    tmp.write_bytes(b"b" * 8)
    os.replace(tmp, target)  # `target` now names a fresh inode; the cache is at its 1-entry cap

    data = _checkout_access.read_at(target, 8)

    assert data == b"b" * 8
    assert key1 not in _checkout_access._fds, "the old inode's slot must be freed (net-zero refresh)"
    with pytest.raises(OSError):
        os.fstat(fd1)  # the retired descriptor must actually be closed, not merely forgotten


def test_a_destroyed_connections_hold_does_not_keep_a_replaced_inode_pinned(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A connection destroyed before the refresh no longer holds the old inode, even before anything drained it.

    ``__del__`` only queues its release; retirement read the undrained ``_held_inodes`` and kept the stale fd
    pinned past the cap (seen in the Linux leg, where an inode number freed by one test is reused by the next).
    """
    monkeypatch.setattr(_checkout_access, "_MAX_PINNED_FDS", 1)
    target = tmp_path / "solo.sqlite3"
    target.write_bytes(b"a" * 8)
    _checkout_access.read_at(target, 8)
    key1 = _checkout_access._path_inode[target]
    conn = _checkout_access.held_connect(target)
    del conn
    gc.collect()  # the connection sits in a reference cycle; collecting it queues its release, undrained
    assert _checkout_access._pending_releases, "precondition: the release is queued, not yet applied"
    _replace_with_new_inode(target, b"b" * 8)

    assert _checkout_access.read_at(target, 8) == b"b" * 8
    assert key1 not in _checkout_access._fds, "a released hold must not keep the old inode pinned"


def test_close_stale_never_closes_a_descriptor_whose_key_was_re_pinned(tmp_path: Path) -> None:
    """PRD-CORE-316 P1 fix (worker-3 review, core-316-sA round 3; BLOCK: data loss via a dropped lock).

    Simulates `_retire_locked` having already unlinked a key, then a DIFFERENT path re-pinning that
    exact same inode before the deferred `_close_stale` runs (a rename-back, or an untracked
    hard-link sibling). Closing the stale fd in that window would drop every POSIX fcntl lock this
    process holds on the inode via the NEW descriptor too -- deterministic, no sleeps: the "race" is
    reproduced directly via the module's own dicts, not by racing real threads.
    """
    path_a = tmp_path / "a.sqlite3"
    path_a.write_bytes(b"a" * 8)
    _checkout_access.read_at(path_a, 8)
    key_a = _checkout_access._path_inode[path_a]
    fd_a = _checkout_access._fds[key_a]

    # Simulate `_retire_locked` having already run its bookkeeping for key_a.
    del _checkout_access._fds[key_a]
    del _checkout_access._inode_paths[key_a]
    del _checkout_access._path_inode[path_a]
    stale = (key_a, fd_a)

    # A different, untracked path re-pins the exact same inode before the deferred close runs.
    path_b = tmp_path / "b.sqlite3"
    os.link(path_a, path_b)
    _checkout_access.read_at(path_b, 8)
    assert _checkout_access._fds[key_a] != fd_a, "path_b must have opened its own, fresh descriptor"

    _checkout_access._close_stale(stale)

    os.fstat(fd_a)  # must NOT raise: closing it would have dropped the re-pin's locks too
    assert fd_a in _checkout_access._race_loser_fds


def test_close_stale_closes_the_descriptor_when_its_key_was_not_re_pinned(tmp_path: Path) -> None:
    """Positive control for the fix above: with no re-pin, `_close_stale` still closes normally."""
    path_a = tmp_path / "a.sqlite3"
    path_a.write_bytes(b"a" * 8)
    _checkout_access.read_at(path_a, 8)
    key_a = _checkout_access._path_inode[path_a]
    fd_a = _checkout_access._fds[key_a]

    del _checkout_access._fds[key_a]
    del _checkout_access._inode_paths[key_a]
    del _checkout_access._path_inode[path_a]
    stale = (key_a, fd_a)

    _checkout_access._close_stale(stale)

    with pytest.raises(OSError):
        os.fstat(fd_a)
    assert fd_a not in _checkout_access._race_loser_fds


def test_delete_regular_file_under_survives_a_parent_directory_swap(tmp_path: Path) -> None:
    """PRD-CORE-316 P0 fix (core-316-sB round 1 review): a naive verify-then-``os.remove(full_path)``
    re-resolves the path string a second time, so a parent directory swapped for a symlink between
    the two calls could redirect the delete outside *anchor* entirely.
    ``delete_regular_file_under`` unlinks through the SAME already-open parent directory descriptor
    its own verification open used, so it is provably immune: this test swaps the on-disk directory
    for a symlink to a DECOY directory partway through the call and proves the ORIGINAL file is
    deleted (via the held fd) while the decoy is never touched.
    """
    real_dir = tmp_path / "code-index"
    real_dir.mkdir()
    real_file = real_dir / "chunks.json"
    real_file.write_text("real")

    decoy_dir = tmp_path / "decoy"
    decoy_dir.mkdir()
    decoy_file = decoy_dir / "chunks.json"
    decoy_file.write_text("decoy")

    real_open = os.open
    swap_done = False

    def _open_and_swap(path: object, flags: int, *args: object, **kwargs: object) -> int:
        nonlocal swap_done
        result: int = real_open(path, flags, *args, **kwargs)  # type: ignore[arg-type]
        if not swap_done and path == "chunks.json" and not (flags & os.O_DIRECTORY):
            # Right after the module's verification open succeeds (holding a dir_fd to `real_dir`),
            # an attacker replaces the directory ENTRY `code-index` with a symlink to `decoy`.
            swap_done = True
            os.replace(real_dir, tmp_path / "code-index-orig")  # frees the name `code-index`
            (tmp_path / "code-index").symlink_to(decoy_dir)
        return result

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(os, "open", _open_and_swap)
        deleted = _checkout_access.delete_regular_file_under(tmp_path, "code-index/chunks.json")

    assert deleted is True
    assert swap_done, "the swap never ran -- this test would pass vacuously without it"
    # The ORIGINAL file (now reachable only via the held dir_fd, since `code-index` on disk now
    # points elsewhere) was deleted -- proving the unlink used the fd, not a re-resolved path string.
    assert not (tmp_path / "code-index-orig" / "chunks.json").exists()
    # The decoy the swapped-in symlink now names must be completely untouched.
    assert decoy_file.exists()
    assert decoy_file.read_text() == "decoy"


def test_delete_regular_file_under_leaves_a_symlink_untouched(tmp_path: Path) -> None:
    """A symlink at the exact target path is never followed, never unlinked through the link."""
    target = tmp_path / "real.json"
    target.write_text("real")
    legacy = tmp_path / "chunks.json"
    legacy.symlink_to(target)

    deleted = _checkout_access.delete_regular_file_under(tmp_path, "chunks.json")

    assert deleted is False
    assert legacy.is_symlink()
    assert target.exists() and target.read_text() == "real"


def test_open_under_never_leaks_the_parent_fd_on_a_missing_directory_component(tmp_path: Path) -> None:
    """PRD-CORE-316 P0 fix (core-316-sB round 2 review): a failed directory-component open must
    close the currently-held parent fd before re-raising, or every refused/missing path leaks one
    descriptor. Repeats past the process's typical low-water-mark cushion so a leak would be visible
    as real fd-count growth, not just a single missed close.
    """
    baseline_fd_count = len(os.listdir("/dev/fd"))

    for _ in range(50):
        with pytest.raises(OSError):
            _checkout_access.open_under(tmp_path, "no-such-dir/nested/chunks.json")

    assert len(os.listdir("/dev/fd")) <= baseline_fd_count + 1


def test_delete_regular_file_under_missing_file_is_a_no_op(tmp_path: Path) -> None:
    """A missing file is a no-op, not an error -- callers must not need to pre-check existence."""
    assert _checkout_access.delete_regular_file_under(tmp_path, "does-not-exist.json") is False


def test_a_stalled_read_on_one_inode_never_blocks_a_read_on_another(tmp_path: Path) -> None:
    """PRD-CORE-316 FR01/NFR02: per-inode locking, not the old process-wide ``_fds_lock``.

    Against the pre-consolidation ``_pinned_read.py`` (one global lock held across ``os.pread``),
    B's read below would block until A's monkeypatched ``pread`` returns -- this test would time out
    waiting on ``a_started`` never releasing B in time, or B's own ``read_at`` would hang. The
    consolidated module's per-inode lock lets B proceed while A is still parked mid-``pread``.
    """
    path_a = tmp_path / "a.sqlite3"
    path_b = tmp_path / "b.sqlite3"
    path_a.write_bytes(b"A" * 16)
    path_b.write_bytes(b"B" * 16)

    # Warm the cache so the test knows which real fd number belongs to path A.
    _checkout_access.read_at(path_a, 16)
    fd_a = _checkout_access._fds[_checkout_access._path_inode[path_a]]

    real_pread = os.pread
    a_started = threading.Event()
    a_may_proceed = threading.Event()

    def _blocking_pread(fd: int, length: int, offset: int) -> bytes:
        if fd == fd_a:
            a_started.set()
            assert a_may_proceed.wait(timeout=30), "test-owned event was never released"
        return real_pread(fd, length, offset)

    result_a: list[bytes] = []

    def _read_a() -> None:
        result_a.append(_checkout_access.read_at(path_a, 16))

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(os, "pread", _blocking_pread)
        thread_a = threading.Thread(target=_read_a)
        thread_a.start()
        assert a_started.wait(timeout=5), "A's read never entered its pread"

        # B must complete here, with A still parked inside its pread and the release Event unset.
        result_b = _checkout_access.read_at(path_b, 16)

        assert result_b == b"B" * 16
        assert not a_may_proceed.is_set(), "B only completed because A's stall had already ended"

        a_may_proceed.set()
        thread_a.join(timeout=10)

    assert result_a == [b"A" * 16]


def test_retiring_one_path_never_closes_a_hard_linked_sibling(tmp_path: Path) -> None:
    """PRD-CORE-316 FR02(a): a path's retirement never closes a descriptor a hard-linked sibling still names."""
    original = tmp_path / "primary.sqlite3"
    linked = tmp_path / "sibling.sqlite3"
    original.write_bytes(b"v0".ljust(32, b"0"))
    os.link(original, linked)

    _checkout_access.read_at(original, 32)
    _checkout_access.read_at(linked, 32)
    assert len(_checkout_access._fds) == 1, "both hard-linked paths should share one pinned descriptor"

    baseline_fd_count = len(os.listdir("/dev/fd"))

    for i in range(5):
        tmp = original.with_suffix(f".new{i}")
        tmp.write_bytes(f"v{i + 1}".encode().ljust(32, b"0"))
        os.replace(tmp, original)  # `original` now names a fresh inode; `linked` still names the old one
        _checkout_access.read_at(original, 32)

        assert _checkout_access.read_at(linked, 32) == b"v0".ljust(32, b"0"), (
            f"iteration {i}: linked's descriptor must survive original's retirement (no EBADF, no stale bytes)"
        )

    # `original`'s own churned descriptor is retired every iteration; only `linked`'s original fd
    # plus `original`'s current one stay live -- no growth beyond that steady state.
    assert len(os.listdir("/dev/fd")) <= baseline_fd_count + 1


def test_stat_open_race_tracks_the_redundant_descriptor_without_closing_it(tmp_path: Path) -> None:
    """PRD-CORE-316 FR02(b): a stat/open race for an already-pinned inode never closes its loser.

    Closing it would risk releasing a live lock this process holds on the same inode via a
    *different* fd (POSIX fcntl locks are per (process, inode), not per fd -- the exact C15 hazard
    this module exists to prevent). B71-82 wanted the race TRACKED, not silently discarded as an
    unreachable Python int; it never asked for a close that reopens the corruption risk this whole
    module removes.
    """
    target = tmp_path / "raced.sqlite3"
    target.write_bytes(b"x" * 16)
    st = os.stat(target)
    key = (st.st_dev, st.st_ino)
    real_open = os.open
    winner_fd = os.open(target, os.O_RDONLY)

    def _racing_open(path: object, flags: int, *args: object, **kwargs: object) -> int:
        # Simulate another thread winning the pin race between our stat and our own open.
        _checkout_access._fds.setdefault(key, winner_fd)
        return real_open(path, flags, *args, **kwargs)  # type: ignore[arg-type]

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(os, "open", _racing_open)
        data = _checkout_access.read_at(target, 16)

    assert data == b"x" * 16
    assert _checkout_access._fds[key] == winner_fd, "the losing (redundant) descriptor must not win the map"
    assert len(_checkout_access._race_loser_fds) == 1, "the redundant descriptor must be tracked, not discarded"
    loser_fd = _checkout_access._race_loser_fds[0]
    os.fstat(loser_fd)  # raises OSError (EBADF) if the loser were closed instead of tracked


@pytest.mark.skipif(os.name == "nt", reason="read_at pins descriptors only on POSIX; Windows reads by path")
def test_reset_restores_room_after_the_cache_is_full(tmp_path: Path) -> None:
    """The helper other test files use to isolate the cap: a cache full of HELD fds refuses a new file, a reset accepts it."""
    pins = []
    for index in range(_checkout_access._MAX_PINNED_FDS):
        pinned = tmp_path / f"pin{index}.txt"
        pinned.write_text("x", encoding="utf-8")
        _checkout_access.read_at(pinned, 1)
        pins.append(pinned)
    fresh = tmp_path / "fresh.txt"
    fresh.write_text("y", encoding="utf-8")
    with holding(*pins), pytest.raises(_checkout_access.PinnedReadCapacityExceeded):
        _checkout_access.read_at(fresh, 1)

    reset_pinned_reads()

    assert _checkout_access.read_at(fresh, 1) == b"y"
