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

logger = structlog.get_logger(__name__)


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
        platform_telemetry_enabled: bool = False,
    ) -> None:
        self._enabled = enabled
        self._output_path = output_path
        self._writer = writer or FileStateWriter()
        self._lock = threading.Lock()
        self._queue: list[TelemetryEvent] = []
        # PRD-SEC-004-FR01 (pre-consent backlog exclusion): the local record
        # write is gated by telemetry_enabled (default on), but UPLOAD eligibility
        # is governed by the separate platform consent flag. Stamp each flushed
        # record with the platform consent in effect at write time so the sender
        # never uploads a pre-consent backlog after a later opt-in.
        self._platform_telemetry_enabled = platform_telemetry_enabled

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
        return cls(
            enabled=cfg.telemetry_enabled,
            output_path=output_path,
            platform_telemetry_enabled=bool(getattr(cfg, "platform_telemetry_enabled", False)),
        )

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
            # PRD-SEC-004-FR08: the legacy session-event path historically wrote
            # the RAW installation_id (a sanitized project-directory name) while
            # the new pipeline path hashes it. Hash it here too so the legacy and
            # library paths egress a consistent non-reversible id.
            _hash_installation_id(record)
            # PRD-SEC-004-FR01: stamp the platform consent in effect at write
            # time so the sender uploads only consented rows.
            from trw_mcp.telemetry.sender import stamp_consent

            stamp_consent(record, consented=self._platform_telemetry_enabled)
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


def _hash_installation_id(record: dict[str, object]) -> None:
    """Hash a record's raw installation_id in place (PRD-SEC-004-FR08).

    The legacy ``TelemetryEvent`` carries ``installation_id`` as a sanitized
    project-directory name (pseudonymous, not anonymous). The new pipeline path
    egresses a non-reversible double-SHA-256 of the id; this brings the legacy
    upload path to parity so no raw directory name leaves the machine. A value
    that is already hashed (16 hex chars) is left untouched (idempotent), and a
    missing/empty/non-string id is left as-is for the backend to handle.
    """
    from trw_mcp.telemetry.anonymizer import anonymize_installation_id

    raw = record.get("installation_id")
    if not isinstance(raw, str) or not raw:
        return
    # anonymize_installation_id returns exactly 16 lowercase-hex chars; treat an
    # input already in that shape as already-hashed to keep this idempotent.
    if len(raw) == 16 and all(c in "0123456789abcdef" for c in raw):
        return
    record["installation_id"] = anonymize_installation_id(raw)


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
        except (
            TypeError,
            ValueError,
        ):  # per-item error handling: fall back to serializer for non-JSON-native values
            result[key] = json_serializer(value)
    return result
