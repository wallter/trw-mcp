"""Unified HPO events writer — events-YYYY-MM-DD.jsonl (PRD-HPO-MEAS-001 FR-3/FR-10).

Every :class:`HPOTelemetryEvent` subclass emits here in parallel with its
legacy CORE-031 counterpart during Phase 1 rollout. The unified file is
the **single source of truth** for H1 substrate queries (``trw_query_events``
in Wave 2c) and H4 meta-tune correlation. Selected event types also mirror
into legacy projection files (for example ``tool_call_events.jsonl``) so
older consumers keep working while unified events remain authoritative.

Design invariants:

1. **One file per UTC date.** Under each run's ``<run_dir>/meta/`` directory
   (or the session-events fallback when no run is pinned). Filename format
   is ``events-YYYY-MM-DD.jsonl`` matching FR-3's AC prose.
2. **Append-only atomic write.** Uses :class:`FileStateWriter.append_jsonl`
   for newline-delimited JSON with file locking — consistent with the
   legacy emitter's durability model.
3. **Schema-validated at write-time.** The event MUST be an
   :class:`HPOTelemetryEvent` instance; callers pass the Pydantic model
   (not a dict) so strict validation runs on every write (NFR-3).
4. **Fail-open.** Write failures log at WARN and return False — writers
   never raise. Per CONSTITUTION §1, truthfulness beats velocity; the
   caller sees the failure signal and can choose to re-raise.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import structlog

from trw_mcp.state.persistence import FileStateWriter
from trw_mcp.telemetry.event_base import HPOTelemetryEvent

logger = structlog.get_logger(__name__)


def _events_filename(now: datetime | None = None) -> str:
    """Return ``events-YYYY-MM-DD.jsonl`` for the given UTC date (now by default)."""
    ts = now or datetime.now(tz=timezone.utc)
    return f"events-{ts.strftime('%Y-%m-%d')}.jsonl"


def resolve_unified_events_path(
    *,
    run_dir: Path | None,
    fallback_dir: Path | None = None,
    now: datetime | None = None,
) -> Path | None:
    """Resolve the canonical ``events-YYYY-MM-DD.jsonl`` path for a session.

    Args:
        run_dir: Active run directory (``<task>/<run_id>/``). When set, the
            events file lives under ``run_dir / "meta" /``.
        fallback_dir: Optional fallback when no run is pinned (typically
            ``<trw_dir>/<context_dir>/``). When both are None, returns None.
        now: Override timestamp for test determinism.

    Returns:
        The resolved path, or None when neither directory is available.
    """
    fname = _events_filename(now)
    if run_dir is not None:
        meta = run_dir / "meta"
        if meta.exists():
            return meta / fname
    if fallback_dir is not None:
        return fallback_dir / fname
    return None


_LEGACY_PROJECTION_EVENT_TYPES = frozenset({"tool_call", "mcp_security"})


def _resolve_projection_path(
    *,
    event: HPOTelemetryEvent,
    run_dir: Path | None,
    fallback_dir: Path | None,
) -> Path | None:
    """Resolve the legacy projection path for selected unified event types."""
    if event.event_type not in _LEGACY_PROJECTION_EVENT_TYPES:
        return None
    if run_dir is not None:
        return run_dir / "meta" / "tool_call_events.jsonl"
    if fallback_dir is not None:
        return fallback_dir / "tool_call_events.jsonl"
    return None


class UnifiedEventWriter:
    """Fail-open appender for :class:`HPOTelemetryEvent` subclasses.

    One instance shared across all emitter retrofits — stateless so
    multiple tool call paths can reuse it without coordination.
    """

    def __init__(self, writer: FileStateWriter | None = None) -> None:
        self._writer = writer or FileStateWriter()

    def write(self, event: HPOTelemetryEvent, path: Path) -> bool:
        """Append the event to ``path``. Returns True on success, False on failure.

        Never raises — all exceptions degrade to a WARN log. Callers that
        need strict write semantics must check the return value.
        """
        try:
            record = json.loads(event.model_dump_json())
        except Exception:  # justified: boundary, Pydantic serialization rarely fails but never block emission path
            logger.warning(
                "unified_event_serialize_failed",
                event_type=event.event_type,
                event_id=event.event_id,
                exc_info=True,
            )
            return False

        try:
            self._writer.append_jsonl(path, record)
        except OSError:  # justified: fail-open, fs errors degrade to WARN
            logger.warning(
                "unified_event_write_failed",
                event_type=event.event_type,
                event_id=event.event_id,
                path=str(path),
                exc_info=True,
            )
            return False

        logger.debug(
            "unified_event_written",
            event_type=event.event_type,
            event_id=event.event_id,
            path=str(path),
        )
        return True


_default_writer: UnifiedEventWriter | None = None


def get_default_writer() -> UnifiedEventWriter:
    """Return the process-wide default :class:`UnifiedEventWriter`."""
    global _default_writer
    if _default_writer is None:
        _default_writer = UnifiedEventWriter()
    return _default_writer


def emit(
    event: HPOTelemetryEvent,
    *,
    run_dir: Path | None,
    fallback_dir: Path | None = None,
) -> bool:
    """Convenience single-call emit: resolve path + write.

    Returns True when the event was written, False when no path could be
    resolved or the write failed.
    """
    path = resolve_unified_events_path(run_dir=run_dir, fallback_dir=fallback_dir)
    if path is None:
        logger.debug(
            "unified_event_path_unresolved",
            event_type=event.event_type,
            event_id=event.event_id,
        )
        return False
    written = get_default_writer().write(event, path)
    if not written:
        return False

    projection_path = _resolve_projection_path(event=event, run_dir=run_dir, fallback_dir=fallback_dir)
    if projection_path is not None:
        try:
            record = json.loads(event.model_dump_json())
            FileStateWriter().append_jsonl(projection_path, record)
        except OSError:
            logger.warning(
                "unified_projection_write_failed",
                event_type=event.event_type,
                event_id=event.event_id,
                path=str(projection_path),
                exc_info=True,
            )
    return True


__all__ = [
    "UnifiedEventWriter",
    "emit",
    "get_default_writer",
    "resolve_unified_events_path",
]
