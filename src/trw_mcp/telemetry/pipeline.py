"""Unified telemetry pipeline with periodic background flush.

Thread-safe singleton. Accepts events from @log_tool_call, sanitizes via
strip_pii/redact_paths, queues in bounded deque, flushes to local JSONL +
backend POST on a timer thread. Fail-open throughout.
"""

from __future__ import annotations

import atexit
import collections
import json
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import ClassVar

import httpx
import structlog
from typing_extensions import TypedDict

from trw_mcp._locking import _lock_ex, _lock_un
from trw_mcp.state._paths import resolve_project_root, resolve_trw_dir
from trw_mcp.state.persistence import FileStateWriter
from trw_mcp.telemetry.anonymizer import redact_paths, strip_pii

logger = structlog.get_logger(__name__)


class PipelineFlushResult(TypedDict):
    """Return type for flush_now()."""

    sent: int
    failed: int
    overflow: int
    skipped_reason: str


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
        # PRD-FIX (F23): guards one-time atexit drain registration so the
        # handler is wired exactly once per instance even under concurrent
        # enqueue(). Protected by self._lock alongside the queue.
        self._atexit_registered = False

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

    # Structural/identifier fields that are safe by construction and would be
    # corrupted by PII scrubbing (e.g. emails-as-ids, timestamps). Everything
    # else that is a string is scrubbed — messages, args, paths, errors all
    # carry user content and must not ship raw PII to telemetry.
    _PII_SAFE_KEYS: ClassVar[frozenset[str]] = frozenset(
        {
            "ts",
            "event_type",
            "event_id",
            "parent_event_id",
            "installation_id",
            "framework_version",
            "phase",
            "tool",
            "tool_name",
            "outcome",
            "session_id",
        }
    )

    def _scrub_pii(self, event: dict[str, object]) -> None:
        """Scrub PII and redact paths from every string field in-place.

        Previously only the ``error`` field was PII-scrubbed, leaking PII in
        other string fields (messages, args, paths). Now ``strip_pii`` is
        applied to all string values except an explicit safe-key allowlist.

        Scrubbing is RECURSIVE: nested dicts and lists are walked so PII
        buried inside ``args``/``payload`` structures cannot leak. The
        ``_PII_SAFE_KEYS`` allowlist applies only to TOP-LEVEL keys; nested
        string values are always scrubbed (a safe key name reused deeper in
        the tree carries no by-construction safety guarantee).
        """
        try:
            project_root: Path | None = resolve_project_root()
        except Exception:  # justified: fail-open, path resolution failure non-fatal
            project_root = None

        for key, value in event.items():
            if key in self._PII_SAFE_KEYS:
                continue
            event[key] = self._scrub_value(value, project_root)

    def _scrub_value(self, value: object, project_root: Path | None) -> object:
        """Recursively scrub a single value (string/dict/list); other types pass through."""
        if isinstance(value, str):
            scrubbed = strip_pii(value)
            if project_root is not None:
                scrubbed = redact_paths(scrubbed, project_root)
            return scrubbed
        if isinstance(value, dict):
            return {k: self._scrub_value(v, project_root) for k, v in value.items()}
        if isinstance(value, list):
            return [self._scrub_value(item, project_root) for item in value]
        return value

    def _enrich_installation_id(self, event: dict[str, object]) -> None:
        """Add a HASHED installation_id if missing (caller-supplied ids preserved).

        PRD-SEC-004-FR08: the installation_id this library resolves from config
        MUST NOT egress as a raw project-directory name (the installer may set
        ``installation_id`` to a raw dir name while the privacy copy says
        "anonymous"). The pipeline is a library telemetry-payload builder, so it
        hashes the resolved id at the egress boundary via
        ``anonymize_installation_id`` (non-reversible double SHA-256). A caller
        that supplied its own installation_id on the event owns that value and
        is left untouched (the no-overwrite contract).
        """
        if "installation_id" in event:
            return
        from trw_mcp.telemetry.anonymizer import anonymize_installation_id

        try:
            from trw_mcp.state._paths import resolve_installation_id

            event["installation_id"] = anonymize_installation_id(resolve_installation_id())
        except Exception:  # justified: fail-open, enrichment failure non-fatal
            event["installation_id"] = "unknown"

    def _enrich_framework_version(self, event: dict[str, object]) -> None:
        """Add framework_version if missing."""
        if "framework_version" in event:
            return
        try:
            from trw_mcp.models.config import get_config

            event["framework_version"] = get_config().framework_version
        except Exception:  # justified: fail-open, config resolution non-fatal
            logger.debug("telemetry_framework_version_enrich_skipped", exc_info=True)  # justified: fail-open

    def _enrich_event_type(self, event: dict[str, object]) -> None:
        """Set event_type if missing."""
        if "event_type" not in event:
            event["event_type"] = "tool_invocation"

    def _enrich_phase(self, event: dict[str, object]) -> None:
        """Auto-populate phase from active run if missing."""
        if "phase" in event:
            return
        try:
            from trw_mcp.state._paths import get_pinned_run
            from trw_mcp.state.persistence import FileStateReader

            # PRD-FIX-083: pin-only — telemetry phase enrichment runs per
            # event publish on the worker thread. The legacy find_active_run()
            # scan would PyYAML-parse every run.yaml on each event when no pin
            # exists. Phase enrichment is best-effort; missing field is fine.
            run_dir = get_pinned_run()
            if run_dir is not None:
                run_yaml = run_dir / "meta" / "run.yaml"
                if run_yaml.exists():
                    data = FileStateReader().read_yaml(run_yaml)
                    event["phase"] = str(data.get("phase", "unknown"))
        except Exception:  # justified: fail-open, phase enrichment non-critical
            logger.debug("telemetry_phase_enrich_skipped", exc_info=True)  # justified: fail-open

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

            register_atexit = False
            with self._lock:
                was_full = len(self._queue) == self._queue.maxlen
                self._queue.append(event)
                if was_full:
                    self._overflow_count += 1
                # PRD-FIX (F23): claim the one-time atexit registration under
                # the same lock that guards the queue so concurrent enqueues
                # never double-register the drain handler.
                if not self._atexit_registered:
                    self._atexit_registered = True
                    register_atexit = True

            # PRD-FIX (F23): the flush thread was previously started only from
            # the session_start path (_ceremony_telemetry). Events enqueued
            # before that (or when session_start never runs) would queue but
            # never flush. start() is idempotent and guarded by the alive
            # check, so a lazy start here is race-safe and self-healing.
            self.start()
            if register_atexit:
                atexit.register(self._atexit_drain)

        except Exception:  # justified: fail-open, telemetry must never block
            logger.debug("telemetry_enqueue_skipped", exc_info=True)  # justified: fail-open

    def _atexit_drain(self) -> None:
        """Flush any queued events on interpreter shutdown (best-effort).

        Registered once via ``atexit`` from the first successful
        :meth:`enqueue`. Covers the residual loss window where the process
        exits before the periodic timer has flushed (e.g. a short-lived
        invocation that never reached the session_start start() call).
        """
        try:
            self.stop(drain=True)
        except Exception:  # justified: fail-open, atexit drain is best-effort
            logger.debug("pipeline_atexit_drain_error", exc_info=True)

    # ------------------------------------------------------------------
    # Timer lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start the background flush timer thread (idempotent).

        If the thread is already alive, this is a no-op.  The thread is
        started as a daemon so it does not prevent process exit.

        Thread-safe: the alive-check and thread creation happen under
        ``self._lock`` so concurrent first-time callers (e.g. the lazy
        start in :meth:`enqueue`) cannot spawn two flush threads.
        """
        with self._lock:
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
        # An explicit stop supersedes the interpreter-shutdown fallback.  In
        # particular, test runners and embedded hosts may close their logging
        # capture streams before Python runs atexit callbacks.  Leaving this
        # instance registered would then repeat the drain after its lifecycle
        # has ended and make otherwise-successful durable writes emit
        # ``ValueError: I/O operation on closed file`` from logging.  Keep the
        # unregister + flag transition under the queue lock so a concurrent
        # enqueue can safely register a fresh callback after stop releases it.
        with self._lock:
            if self._atexit_registered:
                atexit.unregister(self._atexit_drain)
                self._atexit_registered = False

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
            except Exception:  # per-item error handling: one write failure must not block the rest of the batch
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

        # PRD-SEC-004-FR01: single choke point — the documented opt-out flag
        # platform_telemetry_enabled gates ALL off-machine telemetry sends.
        # The local JSONL durable write above is preserved (opt-out suppresses
        # only the network POST, not local buffering). Fail-closed for egress:
        # a connected user who sets the flag false stops uploading immediately.
        if not getattr(cfg, "platform_telemetry_enabled", False):
            return {
                "sent": 0,
                "failed": 0,
                "overflow": self._overflow_count,
                "skipped_reason": "platform_telemetry_disabled",
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

        Follows the BatchSender._send_batch_to pattern: httpx POST with
        exponential backoff. Returns True if any URL accepts the batch.

        PRD-DIST-124 (2026-04-30): migrated from urllib to httpx for
        consistency with the rest of trw-mcp.

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
                    headers: dict[str, str] = {"Content-Type": "application/json"}
                    if api_key:
                        headers["Authorization"] = f"Bearer {api_key}"
                    # endpoint is built from cfg.effective_platform_urls
                    # (operator config, not user input).
                    payload = json.loads(json.dumps({"events": events}, default=str))
                    with httpx.Client(timeout=30.0) as client:
                        response = client.post(endpoint, json=payload, headers=headers)
                    if 200 <= response.status_code < 300:
                        return True
                except (httpx.HTTPError, OSError):
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

        Uses advisory file locking to coordinate with concurrent readers/writers
        (same pattern as _deferred_delivery.py).
        """
        try:
            if not path.exists():
                return
            with path.open("r+", encoding="utf-8") as fh:
                _lock_ex(fh.fileno())
                try:
                    fh.truncate(0)
                    fh.seek(0)
                finally:
                    _lock_un(fh.fileno())
        except OSError:
            # Fail-open: if truncation fails, events remain for next cycle
            logger.debug("pipeline_truncate_error", exc_info=True)
