"""Telemetry batch sender — PRD-CORE-031 FR06-FR08.

Reads accumulated telemetry events from the local JSONL queue and
transmits them to the platform backend in batches. Fail-open: if the
backend is unreachable, events remain in the local queue for the next
attempt. Zero overhead when platform_url is empty (offline mode).
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import structlog

from trw_mcp._checkout_write import write_checkout_file
from trw_mcp.models.config import get_config
from trw_mcp.models.typed_dicts import BatchSendResult
from trw_mcp.state._paths import resolve_trw_dir
from trw_mcp.state._platform_trust import (
    payload_trw_dir,
    platform_auth_headers,
    platform_contact_enabled,
    send_policy,
)
from trw_mcp.state.persistence import FileStateReader, json_serializer

logger = structlog.get_logger(__name__)

# PRD-SEC-004-FR01 (pre-consent backlog exclusion): every telemetry record
# written to the local upload queue is stamped with the consent state in effect
# AT WRITE TIME. The sender uploads ONLY records recorded under consent; records
# written while platform_telemetry_enabled was False are excluded and dropped on
# the first consented flush so a later opt-in can never upload the pre-consent
# backlog wholesale. The marker is a LOCAL-ONLY field stripped before any POST.
_CONSENT_FIELD = "_trw_consent"


def stamp_consent(record: dict[str, object], *, consented: bool) -> dict[str, object]:
    """Stamp a telemetry record with the consent state in effect at write time.

    Returns the same dict (mutated in place) for call-site convenience. Records
    are stamped at the durable-write boundary so the sender can later honor the
    consent that applied when the event was actually recorded.
    """
    record[_CONSENT_FIELD] = bool(consented)
    return record


def _record_consented(record: dict[str, object]) -> bool:
    """True only when a record was explicitly stamped as recorded under consent.

    Fail-closed: an untagged record (no marker) is treated as pre-consent and is
    NOT eligible for upload — a legacy/un-stamped backlog can never leak.
    """
    return record.get(_CONSENT_FIELD) is True


def _strip_consent_marker(record: dict[str, object]) -> dict[str, object]:
    """Return a copy of *record* without the local-only consent marker."""
    return {k: v for k, v in record.items() if k != _CONSENT_FIELD}


class BatchSender:
    """Transmit local telemetry events to the platform backend.

    Usage::
        sender = BatchSender.from_config()
        result = sender.send()  # returns {"sent": N, "failed": M, "remaining": R}
    """

    def __init__(
        self,
        *,
        platform_urls: list[str],
        platform_api_key: str = "",
        input_path: Path,
        batch_size: int = 100,
        max_retries: int = 3,
        backoff_base: float = 1.0,
        platform_telemetry_enabled: bool = False,
        source_trw_dir: Path | None,
    ) -> None:
        self._platform_urls = platform_urls
        self._platform_api_key = platform_api_key
        self._input_path = input_path
        self._batch_size = batch_size
        self._max_retries = max_retries
        self._backoff_base = backoff_base
        # PRD-SEC-004-FR01: the documented opt-out flag gates every off-machine
        # send. Default False is fail-closed for egress — a sender built without
        # an explicit flag never transmits.
        self._platform_telemetry_enabled = platform_telemetry_enabled
        # The .trw the queue (input_path) belongs to; its send policy governs these sends.
        self._source_trw_dir = source_trw_dir
        self._reader = FileStateReader()

    @classmethod
    def from_config(cls) -> BatchSender:
        """Construct a sender from the active TRWConfig singleton."""
        cfg = get_config()
        # An absolute logs_dir/telemetry_file may place the queue in another project: its policy
        # root is the .trw that owns the queue FILE, never the current project's (review r3 P0).
        input_path = resolve_trw_dir() / cfg.logs_dir / cfg.telemetry_file
        return cls(
            platform_urls=cfg.effective_platform_urls,
            platform_api_key=cfg.platform_api_key.get_secret_value(),
            input_path=input_path,
            platform_telemetry_enabled=cfg.platform_telemetry_enabled,
            source_trw_dir=payload_trw_dir(input_path),
        )

    def send(self) -> BatchSendResult:
        """Send all queued events to all platform backends.

        Fan-out: sends to every configured URL. A batch is "sent" if
        at least one backend accepted it.
        Returns dict with: sent, failed, remaining, skipped_reason.
        Fail-open: network errors do NOT raise exceptions.
        """
        # PRD-SEC-004-FR01: single choke point — honor the documented opt-out
        # flag before any off-machine send. Zero POST when disabled; the local
        # JSONL queue is left untouched (no rewrite/truncation), so opt-out
        # suppresses only the network transmission.
        if self._source_trw_dir is None:  # the queue has no owning project: it has no policy to send under
            return {"sent": 0, "failed": 0, "remaining": 0, "skipped_reason": "no_source_project"}
        policy = send_policy(self._source_trw_dir)  # the queue's project: its consent and switch
        if not (self._platform_telemetry_enabled and policy.platform_telemetry):
            return {"sent": 0, "failed": 0, "remaining": 0, "skipped_reason": "platform_telemetry_disabled"}

        # P1-C follow-up: platform_contact_enabled is the global egress kill
        # switch, on top of the per-purpose consent flag above. No request is
        # attempted; the local JSONL queue is left untouched.
        if not platform_contact_enabled(self._source_trw_dir):
            return {"sent": 0, "failed": 0, "remaining": 0, "skipped_reason": "platform_contact_disabled"}

        if not self._platform_urls:
            return {"sent": 0, "failed": 0, "remaining": 0, "skipped_reason": "offline_mode"}

        if not self._input_path.exists():
            return {"sent": 0, "failed": 0, "remaining": 0, "skipped_reason": "no_events"}

        records = self._reader.read_jsonl(self._input_path)
        if not records:
            return {"sent": 0, "failed": 0, "remaining": 0, "skipped_reason": "empty_queue"}

        # PRD-SEC-004-FR01 (pre-consent backlog exclusion): partition the queue
        # by the consent stamped AT WRITE TIME. Only records recorded under
        # consent are eligible for upload; pre-consent records (written while
        # telemetry was disabled, or legacy un-stamped rows) are dropped so a
        # later opt-in cannot upload the historical backlog wholesale. The drop
        # is intentional and permanent — the queue is rewritten without them.
        eligible = [r for r in records if _record_consented(r)]
        pre_consent_dropped = len(records) - len(eligible)
        if pre_consent_dropped:
            logger.info(
                "telemetry_pre_consent_backlog_dropped",
                dropped=pre_consent_dropped,
                eligible=len(eligible),
            )
        if not eligible:
            # Drop the pre-consent backlog from the on-disk queue so it can never
            # be reconsidered after a later opt-in, then report nothing to send.
            self._rewrite_queue([])
            return {"sent": 0, "failed": 0, "remaining": 0, "skipped_reason": "no_consented_events"}

        total_sent = 0
        total_failed = 0
        unsent: list[dict[str, object]] = []

        for i in range(0, len(eligible), self._batch_size):
            batch = eligible[i : i + self._batch_size]
            # Strip the local-only consent marker before the off-machine POST.
            outgoing = [_strip_consent_marker(r) for r in batch]
            any_success = self._send_batch_fanout(outgoing)
            if any_success:
                total_sent += len(batch)
            else:
                total_failed += len(batch)
                # FR04: Track the actual failed batch events so they remain
                # in the queue — records[total_sent:] would skip interleaved
                # failed batches between successful ones. Keep the consent marker
                # on retained rows so a retry stays consent-correct.
                unsent.extend(batch)

        # Rewrite the queue with only the still-pending consented records. This
        # also removes any pre-consent backlog that was excluded above (it was
        # never added to `unsent`), satisfying the drop-on-first-consented-flush
        # contract even when some consented batches succeeded.
        self._rewrite_queue(unsent)

        return {
            "sent": total_sent,
            "failed": total_failed,
            "remaining": len(unsent),
            "skipped_reason": None,
        }

    def _send_batch_fanout(self, batch: list[dict[str, object]]) -> bool:
        """Send a batch to all configured URLs in parallel.

        Each URL is attempted independently via ThreadPoolExecutor so that
        a slow or failing backend never blocks delivery to the others.
        Returns True if at least one backend accepted the batch.
        """
        from concurrent.futures import ThreadPoolExecutor, as_completed

        if len(self._platform_urls) <= 1:
            # Fast path: no parallelism overhead for single URL
            return any(self._send_batch_to(url, batch) for url in self._platform_urls)

        with ThreadPoolExecutor(max_workers=len(self._platform_urls)) as pool:
            futures = {pool.submit(self._send_batch_to, url, batch): url for url in self._platform_urls}
            any_success = False
            for fut in as_completed(futures):
                try:
                    if fut.result():
                        any_success = True
                except (
                    Exception
                ) as exc:  # per-item error handling: one future failure must not stop other URLs from being checked
                    logger.debug("batch_future_failed", exc_type=type(exc).__name__)
            return any_success

    def _send_batch_to(self, base_url: str, batch: list[dict[str, object]]) -> bool:
        """Attempt to send a batch to one URL with exponential backoff retry."""
        url = f"{base_url.rstrip('/')}/v1/telemetry"

        for attempt in range(self._max_retries):
            if not platform_contact_enabled(
                self._source_trw_dir
            ):  # every request asks: the switch may flip mid-flush (B71-106)
                return False  # the batch stays queued
            try:
                success = self._http_post(url, batch)
                if success:
                    return True
            except Exception:  # justified: boundary, HTTP post to external backend
                logger.debug(
                    "batch_send_retry",
                    attempt=attempt + 1,
                    max_retries=self._max_retries,
                    url=url,
                )

            if attempt < self._max_retries - 1:
                sleep_time = self._backoff_base * (2**attempt)
                time.sleep(sleep_time)

        logger.warning("batch_send_failed", batch_size=len(batch), url=url)
        return False

    def _http_post(self, url: str, payload: list[dict[str, object]]) -> bool:
        """POST payload to URL. Returns True on 2xx response.

        PRD-DIST-124 (2026-04-30): migrated from urllib to httpx for
        consistency with sync/. httpx is a transitive dependency via
        fastmcp, so no new package install required. URL is one of
        ``effective_platform_urls``, which a project's tracked
        ``.trw/config.yaml`` CAN set — the bearer is attached only through
        ``platform_auth_headers`` (trw_mcp.state._platform_trust), which
        withholds it from anything off the trusted-host allowlist.
        """
        import httpx

        headers: dict[str, str] = {
            "Content-Type": "application/json",
            **platform_auth_headers(url, self._platform_api_key, source_trw_dir=self._source_trw_dir),
        }

        try:
            with httpx.Client(timeout=30.0) as client:
                response = client.post(url, json={"events": payload}, headers=headers)
            return bool(200 <= response.status_code < 300)
        except (httpx.HTTPError, OSError):
            return False

    def _rewrite_queue(self, remaining: list[dict[str, object]]) -> None:
        """Rewrite the JSONL queue with only remaining (unsent) events, in one atomic publish.

        The same lines ``append_jsonl`` would write, published beside the queue through ``safe_fs`` so a
        symlink planted at the queue file raises ``UnsafeWriteError`` instead of being truncated through.
        """
        lines = "".join(json.dumps(record, default=json_serializer) + "\n" for record in remaining)
        write_checkout_file(self._input_path.parent, self._input_path, lines)
