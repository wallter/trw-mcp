"""Sync pull -- fetch intelligence state from backend -- PRD-INFRA-053."""

from __future__ import annotations

from pathlib import Path
from time import perf_counter
from typing import TYPE_CHECKING, Any, Literal
from urllib.parse import urlparse

import structlog
from pydantic import BaseModel

from trw_mcp.state._origin_project import ORIGIN_PROJECT_KEY, UNKNOWN_ORIGIN_PROJECT
from trw_mcp.state._platform_trust import platform_auth_headers, platform_contact_enabled
from trw_mcp.sync._pulled import finish_pulled_merge
from trw_mcp.sync._team_apply import merge_page
from trw_mcp.sync._team_entry import _local_node_id, team_learning_to_entry
from trw_mcp.sync._team_merge_result import TeamMergeResult
from trw_mcp.sync._team_prefetch import prefetch_existing
from trw_mcp.sync.identity import resolve_sync_client_id
from trw_mcp.sync.push import http_status_from_exception

logger = structlog.get_logger(__name__)

if TYPE_CHECKING:
    from trw_memory.models.memory import MemoryEntry

# PRD-INFRA-139 FR06: the server tags company-tier learnings with
# source=company_sync (in each entry's metadata) so the client can distinguish
# them from team learnings while merging via the same path. Team learnings carry
# no source tag and default to team_sync (existing behavior unchanged).
SyncSource = Literal["team_sync", "company_sync"]
_TEAM_SYNC_SOURCE: SyncSource = "team_sync"
_COMPANY_SYNC_SOURCE: SyncSource = "company_sync"
_KNOWN_SYNC_SOURCES: frozenset[SyncSource] = frozenset({_TEAM_SYNC_SOURCE, _COMPANY_SYNC_SOURCE})


def _resolve_sync_source(metadata: dict[str, str]) -> SyncSource:
    """Return the sync source tag from server metadata, defaulting to team_sync."""
    raw = metadata.get("source", "")
    return raw if raw in _KNOWN_SYNC_SOURCES else _TEAM_SYNC_SOURCE


