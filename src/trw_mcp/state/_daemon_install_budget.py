"""One install's shared budget for waiting on the memory daemon (B71-118). Belongs to :mod:`trw_mcp.state._daemon_store`.

Split out of ``_daemon_store.py`` to keep that module under the 350 effective-LOC gate. The budget is passed in by the caller, which reads
``_daemon_store.INSTALL_DAEMON_BUDGET_S`` at call time, so a test that patches that constant still takes effect.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import time
from collections.abc import Coroutine
from typing import Any

import structlog

from trw_mcp.state._store_selection import DaemonBudgetExhaustedError

logger = structlog.get_logger(__name__)

__all__ = ["wait_within_install_budget"]


def wait_within_install_budget(
    coro: Coroutine[Any, Any, Any], loop: asyncio.AbstractEventLoop, shared: dict[str, Any], budget_s: float
) -> Any:
    """Wait on *coro* for what is left of the install's daemon budget (*budget_s*), then give up on it."""
    waited = float(shared.get("daemon_waited_s", 0.0))
    remaining = budget_s - waited
    exhausted = DaemonBudgetExhaustedError(
        f"the memory daemon did not answer within the install's {budget_s:g}s budget; "
        "this install continues without recalled learnings. Run trw-mcp doctor."
    )
    if remaining <= 0:
        coro.close()
        raise exhausted
    future = asyncio.run_coroutine_threadsafe(coro, loop)
    started = time.monotonic()
    try:
        return future.result(timeout=remaining)
    except concurrent.futures.TimeoutError:
        future.cancel()  # the request is abandoned on the daemon-call loop; nothing waits on it
        shared["daemon_waited_s"] = budget_s
        logger.warning("install_daemon_budget_exhausted", budget_s=budget_s)
        raise exhausted from None
    finally:
        shared["daemon_waited_s"] = max(float(shared.get("daemon_waited_s", 0.0)), waited + time.monotonic() - started)
