"""Batch push of dirty learnings and outcomes to backend — PRD-INFRA-051-FR09.

Follows the fail-open pattern from trw-memory/sync/remote.py:
never raises, returns PushResult on all paths.
"""

from __future__ import annotations

from time import perf_counter
from typing import TYPE_CHECKING

import structlog
from pydantic import BaseModel

from trw_mcp.sync.identity import resolve_sync_client_id

if TYPE_CHECKING:
    from trw_memory.models.memory import MemoryEntry

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
        client_id: str | None = None,
    ) -> None:
        self._backend_url = backend_url.rstrip("/")
        self._api_key = api_key
        self._batch_size = batch_size
        self._timeout = timeout
        self._client_id = (client_id or "").strip() or resolve_sync_client_id()

    def push_learnings(self, entries: list[MemoryEntry]) -> PushResult:
        """Batch push learnings to POST /v1/sync/learnings. Never raises."""
        import httpx

        if not entries:
            return PushResult()

        started_at = perf_counter()
        total_pushed = 0
        total_failed = 0
        total_skipped = 0

        logger.info(
            "sync_push_start",
            event_type="sync_push_start",
            client_id=self._client_id,
            entry_count=len(entries),
            batch_size=self._batch_size,
            outcome="start",
        )

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
            except Exception as exc:  # justified: boundary, remote sync push failures are isolated per batch
                logger.warning(
                    "sync_push_error",
                    event_type="sync_push_error",
                    client_id=self._client_id,
                    batch_index=i // self._batch_size,
                    count=len(batch),
                    duration_ms=int((perf_counter() - started_at) * 1000),
                    error_type=type(exc).__name__,
                    error_message=str(exc)[:200],
                    outcome="error",
                    exc_info=True,
                )
                total_failed += len(batch)

        logger.info(
            "sync_push_complete",
            event_type="sync_push_complete",
            client_id=self._client_id,
            pushed=total_pushed,
            failed=total_failed,
            skipped=total_skipped,
            duration_ms=int((perf_counter() - started_at) * 1000),
            outcome="success" if total_failed == 0 else "partial_error",
        )
        return PushResult(pushed=total_pushed, failed=total_failed, skipped=total_skipped)

    def push_outcomes(self, outcomes: list[dict[str, object]]) -> PushResult:
        """Batch push outcomes to POST /v1/sync/outcomes. Never raises."""
        import httpx

        if not outcomes:
            return PushResult()

        started_at = perf_counter()
        payload = {
            "outcomes": outcomes,
            "client_id": self._get_client_id(),
        }
        logger.info(
            "sync_push_outcomes_start",
            event_type="sync_push_outcomes_start",
            client_id=self._client_id,
            count=len(outcomes),
            outcome="start",
        )
        try:
            with httpx.Client(timeout=self._timeout) as client:
                resp = client.post(
                    f"{self._backend_url}/v1/sync/outcomes",
                    json=payload,
                    headers={"Authorization": f"Bearer {self._api_key}"},
                )
                resp.raise_for_status()
                result = resp.json()
                pushed = result.get("inserted", 0)
                logger.info(
                    "sync_push_outcomes_complete",
                    event_type="sync_push_outcomes_complete",
                    client_id=self._client_id,
                    pushed=pushed,
                    duration_ms=int((perf_counter() - started_at) * 1000),
                    outcome="success",
                )
                return PushResult(pushed=pushed)
        except Exception as exc:  # justified: boundary, outcome push failures must not block local completion
            logger.warning(
                "sync_push_outcomes_error",
                event_type="sync_push_outcomes_error",
                client_id=self._client_id,
                count=len(outcomes),
                duration_ms=int((perf_counter() - started_at) * 1000),
                error_type=type(exc).__name__,
                error_message=str(exc)[:200],
                outcome="error",
                exc_info=True,
            )
            return PushResult(failed=len(outcomes))

    def _serialize_entry(self, entry: MemoryEntry) -> dict[str, object]:
        """Serialize a MemoryEntry for push, applying anonymization."""
        d: dict[str, object] = entry.to_dict() if hasattr(entry, "to_dict") else dict(entry)
        raw_impact = d.get("importance") or d.get("impact") or 0.5
        impact = float(raw_impact) if isinstance(raw_impact, (int, float)) else 0.5
        raw_tags = d.get("tags", [])
        tags = list(raw_tags)[:20] if isinstance(raw_tags, list) else []
        return {
            "source_learning_id": d.get("id", ""),
            "sync_hash": d.get("sync_hash", ""),
            "summary": str(d.get("summary", d.get("content", "")))[:1000],
            "detail": str(d.get("detail", ""))[:10000] if d.get("detail") else None,
            "impact": impact,
            "tags": tags,
            "type": str(d.get("type", "pattern")),
            "status": str(d.get("status", "active")),
            "metadata": {},
        }

    def _get_client_id(self) -> str:
        """Generate a stable client identifier."""
        return self._client_id
