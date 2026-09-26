"""An in-memory ``MemoryStore`` for the store contract and store-selection tests (PRD-CORE-280 FR01)."""

from __future__ import annotations

import functools
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from trw_memory.lifecycle.correction import LearningPatch
from trw_memory.lifecycle.dedup import DedupResult
from trw_memory.lifecycle.verification_pass import MaintainVerifySummary, VerifySettings, assertion_health
from trw_memory.models.memory import MemoryEntry
from trw_memory.sync import AdmissionOutcome

from trw_mcp.state._store_selection import (
    EmbedderStatus,
    NamespaceHealth,
    RecallSpec,
    StoreRequest,
    VectorCoverage,
    VectorSet,
)
from trw_mcp.state._tier_routing import USER_NAMESPACE


class FakeMemoryStore:
    """Keeps rows in a dict keyed by ``(namespace, id)`` and records every call.

    Like the real stores, every write bumps a row's ``sync_seq``; ``synced`` keeps the
    revision last acknowledged, so a row is dirty while its revision is newer.
    """

    def __init__(self) -> None:
        self.rows: dict[tuple[str, str], MemoryEntry] = {}
        self.calls: list[tuple[str, object]] = []
        self.synced: dict[tuple[str, str], int] = {}
        self.stored_vectors: dict[str, list[float]] = {}  # project rows, already in the active space
        self.text_vectors: dict[str, list[float]] = {}  # the daemon encoder's answers, by text
        self.dup_threshold: float | None = 0.9  # ``None``: the daemon has no embedder
        self.graph_edges: dict[tuple[str, str], list[tuple[str, str, float]]] = {}  # (ns, source) -> (target, type, w)
        # Test seam: the fake has no write gate, so a caller scripts the refusal status
        # ("invalid"/"blocked"/"quarantined"/...) the daemon's real gate would return,
        # to drive ``memory_adapter``'s status translation. Cleared after one use.
        self.next_put_status: str | None = None
        self.edges: dict[str, int] = {}  # materialised graph edges per namespace; the fake keeps no graph
        self.embedder: EmbedderStatus = {"available": True, "model": "fake-encoder", "loaded": False, "reason": None}
        self.vector_coverage: VectorCoverage | None = None

    def put(self, summary: str, namespace: str, request: StoreRequest) -> dict[str, object]:
        self.calls.append(("put", (summary, namespace)))
        entry_id = request.get("entry_id") or f"L-fake{len(self.rows)}"
        status = self.next_put_status or "stored"
        self.next_put_status = None
        if status not in ("stored", "updated"):
            return {"memory_id": entry_id, "status": status, "namespace": namespace}
        entry = MemoryEntry(
            id=entry_id,
            content=summary,
            detail=request.get("detail", ""),
            tags=list(request.get("tags") or []),
            importance=request.get("importance", 0.5),
            metadata=dict(request.get("metadata") or {}),
            expires=request.get("expires", ""),
            assertions=list(request.get("assertions") or []),
            namespace=namespace,
        )
        self._write((namespace, entry_id), entry)
        return {"memory_id": entry_id, "status": status, "namespace": namespace}

    def get(self, entry_id: str) -> MemoryEntry | None:
        self.calls.append(("get", entry_id))
        # The project row wins over a user row with the same id, as in the real store.
        found = [(ns, e) for (ns, eid), e in self.rows.items() if eid == entry_id]
        return min(found, key=lambda row: row[0] == USER_NAMESPACE)[1] if found else None

    def correct(self, learning_id: str, patch: LearningPatch) -> dict[str, str]:
        # The patch-to-field mapping is trw-memory's own, so the fake cannot drift from it.
        from trw_memory.lifecycle.correction import _collect, not_found

        self.calls.append(("correct", (learning_id, patch.model_dump(exclude_none=True))))
        entry = self.get(learning_id)
        if entry is None:
            return not_found(learning_id)
        fields, changes = _collect(entry, patch)
        self._write((entry.namespace, learning_id), entry.model_copy(update=fields))
        # Mirrors trw_memory.lifecycle.correction._close_prior: a patch naming
        # ``supersedes`` closes THAT entry's validity window, not the one being
        # corrected. A missing or already-closed prior is a no-op.
        if patch.supersedes is not None and patch.supersedes != learning_id:
            prior = self.get(patch.supersedes)
            if prior is not None and prior.invalid_from is None:
                self._write(
                    (prior.namespace, prior.id),
                    prior.model_copy(
                        update={"invalid_from": datetime.now(timezone.utc), "invalidated_by": learning_id}
                    ),
                )
                changes.append(f"supersedes→{patch.supersedes}")
        if not changes:
            return {"learning_id": learning_id, "status": "no_changes"}
        return {"learning_id": learning_id, "status": "updated", "changes": ", ".join(changes)}

    def recall(self, spec: RecallSpec) -> list[MemoryEntry]:
        # A substring match stands in for search; everything after the page is the shared rule.
        from trw_mcp.state._recall_take import recall_namespaces

        self.calls.append(("recall", (spec.query, spec.ids, spec.include_user)))
        namespaces = ["default", USER_NAMESPACE] if spec.include_user else ["default"]
        return recall_namespaces(
            spec, namespaces, page=functools.partial(self._page, spec), row=lambda ns, eid: self.rows.get((ns, eid))
        )

    def _page(self, spec: RecallSpec, namespace: str, limit: int) -> list[MemoryEntry]:
        needle = "" if spec.query.strip() in ("*", "") else spec.query.lower()
        status = spec.admission.mem_status
        hits = [
            entry
            for (ns, _id), entry in self.rows.items()
            if ns == namespace and needle in entry.content.lower() and status in (None, entry.status)
        ]
        return sorted(hits, key=lambda entry: entry.updated_at, reverse=True)[:limit]

    def admit_shared(self, results: list[dict[str, object]]) -> AdmissionOutcome:
        # No write gate here: sqlite and daemon run the real one.
        self.calls.append(("admit_shared", len(results)))
        return AdmissionOutcome(list(results), 0, 0)

    def vectors(self, ids: list[str]) -> VectorSet | None:
        self.calls.append(("vectors", tuple(ids)))
        if self.dup_threshold is None:
            return None  # the daemon has no embedder
        found = {entry_id: self.stored_vectors[entry_id] for entry_id in ids if entry_id in self.stored_vectors}
        return VectorSet(found, self.dup_threshold)

    def verify(self, namespace: str, project_root: Path | None, settings: VerifySettings) -> MaintainVerifySummary:
        # Counts the rows the real sweep would visit; it checks nothing.
        self.calls.append(("verify", (namespace, project_root)))
        visited = sum(1 for (ns, _), entry in self.rows.items() if ns == namespace and entry.assertions)
        return MaintainVerifySummary(entries_processed=visited)

    def assertion_health(self, namespace: str, stale_days: int) -> dict[str, int] | None:
        # The real counting, over this namespace's active assertion rows.
        self.calls.append(("assertion_health", (namespace, stale_days)))
        rows = [e for (ns, _), e in self.rows.items() if ns == namespace and e.assertions and e.status == "active"]
        page = type("_Page", (), {"entries_with_assertions": lambda _self, namespace: rows})()
        return assertion_health(page, namespace=namespace, stale_days=stale_days)

    def record_surfaced(self, ids: list[str], *, session_start: bool = False) -> None:
        # Counts each id once, in the first namespace that holds it, like the real stores.
        self.calls.append(("record_surfaced", (tuple(ids), session_start)))
        for entry_id in dict.fromkeys(ids):
            key = next((k for k in self.rows if k[1] == entry_id and k[0] != USER_NAMESPACE), None)
            key = key or ((USER_NAMESPACE, entry_id) if (USER_NAMESPACE, entry_id) in self.rows else None)
            if key is not None:
                row = self.rows[key]
                self.rows[key] = row.model_copy(
                    update={
                        "access_count": row.access_count + 1,
                        "recall_count": row.recall_count + 1,
                        "session_count": row.session_count + int(session_start),
                        "last_accessed_at": datetime.now(timezone.utc),
                    }
                )

    def graph_backfill(
        self, namespace: str, after: dict[str, str] | None, limit: int, deadline_seconds: float | None
    ) -> dict[str, Any]:
        # Pages the listing order and counts every row processed; the fake keeps no edges.
        self.calls.append(("graph_backfill", (namespace, after, limit, deadline_seconds)))
        order = sorted((e for (ns, _), e in self.rows.items() if ns == namespace), key=_listing_key, reverse=True)
        rows = [e for e in order if after is None or _listing_key(e) < (after["updated_at"], after["entry_id"])][:limit]
        last = {"updated_at": _listing_key(rows[-1])[0], "entry_id": rows[-1].id} if rows else after
        counts = {"processed": len(rows), "edges_built": 0, "skipped": 0, "failed": 0}
        return {**counts, "next": last, "complete": len(rows) < limit}

    def graph_related(
        self, namespace: str, learning_id: str, depth: int, edge_types: list[str] | None, limit: int
    ) -> tuple[list[dict[str, Any]], bool]:
        # One hop only: traversal depth is the real stores' concern (trw-memory's graph tests).
        self.calls.append(("graph_related", (namespace, learning_id, depth, limit)))
        hops = [
            h for h in self.graph_edges.get((namespace, learning_id), []) if edge_types is None or h[1] in edge_types
        ]
        rows = [
            {**self.rows[(namespace, target)].model_dump(mode="json"), "edge_type": kind, "weight": weight, "depth": 1}
            for target, kind, weight in hops[:limit]
            if (namespace, target) in self.rows and self.rows[(namespace, target)].status == "active"
        ]
        return rows, len(hops) > limit

    def maintain(self, namespace: str, consolidation: dict[str, object]) -> dict[str, Any]:
        # Records the request; the fake has no importance to decay.
        self.calls.append(("maintain", (namespace, consolidation)))
        return {"status": "ok", "passes": {"decay": {"status": "ok", "processed": 0, "remaining": 0}}}

    def similar(
        self, namespace: str, text: str, skip_threshold: float, merge_threshold: float, top_k: int
    ) -> DedupResult | None:
        # The daemon's rule over this fake's rows: ``text_vectors`` stands in for its encoder
        # (a text it does not know means no embedder), and no threshold is calibrated.
        self.calls.append(("similar", (namespace, text, skip_threshold, merge_threshold, top_k)))
        vector = self.text_vectors.get(text)
        if vector is None or not text.strip():
            return None
        norm = math.sqrt(sum(x * x for x in vector)) or 1.0
        best: tuple[float, str, bool] | None = None
        for (ns, entry_id), entry in self.rows.items():
            stored = self.stored_vectors.get(entry_id)
            if ns != namespace or stored is None:
                continue
            other = math.sqrt(sum(x * x for x in stored)) or 1.0
            cosine = sum(a * b for a, b in zip(vector, stored, strict=False)) / (norm * other)
            if best is None or cosine > best[0]:
                best = (cosine, entry_id, entry.status == "active")
        if best is not None and best[0] >= skip_threshold:
            return DedupResult("skip", best[1], best[0])
        if best is not None and best[0] >= merge_threshold and best[2]:
            return DedupResult("merge", best[1], best[0])
        return DedupResult("store", None, max(best[0], 0.0) if best else 0.0)

    def _write(self, key: tuple[str, str], entry: MemoryEntry) -> None:
        previous = self.rows.get(key)
        self.rows[key] = entry.model_copy(update={"sync_seq": (previous.sync_seq if previous else 0) + 1})

    def find_duplicate(self, namespace: str, summary: str, detail: str) -> str | None:
        self.calls.append(("find_duplicate", (namespace, summary, detail)))
        return next(
            (
                eid
                for (ns, eid), e in self.rows.items()
                if ns == namespace and e.status == "active" and (e.content, e.detail) == (summary, detail)
            ),
            None,
        )

    def count(self, namespace: str) -> int:
        self.calls.append(("count", namespace))
        return sum(1 for (ns, _eid) in self.rows if ns == namespace)

    def embedder_status(self, namespace: str) -> EmbedderStatus:
        self.calls.append(("embedder_status", namespace))
        return self.embedder

    def reembed(self, namespace: str) -> dict[str, object]:
        self.calls.append(("reembed", namespace))
        return {"status": "unavailable", "reason": "embedder_error"}

    def coverage(self, namespace: str) -> VectorCoverage | None:
        self.calls.append(("coverage", namespace))
        return self.vector_coverage

    def health(self, namespace: str) -> NamespaceHealth:
        # A derived relation is a tag one of the three newest rows shares with another row.
        self.calls.append(("health", namespace))
        rows = [entry for (ns, _eid), entry in self.rows.items() if ns == namespace]
        newest = sorted(rows, key=lambda entry: (entry.updated_at, entry.id), reverse=True)[:3]
        derived = any(set(root.tags) & set(other.tags) for root in newest for other in rows if other.id != root.id)
        return {
            "entries": len(rows),
            "synced": sum(1 for entry in rows if entry.source == "team_sync"),
            "edges": self.edges.get(namespace, 0),
            "has_relations": self.edges.get(namespace, 0) > 0 or derived,
            "embedded": sum(1 for entry in rows if entry.id in self.stored_vectors),
            "max_recall_count": max((entry.recall_count for entry in rows), default=0),
        }

    def list_entries(
        self, namespace: str, *, status: str | None = None, tags: list[str] | None = None, limit: int
    ) -> list[MemoryEntry]:
        self.calls.append(("list_entries", (namespace, status, tags, limit)))
        return [
            e
            for (ns, _eid), e in self.rows.items()
            if ns == namespace and status in (None, e.status) and set(tags or []) <= set(e.tags)
        ][:limit]

    def page_dirty(self, namespace: str, limit: int) -> list[MemoryEntry]:
        self.calls.append(("page_dirty", (namespace, limit)))
        dirty = [e for key, e in self.rows.items() if key[0] == namespace and self.synced.get(key) != e.sync_seq]
        return dirty[:limit]

    def mark_synced(self, namespace: str, pushed: list[MemoryEntry]) -> int:
        self.calls.append(("mark_synced", (namespace, [entry.id for entry in pushed])))
        marked = 0
        for entry in pushed:
            row = self.rows.get((namespace, entry.id))
            if row is not None and row.sync_seq == entry.sync_seq:
                self.synced[(namespace, entry.id)] = row.sync_seq
                marked += 1
        return marked

    def find_synced(self, namespace: str, remote_id: str, ids: list[str]) -> MemoryEntry | None:
        self.calls.append(("find_synced", (namespace, remote_id, list(ids))))
        matches = (
            e for (ns, eid), e in self.rows.items() if ns == namespace and (e.remote_id == remote_id or eid in ids)
        )
        return next(matches, None)

    def apply_synced(self, namespace: str, entry: MemoryEntry, *, synced: bool = True) -> tuple[str, str]:
        self.calls.append(("apply_synced", (namespace, entry.id)))
        self._write((namespace, entry.id), entry)
        if synced:
            self.synced[(namespace, entry.id)] = self.rows[(namespace, entry.id)].sync_seq
        return "stored", ""


def _listing_key(entry: MemoryEntry) -> tuple[str, str]:
    """``list_entries`` order, ``(updated_at, id)`` descending, as the stored text encoding."""
    return entry.updated_at.isoformat(), entry.id