class PullResult(BaseModel):
    """Result of a pull operation."""

    state: dict[str, Any] | None = None
    etag: str | None = None
    sync_hints: dict[str, Any] | None = None
    team_learnings: list[dict[str, Any]] | None = None
    status_code: int = 0
    not_modified: bool = False
    # PRD-INFRA-139 P1-B: company-tier rows page on an INDEPENDENT per-company
    # cursor disjoint from the org's pull_seq. The server advertises the company
    # high-water mark here; the client advances a SEPARATE company cursor from it
    # (never folding it into the org pull_seq).
    next_company_seq: int = 0


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
        trw_dir: Path | None,
    ) -> None:
        self._backend_url = backend_url.rstrip("/")
        self._api_key = api_key
        self._timeout = timeout
        self._client_id = (client_id or "").strip() or resolve_sync_client_id()
        self._trw_dir = trw_dir
        self._warn_if_insecure_url()

    async def pull_intel_state(
        self,
        etag: str | None = None,
        since_seq: int = 0,
        model_family: str = "",
        trw_version: str = "",
        client_id: str | None = None,
        since_company_seq: int = 0,
    ) -> PullResult | None:
        """GET /v1/intel/state. Returns a typed 304 result or None on real failure.

        PRD-FIX-087 FR01: async + httpx.AsyncClient so backend slowness
        yields the asyncio event loop instead of blocking it. The previous
        sync httpx.Client froze the loop for the entire timeout window
        (5s default), starving in-flight MCP tool calls in the same process.
        """
        import httpx

        started_at = perf_counter()
        effective_client_id = (client_id or "").strip() or self._client_id

        # W38 (7.0.0 security P1): ``platform_contact_enabled: false`` skips
        # this contact entirely — no request is attempted.
        if not platform_contact_enabled(self._trw_dir):
            logger.info(
                "sync_pull_skipped",
                event_type="sync_pull_skipped",
                reason="platform_contact_disabled",
                client_id=effective_client_id,
                outcome="skipped",
            )
            return None

        logger.info(
            "sync_pull_start",
            event_type="sync_pull_start",
            since_seq=since_seq,
            etag=etag or "",
            client_id=effective_client_id,
            outcome="start",
        )

        try:
            url = f"{self._backend_url}/v1/intel/state"
            # platform_auth_headers is the ONE function that may build this
            # header — see trw_mcp.state._platform_trust module docstring. A
            # poisoned backend_url (or a globally disabled
            # platform_contact_enabled) is never sent the credential; the
            # request still proceeds unauthenticated (the backend simply
            # rejects it) rather than being skipped, since no secret is at
            # risk once the header is withheld.
            headers: dict[str, str] = platform_auth_headers(url, self._api_key, source_trw_dir=self._trw_dir)
            if etag:
                headers["If-None-Match"] = f'"{etag}"'

            params: dict[str, Any] = {
                "since_seq": since_seq,
                "since_company_seq": since_company_seq,
                "client_id": effective_client_id,
            }
            if model_family:
                params["model_family"] = model_family
            if trw_version:
                params["trw_version"] = trw_version

            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.get(
                    url,
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
            raw_company_seq = data.get("next_company_seq", 0)
            next_company_seq = int(raw_company_seq) if isinstance(raw_company_seq, (int, float)) else 0
            return PullResult(
                state=data,
                etag=response_etag,
                sync_hints=sync_hints,
                team_learnings=team_learnings,
                status_code=resp.status_code,
                next_company_seq=next_company_seq,
            )
        except Exception as exc:  # justified: boundary, remote sync pull failures must not break local workflows
            logger.warning(
                "sync_pull_error",
                event_type="sync_pull_error",
                endpoint="/v1/intel/state",
                timeout_seconds=self._timeout,
                error_type=type(exc).__name__,
                error_message=str(exc)[:200],
                status_code=http_status_from_exception(exc),
                duration_ms=int((perf_counter() - started_at) * 1000),
                since_seq=since_seq,
                client_id=effective_client_id,
                outcome="error",
                exc_info=True,
            )
            return None

    def merge_team_learnings(
        self,
        team_learnings: list[dict[str, Any]] | None,
        *,
        namespace: str | None = None,
    ) -> TeamMergeResult:
        """Merge pulled team learnings into *namespace*, by default this checkout's project namespace.

        PRD-CORE-245 FR03: the pull target is named explicitly. A peer supplies
        the ``source_learning_id`` this path mints a local id from, so without a
        namespace predicate two peers emitting the same id into two namespaces
        resolve to the same local row and collapse into one. The rows are read
        and written through the checkout's store (PRD-CORE-298 FR01), so a
        migrated checkout merges over the daemon.

        Returns a :class:`TeamMergeResult` rather than the applied count: every
        way an item can fail to land is per-item and was invisible to the caller,
        which received the same number and the same ``outcome="success"`` log
        whether one item merged or forty-nine were refused around it.
        """
        if self._trw_dir is None:
            return TeamMergeResult(unavailable=True)
        if not team_learnings:
            return TeamMergeResult()

        try:
            from trw_mcp.state._store_selection import selected_store

            store, project_namespace = selected_store(self._trw_dir)
        except Exception:  # justified: a store that cannot be reached holds the pull cursor, never breaks sync
            logger.warning(
                "sync_team_merge_store_unavailable", event_type="sync_team_merge", outcome="error", exc_info=True
            )
            return TeamMergeResult(attempted=len(team_learnings), unavailable=True)

        target = namespace or project_namespace
        started_at = perf_counter()
        prefetched = prefetch_existing(store, target, team_learnings, self._local_team_learning_id)
        counts = merge_page(
            store,
            target,
            team_learnings,
            local_id_of=self._local_team_learning_id,
            merge_pulled=self._merge_pulled,
            prefetched=prefetched,
        )

        result = TeamMergeResult(
            attempted=len(team_learnings),
            inserted=counts["inserted"],
            merged=counts["merged"],
            unchanged=counts["unchanged"],
            skipped_no_id=counts["skipped_no_id"],
            invalid=counts["invalid"],
            quarantined=counts["quarantined"],
            blocked=counts["blocked"],
            failed=counts["failed"],
        )
        emit = logger.warning if result.rejected else logger.info
        emit(
            "sync_team_merge_complete",
            event_type="sync_team_merge",
            total=result.applied,
            duration_ms=int((perf_counter() - started_at) * 1000),
            **result.as_log_fields(),
        )
        return result

    def _merge_pulled(
        self, existing: MemoryEntry | None, raw_learning: dict[str, Any], local_id: str, target: str, source_id: str
    ) -> tuple[MemoryEntry, bool] | str:
        """The row to apply for *raw_learning* over *existing* and whether it is the peer's own
        revision (so it lands synced), or ``invalid`` / ``unchanged`` when nothing is to be written."""
        from trw_memory.sync.conflict import increment_clock, resolve_conflict

        remote_entry = team_learning_to_entry(
            self, raw_learning, local_id=existing.id if existing is not None else local_id, namespace=target
        )
        if remote_entry is None:
            return "invalid"
        if existing is not None and remote_entry.vector_clock and existing.vector_clock == remote_entry.vector_clock:
            return "unchanged"  # this revision already landed; merging it again would only append a conflict outcome
        local = existing
        if local is not None and local.last_synced_at is None:
            # An unpushed local edit does not tick the clock; count it here so a
            # teammate's revision merges with it instead of replacing it.
            local = local.model_copy(update={"vector_clock": increment_clock(local.vector_clock, _local_node_id())})
        winner = resolve_conflict(local, remote_entry) if local is not None else remote_entry
        if winner is local:
            # The local row already dominates this revision. Re-applying it would mark
            # an unpushed local edit synced, so the edit would never be pushed.
            return "unchanged"
        # Only the peer's own revision is what the server holds; a merge carries
        # local content too, so it stays dirty for the next push.
        remote_won = winner is remote_entry
        if existing is not None and winner.sync_seq < existing.sync_seq:
            # The store writes counter + 1; a revision built from the payload starts
            # at 0, which would let this client's next edit of the entry push a lower
            # counter than it already pushed, and the backend would call it stale.
            winner = winner.model_copy(update={"sync_seq": existing.sync_seq})
        resolved = self._normalize_team_sync_entry(
            winner,
            source_learning_id=source_id,
            remote_metadata=raw_learning.get("metadata"),
            pull_seq=raw_learning.get("sync_seq"),
        )
        resolved = finish_pulled_merge(resolved, existing, remote_won)
        return resolved, remote_won

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
        # PRD-CORE-278 FR08: record WHERE this row was authored, from the payload
        # only. The server currently sends no project identity (its
        # TeamLearningEntry model forbids extra fields, so it could only ever
        # arrive inside `metadata`), which is exactly why the literal `unknown`
        # is written rather than left absent: "we do not know" is a fact worth
        # recording, and the namespace, tags and content are NOT evidence of
        # origin — inferring from them would write a guess that the next reader
        # cannot tell from a fact.
        recorded_origin = str(metadata.get(ORIGIN_PROJECT_KEY, "")).strip()
        metadata[ORIGIN_PROJECT_KEY] = recorded_origin or UNKNOWN_ORIGIN_PROJECT
        # FR06: honor the server-provided source tag (company_sync vs team_sync).
        sync_source = _resolve_sync_source(metadata)
        return entry.model_copy(
            update={
                "source": sync_source,
                "source_identity": sync_source,
                "client_profile": sync_source,
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
