"""Explicit bounded regeneration; never called by recall or automatic backfill."""

from __future__ import annotations

from typing import Literal

from trw_memory.embeddings.interface import EmbeddingProvider
from trw_memory.embeddings.provenance import VectorProvenance, provider_embedding_space
from trw_memory.exceptions import MemoryError as TRWMemoryError
from trw_memory.storage.interface import EntryCursor
from trw_memory.storage.sqlite_backend import SQLiteBackend
from typing_extensions import NotRequired, TypedDict


class RepairResult(TypedDict):
    """Observed outcome of one bounded maintenance page; no canonical writes."""

    status: Literal["blocked", "failed", "partial", "completed"]
    inspected: int
    repaired: int
    skipped: int
    changed: int
    failed: int
    next_cursor: dict[str, str] | None
    reason: NotRequired[str]


def repair_page(
    backend: SQLiteBackend, provider: EmbeddingProvider, *, max_entries: int, after: EntryCursor | None = None
) -> RepairResult:
    """Repair one default-namespace page without holding a lock during inference.

    A page is not a snapshot of a changing corpus. The cursor advances through
    inspected rows; a later sweep revisits concurrent edits. Canonical entries,
    access counters and timestamps are never rewritten by this operation.
    """
    if type(max_entries) is not int or not 1 <= max_entries <= 1000:
        raise ValueError("max_entries must be between 1 and 1000")
    result: RepairResult = {
        "status": "blocked",
        "inspected": 0,
        "repaired": 0,
        "skipped": 0,
        "changed": 0,
        "failed": 0,
        "next_cursor": None,
    }
    space = provider_embedding_space(provider)
    if space is None or not backend.vec_available:
        result["reason"] = "provider_identity_or_vector_storage_unavailable"
        return result
    entries = backend.list_entries(namespace="default", limit=max_entries + 1, after=after)
    page = entries[:max_entries]
    records = backend.get_vector_records([entry.id for entry in page], namespace="default")
    for entry in page:
        result["inspected"] += 1
        text = f"{entry.content} {entry.detail}"
        record = records.get(entry.id)
        if (
            entry.metadata.get("system_canary") == "true"
            or not text.strip()
            or (
                record is not None
                and record.provenance is not None
                and record.provenance.matches(space, text, record.embedding)
            )
        ):
            result["skipped"] += 1
            continue
        try:
            if provider_embedding_space(provider) != space:
                raise ValueError("provider identity changed")
            vector = provider.embed(text)
            if vector is None or provider_embedding_space(provider) != space:
                raise ValueError("query provider unavailable or changed")
            proof = VectorProvenance.for_vector(space, text, vector)
            with backend.transaction():
                current = backend.get(entry.id, namespace="default")
                if (
                    current is None
                    or f"{current.content} {current.detail}" != text
                    or current.metadata.get("system_canary") == "true"
                ):
                    result["changed"] += 1
                    continue
                backend.upsert_vector(entry.id, vector, namespace="default", provenance=proof)
                written = backend.get_vector_records([entry.id], namespace="default").get(entry.id)
                if written is None or written.provenance != proof or not proof.matches_vector(written.embedding):
                    raise RuntimeError("vector replacement was not persisted")
            result["repaired"] += 1
        except (OSError, ValueError, RuntimeError, TRWMemoryError):
            result["failed"] += 1
    result["status"] = (
        "failed"
        if result["failed"]
        else ("partial" if len(entries) > max_entries or result["changed"] else "completed")
    )
    if len(entries) > max_entries:
        cursor = EntryCursor.from_entry(page[-1])
        result["next_cursor"] = {"updated_at": cursor.updated_at, "entry_id": cursor.entry_id}
    return result
