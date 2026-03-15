"""TelemetryClient — local JSONL queue for opt-in telemetry. PRD-CORE-031.

Events are enqueued in-memory and flushed to
``.trw/logs/<telemetry_file>`` via the standard JSONL persistence layer.
Thread-safe: concurrent tool invocations can call ``record_event`` safely.
Zero overhead when disabled (``TRWConfig.telemetry_enabled`` is False).
"""

from __future__ import annotations

import threading
from pathlib import Path

import structlog

from trw_mcp.state._paths import resolve_trw_dir
from trw_mcp.state.persistence import FileStateWriter, json_serializer
from trw_mcp.telemetry.models import TelemetryEvent

logger = structlog.get_logger()


class TelemetryClient:
    """Thread-safe local JSONL queue for telemetry events.

    Usage::

        client = TelemetryClient.from_config()
        client.record_event(SessionStartEvent(...))
        client.flush()  # writes all queued events to disk

    When ``enabled`` is False, ``record_event`` and ``flush`` are no-ops
    so call sites do not need to guard every invocation.
    """

    def __init__(
        self,
        *,
        enabled: bool,
        output_path: Path,
        writer: FileStateWriter | None = None,
    ) -> None:
        self._enabled = enabled
        self._output_path = output_path
        self._writer = writer or FileStateWriter()
        self._lock = threading.Lock()
        self._queue: list[TelemetryEvent] = []

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def from_config(cls) -> TelemetryClient:
        """Construct a client from the active TRWConfig singleton.

        Resolves the output path as::

            {trw_dir}/logs/{config.telemetry_file}
        """
        from trw_mcp.models.config import get_config  # local to avoid circular

        cfg = get_config()
        trw_dir = resolve_trw_dir()
        output_path = trw_dir / cfg.logs_dir / cfg.telemetry_file
        return cls(enabled=cfg.telemetry_enabled, output_path=output_path)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def enabled(self) -> bool:
        """True if telemetry recording is active."""
        return self._enabled

    def record_event(self, event: TelemetryEvent) -> None:
        """Enqueue *event* for the next flush.

        No-op when the client is disabled.

        Args:
            event: Any ``TelemetryEvent`` subclass instance.
        """
        if not self._enabled:
            return
        with self._lock:
            self._queue.append(event)

    def flush(self) -> int:
        """Write all queued events to the JSONL file and clear the queue.

        No-op when the client is disabled.  Returns the number of events
        written so callers can log or assert in tests.

        Returns:
            Number of events flushed (0 when disabled or queue was empty).
        """
        if not self._enabled:
            return 0
        with self._lock:
            pending = list(self._queue)
            self._queue.clear()

        if not pending:
            return 0

        written = 0
        failed: list[TelemetryEvent] = []
        for event in pending:
            record = _event_to_record(event)
            try:
                self._writer.append_jsonl(self._output_path, record)
                written += 1
            except Exception:  # justified: fail-open, telemetry write errors must not block
                # justified: telemetry is fail-open — individual write errors
                # must not propagate, but we preserve the event for retry.
                logger.warning(
                    "telemetry_flush_error",
                    exc_info=True,
                    output_path=str(self._output_path),
                    event_type=event.event_type,
                )
                failed.append(event)

        # FR03: Restore failed events to the queue so they are retried on
        # the next flush() call instead of being silently dropped.
        if failed:
            with self._lock:
                self._queue[:0] = failed

        logger.debug(
            "telemetry_flushed",
            written=written,
            failed=len(failed),
            path=str(self._output_path),
        )
        return written

    def queue_size(self) -> int:
        """Return the number of events currently waiting to be flushed."""
        with self._lock:
            return len(self._queue)


# ------------------------------------------------------------------
# Internal helpers
# ------------------------------------------------------------------


def _event_to_record(event: TelemetryEvent) -> dict[str, object]:
    """Serialize a TelemetryEvent to a plain dict suitable for JSONL.

    Uses Pydantic's ``model_dump`` for field extraction and the shared
    ``json_serializer`` for datetime coercion.
    """
    raw = event.model_dump()
    # Coerce any non-JSON-native values (e.g. datetime) to strings
    result: dict[str, object] = {}
    for key, value in raw.items():
        try:
            import json as _json

            _json.dumps(value)
            result[key] = value
        except (TypeError, ValueError):  # per-item error handling: fall back to serializer for non-JSON-native values  # noqa: PERF203
            result[key] = json_serializer(value)
    return result
