"""Unified telemetry pipeline with periodic background flush.

Thread-safe singleton. Accepts events from @log_tool_call, sanitizes via
strip_pii/redact_paths, queues in bounded deque, flushes to local JSONL +
backend POST on a timer thread. Fail-open throughout.
"""

from __future__ import annotations

import collections
import fcntl
import json
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import ClassVar, TypedDict

import structlog

from trw_mcp.state._paths import resolve_project_root, resolve_trw_dir
from trw_mcp.state.persistence import FileStateWriter
from trw_mcp.telemetry.anonymizer import redact_paths, strip_pii

logger = structlog.get_logger()


class PipelineFlushResult(TypedDict):
    """Return type for flush_now()."""

    sent: int
    failed: int
    overflow: int
    skipped_reason: str


def _resolve_installation_id() -> str:
    """Resolve installation ID from config, generating a stable fallback.

    Mirrors the helper in ``_deferred_delivery.py`` but avoids the circular
    import through ``ceremony.py`` by importing ``get_config`` directly.
    """
    import hashlib

    from trw_mcp.models.config import get_config  # local to avoid circular

    cfg = get_config()
    iid = cfg.installation_id.strip() if cfg.installation_id else ""
    if iid:
        return iid
    project_root = str(resolve_project_root())
    return "inst-" + hashlib.sha256(project_root.encode()).hexdigest()[:12]


