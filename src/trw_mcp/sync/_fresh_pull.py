"""Fresh enough: a bounded team pull before local recall reads (SHARED-RECALL-LOCAL).

Learnings from the operator's other hosts reach recall only through team sync,
so a recall that runs on a stale pull cursor misses them. When the last pull is
older than ``team_sync_fresh_after_seconds``, :func:`ensure_fresh` runs the sync
cycle's pull half (:func:`trw_mcp.sync._client_cycle.run_one_cycle`) page after
page until the cursor stops moving, because the backend caps one page at
``intel_state_team_learnings_limit``. At most :data:`MAX_PAGES` pages per catch-up.

The caller waits at most *budget_seconds*. The pull runs on its own thread, so a
slow backend leaves recall answering from what is local (``status: timeout``)
while the page in flight finishes; any call that finds it still running answers
``status: in_flight`` at once instead of waiting too. The pull is gated like every pull: ``team_sync_enabled``, a sync
target, and the platform contact switch (checked first here, so a recall with
contact off starts nothing and records nothing).
"""

from __future__ import annotations

import asyncio
import threading
from datetime import datetime, timezone
from pathlib import Path
from time import monotonic
from typing import TYPE_CHECKING

import structlog

from trw_mcp.state._platform_trust import platform_contact_enabled

if TYPE_CHECKING:
    from trw_mcp.models.config import TRWConfig
    from trw_mcp.sync.client import BackendSyncClient

logger = structlog.get_logger(__name__)

__all__ = ["BUDGET_SECONDS", "MAX_PAGES", "ensure_fresh", "finish_inflight", "reset_inflight"]

#: How long a recall waits for the catch-up before it answers from local rows.
BUDGET_SECONDS = 1.5
#: Pages per catch-up; a deeper backlog continues from the cursor on the next call.
MAX_PAGES = 5
#: A one-shot CLI waits this long, at most, for the pull it started (see :func:`finish_inflight`): the thread is a
#: daemon, so without the wait the exit would kill it mid-page while the recorded attempt held off every retry.
EXIT_GRACE_SECONDS = 5.0

_lock = threading.Lock()
#: The catch-up in flight and the slot its answer lands in.
_inflight: tuple[threading.Thread, dict[str, object]] | None = None
#: When this process last tried; suppresses retries when the attempt could not be persisted (offline, read-only).
_last_attempt_monotonic: float | None = None


def finish_inflight() -> bool:
    """Wait (at most :data:`EXIT_GRACE_SECONDS`) for the catch-up this process started; ``True`` when one finished.

    A one-shot CLI calls this before it returns, while the interpreter is fully alive: once shutdown has begun, a
    thread cannot start new asyncio work (name resolution uses the default executor, which refuses new work), so a
    join in an ``atexit`` handler is too late for a real pull. A catch-up that already finished inside the caller's
    budget needs no wait and answers ``False``: its rows were merged before the recall read.
    """
    running = _inflight
    if running is None or not running[0].is_alive():
        return False
    running[0].join(EXIT_GRACE_SECONDS)
    if running[0].is_alive():
        logger.info("sync_fresh_pull_exit_gave_up", waited_seconds=EXIT_GRACE_SECONDS)
        return False
    return True


def reset_inflight() -> None:
    """Forget a catch-up in flight (tests)."""
    global _inflight, _last_attempt_monotonic
    with _lock:
        _inflight = None
        _last_attempt_monotonic = None


def _client(config: TRWConfig, trw_dir: Path) -> BackendSyncClient:
    from trw_mcp.sync.client import BackendSyncClient

    return BackendSyncClient(config=config, trw_dir=trw_dir)


def _read_pull_state(trw_dir: Path) -> tuple[float | None, bool]:
    """``(seconds since the last pull attempt, a capped backlog is waiting)``, read once.

    The age is ``None`` when this checkout never attempted a pull. A recall's own attempt
    counts, failed or not (``last_pull_attempt_at`` is stamped before any network work), so
    during an outage each recall does not pay the budget again. ``last_error_at`` does NOT
    count: it is written by whichever process last failed a cycle, and one aborted by a
    short-lived process (an installer's smoke start) would silence a new host's first
    catch-up for the whole threshold.
    """
    from trw_mcp.sync.coordinator import SyncCoordinator

    state = SyncCoordinator(trw_dir)._read_state()
    stamps: list[datetime] = []
    for key in ("last_pull_at", "last_pull_attempt_at"):
        raw = state.get(key)
        try:
            stamp = datetime.fromisoformat(raw) if isinstance(raw, str) and raw else None
        except ValueError:
            stamp = None
        if stamp is not None:
            stamps.append(stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc))
    age = (datetime.now(tz=timezone.utc) - max(stamps)).total_seconds() if stamps else None
    return age, state.get("fresh_pull_backlog_pending") is True


def _update_state(trw_dir: Path, **fields: object) -> None:
    from trw_mcp.sync.coordinator import SyncCoordinator

    coordinator = SyncCoordinator(trw_dir)
    with coordinator.acquire_sync_lock() as acquired:
        if acquired:
            state = coordinator._read_state()
            state.update(fields)
            coordinator._write_state(state)


def _note_attempt(trw_dir: Path) -> None:
    """Record the attempt before any network work, in process and (best effort) on disk."""
    global _last_attempt_monotonic
    _last_attempt_monotonic = monotonic()
    try:
        _update_state(
            trw_dir, last_pull_attempt_at=datetime.now(tz=timezone.utc).isoformat(), fresh_pull_backlog_pending=False
        )
    except Exception:  # justified: the in-process timestamp still suppresses repeated offline attempts
        logger.warning("sync_fresh_pull_attempt_record_failed", outcome="fail_open", exc_info=True)


