"""The daemon implementation of the store protocol (PRD-CORE-298 FR01).

A migrated checkout's memory lives in the trw-memory daemon. This module is the
one place in trw-mcp that knows it: ``selected_store`` hands callers a
:class:`DaemonMemoryStore`, and every call becomes a typed ``DaemonClient``
request presenting the checkout's grant. Nothing here opens a SQLite file, so an
unreachable daemon or a missing grant fails closed as
:class:`StoreUnavailableError` naming ``trw-mcp doctor``.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import dataclasses
import functools
import json
import os
import threading
from collections.abc import Coroutine
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import structlog
from pydantic_core import to_jsonable_python

from trw_mcp.state._store_selection import (
    EmbedderStatus,
    NamespaceHealth,
    RecallSpec,
    StoreRequest,
    StoreUnavailableError,
    VectorCoverage,
    VectorSet,
    dedup_answer,
)
from trw_mcp.state._tier_routing import USER_NAMESPACE

if TYPE_CHECKING:
    from trw_memory.daemon.client import DaemonClient
    from trw_memory.lifecycle.correction import LearningPatch
    from trw_memory.lifecycle.dedup import DedupResult
    from trw_memory.lifecycle.verification_pass import MaintainVerifySummary, VerifySettings
    from trw_memory.models.memory import MemoryEntry
    from trw_memory.sync import AdmissionOutcome

logger = structlog.get_logger(__name__)

#: The daemon's ``memory_list_page`` bound (``trw_memory.tools.listing.LIST_PAGE_MAX``).
_LIST_PAGE_MAX = 1000
#: The per-pass counts ``memory_reembed`` returns, summed across a run's passes.
_REEMBED_COUNTS = ("examined", "reembedded", "already_current", "skipped", "warm_examined", "warm_reembedded")

#: One client per grant, bound to the daemon that answered its check, with the local settings it passed for.
_clients: dict[str, tuple[DaemonClient, tuple[tuple[int, str], str]]] = {}
_clients_lock = threading.Lock()


def daemon_store_for(trw_dir: Path, project_namespace: str) -> DaemonMemoryStore:
    """The store for a pinned checkout: one ``DaemonClient`` per grant per process."""
    from trw_memory.daemon import read_checkout_grant
    from trw_memory.daemon.client import DaemonClient
    from trw_memory.exceptions import DaemonAuthError

    try:
        token = read_checkout_grant(trw_dir.parent)
    except DaemonAuthError as exc:
        raise StoreUnavailableError(f"{exc}; then run trw-mcp doctor") from exc
    from trw_memory.models.config import MemoryConfig, daemon_wide_security

    local = to_jsonable_python(daemon_wide_security(MemoryConfig()))
    wanted = (_daemon_instance(), json.dumps(local, sort_keys=True))
    with _clients_lock:
        client, checked = _clients.get(token, (None, None))
    if client is None or checked != wanted:
        # A restarted daemon or a changed local setting is checked again. The cached
        # client is bound to the daemon that ANSWERED, and refuses to call any other.
        answered_by = _require_matching_security(DaemonClient(token), project_namespace, local)
        client = DaemonClient(token, instance=answered_by, keep_session=True)
        with _clients_lock:
            replaced, _ = _clients.get(token, (None, None))
            _clients[token] = (client, (answered_by, wanted[1]))
        if replaced is not None and replaced is not client:
            _retire(replaced)
    return DaemonMemoryStore(client, project_namespace)


def _retire(client: DaemonClient) -> None:
    """Close a replaced client's held session on the loop that owns it, without waiting (RES-01).

    ``retire`` waits for the client's in-flight calls, and a close against a dead
    daemon must not block the caller that just attached to the new one.
    """
    loop, _thread = _daemon_loop()
    future = asyncio.run_coroutine_threadsafe(client.retire(), loop)
    future.add_done_callback(_log_retire_failure)


def _log_retire_failure(future: concurrent.futures.Future[None]) -> None:
    if not future.cancelled() and future.exception() is not None:
        logger.debug("daemon_client_retire_failed", error=repr(future.exception()))


def _daemon_instance() -> tuple[int, str] | None:
    """The live daemon's pid and start time, or ``None`` when none is published."""
    from trw_memory.daemon import DaemonPaths
    from trw_memory.daemon._discovery import DaemonInfo, read_live_discovery

    info = read_live_discovery(DaemonPaths.resolve())
    return (info.pid, info.started_at) if isinstance(info, DaemonInfo) else None


