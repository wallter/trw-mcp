"""The two evaluation points for the WAL-checkpoint trigger (PRD-CORE-248 FR04 clause 2).

Parent facade: :mod:`trw_mcp.state.memory_adapter`. The policy itself lives in
:mod:`trw_mcp.state._wal_triggers`; this module only decides *when* it is asked.

Before this, ``maybe_checkpoint_wal`` had exactly one call site — session-start
auto-maintenance — so a long-lived server that never ran ``trw_session_start``
never checkpointed at all, and one that did had the checkpoint cancelled by
writer pressure anyway. Two evaluation points replace that:

``checkpoint_after_commit``
    Runs after a write commits through the memory adapter. The common case is a
    single ``stat`` that answers "not due" and returns, so a burst of
    ``trw_learn`` calls pays a stat each and nothing else.

``start_wal_checkpoint_sweeper``
    A named daemon thread (``trw-wal-checkpoint``) evaluating every
    ``wal_checkpoint_idle_interval_seconds``, matching the existing
    ``trw-boot-gc`` / ``trw-embed-warmup`` pattern. It exists because an idle
    server has no other periodic task, and an idle server with a stale WAL is
    precisely the state nobody was observing.

Both are fail-open (NFR02): a failure is logged at WARNING and the server keeps
serving. Neither may raise into a tool call or the boot path.
"""

from __future__ import annotations

import threading
from pathlib import Path

import structlog

logger = structlog.get_logger(__name__)

__all__ = [
    "SWEEPER_THREAD_NAME",
    "checkpoint_after_commit",
    "start_wal_checkpoint_sweeper",
]

#: Named so an operator reading a thread dump can attribute a checkpoint to it.
SWEEPER_THREAD_NAME = "trw-wal-checkpoint"

#: Guards against a second sweeper if boot ever runs twice in one process.
_sweeper_lock = threading.Lock()
_sweeper_thread: threading.Thread | None = None


def checkpoint_after_commit(trw_dir: Path) -> None:
    """Evaluate the checkpoint trigger after a write commit; fail-open.

    Cheap by construction: :func:`~trw_mcp.state._wal_triggers.evaluate_wal_trigger`
    opens no SQLite connection, so a not-due evaluation costs one ``stat``.
    """
    try:
        from trw_mcp.models.config import get_config
        from trw_mcp.state._wal_triggers import evaluate_wal_trigger

        if not evaluate_wal_trigger(trw_dir, get_config()).due:
            return
        from trw_mcp.state.memory_adapter import maybe_checkpoint_wal

        maybe_checkpoint_wal(trw_dir)
    except Exception:  # justified: fail-open, a store must never fail on maintenance
        logger.warning("wal_checkpoint_after_commit_failed", exc_info=True)


def _sweep_forever(trw_dir: Path, stop: threading.Event) -> None:
    """Evaluate the trigger every interval until *stop* is set."""
    from trw_mcp.models.config import get_config

    while not stop.is_set():
        try:
            interval = float(get_config().wal_checkpoint_idle_interval_seconds)
        except Exception:  # justified: fail-open, an unreadable config must not kill the thread
            logger.warning("wal_sweeper_config_failed", exc_info=True)
            interval = 60.0
        if stop.wait(interval):
            return
        checkpoint_after_commit(trw_dir)


def start_wal_checkpoint_sweeper(trw_dir: Path) -> threading.Thread | None:
    """Start the idle checkpoint sweeper on a named daemon thread; fail-open.

    Returns the thread, or ``None`` when one is already running. Daemon, so it
    never holds process exit open, and it takes no lock while idle — a wakeup
    that finds no trigger due does not touch the backend at all (NFR01).
    """
    global _sweeper_thread
    with _sweeper_lock:
        if _sweeper_thread is not None and _sweeper_thread.is_alive():
            return None
        stop = threading.Event()

        def _run() -> None:
            try:
                _sweep_forever(trw_dir, stop)
            except Exception:  # justified: a daemon thread must never leak a traceback to stderr
                logger.warning("wal_sweeper_thread_failed", exc_info=True)

        thread = threading.Thread(target=_run, name=SWEEPER_THREAD_NAME, daemon=True)
        thread.start()
        _sweeper_thread = thread
        logger.debug("wal_sweeper_started", thread=SWEEPER_THREAD_NAME)
        return thread