def _failed(event: str, exc: Exception) -> dict[str, object]:
    """Fail open: recall answers from local rows."""
    logger.warning(event, outcome="fail_open", exc_info=True)
    return {"status": "failed", "reason": type(exc).__name__, "pages": 0, "caught_up": False}


async def _catch_up(client: BackendSyncClient) -> dict[str, object]:
    pages = 0
    for _ in range(MAX_PAGES):
        before = client._coordinator.get_last_pull_seq()
        outcome = await client._run_one_cycle(force=True, push=False, pull=True)
        if outcome != "ok":
            return {"status": outcome, "pages": pages, "caught_up": False}
        pages += 1
        if client._coordinator.get_last_pull_seq() <= before:
            return {"status": "ok", "pages": pages, "caught_up": True}
    return {"status": "ok", "pages": pages, "caught_up": False}


def _run(client: BackendSyncClient, slot: dict[str, object]) -> None:
    try:
        result = asyncio.run(_catch_up(client))
    except Exception as exc:  # justified: a fresh pull is best-effort; recall answers from local rows
        result = _failed("sync_fresh_pull_failed", exc)
    slot.update(result)
    try:
        # A capped catch-up resumes on the very next call instead of waiting out the threshold.
        _update_state(
            client._trw_dir,
            fresh_pull_backlog_pending=result.get("status") == "ok" and result.get("caught_up") is False,
        )
    except Exception:
        logger.warning("sync_fresh_pull_backlog_record_failed", outcome="fail_open", exc_info=True)


def _timeout_answer(budget_seconds: float, *, continuing: bool) -> dict[str, object]:
    budget_ms = int(max(budget_seconds, 0.0) * 1000)
    logger.info("sync_fresh_pull_timeout", budget_ms=budget_ms)
    detail = (
        "the team pull did not finish in time; recall used local rows and the pull continues"
        if continuing
        else "the team pull was skipped because setup did not finish in time; recall used local rows"
    )
    return {"status": "timeout", "budget_ms": budget_ms, "detail": detail}


def _start(
    trw_dir: Path, config: TRWConfig, threshold: int, deadline: float, budget_seconds: float
) -> tuple[threading.Thread, dict[str, object]] | dict[str, object] | None:
    """Start a catch-up when one is due; an answer when none is, ``None`` when nothing is due. Holds ``_lock``."""
    if _last_attempt_monotonic is not None and monotonic() - _last_attempt_monotonic < threshold:
        return None
    try:
        age, pending = _read_pull_state(trw_dir)
    except Exception as exc:  # justified: recall answers from local rows if sync state is unreadable
        _note_attempt(trw_dir)
        return _failed("sync_fresh_pull_state_failed", exc)
    if not pending and age is not None and age < threshold:
        return None
    if deadline - monotonic() <= 0:
        return _timeout_answer(budget_seconds, continuing=False)

    _note_attempt(trw_dir)
    if deadline - monotonic() <= 0:
        return _timeout_answer(budget_seconds, continuing=False)
    try:
        client = _client(config, trw_dir)
        if not client._targets:
            return None
    except Exception as exc:  # justified: a fresh pull is best-effort; recall answers from local rows
        return _failed("sync_fresh_pull_client_failed", exc)
    if deadline - monotonic() <= 0:
        return _timeout_answer(budget_seconds, continuing=False)
    slot: dict[str, object] = {}
    thread = threading.Thread(target=_run, args=(client, slot), name="trw-fresh-pull", daemon=True)
    try:
        thread.start()
    except Exception as exc:  # justified: a fresh pull is best-effort; recall answers from local rows
        return _failed("sync_fresh_pull_thread_failed", exc)
    return thread, slot


def ensure_fresh(
    trw_dir: Path, config: TRWConfig, *, budget_seconds: float = BUDGET_SECONDS
) -> dict[str, object] | None:
    """Catch the team pull up when it is stale; ``None`` when nothing was asked.

    Returns the catch-up's ``{"status", "pages", "caught_up"}``, or
    ``{"status": "timeout", ...}`` when it outlived *budget_seconds*.
    """
    global _inflight
    budget_seconds = max(budget_seconds, 0.0)
    deadline = monotonic() + budget_seconds
    threshold = int(getattr(config, "team_sync_fresh_after_seconds", 0))
    if not getattr(config, "team_sync_enabled", False) or threshold <= 0:
        return None
    if not platform_contact_enabled(trw_dir):
        return None  # no pull can happen: starting one would only record a false failure and an attempt

    if not _lock.acquire(timeout=max(deadline - monotonic(), 0.0)):
        return _timeout_answer(budget_seconds, continuing=False)
    try:
        running = _inflight
        if running is not None and running[0].is_alive():
            return {"status": "in_flight"}  # only the call that starts a catch-up waits for it
        started = _start(trw_dir, config, threshold, deadline, budget_seconds)
        if not isinstance(started, tuple):
            _inflight = None
            return started
        running = _inflight = started
    finally:
        _lock.release()

    thread, slot = running
    thread.join(max(deadline - monotonic(), 0.0))
    if thread.is_alive():
        return _timeout_answer(budget_seconds, continuing=True)
    if _lock.acquire(timeout=max(deadline - monotonic(), 0.0)):
        try:
            if _inflight is running:
                _inflight = None
        finally:
            _lock.release()
    return dict(slot)
