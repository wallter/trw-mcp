"""Bounded same-owner inbox wait loop (PRD-CORE-274 Amendment 01, FR11).

Belongs to the ``comms/__init__.py`` facade. Pure: no SQLite, no config, no
identity. The facade supplies one ``attempt`` callable that performs a COMPLETE
ordinary inbox operation — its own transaction, opened and closed inside the
call — and reports whether the result was an empty fetch page. This module only
decides when to call it again and when to stop; between calls nothing is held.

Guarantees, and their limits:

* No attempt begins after ``deadline`` (a monotonic instant). A sleep never
  exceeds the remaining time. Total elapsed time is NOT bounded here: an
  attempt that blocks inside SQLite runs to the store's own limits.
* Cancellation is cooperative: ``check_cancelled`` runs before every sleep,
  after every sleep, and after every attempt. While sleeping, a cancellation is
  noticed within one interval; during a blocked attempt, only when that attempt
  returns — and then before its result is used.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

# trw:intentional Hard import, no fallback: the reviewed contract swallows ONLY this
# exception. An AnyIO without it must fail loudly at import, never widen the catch.
from anyio import NoEventLoopError

#: One complete inbox operation. Returns ``(payload, retry)``; ``retry`` is
#: True only for an empty fetch page that the caller asked to wait on.
Attempt = Callable[[], tuple[dict[str, Any], bool]]


def check_cancelled_cooperatively() -> None:
    """Raise the host task's cancellation inside an AnyIO worker thread.

    A direct synchronous caller (tests, a script) has no host task; that one
    condition is swallowed. Nothing else is: a real cancellation propagates.
    """
    from anyio.from_thread import check_cancelled

    try:
        check_cancelled()
    except NoEventLoopError:
        # Only this condition is ignored; other errors and cancellation propagate.
        # trw-fail-silent-allow: direct sync callers have no AnyIO host task.
        return


def run_bounded_wait(
    attempt: Attempt,
    *,
    last_empty: dict[str, Any],
    deadline: float,
    interval_seconds: Callable[[], float],
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    check_cancelled: Callable[[], None] = check_cancelled_cooperatively,
) -> dict[str, Any]:
    """Repeat ``attempt`` until it returns a non-retry result or the deadline passes.

    ``last_empty`` is the page the first ordinary attempt already produced; it
    is what a deadline returns, so the caller never receives an invented result.
    """
    payload = last_empty
    while True:
        check_cancelled()
        remaining = deadline - clock()
        if remaining <= 0:
            return payload
        sleep(min(interval_seconds(), remaining))
        check_cancelled()
        if clock() >= deadline:
            # A sleep that ran to (or over) the deadline must not be followed by an attempt.
            return payload
        payload, retry = attempt()
        # A cancellation that arrived during the attempt is honoured before the
        # result is used or another sleep begins; the attempt itself stays committed.
        check_cancelled()
        if not retry:
            return payload


__all__ = ["Attempt", "check_cancelled_cooperatively", "run_bounded_wait"]
