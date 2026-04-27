"""Sync pull -- fetch intelligence state from backend -- PRD-INFRA-053."""

from __future__ import annotations

from pathlib import Path
from time import perf_counter
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

import structlog
from pydantic import BaseModel

from trw_mcp.sync.identity import resolve_sync_client_id

logger = structlog.get_logger(__name__)

if TYPE_CHECKING:
    from trw_memory.models.memory import MemoryEntry


class PullResult(BaseModel):
    """Result of a pull operation."""

    state: dict[str, Any] | None = None
    etag: str | None = None
    sync_hints: dict[str, Any] | None = None
    team_learnings: list[dict[str, Any]] | None = None
    status_code: int = 0
    not_modified: bool = False


def _validate_pull_payload(raw_data: object) -> tuple[dict[str, Any], str, dict[str, Any], list[dict[str, Any]]]:
    """Validate the 200 pull payload before treating it as a successful sync."""
    if not isinstance(raw_data, dict):
        raise TypeError("pull response body must be a JSON object")

    etag = raw_data.get("etag")
    if not isinstance(etag, str) or not etag.strip():
        raise ValueError("pull response missing valid etag")

    sync_hints = raw_data.get("sync_hints")
    if not isinstance(sync_hints, dict):
        raise TypeError("pull response missing valid sync_hints")

    team_learnings = raw_data.get("team_learnings")
    if not isinstance(team_learnings, list) or any(not isinstance(item, dict) for item in team_learnings):
        raise ValueError("pull response missing valid team_learnings")

    return raw_data, etag, sync_hints, team_learnings


