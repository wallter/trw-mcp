"""Bounded, content-free foreground timing; immutable context isolates child calls."""

from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass, replace
from time import monotonic
from typing import Literal

Stage = Literal[
    "handler", "preflight", "journal", "migration", "metadata", "active_set_dedup", "anchors", "store", "poststore"
]


@dataclass(frozen=True)
class _Timing:
    stage: Stage
    started: float
    elapsed: tuple[tuple[Stage, float], ...] = ()


_current: ContextVar[_Timing | None] = ContextVar("learn_stage_timing", default=None)


def _close(value: _Timing, now: float) -> _Timing:
    totals = dict(value.elapsed)
    totals[value.stage] = totals.get(value.stage, 0.0) + max(0.0, now - value.started)
    return replace(value, started=now, elapsed=tuple(totals.items()))


def begin(tool_name: str) -> Token[_Timing | None]:
    """Mask parent even for other tools; a nested tool's time is not parent work."""
    parent = _current.get()
    try:
        now = monotonic()
        if parent is not None:
            _current.set(_close(parent, now))
        value = _Timing("handler", now) if tool_name == "trw_learn" else None
    except Exception:  # justified: timing failure must not affect primary operation
        value = None
    return _current.set(value)


def advance(stage: Stage) -> None:
    """Switch exclusive foreground stage; uninstrumented direct calls are no-ops."""
    current = _current.get()
    if current is None:
        return
    try:
        _current.set(replace(_close(current, monotonic()), stage=stage))
    except Exception:  # trw-fail-silent-allow: abandon the MEASUREMENT, not the learning -- timing telemetry must never change whether a learning is recorded, and _current is reset to None so the next stage starts clean rather than reporting a bogus elapsed
        _current.set(None)


def finish() -> dict[str, float] | None:
    """Snapshot handler work, leaving the parent masked during telemetry cleanup."""
    result: dict[str, float] | None = None
    try:
        current = _current.get()
        if current is not None:
            result = {key: round(value * 1000, 2) for key, value in _close(current, monotonic()).elapsed}
    except Exception:  # trw-fail-silent-allow: an ABSENT measurement is preferable to a changed result -- finish() returns None, which callers already treat as "not measured", and the finally block still clears _current so no stale timing survives
        pass
    finally:
        _current.set(None)
    return result


def restore(token: Token[_Timing | None]) -> None:
    """Resume the parent only after the complete nested wrapper has returned."""
    _current.reset(token)
    parent = _current.get()
    if parent is not None:
        try:
            _current.set(replace(parent, started=monotonic()))
        except Exception:  # justified: discard failed measurement only
            _current.set(None)