def _same(daemon: object, local: object) -> bool:
    """Equal in type as well as value, so a reported ``1`` never matches ``True``."""
    if isinstance(daemon, dict) and isinstance(local, dict):
        return daemon.keys() == local.keys() and all(_same(daemon[key], local[key]) for key in local)
    return type(daemon) is type(local) and daemon == local


def _require_matching_security(client: DaemonClient, namespace: str, local: dict[str, Any]) -> tuple[int, str]:
    """Refuse a daemon whose security settings differ from *local* (PRD-CORE-298 FR07); else who answered.

    The daemon enforces its own values for every client, so a mismatch would
    silently drop the policy this client resolved.
    """
    status = _run(client.call_tool("memory_status", {"namespace": namespace, "security_settings_only": True}))
    daemon = status.get("security_settings") if isinstance(status, dict) else None
    if not isinstance(daemon, dict):
        reason = status.get("error") if isinstance(status, dict) else None
        raise StoreUnavailableError(
            f"the memory daemon did not report its security settings ({reason or 'restart it on this version'}). "
            "Run trw-mcp doctor."
        )
    for key, value in local.items():
        if not _same(daemon.get(key), value):
            raise StoreUnavailableError(
                f"memory security setting {key} is {daemon.get(key)!r} in the daemon but {value!r} here; "
                f"it is daemon-wide: set MEMORY_{key.upper()} the same for both and restart the daemon."
            )
    answered_by = status.get("daemon")
    if not (isinstance(answered_by, (list, tuple)) and len(answered_by) == 2):
        raise StoreUnavailableError("the memory daemon did not say which daemon answered. Run trw-mcp doctor.")
    return (answered_by[0], answered_by[1])


#: The one loop every daemon call runs on, with the pid that started it (a fork starts its own).
_loop: tuple[int, asyncio.AbstractEventLoop, threading.Thread] | None = None
_loop_lock = threading.Lock()


def _daemon_loop() -> tuple[asyncio.AbstractEventLoop, threading.Thread]:
    """The process's daemon-call loop, running on its own thread; started on first use.

    One long-lived loop is what lets a ``DaemonClient`` keep its MCP session open
    across calls (W27): a loop per call made every call open a session.
    """
    global _loop
    with _loop_lock:
        if _loop is None or _loop[0] != os.getpid() or not _loop[2].is_alive():
            loop = asyncio.new_event_loop()
            thread = threading.Thread(target=loop.run_forever, name="trw-mcp-daemon-store", daemon=True)
            thread.start()
            _loop = (os.getpid(), loop, thread)
        return _loop[1], _loop[2]


def _run(coro: Coroutine[Any, Any, Any]) -> Any:
    """Run *coro* on the daemon-call loop and wait; tool handlers may already own a running loop."""
    from trw_memory.exceptions import DaemonAuthError, DaemonRecordInvalidError, DaemonUnreachableError

    loop, thread = _daemon_loop()
    if threading.current_thread() is thread:
        coro.close()
        raise RuntimeError("a daemon call waited on the daemon-call loop from inside it")
    try:
        return asyncio.run_coroutine_threadsafe(coro, loop).result()
    except (DaemonUnreachableError, DaemonAuthError, DaemonRecordInvalidError) as exc:
        raise StoreUnavailableError(f"{exc} Run trw-mcp doctor.") from exc


