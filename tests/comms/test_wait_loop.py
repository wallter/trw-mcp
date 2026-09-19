"""Fake-clock unit proof of the FR11 bounded wait loop (comms/_wait.py).

Pure: no SQLite, no config, no real sleep. ``run_bounded_wait`` takes an
injectable ``clock``/``sleep`` pair, which is exactly the seam this file drives
so every case here is deterministic and instant.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from typing import Any

import anyio
import pytest

from trw_mcp.comms._wait import check_cancelled_cooperatively, run_bounded_wait

_EMPTY_PAGE: dict[str, Any] = {"status": "ok", "delivery": "pull_only", "items": [], "next_cursor": None}
_ITEMS_PAGE: dict[str, Any] = {
    "status": "ok",
    "delivery": "pull_only",
    "items": [{"message_id": "m"}],
    "next_cursor": None,
}


class _Boom(Exception):
    """Distinct from any exception ``run_bounded_wait`` or AnyIO might raise on its own."""


class FakeClock:
    """A monotonic clock the test fully controls; ``sleep`` advances it by exactly the requested amount."""

    def __init__(self, start: float = 0.0) -> None:
        self.now = start
        self.sleeps: list[float] = []

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


class OversleepClock(FakeClock):
    """Simulates scheduler jitter: every sleep advances the clock past what was requested."""

    def __init__(self, start: float = 0.0, *, jitter: float = 0.5) -> None:
        super().__init__(start)
        self.jitter = jitter

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds + self.jitter


def _counting_attempt(
    page: dict[str, Any], *, empties_before_items: int = 10**9
) -> tuple[Callable[[], tuple[dict[str, Any], bool]], list[int]]:
    """An attempt that returns empty pages, then *page*, and records its call count."""
    calls = [0]

    def attempt() -> tuple[dict[str, Any], bool]:
        calls[0] += 1
        if calls[0] > empties_before_items:
            return page, False
        return dict(_EMPTY_PAGE), True

    return attempt, calls


def _never_called_attempt(calls: list[int]) -> Callable[[], tuple[dict[str, Any], bool]]:
    def attempt() -> tuple[dict[str, Any], bool]:
        calls[0] += 1
        raise AssertionError("attempt must not be called")

    return attempt


def _noop_check() -> None:
    return None


def test_matches_recorded_smoke_trace_three_attempts_and_final_partial_sleep() -> None:
    """Regression pin for the implementer's own fake-clock trace (plan §4 progress record)."""
    clock = FakeClock()
    attempt, calls = _counting_attempt(_ITEMS_PAGE, empties_before_items=10**9)
    result = run_bounded_wait(
        attempt,
        last_empty=_EMPTY_PAGE,
        deadline=3.05,
        interval_seconds=lambda: 1.0,
        clock=clock.clock,
        sleep=clock.sleep,
        check_cancelled=_noop_check,
    )
    assert calls[0] == 3
    assert clock.sleeps == pytest.approx([1.0, 1.0, 1.0, 0.05])
    assert result == _EMPTY_PAGE


@pytest.mark.parametrize(
    ("deadline", "interval"),
    [(3.05, 1.0), (5.0, 2.0), (10.0, 3.0), (0.5, 1.0), (7.0, 1.5)],
)
def test_attempts_never_exceed_ceil_n_over_interval_plus_one(deadline: float, interval: float) -> None:
    clock = FakeClock()
    attempt, calls = _counting_attempt(_ITEMS_PAGE)
    run_bounded_wait(
        attempt,
        last_empty=_EMPTY_PAGE,
        deadline=deadline,
        interval_seconds=lambda: interval,
        clock=clock.clock,
        sleep=clock.sleep,
        check_cancelled=_noop_check,
    )
    bound = math.ceil(deadline / interval) + 1
    assert calls[0] <= bound


def test_deadline_already_passed_makes_no_attempt_and_no_sleep() -> None:
    clock = FakeClock(start=5.0)
    calls: list[int] = []
    result = run_bounded_wait(
        _never_called_attempt(calls),
        last_empty=_EMPTY_PAGE,
        deadline=1.0,
        interval_seconds=lambda: 1.0,
        clock=clock.clock,
        sleep=clock.sleep,
        check_cancelled=_noop_check,
    )
    assert result == _EMPTY_PAGE
    assert calls == []
    assert clock.sleeps == []


def test_sleep_ending_exactly_at_deadline_returns_without_a_further_attempt() -> None:
    clock = FakeClock()
    calls: list[int] = []
    result = run_bounded_wait(
        _never_called_attempt(calls),
        last_empty=_EMPTY_PAGE,
        deadline=1.0,
        interval_seconds=lambda: 1.0,
        clock=clock.clock,
        sleep=clock.sleep,
        check_cancelled=_noop_check,
    )
    assert result == _EMPTY_PAGE
    assert calls == []
    assert clock.sleeps == [1.0]
    assert clock.now == 1.0


def test_oversleep_past_deadline_returns_without_a_further_attempt() -> None:
    clock = OversleepClock(jitter=0.5)
    calls: list[int] = []
    result = run_bounded_wait(
        _never_called_attempt(calls),
        last_empty=_EMPTY_PAGE,
        deadline=1.0,
        interval_seconds=lambda: 2.0,
        clock=clock.clock,
        sleep=clock.sleep,
        check_cancelled=_noop_check,
    )
    assert result == _EMPTY_PAGE
    assert calls == []
    assert clock.now > 1.0