class SyncPuller:
    """Pull intelligence state from backend and merge team learnings. Never raises."""

    def __init__(
        self,
        backend_url: str,
        api_key: str,
        timeout: float = 5.0,
        *,
        client_id: str | None = None,
        trw_dir: Path | None = None,
    ) -> None:
        self._backend_url = backend_url.rstrip("/")
        self._api_key = api_key
        self._timeout = timeout
        self._client_id = (client_id or "").strip() or resolve_sync_client_id()
        self._trw_dir = trw_dir
        self._warn_if_insecure_url()

    def pull_intel_state(
        self,
        etag: str | None = None,
        since_seq: int = 0,
        model_family: str = "",
        trw_version: str = "",
        client_id: str | None = None,
    ) -> PullResult | None:
        """GET /v1/intel/state. Returns a typed 304 result or None on real failure."""
        import httpx

        started_at = perf_counter()
        effective_client_id = (client_id or "").strip() or self._client_id
        logger.info(
            "sync_pull_start",
            event_type="sync_pull_start",
            since_seq=since_seq,
            etag=etag or "",
            client_id=effective_client_id,
            outcome="start",
        )

        try:
            headers: dict[str, str] = {
                "Authorization": f"Bearer {self._api_key}",
            }
            if etag:
                headers["If-None-Match"] = f'"{etag}"'

            params: dict[str, Any] = {
                "since_seq": since_seq,
                "client_id": effective_client_id,
            }
            if model_family:
                params["model_family"] = model_family
            if trw_version:
                params["trw_version"] = trw_version

            with httpx.Client(timeout=self._timeout) as client:
                resp = client.get(
                    f"{self._backend_url}/v1/intel/state",
                    headers=headers,
                    params=params,
                )

            duration_ms = int((perf_counter() - started_at) * 1000)
            if resp.status_code == 304:
                logger.info(
                    "sync_pull_complete",
                    event_type="sync_pull_complete",
                    status_code=304,
                    duration_ms=duration_ms,
                    since_seq=since_seq,
                    client_id=effective_client_id,
                    team_learnings_count=0,
                    outcome="not_modified",
                )
                return PullResult(
                    etag=etag,
                    status_code=304,
                    not_modified=True,
                )

            resp.raise_for_status()
            data, response_etag, sync_hints, team_learnings = _validate_pull_payload(resp.json())
            logger.info(
                "sync_pull_complete",
                event_type="sync_pull_complete",
                status_code=resp.status_code,
                duration_ms=duration_ms,
                since_seq=since_seq,
                client_id=effective_client_id,
                team_learnings_count=len(team_learnings) if isinstance(team_learnings, list) else 0,
                outcome="success",
            )
            return PullResult(
                state=data,
                etag=response_etag,
                sync_hints=sync_hints,
                team_learnings=team_learnings,
                status_code=resp.status_code,
            )
        except Exception as exc:  # justified: boundary, remote sync pull failures must not break local workflows
            logger.warning(
                "sync_pull_error",
                event_type="sync_pull_error",
                error_type=type(exc).__name__,
                error_message=str(exc)[:200],
                duration_ms=int((perf_counter() - started_at) * 1000),
                since_seq=since_seq,
                client_id=effective_client_id,
                outcome="error",
                exc_info=True,
            )
            return None

    def pull_team_learnings(
        self,
        since_seq: int,
        *,
        etag: str | None = None,
        model_family: str = "",
        trw_version: str = "",
        client_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return only the team learning section of a pull response."""
        result = self.pull_intel_state(
            etag=etag,
            since_seq=since_seq,
            model_family=model_family,
            trw_version=trw_version,
            client_id=client_id,
        )
        if result is None or not isinstance(result.team_learnings, list):
            return []
        return result.team_learnings

    def merge_team_learnings(self, team_learnings: list[dict[str, Any]] | None) -> int:
        """Merge pulled team learnings into local storage."""
        if not team_learnings or self._trw_dir is None:
            return 0

        try:
            from trw_memory.models.config import MemoryConfig
            from trw_memory.security.runtime import prepare_entry_for_store, store_quarantined_entry
            from trw_memory.storage._row_mapper import row_to_entry
            from trw_memory.sync.conflict import resolve_conflict
            from trw_memory.sync.delta import DeltaTracker

            from trw_mcp.state._memory_connection import get_backend as _get_backend
        except Exception:  # justified: import-guard, optional sync merge dependencies may be unavailable
            logger.warning("sync_team_merge_import_error", event_type="sync_team_merge", outcome="error", exc_info=True)
            return 0

        started_at = perf_counter()
        inserted = 0
        merged = 0
        backend = _get_backend(self._trw_dir)
        sec_cfg = MemoryConfig(storage_path=str(self._trw_dir / "memory"))

        def find_existing(source_learning_id: str) -> MemoryEntry | None:
            conn = getattr(backend, "_conn", None)
            if conn is not None:
                row = conn.execute(
                    "SELECT * FROM memories WHERE remote_id = ? OR id = ? OR id = ? LIMIT 1",
                    (
                        source_learning_id,
                        self._local_team_learning_id(source_learning_id),
                        source_learning_id,
                    ),
                ).fetchone()
                if row is not None:
                    return row_to_entry(tuple(row))
            limit = max(backend.count(), 1)
            for candidate in backend.list_entries(limit=limit):
                if candidate.remote_id == source_learning_id:
                    return candidate
            return None

        for raw_learning in team_learnings:
            source_learning_id = str(raw_learning.get("source_learning_id", "")).strip()
            if not source_learning_id:
                continue

            existing = find_existing(source_learning_id)
            local_id = existing.id if existing is not None else self._local_team_learning_id(source_learning_id)
            remote_entry = self._team_learning_to_entry(raw_learning, local_id=local_id)
            if remote_entry is None:
                continue

            resolved = self._normalize_team_sync_entry(
                resolve_conflict(existing, remote_entry) if existing is not None else remote_entry,
                source_learning_id=source_learning_id,
                remote_metadata=raw_learning.get("metadata"),
                pull_seq=raw_learning.get("sync_seq"),
            )
            try:
                decision = prepare_entry_for_store(resolved, backend=backend, config=sec_cfg, session_id=None)
                if decision.quarantined:
                    store_quarantined_entry(sec_cfg, decision.entry)
                    continue
                backend.store(decision.entry)
                DeltaTracker.mark_synced([resolved.id], backend)
                if existing is None:
                    inserted += 1
                else:
                    merged += 1
            except Exception:  # justified: per-item, one invalid team learning must not abort the full merge
                logger.warning(
                    "sync_team_merge_entry_error",
                    event_type="sync_team_merge",
                    outcome="error",
                    source_learning_id=source_learning_id,
                    exc_info=True,
                )

        total = inserted + merged
        logger.info(
            "sync_team_merge_complete",
            event_type="sync_team_merge",
            inserted=inserted,
            merged=merged,
            total=total,
            duration_ms=int((perf_counter() - started_at) * 1000),
            outcome="success",
        )
        return total

    def _team_learning_to_entry(self, raw_learning: dict[str, Any], *, local_id: str) -> MemoryEntry | None:
        try:
            from trw_memory.models.memory import MemoryEntry, MemoryStatus, MemoryType

            metadata = {str(key): str(value) for key, value in dict(raw_learning.get("metadata") or {}).items()}
            pull_seq = raw_learning.get("sync_seq")
            if pull_seq is not None:
                metadata["team_sync_pull_seq"] = str(pull_seq)

            return MemoryEntry(
                id=local_id,
                remote_id=str(raw_learning.get("source_learning_id", "")).strip() or None,
                content=str(raw_learning.get("summary", "")),
                detail=str(raw_learning.get("detail", "")),
                tags=[str(tag) for tag in raw_learning.get("tags", []) if isinstance(tag, str)],
                importance=self._coerce_importance(raw_learning.get("impact")),
                status=MemoryStatus(str(raw_learning.get("status", "active"))),
                type=MemoryType(str(raw_learning.get("type", "pattern"))),
                vector_clock=self._coerce_vector_clock(raw_learning.get("vector_clock")),
                source="team_sync",
                source_identity="team_sync",
                client_profile="team_sync",
                metadata=metadata,
            )
        except Exception:  # justified: boundary, malformed remote payload must fail open for that entry
            logger.warning(
                "sync_team_merge_invalid_entry",
                event_type="sync_team_merge",
                outcome="error",
                source_learning_id=str(raw_learning.get("source_learning_id", "")),
                exc_info=True,
            )
            return None

    def _normalize_team_sync_entry(
        self,
        entry: MemoryEntry,
        *,
        source_learning_id: str,
        remote_metadata: object,
        pull_seq: object,
    ) -> MemoryEntry:
        metadata = {str(key): str(value) for key, value in dict(entry.metadata or {}).items()}
        if isinstance(remote_metadata, dict):
            metadata.update({str(key): str(value) for key, value in remote_metadata.items()})
        if pull_seq is not None:
            metadata["team_sync_pull_seq"] = str(pull_seq)
        return entry.model_copy(
            update={
                "source": "team_sync",
                "source_identity": "team_sync",
                "client_profile": "team_sync",
                "remote_id": source_learning_id,
                "metadata": metadata,
            }
        )

    @staticmethod
    def _coerce_importance(raw: object) -> float:
        if isinstance(raw, (int, float)):
            return max(0.0, min(float(raw), 1.0))
        return 0.5

    @staticmethod
    def _coerce_vector_clock(raw: object) -> dict[str, int]:
        if not isinstance(raw, dict):
            return {}
        result: dict[str, int] = {}
        for key, value in raw.items():
            try:
                result[str(key)] = int(value)
            except (TypeError, ValueError):
                continue
        return result

    @staticmethod
    def _local_team_learning_id(source_learning_id: str) -> str:
        return f"team-sync-{source_learning_id}"

    def _warn_if_insecure_url(self) -> None:
        parsed = urlparse(self._backend_url)
        if parsed.scheme != "http":
            return
        if parsed.hostname in {"localhost", "127.0.0.1", "::1"}:
            return
        logger.warning("sync_pull_insecure_url", url=self._backend_url)
