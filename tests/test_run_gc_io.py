"""Regression test for the ``_dump_run_yaml_atomic`` double-close bug.

PRD-FIX-126 P1 finding: the writer wrapped a temp-file ``fd`` with
``os.fdopen(fd, "w")`` inside a ``with`` block (which closes ``fd`` on every
exit path) and then, on TOP of that, ran an unconditional
``finally: os.close(fd)``. Single-threaded that is a suppressed ``EBADF``.
Under threads the freed fd number can be handed by the OS to an unrelated
file opened by another thread in the race window between the ``with``
block's close and the writer's own ``finally`` close -- the second close
then closes THAT thread's descriptor out from under it, corrupting whatever
it was writing.

This test forces that exact race window deterministically (via a patched
``os.replace``, the last call inside the writer's try-block, which runs
strictly AFTER ``os.fdopen``'s ``with`` block has already closed the temp
fd) and asserts the concurrent thread's file survives untouched.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path

import pytest

from trw_mcp.state import _run_gc_io


def test_dump_run_yaml_atomic_does_not_clobber_recycled_fd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A concurrent thread's freshly-opened fd must survive a run.yaml dump.

    Fails before the fix: the pre-fix writer's blanket
    ``finally: os.close(fd)`` closes the recycled fd number a second time,
    so the victim thread's post-race write raises ``OSError`` (EBADF) and
    its file never receives the second write.
    """
    run_yaml_path = tmp_path / "run.yaml"
    victim_path = tmp_path / "victim.txt"

    real_replace = os.replace
    fd_freed = threading.Event()
    victim_done = threading.Event()
    victim_fd_box: list[int] = []
    victim_open_error_box: list[BaseException] = []

    def _patched_replace(src: object, dst: object) -> None:
        # By the time os.replace runs, os.fdopen's `with` block has ALREADY
        # closed the mkstemp fd -- the OS is free to hand that fd number to
        # anyone. Hand control to the victim thread exactly here, and wait
        # for it to open (and write once to) its own file before letting
        # _dump_run_yaml_atomic proceed into whatever runs after replace.
        real_replace(src, dst)  # type: ignore[arg-type]
        fd_freed.set()
        victim_done.wait(timeout=5)

    def _victim() -> None:
        fd_freed.wait(timeout=5)
        try:
            fd = os.open(str(victim_path), os.O_CREAT | os.O_WRONLY, 0o600)
            victim_fd_box.append(fd)
            os.write(fd, b"victim-data")
        except OSError as exc:  # pragma: no cover - only on unexpected failure
            victim_open_error_box.append(exc)
        finally:
            victim_done.set()

    monkeypatch.setattr(_run_gc_io.os, "replace", _patched_replace)

    victim_thread = threading.Thread(target=_victim)
    victim_thread.start()
    _run_gc_io._dump_run_yaml_atomic(run_yaml_path, {"status": "active"})
    victim_thread.join(timeout=5)

    assert not victim_open_error_box, f"victim thread failed to open its file: {victim_open_error_box}"
    assert victim_fd_box, "victim thread never recorded its fd"
    victim_fd = victim_fd_box[0]

    # The load-bearing assertion: the victim's fd must still be open and
    # usable after _dump_run_yaml_atomic returns. A second, erroneous
    # os.close(fd) in the writer would close THIS descriptor if the OS
    # recycled the freed fd number to it, and this write would raise
    # OSError(EBADF) instead of succeeding.
    try:
        os.write(victim_fd, b"-still-open")
        os.fsync(victim_fd)
    finally:
        os.close(victim_fd)

    assert victim_path.read_bytes() == b"victim-data-still-open"
    assert run_yaml_path.exists()
