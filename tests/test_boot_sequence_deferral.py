"""Tests for boot-GC deferral off the MCP handshake critical path.

Production feedback ``sub_psVs_nUWnLJGvOs3``: ``_boot_sequence`` (stale-pin +
stale-run sweep) previously ran synchronously on the main thread before
``resolve_and_run_transport``, so a large repo's historical-run scan delayed
the MCP ``initialize`` handshake past client connect timeouts.

These tests exercise the ``_start_boot_sequence`` seam directly — no real
FastMCP server is spawned — verifying it runs the sweep in a named daemon
thread by default, runs inline when deferral is disabled, and never lets a
background-thread exception escape.
"""

from __future__ import annotations

import threading
import time
from typing import Any
from unittest.mock import MagicMock

import pytest


def test_boot_gc_deferred_config_default_is_true() -> None:
    from trw_mcp.models.config import TRWConfig

    assert TRWConfig().boot_gc_deferred is True


def test_start_boot_sequence_deferred_runs_in_named_daemon_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from trw_mcp.models.config import TRWConfig
    from trw_mcp.server import _cli

    ran = threading.Event()
    captured: dict[str, Any] = {}

    def _fake_boot(config: Any, log: Any) -> None:
        captured["thread_name"] = threading.current_thread().name
        ran.set()

    monkeypatch.setattr(_cli, "_boot_sequence", _fake_boot)

    thread = _cli._start_boot_sequence(TRWConfig(), MagicMock(), deferred=True)

    assert thread is not None
    assert thread.name == "trw-boot-gc"
    assert thread.daemon is True
    assert ran.wait(timeout=5.0), "deferred boot sequence never ran"
    thread.join(timeout=5.0)
    assert not thread.is_alive()
    # The sweep ran off the caller thread, on the named background thread.
    assert captured["thread_name"] == "trw-boot-gc"


def test_start_boot_sequence_deferred_returns_before_slow_sweep_completes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The whole point of deferral: the caller must NOT block on the sweep.

    A ``thread.join()`` regression (waiting on the background thread before
    returning) would make ``_start_boot_sequence`` take as long as the sweep,
    defeating the fix for ``sub_psVs_nUWnLJGvOs3``. Here the fake boot blocks on
    an Event for ~1s; we assert the caller returns in well under that, then
    release the Event and join so the daemon thread is not left running.
    """
    from trw_mcp.models.config import TRWConfig
    from trw_mcp.server import _cli

    release = threading.Event()
    entered = threading.Event()

    def _slow_boot(config: Any, log: Any) -> None:
        entered.set()
        # Block until the test releases us (bounded so a stuck test still ends).
        release.wait(timeout=5.0)

    monkeypatch.setattr(_cli, "_boot_sequence", _slow_boot)

    start = time.monotonic()
    thread = _cli._start_boot_sequence(TRWConfig(), MagicMock(), deferred=True)
    elapsed = time.monotonic() - start

    try:
        # Caller returned promptly even though the sweep is still blocked.
        assert elapsed < 0.5, f"caller blocked on the sweep for {elapsed:.3f}s"
        assert thread is not None
        # The sweep genuinely started on the background thread and is still running.
        assert entered.wait(timeout=5.0), "background sweep never started"
        assert thread.is_alive()
    finally:
        release.set()
        thread.join(timeout=5.0)
    assert not thread.is_alive()


def test_start_boot_sequence_not_deferred_runs_inline_on_caller_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from trw_mcp.models.config import TRWConfig
    from trw_mcp.server import _cli

    calls: dict[str, Any] = {"n": 0, "thread": None}

    def _fake_boot(config: Any, log: Any) -> None:
        calls["n"] += 1
        calls["thread"] = threading.current_thread().name

    monkeypatch.setattr(_cli, "_boot_sequence", _fake_boot)

    thread = _cli._start_boot_sequence(TRWConfig(), MagicMock(), deferred=False)

    assert thread is None
    assert calls["n"] == 1
    assert calls["thread"] == threading.current_thread().name


def test_start_boot_sequence_deferred_swallows_background_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from trw_mcp.models.config import TRWConfig
    from trw_mcp.server import _cli

    def _boom(config: Any, log: Any) -> None:
        raise RuntimeError("kaboom")

    monkeypatch.setattr(_cli, "_boot_sequence", _boom)

    log = MagicMock()
    thread = _cli._start_boot_sequence(TRWConfig(), log, deferred=True)

    assert thread is not None
    thread.join(timeout=5.0)
    assert not thread.is_alive(), "background thread must terminate, not hang"
    # Exception was logged, never propagated to crash the server.
    assert log.warning.called
    assert log.warning.call_args.args[0] == "boot_gc_thread_failed"