class DaemonMemoryStore:
    """``MemoryStore`` over the daemon: the pinned project namespace, then ``user:local``."""

    def __init__(self, client: DaemonClient, project_namespace: str) -> None:
        self._client = client
        self._namespaces = (project_namespace, USER_NAMESPACE)

    def put(self, summary: str, namespace: str, request: StoreRequest) -> dict[str, object]:
        from trw_memory.tools.store import LearningFields

        # memory_store takes the typed learning fields as one ``learning`` object.
        payload = to_jsonable_python(request)
        learning = {key: payload.pop(key) for key in list(payload) if key in LearningFields.model_fields}
        if learning:
            payload["learning"] = learning
        result: dict[str, object] = _run(self._client.store(summary, namespace, **payload))
        return result

    def get(self, entry_id: str) -> MemoryEntry | None:
        return next(filter(None, (self._row(namespace, entry_id) for namespace in self._namespaces)), None)

    def correct(self, learning_id: str, patch: LearningPatch) -> dict[str, str]:
        from trw_memory.lifecycle.correction import not_found

        entry = self.get(learning_id)
        if entry is None:
            return not_found(learning_id)
        # The daemon resolves a supersedes prior in the entry's own namespace.
        result: dict[str, str] = _run(
            self._client.update(learning_id, entry.namespace, patch.model_dump(mode="json", exclude_none=True))
        )
        return result

    def find_duplicate(self, namespace: str, summary: str, detail: str) -> str | None:
        found = _run(self._client.find_duplicate(namespace, summary, detail))
        if found.get("status") != "ok":
            raise ValueError(f"memory_find_duplicate refused {namespace}: {found.get('error')}")
        entry_id = found["entry_id"]
        return str(entry_id) if entry_id is not None else None

    def count(self, namespace: str) -> int:
        # memory_status scopes the count to the one namespace named (PRD-CORE-298 FR02:
        # no store-wide count reaches a granted caller).
        return int(_run(self._client.status(namespace))["total_entries"])

    def health(self, namespace: str) -> NamespaceHealth:
        status = _run(self._client.status(namespace))
        if "health" not in status:
            raise ValueError(f"memory_status measured no health for {namespace}: {status.get('error')}")
        health: NamespaceHealth = status["health"]
        return health

    def embedder_status(self, namespace: str) -> EmbedderStatus:
        status = _run(self._client.status(namespace))
        if "embedder" not in status:
            raise ValueError(f"memory_status reported no embedder for {namespace}: {status.get('error')}")
        embedder: EmbedderStatus = status["embedder"]
        return embedder

    def reembed(self, namespace: str) -> dict[str, object]:
        """Every bounded pass of ``memory_reembed``, its counts summed (the daemon does one per call)."""
        totals: dict[str, int] = {}
        cursor: str | None = None
        while True:
            answer: dict[str, object] = _run(self._client.reembed(namespace, cursor))
            if answer.get("status") != "ok":
                return answer
            for key in _REEMBED_COUNTS:
                totals[key] = totals.get(key, 0) + int(cast("int", answer.get(key) or 0))
            sent, cursor = cursor, cast("str | None", answer.get("cursor"))
            if cursor is None:
                return {**answer, **totals}
            if cursor == sent:  # every pass consumes a row, so a repeat would loop forever
                return {"status": "unavailable", "reason": "reembed_stalled", "error": f"no progress past {cursor}"}

    def coverage(self, namespace: str) -> VectorCoverage | None:
        coverage: VectorCoverage | None = _run(self._client.status(namespace)).get("coverage")
        return coverage

    def list_entries(
        self, namespace: str, *, status: str | None = None, tags: list[str] | None = None, limit: int
    ) -> list[MemoryEntry]:
        rows: list[MemoryEntry] = []
        after: dict[str, str] | None = None
        while len(rows) < limit:
            size = min(limit - len(rows), _LIST_PAGE_MAX)
            page = _run(self._client.list_page(namespace, size, after, status=status, tags=tags))
            if page.get("status") != "ok":
                raise ValueError(f"memory_list_page refused {namespace}: {page.get('error')}")
            rows.extend(_entry(row) for row in page["entries"])
            after = page["next"]
            if after is None:
                break
        return rows

    def page_dirty(self, namespace: str, limit: int) -> list[MemoryEntry]:
        page = _run(self._client.sync_dirty_page(namespace, limit))
        return [_entry(row) for row in page["entries"]]

    def mark_synced(self, namespace: str, pushed: list[MemoryEntry]) -> int:
        acks = {entry.id: entry.sync_seq for entry in pushed}
        marked: int = _run(self._client.sync_mark_synced(namespace, acks))["marked"]
        return marked

    def find_synced(self, namespace: str, remote_id: str, ids: list[str]) -> MemoryEntry | None:
        found = _run(self._client.sync_find(namespace, remote_id, ids))
        return _entry(found["entry"]) if found.get("status") == "ok" else None

    def apply_synced(self, namespace: str, entry: MemoryEntry, *, synced: bool = True) -> tuple[str, str]:
        result = _run(self._client.sync_apply(namespace, entry.model_dump(mode="json"), synced=synced))
        return str(result["status"]), str(result.get("reason", ""))

    def recall(self, spec: RecallSpec) -> list[MemoryEntry]:
        # Each daemon namespace is its own store, read as a checkout reads its project
        # file then its user store; dedup, admission and caps are the shared rule.
        from trw_mcp.state._recall_take import recall_namespaces

        namespaces = self._namespaces if spec.include_user else self._namespaces[:1]
        return recall_namespaces(spec, namespaces, page=functools.partial(self._page, spec), row=self._row)

    def admit_shared(self, results: list[dict[str, object]]) -> AdmissionOutcome:
        from trw_memory.sync import AdmissionOutcome

        answer = _run(self._client.admit_shared(self._namespaces[0], results))
        if answer.get("status") != "ok":
            raise ValueError(f"memory_admit_shared refused: {answer.get('error')}")
        return AdmissionOutcome(list(answer["admitted"]), int(answer["refused"]), int(answer["gate_errors"]))

    def vectors(self, ids: list[str]) -> VectorSet | None:
        answer = _run(self._client.vectors(self._namespaces[0], list(ids)))
        if answer.get("status") == "unavailable":
            return None
        if answer.get("status") != "ok":
            raise ValueError(f"memory_vectors refused: {answer.get('error')}")
        vectors = {str(entry_id): [float(x) for x in vector] for entry_id, vector in answer["vectors"].items()}
        return VectorSet(vectors, float(answer["dup_threshold"]))

    def verify(self, namespace: str, project_root: Path | None, settings: VerifySettings) -> MaintainVerifySummary:
        from trw_memory.lifecycle.verification_pass import MaintainVerifySummary
        from trw_memory.tools._maintain_sweep import add_sweep_counts

        root = str(project_root) if project_root is not None else None
        summary: dict[str, object] = {}
        reached: list[str] = []
        # One daemon call verifies a bounded part of the namespace and resumes where the last stopped;
        # call until it is done, and refuse a daemon whose position does not move forward.
        while True:
            answer = _run(self._client.verify(namespace, root, dataclasses.asdict(settings)))
            if answer.get("status") != "ok":
                raise ValueError(f"memory_verify refused {namespace}: {answer.get('error')}")
            summary = add_sweep_counts(summary, answer["summary"]) if summary else answer["summary"]
            if not isinstance(position := answer.get("next"), list):
                return MaintainVerifySummary(**summary)
            if reached and position <= reached:
                raise ValueError(f"memory_verify of {namespace} did not advance past {reached}")
            reached = position

    def assertion_health(self, namespace: str, stale_days: int) -> dict[str, int] | None:
        answer = _run(self._client.assertion_health(namespace, stale_days))
        if answer.get("status") != "ok":
            raise ValueError(f"memory_assertion_health refused {namespace}: {answer.get('error')}")
        health: dict[str, int] | None = answer["health"]
        return health

    def record_surfaced(self, ids: list[str], *, session_start: bool = False) -> None:
        from trw_memory.tools.recall_support import SURFACED_MAX

        rest = list(dict.fromkeys(ids))
        for namespace in self._namespaces:
            counted: set[str] = set()
            for start in range(0, len(rest), SURFACED_MAX):
                chunk = rest[start : start + SURFACED_MAX]
                answer = _run(self._client.record_surfaced(namespace, chunk, session_start=session_start))
                if answer.get("status") != "ok":
                    raise ValueError(f"memory_record_surfaced refused {namespace}: {answer.get('error')}")
                counted.update(answer["counted"])
            rest = [i for i in rest if i not in counted]

    def graph_backfill(
        self, namespace: str, after: dict[str, str] | None, limit: int, deadline_seconds: float | None
    ) -> dict[str, Any]:
        answer: dict[str, Any] = _run(self._client.graph_backfill(namespace, after, limit, deadline_seconds))
        if answer.pop("status", None) != "ok":
            raise ValueError(f"memory_graph_backfill refused {namespace}: {answer.get('error')}")
        return answer

    def graph_related(
        self, namespace: str, learning_id: str, depth: int, edge_types: list[str] | None, limit: int
    ) -> tuple[list[dict[str, Any]], bool]:
        answer = _run(self._client.graph_related(namespace, learning_id, depth, edge_types, limit))
        if answer.get("status") != "ok":
            raise ValueError(f"memory_graph_related refused {namespace}: {answer.get('error')}")
        return answer["related"], bool(answer["truncated"])

    def maintain(self, namespace: str, consolidation: dict[str, object]) -> dict[str, Any]:
        answer: dict[str, Any] = _run(self._client.maintain(namespace, consolidation))
        if "passes" not in answer and answer.get("status") != "busy":
            raise ValueError(f"memory_maintain refused {namespace}: {answer.get('error')}")
        return answer

    def similar(
        self, namespace: str, text: str, skip_threshold: float, merge_threshold: float, top_k: int
    ) -> DedupResult | None:
        answer = _run(self._client.similar(namespace, text, skip_threshold, merge_threshold, top_k))
        logger.debug(
            "dedup_daemon_verdict",
            status=answer.get("status"),
            mode=answer.get("mode"),
            examined=answer.get("examined"),
            elapsed_ms=answer.get("elapsed_ms"),
            reason=answer.get("reason"),
        )
        return dedup_answer(answer)

    def _row(self, namespace: str, entry_id: str) -> MemoryEntry | None:
        found = _run(self._client.get(entry_id, namespace))
        return _entry(found["entry"]) if found.get("status") == "ok" else None

    def _page(self, spec: RecallSpec, namespace: str, limit: int) -> list[MemoryEntry]:
        # The namespace's first *limit* candidates in the requested status, not yet admitted; at most
        # the daemon's recall ceiling, which a local recall's DEFAULT_LIST_LIMIT scan also stops at.
        from trw_memory.retrieval.recall_policy import MAX_RECALL_LIMIT

        status = spec.admission.mem_status
        wanted = str(status.value) if status is not None else None
        if spec.query.strip() in ("*", ""):
            return self.list_entries(namespace, status=wanted, tags=spec.tags, limit=limit)
        page = _run(
            self._client.recall(
                spec.query,
                namespace,
                limit=min(limit, MAX_RECALL_LIMIT),
                tags=spec.tags,
                status=wanted,
                include_org_memories=False,
                record_access=False,  # the page is over-fetched; record_surfaced counts what was shown
            )
        )
        if "memories" not in page:
            raise ValueError(f"memory_recall refused {namespace}: {page.get('error')}")
        return [row for row in map(_entry, page["memories"]) if row.namespace == namespace]


def _entry(row: object) -> MemoryEntry:
    from trw_memory.models.memory import MemoryEntry

    return MemoryEntry.model_validate_json(json.dumps(row))
