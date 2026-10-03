"""Batch push of dirty learnings and outcomes to backend — PRD-INFRA-051-FR09.

Follows the fail-open pattern from trw-memory/sync/remote.py:
never raises, returns PushResult on all paths.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from functools import partial
from pathlib import Path
from time import perf_counter
from typing import TYPE_CHECKING

import structlog
from pydantic import BaseModel
from trw_memory.labels import LabelPolicy, Sink
from trw_memory.security.egress_gate import entry_egress_refused
from trw_memory.security.pii import anonymize_installation_id, redact_paths

from trw_mcp.state._origin_project import ORIGIN_PROJECT_KEY
from trw_mcp.state._platform_trust import platform_auth_headers, platform_contact_enabled, send_policy
from trw_mcp.state._project_identity import project_id
from trw_mcp.sync._push_batch import send_learning_batch
from trw_mcp.sync.identity import resolve_sync_client_id
from trw_mcp.telemetry.anonymizer import redact_secrets

if TYPE_CHECKING:
    from trw_memory.models.memory import MemoryEntry

logger = structlog.get_logger(__name__)


# The platform sync API validates `LearningSync.sync_hash` against this exact
# pattern under `extra="forbid"`. An empty/invalid hash fails the
# pattern and FastAPI 422-rejects the ENTIRE batch (up to 500 entries) — the
# primary driver of the 2026-05-20 MCP-server team-sync snare. Keep a valid
# stored hash; otherwise synthesize a stable content hash so a single unhashed
# (e.g. legacy) row can never sink the whole batch.
_SYNC_HASH_RE = re.compile(r"[0-9a-f]{64}")
_SYNC_HASH_CONTENT_FIELDS = (
    "source_learning_id",
    "summary",
    "detail",
    "impact",
    "tags",
    "type",
    "status",
)


def _is_valid_sync_hash(value: object) -> bool:
    """True when value is a 64-char lowercase-hex string the backend accepts."""
    return isinstance(value, str) and _SYNC_HASH_RE.fullmatch(value) is not None


def _content_sync_hash(payload: dict[str, object]) -> str:
    """Deterministic SHA-256 (64 hex) over the outgoing content fields.

    Mirrors trw-memory ``DeltaTracker.compute_sync_hash`` serialization (sorted
    keys, compact separators) so re-pushing unchanged content yields the same
    hash and the backend idempotency SKIP/UPDATE path keeps working.
    """
    canonical = {key: payload.get(key) for key in _SYNC_HASH_CONTENT_FIELDS}
    raw = json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _sanitize_metadata_value(value: object) -> object:
    """Redact secrets and PII from a metadata value, recursing through lists and dicts.

    Nested KEYS are redacted too: metadata is caller-supplied, so a key can carry a
    pasted credential (two keys that redact alike collapse; both values are already
    redacted, so nothing leaks). Egress is the sole
    masking boundary now that the write path stores verbatim
    (``trw_memory.security._runtime_pii``), so an unsanitized value here leaves
    the machine raw.
    """
    if isinstance(value, str):
        return redact_secrets(value)
    if isinstance(value, (list, tuple)):
        return [_sanitize_metadata_value(item) for item in value]
    if isinstance(value, dict):
        return {redact_secrets(str(key)): _sanitize_metadata_value(item) for key, item in value.items()}
    return value


def http_status_from_exception(exc: BaseException) -> int | None:
    """Extract an HTTP status code from httpx-style exceptions when present (shared by pull and backup)."""
    response = getattr(exc, "response", None)
    raw_status = getattr(response, "status_code", None)
    return int(raw_status) if isinstance(raw_status, int) else None


def _any_refused(rows: dict[str, MemoryEntry], ids: list[str]) -> bool:
    """Whether the quarantine check refuses any of *ids* now (PRD-CORE-333: asked before every re-send too)."""
    return any(entry_egress_refused(rows[entry_id]) is not None for entry_id in ids)


def split_by_label(entries: list[MemoryEntry]) -> tuple[list[MemoryEntry], list[MemoryEntry]]:
    """``(rows the platform may have, rows labelled above team)``, each in order (PRD-SEC-023 FR05)."""
    admitted = LabelPolicy.current().admit(entries, Sink.PLATFORM).admitted
    kept = {id(entry) for entry in admitted}
    return admitted, [entry for entry in entries if id(entry) not in kept]


class PushResult(BaseModel):
    """Result of a push operation."""

    #: PRD-SEC-023 FR05: rows labelled above team that were not sent (a count only).
    withheld_by_label: int = 0
    pushed: int = 0
    failed: int = 0
    skipped: int = 0
    #: Entry id -> the server's reason, for entries the backend refused (INC-147).
    rejected: dict[str, str] = {}
    #: The last error a failed request met, naming its HTTP status and the server's words.
    last_error: str | None = None


class SyncPusher:
    """Batch push dirty learnings and outcomes to backend."""

    def __init__(
        self,
        backend_url: str,
        api_key: str,
        batch_size: int = 100,
        timeout: float = 10.0,
        client_id: str | None = None,
        *,
        source_trw_dir: Path | None,
        learning_sharing_enabled: bool = False,
        platform_telemetry_enabled: bool = False,
    ) -> None:
        self._backend_url = backend_url.rstrip("/")
        self._api_key = api_key
        self._batch_size = batch_size
        self._timeout = timeout
        self._client_id = (client_id or "").strip() or resolve_sync_client_id()
        self._project_root = os.getenv("TRW_PROJECT_ROOT", os.getcwd())
        # The .trw the pushed learnings and outcomes are read from; its policy governs these sends.
        self._source_trw_dir = source_trw_dir
        # PRD-SEC-004-FR05/FR01: the background sync push is a second off-machine
        # egress path (alongside publisher.py). Learning CONTENT (summary+detail)
        # rides /v1/sync/learnings and is gated by learning_sharing_enabled;
        # session-outcome metrics ride /v1/sync/outcomes and are gated by the
        # anonymous-usage flag platform_telemetry_enabled. Both default False
        # (fail-closed for egress) so a pusher constructed without explicit
        # consent never transmits — belt-and-suspenders behind the cycle-level
        # gate in BackendSyncClient._run_one_cycle.
        self._learning_sharing_enabled = learning_sharing_enabled
        self._platform_telemetry_enabled = platform_telemetry_enabled

    @property
    def source_trw_dir(self) -> Path | None:
        """The ``.trw`` this pusher's payload is read from, whose send policy governs it."""
        return self._source_trw_dir

    async def push_learnings(self, entries: list[MemoryEntry]) -> PushResult:
        """Batch push learnings to POST /v1/sync/learnings. Never raises.

        PRD-FIX-087 FR02: async + httpx.AsyncClient so each batch yields
        the asyncio event loop instead of blocking it. Pre-fix, 44+
        sequential POST batches blocked the loop for 5+ seconds total.
        """

        if not entries:
            return PushResult()

        # PRD-SEC-004-FR05: defensive consent gate at the egress boundary. A
        # user who set learning_sharing_enabled=false (the default) NEVER has
        # learning summary/detail leave the machine via background sync, even if
        # a caller hands this pusher dirty entries. Zero off-machine POST when
        # disabled; entries stay locally dirty for a future consented push.
        policy = send_policy(self._source_trw_dir)  # the payload project's own consent and switch
        if not (self._learning_sharing_enabled and policy.learning_sharing):
            logger.debug(
                "sync_push_skipped",
                reason="learning_sharing_disabled",
                client_id=self._client_id,
                entry_count=len(entries),
            )
            return PushResult()

        # P1-C follow-up: platform_contact_enabled is the global egress kill
        # switch. platform_auth_headers only withholds the bearer for an
        # untrusted host below -- it must not be the only thing standing
        # between a disabled switch and an unauthenticated POST of learning
        # CONTENT. No request is attempted; entries stay locally dirty for a
        # future consented push, same as the learning_sharing_enabled gate above.
        if not platform_contact_enabled(self._source_trw_dir):
            logger.debug(
                "sync_push_skipped",
                reason="platform_contact_disabled",
                client_id=self._client_id,
                entry_count=len(entries),
            )
            return PushResult()

        # PRD-SEC-023 FR05: no row labelled above team leaves the host, whoever hands it to this pusher. The sync cycle
        # already withholds them (and marks them synced); this is the egress point itself, so it asks again.
        entries, labelled = split_by_label(entries)
        withheld = len(labelled)
        if withheld:
            logger.info("sync_push_withheld_by_label", client_id=self._client_id, withheld=withheld)
        if not entries:
            return PushResult(withheld_by_label=withheld)

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

        # Batch entries. INC-147: an entry the backend rejects is isolated and
        # returned with its reason; the rest of its batch is still pushed.
        rejected: dict[str, str] = {}
        last_error: str | None = None
        url = f"{self._backend_url}/v1/sync/learnings"
        for i in range(0, len(entries), self._batch_size):
            if not platform_contact_enabled(self._source_trw_dir) or not self._learnings_consented():
                break  # every request asks (B71-106, CONSENT-FLAGS-READ-LIVE); unsent entries stay dirty
            batch = entries[i : i + self._batch_size]
            # PRD-CORE-333: asked immediately before the POST, so a row quarantined after it was paged never leaves the
            # host. The push stops here rather than dropping the row: the cycle acknowledges by count, as a prefix of
            # the page, so the unsent rows stay dirty and the next page leaves the blocked row out.
            if refusal := next(filter(None, map(entry_egress_refused, batch)), None):
                logger.info("sync_push_stopped_by_quarantine", client_id=self._client_id, reason=refusal)
                break

            items = [self._serialize_entry(e) for e in batch]
            by_id = {str(item["source_learning_id"]): entry for item, entry in zip(items, batch, strict=True)}
            outcome = await send_learning_batch(
                partial(self._post_learnings, url),
                [(str(item["source_learning_id"]), item) for item in items],
                client_id=self._client_id,
                refused=partial(_any_refused, by_id),
            )
            total_pushed += outcome.pushed
            total_skipped += outcome.skipped
            total_failed += outcome.failed
            rejected.update(outcome.rejected)
            last_error = outcome.last_error or last_error

        logger.info(
            "sync_push_complete",
            event_type="sync_push_complete",
            client_id=self._client_id,
            pushed=total_pushed,
            failed=total_failed,
            skipped=total_skipped,
            rejected=len(rejected),
            duration_ms=int((perf_counter() - started_at) * 1000),
            outcome="success" if total_failed == 0 else "partial_error",
        )
        return PushResult(
            pushed=total_pushed,
            failed=total_failed,
            skipped=total_skipped,
            withheld_by_label=withheld,
            rejected=rejected,
            last_error=last_error,
        )

    def _learnings_consented(self) -> bool:
        """Whether learning content may leave this host RIGHT NOW: the caller-side flag AND the payload project's consent,
        read from its ``config.yaml`` on every call (CONSENT-FLAGS-READ-LIVE). A consent withdrawn while the server runs
        stops the very next request, including the batches of a push already under way."""
        return self._learning_sharing_enabled and send_policy(self._source_trw_dir).learning_sharing

    async def _post_learnings(self, url: str, entry_payloads: list[dict[str, object]]) -> object:
        """POST one ``entries`` payload; raises on a transport failure (the batch sender handles it).

        A 422 makes the batch sender re-send or split, and every one of those is a
        new request, so each asks the live egress switch (B71-106); a refusal
        raises, which counts the entries failed and keeps them dirty.
        """
        if not platform_contact_enabled(self._source_trw_dir):
            raise PermissionError("platform contact was disabled; entries stay dirty")
        if not self._learnings_consented():
            raise PermissionError("learning sharing was withdrawn; entries stay dirty")
        import httpx

        payload = {
            "entries": entry_payloads,
            "client_id": self._get_client_id(),
            "push_seq": max(int(str(e.get("sync_seq") or 0)) for e in entry_payloads),
        }
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            # platform_auth_headers is the ONE function that may build this header —
            # see _platform_trust module docstring. An untrusted backend_url
            # (project-tracked config) or a disabled platform_contact_enabled never
            # receives the bearer.
            return await client.post(
                url,
                json=payload,
                headers=platform_auth_headers(url, self._api_key, source_trw_dir=self._source_trw_dir),
            )

    async def push_outcomes(self, outcomes: list[dict[str, object]]) -> PushResult:
        """Batch push outcomes to POST /v1/sync/outcomes. Never raises.

        PRD-FIX-087 FR02: async + httpx.AsyncClient.
        """
        import httpx

        if not outcomes:
            return PushResult()

        # PRD-SEC-004-FR01: session-outcome metrics are anonymous usage
        # telemetry — defensively gated on platform_telemetry_enabled. Zero
        # off-machine POST when disabled (the documented opt-out flag); pending
        # outcomes remain locally queued for a future consented push.
        policy = send_policy(self._source_trw_dir)
        if not (self._platform_telemetry_enabled and policy.platform_telemetry):
            logger.debug(
                "sync_push_outcomes_skipped",
                reason="platform_telemetry_disabled",
                client_id=self._client_id,
                count=len(outcomes),
            )
            return PushResult()

        if not platform_contact_enabled(self._source_trw_dir):
            logger.debug(
                "sync_push_outcomes_skipped",
                reason="platform_contact_disabled",
                client_id=self._client_id,
                count=len(outcomes),
            )
            return PushResult()

        started_at = perf_counter()
        total_pushed = 0
        total_failed = 0
        logger.info(
            "sync_push_outcomes_start",
            event_type="sync_push_outcomes_start",
            client_id=self._client_id,
            count=len(outcomes),
            outcome="start",
        )
        for i in range(0, len(outcomes), self._batch_size):
            if not platform_contact_enabled(self._source_trw_dir):  # every request asks (B71-106)
                break
            if not (self._platform_telemetry_enabled and send_policy(self._source_trw_dir).platform_telemetry):
                break  # consent withdrawn mid-push (CONSENT-FLAGS-READ-LIVE): the rest stays queued
            batch = outcomes[i : i + self._batch_size]
            payload = {
                "outcomes": batch,
                "client_id": self._get_client_id(),
            }
            url = f"{self._backend_url}/v1/sync/outcomes"
            try:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    resp = await client.post(
                        url,
                        json=payload,
                        headers=platform_auth_headers(url, self._api_key, source_trw_dir=self._source_trw_dir),
                    )
                    resp.raise_for_status()
                    result = resp.json()
                    total_pushed += result.get("inserted", 0)
            except Exception as exc:  # justified: boundary, outcome push failures must not block local completion
                logger.warning(
                    "sync_push_outcomes_error",
                    event_type="sync_push_outcomes_error",
                    client_id=self._client_id,
                    endpoint="/v1/sync/outcomes",
                    batch_index=i // self._batch_size,
                    count=len(batch),
                    duration_ms=int((perf_counter() - started_at) * 1000),
                    error_type=type(exc).__name__,
                    error_message=str(exc)[:200],
                    status_code=http_status_from_exception(exc),
                    outcome="error",
                    exc_info=True,
                )
                total_failed += len(batch)

        logger.info(
            "sync_push_outcomes_complete",
            event_type="sync_push_outcomes_complete",
            client_id=self._client_id,
            pushed=total_pushed,
            failed=total_failed,
            duration_ms=int((perf_counter() - started_at) * 1000),
            outcome="success" if total_failed == 0 else "partial_error",
        )
        return PushResult(pushed=total_pushed, failed=total_failed)

    def _own_project_id(self, namespace: str) -> str:
        """This project's id, ``""`` when it cannot be computed (the row then goes out as before)."""
        root = self._source_trw_dir.parent if self._source_trw_dir is not None else Path(self._project_root)
        try:
            return project_id(root, namespace=namespace)
        # trw-fail-silent-allow: the id is an optional field; a push never fails for want of it
        except Exception:  # justified: a lone-surrogate namespace raises UnicodeEncodeError, not only OSError
            logger.warning("sync_push_project_id_unavailable", event_type="sync_push", exc_info=True)
            return ""

    def _serialize_entry(self, entry: MemoryEntry) -> dict[str, object]:
        """Serialize a MemoryEntry for push, applying anonymization."""
        d: dict[str, object] = entry.to_dict() if hasattr(entry, "to_dict") else dict(entry)
        raw_impact = d.get("importance") or d.get("impact") or 0.5
        impact = float(raw_impact) if isinstance(raw_impact, (int, float)) else 0.5
        raw_tags = d.get("tags", [])
        # Tags and metadata are egressed content just like summary/detail: a
        # credential or address pasted into a tag used to be masked by the write
        # path, which no longer mutates anything, so this boundary owns it.
        tags = [redact_secrets(str(tag)) for tag in list(raw_tags)[:20]] if isinstance(raw_tags, list) else []
        summary = redact_secrets(str(d.get("summary", d.get("content", ""))))
        summary = redact_paths(summary, self._project_root)[:1000]
        raw_detail = d.get("detail")
        detail: str | None = None
        if raw_detail:
            detail = redact_paths(redact_secrets(str(raw_detail)), self._project_root)[:10000]
        raw_vector_clock = d.get("vector_clock", {})
        vector_clock = dict(raw_vector_clock) if isinstance(raw_vector_clock, dict) else {}
        raw_metadata = d.get("metadata", {})
        raw_metadata = raw_metadata if isinstance(raw_metadata, dict) else {}
        metadata: dict[str, object] = {
            redact_secrets(str(key)): _sanitize_metadata_value(value) for key, value in raw_metadata.items()
        }
        # Anonymized from the ORIGINAL value: the id is a hash input, and
        # redaction would reshape digit-heavy ids before hashing.
        installation_id = raw_metadata.get("installation_id")
        if isinstance(installation_id, str) and installation_id:
            metadata["installation_id"] = anonymize_installation_id(installation_id)
        # SYNC-PROJECT-IDENTITY: say which project wrote this row (the backend stores and returns metadata
        # verbatim). A row that already records an origin keeps it: a teammate's learning merged with a local edit
        # is still that project's knowledge.
        if not str(metadata.get(ORIGIN_PROJECT_KEY, "")).strip() and (
            own_project := self._own_project_id(str(d.get("namespace", "")))
        ):
            metadata[ORIGIN_PROJECT_KEY] = own_project
        payload: dict[str, object] = {
            "source_learning_id": d.get("id", ""),
            "summary": summary,
            "detail": detail,
            "impact": impact,
            "tags": tags,
            "type": str(d.get("type", "pattern")),
            "status": str(d.get("status", "active")),
            "vector_clock": vector_clock,
            "metadata": metadata,
            # This entry's own write counter: the backend's stale guard compares it with
            # this client's mark for the row. push_seq is a max over OTHER entries too.
            "sync_seq": int(str(d.get("sync_seq") or 0)),
        }
        raw_sync_hash = d.get("sync_hash")
        payload["sync_hash"] = raw_sync_hash if _is_valid_sync_hash(raw_sync_hash) else _content_sync_hash(payload)
        return payload

    def _get_client_id(self) -> str:
        """Generate a stable client identifier."""
        return self._client_id