class TelemetryPipeline:
    """Unified telemetry pipeline with periodic background flush.

    Thread-safe singleton. Accepts events from @log_tool_call, sanitizes via
    strip_pii/redact_paths, queues in bounded deque, flushes to local JSONL +
    backend POST on a timer thread. Fail-open throughout.
    """

    _instance: ClassVar[TelemetryPipeline | None] = None
    _instance_lock: ClassVar[threading.Lock] = threading.Lock()

    def __init__(
        self,
        *,
        flush_interval_secs: float = 30.0,
        batch_size: int = 100,
        max_retries: int = 3,
        backoff_base: float = 1.0,
        max_queue_size: int = 10_000,
    ) -> None:
        self._flush_interval_secs = flush_interval_secs
        self._batch_size = batch_size
        self._max_retries = max_retries
        self._backoff_base = backoff_base

        self._queue: collections.deque[dict[str, object]] = collections.deque(
            maxlen=max_queue_size,
        )
        self._lock = threading.Lock()
        self._shutdown = threading.Event()
        self._thread: threading.Thread | None = None
        self._overflow_count = 0
        self._enabled = True
        self._writer = FileStateWriter()

    # ------------------------------------------------------------------
    # Singleton
    # ------------------------------------------------------------------

    @classmethod
    def get_instance(cls) -> TelemetryPipeline:
        """Return (or create) the process-wide singleton.

        Uses double-checked locking to avoid taking the lock on every call
        once the instance is initialized.
        """
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        """Tear down the singleton (for testing or hot-reload).

        Acquires the instance lock, stops the background thread (without
        draining), and clears the class-level reference.
        """
        with cls._instance_lock:
            if cls._instance is not None:
                try:
                    cls._instance.stop(drain=False)
                except Exception:  # justified: cleanup, reset must not raise
                    logger.debug("pipeline_reset_stop_error", exc_info=True)
                cls._instance = None

    # ------------------------------------------------------------------
    # Enqueue
    # ------------------------------------------------------------------

    def _scrub_pii(self, event: dict[str, object]) -> None:
        """Scrub PII and redact paths from event in-place."""
        error_val = event.get("error")
        if isinstance(error_val, str):
            event["error"] = strip_pii(error_val)

        try:
            project_root = resolve_project_root()
        except Exception:  # justified: fail-open, path resolution failure non-fatal
            project_root = None

        if project_root is not None:
            for key, value in event.items():
                if isinstance(value, str):
                    event[key] = redact_paths(value, project_root)

    def _enrich_installation_id(self, event: dict[str, object]) -> None:
        """Add installation_id if missing."""
        if "installation_id" in event:
            return
        try:
            event["installation_id"] = _resolve_installation_id()
        except Exception:  # justified: fail-open, enrichment failure non-fatal
            event["installation_id"] = "unknown"

    def _enrich_framework_version(self, event: dict[str, object]) -> None:
        """Add framework_version if missing."""
        if "framework_version" in event:
            return
        try:
            from trw_mcp.models.config import get_config

            event["framework_version"] = get_config().framework_version
        except Exception:  # justified: fail-open, config resolution non-fatal  # noqa: S110
            pass

    def _enrich_event_type(self, event: dict[str, object]) -> None:
        """Set event_type if missing."""
        if "event_type" not in event:
            event["event_type"] = "tool_invocation"

    def _enrich_phase(self, event: dict[str, object]) -> None:
        """Auto-populate phase from active run if missing."""
        if "phase" in event:
            return
        try:
            from trw_mcp.state._paths import find_active_run
            from trw_mcp.state.persistence import FileStateReader

            run_dir = find_active_run()
            if run_dir is not None:
                run_yaml = run_dir / "meta" / "run.yaml"
                if run_yaml.exists():
                    data = FileStateReader().read_yaml(run_yaml)
                    event["phase"] = str(data.get("phase", "unknown"))
        except Exception:  # justified: fail-open, phase enrichment non-critical  # noqa: S110
            pass

    def _enrich_timestamp(self, event: dict[str, object]) -> None:
        """Add timestamp if missing."""
        if "ts" not in event:
            event["ts"] = datetime.now(timezone.utc).isoformat()

    def enqueue(self, event: dict[str, object]) -> None:
        """Sanitize and enqueue a telemetry event.

        Fail-open: exceptions are caught and silently swallowed so that
        telemetry never blocks tool execution.

        Args:
            event: Raw telemetry event dict. Modified in-place for
                enrichment (installation_id, framework_version, event_type).
        """
        try:
            if not self._enabled:
                return

            self._scrub_pii(event)
            self._enrich_installation_id(event)
            self._enrich_framework_version(event)
            self._enrich_event_type(event)
            self._enrich_phase(event)
            self._enrich_timestamp(event)

            with self._lock:
                was_full = len(self._queue) == self._queue.maxlen
                self._queue.append(event)
                if was_full:
                    self._overflow_count += 1

        except Exception:  # justified: fail-open, telemetry must never block  # noqa: S110
            pass

    # ------------------------------------------------------------------
    # Timer lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start the background flush timer thread (idempotent).

        If the thread is already alive, this is a no-op.  The thread is
        started as a daemon so it does not prevent process exit.
        """
        if self._thread is not None and self._thread.is_alive():
            return
        self._shutdown.clear()
        self._thread = threading.Thread(
            target=self._timer_loop,
            name="telemetry-pipeline",
            daemon=True,
        )
        self._thread.start()

    def stop(self, *, drain: bool = True, timeout: float = 10.0) -> None:
        """Signal the timer thread to stop and optionally drain the queue.

        Args:
            drain: If True and the queue is non-empty after the thread
                exits, perform one final ``flush_now()`` call.
            timeout: Maximum seconds to wait for the thread to join.
        """
        self._shutdown.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=timeout)
        if drain:
            with self._lock:
                has_events = len(self._queue) > 0
            if has_events:
                try:
                    self.flush_now()
                except Exception:  # justified: fail-open, drain is best-effort
                    logger.debug("pipeline_drain_error", exc_info=True)

    # ------------------------------------------------------------------
    # Flush
    # ------------------------------------------------------------------

    def flush_now(self) -> PipelineFlushResult:
        """Immediately flush all queued events to local JSONL + backend.

        Returns a summary dict with counts of sent, failed, and overflow
        events. Fail-open: network errors do NOT raise.

        Returns:
            PipelineFlushResult with sent/failed/overflow/skipped_reason.
        """
        # Swap queue under lock
        with self._lock:
            events = list(self._queue)
            self._queue.clear()

        if not events:
            return {
                "sent": 0,
                "failed": 0,
                "overflow": self._overflow_count,
                "skipped_reason": "empty_queue",
            }

        # Write events to local JSONL as durable buffer
        jsonl_path = self._resolve_jsonl_path()
        for event in events:
            try:
                self._writer.append_jsonl(jsonl_path, event)
            except Exception:  # per-item error handling: one write failure must not block the rest of the batch  # noqa: PERF203
                logger.debug("pipeline_jsonl_write_error", exc_info=True)

        # Resolve config for remote send
        try:
            from trw_mcp.models.config import get_config

            cfg = get_config()
        except Exception:  # justified: fail-open, config unavailable = offline
            logger.debug("pipeline_config_error", exc_info=True)
            return {
                "sent": 0,
                "failed": len(events),
                "overflow": self._overflow_count,
                "skipped_reason": "config_unavailable",
            }

        urls = cfg.effective_platform_urls
        if not urls:
            return {
                "sent": 0,
                "failed": 0,
                "overflow": self._overflow_count,
                "skipped_reason": "offline_mode",
            }

        try:
            api_key = cfg.platform_api_key.get_secret_value()
        except Exception:  # justified: fail-open, missing key = offline
            api_key = ""

        # Chunk events into batches and send
        total_sent = 0
        total_failed = 0
        all_sent = True

        for i in range(0, len(events), self._batch_size):
            batch = events[i : i + self._batch_size]
            success = self._send_batch(batch, urls, api_key)
            if success:
                total_sent += len(batch)
            else:
                total_failed += len(batch)
                all_sent = False

        # If all batches sent successfully, truncate the local JSONL
        if all_sent and total_sent > 0:
            self._truncate_jsonl(jsonl_path)

        return {
            "sent": total_sent,
            "failed": total_failed,
            "overflow": self._overflow_count,
            "skipped_reason": "",
        }

    # ------------------------------------------------------------------
    # Internal: HTTP send
    # ------------------------------------------------------------------

    def _send_batch(
        self,
        events: list[dict[str, object]],
        urls: list[str],
        api_key: str,
    ) -> bool:
        """Send a batch to the first accepting backend URL with retry.

        Follows the BatchSender._send_batch_to pattern: urllib POST with
        exponential backoff. Returns True if any URL accepts the batch.

        Args:
            events: List of event dicts to transmit.
            urls: Platform backend URLs to try.
            api_key: Bearer token for Authorization header.

        Returns:
            True if at least one URL accepted the batch.
        """
        for url in urls:
            endpoint = f"{url.rstrip('/')}/v1/telemetry"
            for attempt in range(self._max_retries):
                try:
                    data = json.dumps({"events": events}, default=str).encode("utf-8")
                    headers: dict[str, str] = {"Content-Type": "application/json"}
                    if api_key:
                        headers["Authorization"] = f"Bearer {api_key}"
                    req = urllib.request.Request(  # noqa: S310 — endpoint is built from cfg.effective_platform_urls (operator config, not user input)
                        endpoint,
                        data=data,
                        headers=headers,
                        method="POST",
                    )
                    with urllib.request.urlopen(req, timeout=30) as response:  # noqa: S310 — see Request comment above
                        if 200 <= response.status < 300:
                            return True
                except (urllib.error.URLError, urllib.error.HTTPError, OSError):
                    logger.debug(
                        "pipeline_batch_retry",
                        attempt=attempt + 1,
                        max_retries=self._max_retries,
                        url=endpoint,
                    )

                if attempt < self._max_retries - 1:
                    sleep_time = min(
                        self._backoff_base * (2**attempt),
                        3.0,
                    )
                    time.sleep(sleep_time)

        logger.debug("pipeline_batch_failed", batch_size=len(events))
        return False

    # ------------------------------------------------------------------
    # Internal: timer
    # ------------------------------------------------------------------

    def _timer_loop(self) -> None:
        """Background loop: periodically call flush_now().

        Runs until ``_shutdown`` is set. Uses ``Event.wait(timeout=...)``
        so the thread wakes immediately when stop() is called.
        """
        while not self._shutdown.is_set():
            self._shutdown.wait(timeout=self._flush_interval_secs)
            if self._shutdown.is_set():
                break
            try:
                self.flush_now()
            except Exception:  # justified: fail-open, periodic flush errors non-fatal
                logger.debug("pipeline_flush_error", exc_info=True)

    # ------------------------------------------------------------------
    # Internal: paths & file ops
    # ------------------------------------------------------------------

    @staticmethod
    def _resolve_jsonl_path() -> Path:
        """Return the path to the pipeline events JSONL file."""
        return resolve_trw_dir() / "logs" / "pipeline-events.jsonl"

    @staticmethod
    def _truncate_jsonl(path: Path) -> None:
        """Truncate the JSONL file under an advisory flock.

        Uses fcntl.flock to coordinate with concurrent readers/writers
        (same pattern as _deferred_delivery.py).
        """
        try:
            if not path.exists():
                return
            with path.open("r+", encoding="utf-8") as fh:
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
                try:
                    fh.truncate(0)
                    fh.seek(0)
                finally:
                    fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        except OSError:
            # Fail-open: if truncation fails, events remain for next cycle
            logger.debug("pipeline_truncate_error", exc_info=True)
