"""PRD-FIX-153 FR02 (second criterion) and NFR04: with no parent to lose, the watch is not started.

A server that is already orphaned (parent pid 1) has nothing to watch, and a platform that is not POSIX has no
reparenting to detect. Both must answer ``False`` and start no thread: a watch started there would compare
against pid 1 for ever, or end a healthy server. The exit-on-parent-death path itself runs against a real
subprocess in ``test_stdio_server_lifecycle.py``.
"""

from __future__ import annotations

import os
import signal
import threading

import pytest

from trw_mcp.server import _parent_watch


def _watch_threads() -> list[threading.Thread]:
    return [thread for thread in threading.enumerate() if thread.name == "trw-parent-watch"]


@pytest.mark.parametrize("parent", [1, 0])
def test_an_already_orphaned_server_starts_no_watch(monkeypatch: pytest.MonkeyPatch, parent: int) -> None:
    monkeypatch.setattr(os, "getppid", lambda: parent)

    assert _parent_watch.start_parent_watch() is False
    assert _watch_threads() == []


def test_a_platform_that_is_not_posix_starts_no_watch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(os, "getppid", lambda: 4242)
    monkeypatch.setattr(os, "name", "nt")

    assert _parent_watch.start_parent_watch() is False
    assert _watch_threads() == []


def test_a_server_with_a_parent_is_watched_quiet_while_it_lives_and_ended_when_it_goes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-vacuity for the cases above: the same call does start a thread when there is a parent. It does nothing
    while the parent pid is unchanged, and when the pid changes it interrupts the server and then exits. The
    thread ends inside this test, so no watch outlives it into another test."""
    if os.name != "posix":
        pytest.skip("the watch exists on POSIX only")
    parent = {"pid": 4242}
    calls: list[tuple[object, ...]] = []
    monkeypatch.setattr(os, "getppid", lambda: parent["pid"])
    monkeypatch.setattr(os, "kill", lambda pid, sig: calls.append(("kill", pid, sig)))
    monkeypatch.setattr(os, "_exit", lambda code: calls.append(("exit", code)))

    assert _parent_watch.start_parent_watch(interval_s=0.01, grace_s=0.0) is True

    (thread,) = _watch_threads()
    thread.join(timeout=0.2)
    assert thread.is_alive() and calls == []

    parent["pid"] = 1  # the launching client is gone and the server was reparented
    thread.join(timeout=10)
    assert not thread.is_alive()
    assert calls == [("kill", os.getpid(), signal.SIGINT), ("exit", 0)]