def test_sleep_duration_never_exceeds_the_remaining_time() -> None:
    """Interval (1s) is larger than remaining (0.3s): the sleep is clamped to remaining."""
    clock = FakeClock()
    calls: list[int] = []
    run_bounded_wait(
        _never_called_attempt(calls),
        last_empty=_EMPTY_PAGE,
        deadline=0.3,
        interval_seconds=lambda: 1.0,
        clock=clock.clock,
        sleep=clock.sleep,
        check_cancelled=_noop_check,
    )
    assert clock.sleeps == [0.3]


def test_non_empty_page_stops_the_loop_immediately() -> None:
    clock = FakeClock()
    attempt, calls = _counting_attempt(_ITEMS_PAGE, empties_before_items=0)
    result = run_bounded_wait(
        attempt,
        last_empty=_EMPTY_PAGE,
        deadline=100.0,
        interval_seconds=lambda: 1.0,
        clock=clock.clock,
        sleep=clock.sleep,
        check_cancelled=_noop_check,
    )
    assert calls[0] == 1
    assert result == _ITEMS_PAGE


def test_cancellation_at_the_first_check_point_propagates_before_any_sleep_or_attempt() -> None:
    """First check point: before computing remaining / before the sleep."""
    clock = FakeClock()
    calls: list[int] = []

    def check_cancelled() -> None:
        raise _Boom("cancelled before sleeping")

    with pytest.raises(_Boom):
        run_bounded_wait(
            _never_called_attempt(calls),
            last_empty=_EMPTY_PAGE,
            deadline=100.0,
            interval_seconds=lambda: 1.0,
            clock=clock.clock,
            sleep=clock.sleep,
            check_cancelled=check_cancelled,
        )
    assert calls == []
    assert clock.sleeps == []


def test_cancellation_at_the_second_check_point_propagates_after_sleep_before_attempt() -> None:
    """Second check point: after the sleep, before the retry attempt."""
    clock = FakeClock()
    calls: list[int] = []
    seen = [0]

    def check_cancelled() -> None:
        seen[0] += 1
        if seen[0] == 2:
            raise _Boom("cancelled after sleeping")

    with pytest.raises(_Boom):
        run_bounded_wait(
            _never_called_attempt(calls),
            last_empty=_EMPTY_PAGE,
            deadline=100.0,
            interval_seconds=lambda: 1.0,
            clock=clock.clock,
            sleep=clock.sleep,
            check_cancelled=check_cancelled,
        )
    assert calls == []
    assert clock.sleeps == [1.0]


def test_cancellation_at_the_third_check_point_propagates_after_a_committed_attempt() -> None:
    """Third check point: after ``attempt()`` returns, before its result is used again."""
    clock = FakeClock()
    attempt, calls = _counting_attempt(_ITEMS_PAGE, empties_before_items=10**9)
    seen = [0]

    def check_cancelled() -> None:
        seen[0] += 1
        if seen[0] == 3:  # entry check (1), post-sleep check (2), post-attempt check (3)
            raise _Boom("cancelled after the attempt committed")

    with pytest.raises(_Boom):
        run_bounded_wait(
            attempt,
            last_empty=_EMPTY_PAGE,
            deadline=100.0,
            interval_seconds=lambda: 1.0,
            clock=clock.clock,
            sleep=clock.sleep,
            check_cancelled=check_cancelled,
        )
    # The attempt already ran and is not undone by the later cancellation.
    assert calls[0] == 1


def test_check_cancelled_cooperatively_swallows_only_no_event_loop_error() -> None:
    """A direct synchronous caller (this test) has no AnyIO worker-thread token."""
    check_cancelled_cooperatively()  # must not raise


def test_check_cancelled_cooperatively_propagates_a_plain_runtime_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Only ``anyio.NoEventLoopError`` is swallowed; every other exception propagates.

    Guards against a widened ``except Exception`` accidentally re-appearing: a
    plain ``RuntimeError`` from the hook (distinct from ``NoEventLoopError``,
    which is itself a ``RuntimeError`` subclass) must reach the caller.
    """
    import anyio.from_thread as from_thread

    def boom() -> None:
        raise RuntimeError("not a NoEventLoopError")

    monkeypatch.setattr(from_thread, "check_cancelled", boom)
    with pytest.raises(RuntimeError, match="not a NoEventLoopError"):
        check_cancelled_cooperatively()


def test_check_cancelled_cooperatively_still_swallows_no_event_loop_error_when_monkeypatched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Positive control for the test above: the ONE exception it must swallow still is."""
    import anyio.from_thread as from_thread

    def raise_no_event_loop() -> None:
        raise anyio.NoEventLoopError("no worker thread token")

    monkeypatch.setattr(from_thread, "check_cancelled", raise_no_event_loop)
    check_cancelled_cooperatively()  # must not raise


def test_run_bounded_wait_propagates_a_plain_runtime_error_from_check_cancelled() -> None:
    """Lead requirement (board seq 145): production hard-imports ``anyio.NoEventLoopError``
    with no ``RuntimeError`` fallback, so a plain ``RuntimeError`` from the injected
    ``check_cancelled`` hook must propagate out of ``run_bounded_wait`` exactly like any
    other unrelated exception — it is never mistaken for the one swallowed case.
    """
    clock = FakeClock()
    calls: list[int] = []

    def check_cancelled() -> None:
        raise RuntimeError("unrelated runtime error")

    with pytest.raises(RuntimeError, match="unrelated runtime error"):
        run_bounded_wait(
            _never_called_attempt(calls),
            last_empty=_EMPTY_PAGE,
            deadline=100.0,
            interval_seconds=lambda: 1.0,
            clock=clock.clock,
            sleep=clock.sleep,
            check_cancelled=check_cancelled,
        )
    assert calls == []
