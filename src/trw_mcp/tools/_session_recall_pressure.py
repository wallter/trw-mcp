"""Writer-pressure seam for the session-start recall step (PRD-CORE-257-FR09).

Belongs to the ``_session_recall_helpers.py`` facade, which re-exports every
public name here so existing imports and monkeypatch targets keep resolving.
Extracted because the parent had 22 effective-LOC of headroom against the 350
gate and the typed surface-tracking result costs more than that.

The module owns three things:

* the ONE census the recall step takes (FR01 — this step used to take three,
  two of them differently calibrated);
* :func:`record_session_start_surfaces`, whose return value now says whether it
  actually wrote. It used to return the same ``unique_ids`` list whether it
  recorded or skipped, and the caller then wrote a recall receipt naming ids
  that were never recorded — a receipt for work that did not happen;
* the itemised deferral advisory. One ``side_effects_deferred`` block used to
  hide five distinct losses behind one generic key.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import structlog

from trw_mcp.models.config import TRWConfig
from trw_mcp.state.memory_pressure import WriterCensus, take_writer_census

logger = structlog.get_logger(__name__)

DEFERRABLE_SIDE_EFFECTS: tuple[str, ...] = (
    "propensity_log",
    "session_counts",
    "access_tracking",
    "surface_events",
    "recall_receipt",
)
"""The effects writer pressure may skip, named individually.

The injected-ids dedup write is deliberately NOT here: it writes a bounded,
de-duplicated text file under the context directory and opens no SQLite
connection, so pressure is not a reason to skip it — skipping it costs
correctness (hook re-injection of learnings this session already surfaced) to
save nothing.
"""


@dataclass(frozen=True, slots=True)
class SurfaceTrackingResult:
    """What ``record_session_start_surfaces`` actually did."""

    ids: list[str]
    recorded: bool
    deferred_effects: tuple[str, ...]


def session_start_census(config: TRWConfig, trw_dir: Path) -> WriterCensus:
    """Measure writer pressure once for the whole recall step (FR01).

    Fail-open: an unreadable registry yields an ``unreadable`` census with zero
    counts, which no caller may read as a healthy zero.
    """
    try:
        return take_writer_census(
            trw_dir,
            threshold=config.session_start_writer_pressure_threshold,
            pin_ttl_hours=config.pin_ttl_hours,
        )
    except Exception:  # justified: pressure detection is advisory and fail-open
        logger.warning("session_start_response_pressure_check_failed", exc_info=True)
        return WriterCensus(
            writer_pids=(),
            writer_count=0,
            peer_writer_count=0,
            threshold=config.session_start_writer_pressure_threshold,
            under_pressure=False,
            census_state="unreadable",
            identity_state="unverified",
            heartbeat_state="unavailable",
        )


def session_start_defers(config: TRWConfig, census: WriterCensus) -> bool:
    """Return whether measured pressure should actually defer optional work."""

    return config.session_start_defer_under_writer_pressure and census.under_pressure


def dedupe_learning_ids(learning_ids: list[str]) -> list[str]:
    """Preserve order while removing duplicate/empty learning IDs."""

    seen: set[str] = set()
    unique_ids: list[str] = []
    for learning_id in learning_ids:
        if not learning_id or learning_id in seen:
            continue
        seen.add(learning_id)
        unique_ids.append(learning_id)
    return unique_ids


def _log_session_start_surfaces(trw_dir: Path, learning_ids: list[str]) -> None:
    """Best-effort session-start surface logging with structured observability."""

    try:
        from trw_mcp.state._session_id import resolve_effective_session_id
        from trw_mcp.state.surface_tracking import log_surface_event

        sid = resolve_effective_session_id(trw_dir)
        for learning_id in learning_ids:
            log_surface_event(
                trw_dir,
                learning_id=learning_id,
                surface_type="session_start",
                session_id=sid,
            )
    except (ImportError, OSError, RuntimeError, ValueError, TypeError):
        logger.warning(
            "session_start_surface_log_failed",
            op="session_recall",
            outcome="fail_open",
            exc_info=True,
        )


def record_session_start_surfaces(
    trw_dir: Path, learning_ids: list[str], *, defer: bool = False
) -> SurfaceTrackingResult:
    """Record shared session-start side effects, and say whether it did.

    FR01: the deferral verdict is supplied by the caller's single threaded
    census; this function no longer takes a second, differently calibrated one.
    FR09: ``recorded`` is the honest answer the caller needs before it writes a
    recall receipt, and ``deferred_effects`` names what was lost instead of
    hiding several losses behind one generic key.
    """

    from trw_mcp.state.memory_adapter import increment_session_counts
    from trw_mcp.state.memory_adapter import update_access_tracking as adapter_update_access

    unique_ids = dedupe_learning_ids(learning_ids)
    if not unique_ids:
        return SurfaceTrackingResult(ids=[], recorded=not defer, deferred_effects=())
    if defer:
        # NFR03 (one-event-per-condition): the sole caller that can pass
        # ``defer=True`` (``perform_session_recalls``) already logs
        # ``session_start_side_effects_deferred`` for this exact condition with
        # richer context (writer/peer counts, threshold, deferral age) than this
        # function has access to. Logging here too made one deferral look like
        # two to anything counting events, so the event moved to the caller and
        # this branch stays a silent, typed return.
        return SurfaceTrackingResult(ids=unique_ids, recorded=False, deferred_effects=DEFERRABLE_SIDE_EFFECTS)
    increment_session_counts(trw_dir, unique_ids)
    adapter_update_access(trw_dir, unique_ids)
    _log_session_start_surfaces(trw_dir, unique_ids)
    return SurfaceTrackingResult(ids=unique_ids, recorded=True, deferred_effects=())


__all__ = [
    "DEFERRABLE_SIDE_EFFECTS",
    "SurfaceTrackingResult",
    "dedupe_learning_ids",
    "record_session_start_surfaces",
    "session_start_census",
    "session_start_defers",
]
