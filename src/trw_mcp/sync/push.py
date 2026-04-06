"""Batch push of dirty learnings and outcomes to backend — PRD-INFRA-051-FR09.

Follows the fail-open pattern from trw-memory/sync/remote.py:
never raises, returns PushResult on all paths.
"""

from __future__ import annotations

from typing import Any

import structlog
from pydantic import BaseModel

logger = structlog.get_logger(__name__)


class PushResult(BaseModel):
    """Result of a push operation."""
    pushed: int = 0
    failed: int = 0
    skipped: int = 0


class SyncPusher:
    """Batch push dirty learnings and outcomes to backend."""

    def __init__(
        self,
        backend_url: str,
        api_key: str,
        batch_size: int = 100,
        timeout: float = 10.0,
    ) -> None:
        self._backend_url = backend_url.rstrip("/")
        self._api_key = api_key
        self._batch_size = batch_size
        self._timeout = timeout

    def push_learnings(self, entries: list[Any]) -> PushResult:
        """Batch push learnings to POST /v1/sync/learnings. Never raises."""
        import httpx

        if not entries:
            return PushResult()

        total_pushed = 0
        total_failed = 0
        total_skipped = 0

        # Batch entries
        for i in range(0, len(entries), self._batch_size):
            batch = entries[i:i + self._batch_size]
            payload = {
                "entries": [self._serialize_entry(e) for e in batch],
                "client_id": self._get_client_id(),
                "push_seq": max(e.sync_seq for e in batch) if batch else 0,
            }
            try:
                with httpx.Client(timeout=self._timeout) as client:
                    resp = client.post(
                        f"{self._backend_url}/v1/sync/learnings",
                        json=payload,
                        headers={"Authorization": f"Bearer {self._api_key}"},
                    )
                    resp.raise_for_status()
                    result = resp.json()
                    total_pushed += result.get("inserted", 0) + result.get("updated", 0)
                    total_skipped += result.get("skipped", 0)
            except Exception:
                logger.debug("sync_push_failed", batch_index=i // self._batch_size, count=len(batch))
                total_failed += len(batch)

        logger.info(
            "sync_push_completed",
            pushed=total_pushed,
            failed=total_failed,
            skipped=total_skipped,
        )
        return PushResult(pushed=total_pushed, failed=total_failed, skipped=total_skipped)

    def push_outcomes(self, outcomes: list[dict[str, object]]) -> PushResult:
        """Batch push outcomes to POST /v1/sync/outcomes. Never raises."""
        import httpx

        if not outcomes:
            return PushResult()

        payload = {
            "outcomes": outcomes,
            "client_id": self._get_client_id(),
        }
        try:
            with httpx.Client(timeout=self._timeout) as client:
                resp = client.post(
                    f"{self._backend_url}/v1/sync/outcomes",
                    json=payload,
                    headers={"Authorization": f"Bearer {self._api_key}"},
                )
                resp.raise_for_status()
                result = resp.json()
                return PushResult(pushed=result.get("inserted", 0))
        except Exception:
            logger.debug("sync_push_outcomes_failed", count=len(outcomes))
            return PushResult(failed=len(outcomes))

    def _serialize_entry(self, entry: Any) -> dict[str, object]:
        """Serialize a MemoryEntry for push, applying anonymization."""
        d = entry.to_dict() if hasattr(entry, "to_dict") else dict(entry)
        return {
            "source_learning_id": d.get("id", ""),
            "sync_hash": d.get("sync_hash", ""),
            "summary": str(d.get("summary", d.get("content", "")))[:1000],
            "detail": str(d.get("detail", ""))[:10000] if d.get("detail") else None,
            "impact": float(d.get("importance", d.get("impact", 0.5))),
            "tags": list(d.get("tags", []))[:20],
            "type": str(d.get("type", "pattern")),
            "status": str(d.get("status", "active")),
            "metadata": {},
        }

    def _get_client_id(self) -> str:
        """Generate a stable client identifier."""
        import hashlib
        import socket
        raw = f"{socket.gethostname()}-{id(self)}"
        return hashlib.sha256(raw.encode()).hexdigest()[:32]
