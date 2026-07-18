"""Typed fail-open degradation collector for the ceremony hot path.

Belongs to the ``ceremony.py`` facade. Re-exported there for back-compat.

The ceremony hot path (``trw_session_start`` / ``trw_deliver``) is dotted with
``except Exception: logger.debug(...); pass`` swallows — each one keeps a
non-fatal failure from blocking the session, but leaves NO trace in the tool
response the agent actually sees. This module replaces those ad-hoc swallows
with ONE typed, logged, counted mechanism:

- :class:`DegradationCollector` accumulates :class:`Degradation` records and
  merges them into a result mapping under ``degradations`` / ``degraded_steps``.
- :func:`record_into` is the one-shot convenience for the many step functions
  that already hold the ``results`` dict but not a long-lived collector.

INVARIANT (mcp-x-failopen): recording a degradation NEVER changes WHEN the
session survives. ``success`` remains governed solely by the ``errors`` list;
degradations are pure observability. A step that silently swallowed before
still does not fail the session — it is now also enumerable in the payload.
"""

from __future__ import annotations

from collections.abc import MutableMapping
from typing import Literal

import structlog

from trw_mcp.models.typed_dicts import Degradation

logger = structlog.get_logger(__name__)

Severity = Literal["info", "warn"]


class DegradationCollector:
    """Accumulates typed fail-open degradations for one ceremony call.

    Not thread-safe by design — one collector is created per
    ``trw_session_start`` / ``trw_deliver`` invocation and lives entirely on
    that call's stack, so there is no cross-session sharing to guard.
    """

    def __init__(self) -> None:
        self._items: list[Degradation] = []

    def record(self, step: str, exc: BaseException, *, severity: Severity = "warn") -> None:
        """Record one swallowed failure — logs it AND appends a typed entry.

        ``severity='warn'`` for expected fail-open swallows; ``'info'`` for the
        previously-silent control-flow fallbacks whose only purpose is to stop
        being invisible. Emits a ``{step}_degraded`` structured log at the
        matching level (with ``exc_info`` for the traceback) so nothing is lost
        even for callers that never read the payload.
        """
        entry: Degradation = {
            "step": step,
            "error_class": type(exc).__name__,
            "message": str(exc),
            "severity": severity,
        }
        self._items.append(entry)
        emit = logger.warning if severity == "warn" else logger.info
        emit(f"{step}_degraded", step=step, error_class=entry["error_class"], exc_info=True)

    @property
    def items(self) -> list[Degradation]:
        """A copy of the recorded degradations (defensive — callers cannot mutate)."""
        return list(self._items)

    def __len__(self) -> int:
        return len(self._items)

    def into(self, results: MutableMapping[str, object]) -> None:
        """Merge accumulated degradations into ``results`` in-place.

        No-op when nothing was recorded, so a fully-clean session/deliver payload
        is byte-identical to the pre-migration shape (no empty ``degradations``
        key). Merges with any degradations already present (multiple collectors
        or step functions can contribute to the same result), and refreshes the
        ``degraded_steps`` count to the merged total.
        """
        if not self._items:
            return
        existing = results.get("degradations")
        merged: list[Degradation] = list(existing) if isinstance(existing, list) else []
        merged.extend(self._items)
        results["degradations"] = merged
        results["degraded_steps"] = len(merged)


def record_into(
    results: MutableMapping[str, object],
    step: str,
    exc: BaseException,
    *,
    severity: Severity = "warn",
) -> None:
    """Record a single degradation directly into a ``results`` mapping.

    Convenience for step functions that hold the result dict but not a
    long-lived collector. Equivalent to constructing a
    :class:`DegradationCollector`, recording one entry, and merging it in — so
    it composes with a driver-level collector via the merge in
    :meth:`DegradationCollector.into`.
    """
    collector = DegradationCollector()
    collector.record(step, exc, severity=severity)
    collector.into(results)
