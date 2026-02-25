"""Telemetry batch sender — PRD-CORE-031 FR06-FR08.

Reads accumulated telemetry events from the local JSONL queue and
transmits them to the platform backend in batches. Fail-open: if the
backend is unreachable, events remain in the local queue for the next
attempt. Zero overhead when platform_url is empty (offline mode).
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import structlog

from trw_mcp.models.config import get_config
from trw_mcp.state._paths import resolve_trw_dir
from trw_mcp.state.persistence import FileStateReader, FileStateWriter

logger = structlog.get_logger()


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
    ) -> None:
        self._platform_urls = platform_urls
        self._platform_api_key = platform_api_key
        self._input_path = input_path
        self._batch_size = batch_size
        self._max_retries = max_retries
        self._backoff_base = backoff_base
        self._reader = FileStateReader()
        self._writer = FileStateWriter()

    @classmethod
    def from_config(cls) -> "BatchSender":
        """Construct a sender from the active TRWConfig singleton."""
        cfg = get_config()
        trw_dir = resolve_trw_dir()
        input_path = trw_dir / cfg.logs_dir / cfg.telemetry_file
        return cls(
            platform_urls=cfg.effective_platform_urls,
            platform_api_key=cfg.platform_api_key,
            input_path=input_path,
        )

    def send(self) -> dict[str, object]:
        """Send all queued events to all platform backends.

        Fan-out: sends to every configured URL. A batch is "sent" if
        at least one backend accepted it.
        Returns dict with: sent, failed, remaining, skipped_reason.
        Fail-open: network errors do NOT raise exceptions.
        """
        if not self._platform_urls:
            return {"sent": 0, "failed": 0, "remaining": 0, "skipped_reason": "offline_mode"}

        if not self._input_path.exists():
            return {"sent": 0, "failed": 0, "remaining": 0, "skipped_reason": "no_events"}

        records = self._reader.read_jsonl(self._input_path)
        if not records:
            return {"sent": 0, "failed": 0, "remaining": 0, "skipped_reason": "empty_queue"}

        total_sent = 0
        total_failed = 0

        for i in range(0, len(records), self._batch_size):
            batch = records[i : i + self._batch_size]
            any_success = self._send_batch_fanout(batch)
            if any_success:
                total_sent += len(batch)
            else:
                total_failed += len(batch)

        remaining = records[total_sent:]
        if total_sent > 0:
            self._rewrite_queue(remaining)

        return {
            "sent": total_sent,
            "failed": total_failed,
            "remaining": len(remaining),
            "skipped_reason": None,
        }

    def _send_batch_fanout(self, batch: list[dict[str, Any]]) -> bool:
        """Send a batch to all configured URLs. Returns True if any succeeded."""
        any_success = False
        for base_url in self._platform_urls:
            if self._send_batch_to(base_url, batch):
                any_success = True
        return any_success

    def _send_batch_to(self, base_url: str, batch: list[dict[str, Any]]) -> bool:
        """Attempt to send a batch to one URL with exponential backoff retry."""
        url = f"{base_url.rstrip('/')}/v1/telemetry"

        for attempt in range(self._max_retries):
            try:
                success = self._http_post(url, batch)
                if success:
                    return True
            except Exception:
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

    def _http_post(self, url: str, payload: list[dict[str, Any]]) -> bool:
        """POST payload to URL. Returns True on 2xx response.

        Uses urllib.request to avoid adding httpx as a hard dependency.
        """
        import json
        import urllib.error
        import urllib.request

        data = json.dumps({"events": payload}).encode("utf-8")
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self._platform_api_key:
            headers["Authorization"] = f"Bearer {self._platform_api_key}"
        req = urllib.request.Request(
            url,
            data=data,
            headers=headers,
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=30) as response:
                return bool(200 <= response.status < 300)
        except (urllib.error.URLError, urllib.error.HTTPError, OSError):
            return False

    def _rewrite_queue(self, remaining: list[dict[str, Any]]) -> None:
        """Rewrite the JSONL queue with only remaining (unsent) events."""
        if not remaining:
            self._input_path.write_text("", encoding="utf-8")
            return

        self._input_path.write_text("", encoding="utf-8")
        for record in remaining:
            self._writer.append_jsonl(self._input_path, record)
