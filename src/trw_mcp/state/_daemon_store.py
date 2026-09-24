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
import dataclasses
import functools
import json
import threading
from collections.abc import Coroutine
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic_core import to_jsonable_python

from trw_mcp.state._store_selection import (
    NamespaceHealth,
    RecallSpec,
    SimilarWindow,
    StoreRequest,
    StoreUnavailableError,
    similar_window,
)
from trw_mcp.state._tier_routing import USER_NAMESPACE

if TYPE_CHECKING:
    from trw_memory.daemon.client import DaemonClient
    from trw_memory.embeddings.provenance import EmbeddingSpace
    from trw_memory.lifecycle.correction import LearningPatch
    from trw_memory.lifecycle.verification_pass import MaintainVerifySummary, VerifySettings
    from trw_memory.models.memory import MemoryEntry
    from trw_memory.sync import AdmissionOutcome

#: The daemon's ``memory_list_page`` bound (``trw_memory.tools.listing.LIST_PAGE_MAX``).
_LIST_PAGE_MAX = 1000

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
        client = DaemonClient(token, instance=answered_by)
        with _clients_lock:
            _clients[token] = (client, (answered_by, wanted[1]))
    return DaemonMemoryStore(client, project_namespace)


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


def _run(coro: Coroutine[Any, Any, Any]) -> Any:
    """Run *coro* on a private loop in a worker thread; tool handlers may already own a running loop."""
    from trw_memory.exceptions import DaemonAuthError, DaemonRecordInvalidError, DaemonUnreachableError

    outcome: list[Any] = []

    def _worker() -> None:
        try:
            outcome.append(asyncio.run(coro))
        except BaseException as exc:  # re-raised on the calling thread below
            outcome.append(exc)

    thread = threading.Thread(target=_worker, name="trw-mcp-daemon-store")
    thread.start()
    thread.join()
    result = outcome[0]
    if isinstance(result, (DaemonUnreachableError, DaemonAuthError, DaemonRecordInvalidError)):
        raise StoreUnavailableError(f"{result} Run trw-mcp doctor.") from result
    if isinstance(result, BaseException):
        raise result
    return result


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
        status = _run(self._client.status(namespace))
        return int(status["total_entries"])

    def health(self, namespace: str) -> NamespaceHealth:
        status = _run(self._client.status(namespace))
        if "health" not in status:
            raise ValueError(f"memory_status measured no health for {namespace}: {status.get('error')}")
        health: NamespaceHealth = status["health"]
        return health

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

    def vectors(self, ids: list[str], space: EmbeddingSpace) -> dict[str, list[float]]:
        if not ids:
            return {}
        answer = _run(self._client.vectors(self._namespaces[0], list(ids), dataclasses.asdict(space)))
        if answer.get("status") != "ok":
            raise ValueError(f"memory_vectors refused: {answer.get('error')}")
        return {str(entry_id): [float(x) for x in vector] for entry_id, vector in answer["vectors"].items()}

    def verify(self, namespace: str, project_root: Path | None, settings: VerifySettings) -> MaintainVerifySummary:
        from trw_memory.lifecycle.verification_pass import MaintainVerifySummary

        root = str(project_root) if project_root is not None else None
        answer = _run(self._client.verify(namespace, root, dataclasses.asdict(settings)))
        if answer.get("status") != "ok":
            raise ValueError(f"memory_verify refused {namespace}: {answer.get('error')}")
        return MaintainVerifySummary(**answer["summary"])

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

    def maintain(self, namespace: str) -> dict[str, Any]:
        answer: dict[str, Any] = _run(self._client.maintain(namespace))
        if "passes" not in answer:
            raise ValueError(f"memory_maintain refused {namespace}: {answer.get('error')}")
        return answer

    def similar(self, namespace: str, vector: list[float], space: EmbeddingSpace | None, top_k: int) -> SimilarWindow:
        wanted = dataclasses.asdict(space) if space is not None else None
        return similar_window(_run(self._client.similar(namespace, vector, wanted, top_k)))

    def _row(self, namespace: str, entry_id: str) -> MemoryEntry | None:
        found = _run(self._client.get(entry_id, namespace))
        return _entry(found["entry"]) if found.get("status") == "ok" else None

    def _page(self, spec: RecallSpec, namespace: str, limit: int) -> list[MemoryEntry]:
        # The namespace's first *limit* candidates in the requested status, not yet admitted.
        status = spec.admission.mem_status
        wanted = str(status.value) if status is not None else None
        if spec.query.strip() in ("*", ""):
            return self.list_entries(namespace, status=wanted, tags=spec.tags, limit=limit)
        page = _run(
            self._client.recall(
                spec.query,
                namespace,
                limit=limit,
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
