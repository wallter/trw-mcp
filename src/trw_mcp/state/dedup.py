"""trw_learn's adapter onto trw-memory's dedup — PRD-CORE-042, PRD-CORE-291.

``trw_memory.lifecycle.dedup`` is the one dedup implementation: thresholds, the
skip/merge/store classification and the lossless merge live there. This module
keeps only what trw-memory cannot own: the ``.trw/learnings`` YAML sidecars, the
backend KNN scoped to ``DEFAULT_NAMESPACE`` and to the loaded embedding space,
the embedding-independent exact-content pre-check, and the post-merge re-embed.

Obsolete entries are checked for skip but NOT for merge, preventing runaway
re-learning of content surfaced by session_start.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path
from typing import cast

import structlog
from trw_memory.lifecycle.dedup import DedupResult, _validated_thresholds, check_duplicate, merge_entries
from trw_memory.models.config import MemoryConfig
from trw_memory.models.entry_factory import new_entry
from trw_memory.models.memory import (
    Assertion,
    AssertionType,
    Confidence,
    MemoryEntry,
    MemoryStatus,
    MemoryType,
    ProtectionTier,
)

from trw_mcp.exceptions import StateError
from trw_mcp.models.config import TRWConfig
from trw_mcp.state._constants import DEFAULT_NAMESPACE
from trw_mcp.state._embedding_space import loaded_embedding_space
from trw_mcp.state._helpers import iter_yaml_entry_files
from trw_mcp.state.memory_adapter import embed_text as embed
from trw_mcp.state.persistence import FileStateReader, FileStateWriter

logger = structlog.get_logger(__name__)

__all__ = ["DedupResult", "dedup_verdict", "merge_into_survivor"]

_CONFIDENCES = {c.value for c in Confidence}


class _SeamEmbedder:
    """trw-memory ``EmbeddingProvider`` over this module's ``embed`` seam.

    Its model id is the LOADED encoder's, so trw-memory translates the configured
    reference-scale thresholds to the scale of the vectors actually compared.
    """

    def __init__(self, text: str, vector: list[float]) -> None:
        from trw_mcp.state import _memory_connection as conn

        self.model_name = getattr(conn.get_initialized_embedder(), "model_name", None)
        self._known = {text: vector}  # the new learning is embedded once, not twice

    def available(self) -> bool:
        return True

    def embed(self, text: str) -> list[float] | None:
        return self._known.get(text) or embed(text)

    def embed_batch(self, texts: list[str]) -> list[list[float] | None]:
        return [embed(text) for text in texts]

    def dim(self) -> int:
        return 0


def _strs(value: object) -> list[str]:
    return [str(item) for item in cast("list[object]", value or [])]


def _entry_view(data: dict[str, object], *, status: MemoryStatus = MemoryStatus.ACTIVE) -> MemoryEntry:
    """The merge-relevant fields of a YAML sidecar as a ``MemoryEntry``.

    Confidence values trw-memory does not model (``hypothesis``) read as its weakest
    known level; :func:`merge_into_survivor` restores the raw value when it survives.
    """
    confidence = str(data.get("confidence") or "unverified")
    raw_assertions = data.get("assertions")
    return new_entry(
        entry_id=str(data.get("id") or "unknown"),
        content=str(data.get("summary", "")),
        namespace=DEFAULT_NAMESPACE,
        local_node_id="trw-mcp-dedup-view",
        fields={
            "detail": str(data.get("detail", "")),
            "tags": _strs(data.get("tags")),
            "evidence": _strs(data.get("evidence")),
            "importance": min(max(float(str(data.get("impact", 0.5))), 0.0), 1.0),
            "recurrence": max(int(str(data.get("recurrence", 1))), 0),
            "merged_from": _strs(data.get("merged_from")),
            "status": status,
            "protection_tier": ProtectionTier(str(data.get("protection_tier") or "normal")),
            "confidence": Confidence(confidence if confidence in _CONFIDENCES else "unverified"),
            "type": MemoryType(str(data.get("type") or "pattern")),
            "assertions": [
                Assertion(
                    type=AssertionType(str(a["type"])), pattern=str(a.get("pattern", "")), target=str(a["target"])
                )
                for a in (raw_assertions if isinstance(raw_assertions, list) else [])
                if isinstance(a, dict)
            ],
        },
    )


def _check_duplicate_via_backend(
    new_vector: list[float],
    trw_dir: Path,
    skip_threshold: float,
    merge_threshold: float,
) -> DedupResult | None:
    """KNN verdict from the checkout's store, or None to fall back to the YAML scan.

    PRD-CORE-245: the KNN is scoped to this project's namespace, and only
    neighbours encoded in new_vector's space are comparable — ``store.similar``
    returning None means the dense verdict would be incomplete.
    """
    try:
        from trw_mcp.state._store_selection import selected_store

        store, namespace = selected_store(trw_dir)
        window = store.similar(namespace, new_vector, loaded_embedding_space(), 10)
        if window.size == 0 or window.hits is None:
            return None  # Nothing indexed yet, or an incomplete window: the YAML scan decides
        if not window.hits:
            # No loaded space (nothing is comparable) or no neighbour row left: no scan either.
            return DedupResult("store", None, 0.0)
        best = max(window.hits, key=lambda hit: hit.similarity)
        if best.similarity >= skip_threshold:
            return DedupResult("skip", best.entry_id, best.similarity)
        if best.similarity >= merge_threshold and best.active:
            return DedupResult("merge", best.entry_id, best.similarity)
        return DedupResult("store", None, max(best.similarity, 0.0))
    except Exception:  # trw-fail-silent-allow: fail-open to the YAML scan when the backend is unavailable
        logger.debug("dedup_backend_unavailable_fallback_to_yaml", exc_info=True)
        return None


def _check_exact_content_duplicate(summary: str, detail: str, entries_dir: Path) -> str | None:
    """Id of an ACTIVE entry whose summary and detail match byte-for-byte (fail-open None)."""
    try:
        from trw_mcp.state._store_selection import selected_store

        store, namespace = selected_store(entries_dir.parent.parent)
        return store.find_duplicate(namespace, summary, detail)
    except Exception:  # trw-fail-silent-allow: fail-open; exact dedup must never block storage when the backend is unavailable (logged)
        logger.debug("dedup_exact_content_unavailable", exc_info=True)
        return None


def _yaml_scan(
    summary: str, detail: str, entries_dir: Path, reader: FileStateReader, embedder: _SeamEmbedder, config: MemoryConfig
) -> DedupResult:
    """Exhaustive verdict over the YAML sidecars, classified by trw-memory.

    Every status is offered as a candidate so an obsolete near-copy still earns
    ``skip``; a ``merge`` into a non-active best match is refused afterwards.
    """
    views: list[MemoryEntry] = []
    statuses: dict[str, str] = {}
    for yaml_file in iter_yaml_entry_files(entries_dir) if entries_dir.exists() else ():
        try:
            data = reader.read_yaml(yaml_file)
            view = _entry_view(data)
        except (
            OSError,
            StateError,
            ValueError,
            KeyError,
        ):  # trw-fail-silent-allow: an unreadable sidecar is not a duplicate candidate
            continue
        views.append(view)
        statuses[view.id] = str(data.get("status", "active"))
    result = check_duplicate(summary, views, embedder, detail=detail, config=config)
    if result.action == "merge" and statuses.get(result.existing_id or "") != "active":
        return DedupResult("store", None, result.similarity)
    return result


def dedup_verdict(
    summary: str,
    detail: str,
    entries_dir: Path,
    reader: FileStateReader,
    *,
    config: TRWConfig | None = None,
) -> DedupResult:
    """Skip, merge or store for a new learning.

    1. exact-content match (embedding-independent) → ``merge`` at 1.0;
    2. embeddings disabled or unavailable → ``store``;
    3. backend KNN when it gives a complete answer, else the YAML scan.
    """
    _t0 = time.monotonic()
    cfg = config or TRWConfig()

    # Runs BEFORE the embeddings gate so default installs (embeddings off) still
    # collapse byte-identical re-learns. "merge", not "skip", so the new entry's
    # tags/evidence/impact still fold into the survivor.
    exact_id = _check_exact_content_duplicate(summary, detail, entries_dir)
    if exact_id is not None:
        logger.debug("dedup_exact_content_match", existing_id=exact_id, path="exact")
        return DedupResult("merge", exact_id, 1.0)
    if not cfg.embeddings_enabled:
        return DedupResult("store", None, 0.0)
    new_text = summary + " " + detail
    new_vector = embed(new_text)
    if new_vector is None:
        logger.debug("dedup_embed_unavailable", text_len=len(new_text))
        return DedupResult("store", None, 0.0)

    embedder = _SeamEmbedder(new_text, new_vector)
    mem_cfg = MemoryConfig(
        dedup_skip_threshold=cfg.dedup_skip_threshold, dedup_merge_threshold=cfg.dedup_merge_threshold
    )
    skip_threshold, merge_threshold = _validated_thresholds(mem_cfg, embedder)
    result = _check_duplicate_via_backend(new_vector, entries_dir.parent.parent, skip_threshold, merge_threshold)
    path = "backend"
    if result is None:
        result, path = _yaml_scan(summary, detail, entries_dir, reader, embedder, mem_cfg), "yaml_fallback"
    logger.debug(
        "dedup_check_complete",
        duration_ms=round((time.monotonic() - _t0) * 1000, 2),
        action=result.action,
        similarity=round(result.similarity, 4),
        path=path,
    )
    return result


def merge_into_survivor(
    existing_path: Path,
    new_entry_data: dict[str, object],
    reader: FileStateReader,
    writer: FileStateWriter,
    *,
    max_merge_tags: int = 20,
    existing_data: dict[str, object] | None = None,
    merged_out: dict[str, object] | None = None,
) -> Path:
    """Fold *new_entry_data* into the survivor sidecar with trw-memory's lossless merge.

    PRD-FIX-130-FR07: ``existing_data`` is the body the caller already parsed and
    ``merged_out`` receives the merged body, so the file is read at most once.
    Only merge-owned keys are rewritten; every other sidecar key is preserved.
    Tags stay capped at *max_merge_tags*, existing tags first (FIX-071-FR04).
    """
    existing = reader.read_yaml(existing_path) if existing_data is None else existing_data
    survivor = _entry_view(existing)
    incoming = _entry_view(new_entry_data)
    merged = merge_entries(survivor, incoming)

    by_key = {
        (str(a.get("type", "")), str(a.get("pattern", "")), str(a.get("target", ""))): a
        for a in cast("list[object]", existing.get("assertions") or [])
        + cast("list[object]", new_entry_data.get("assertions") or [])
        if isinstance(a, dict)
    }
    if new_entry_data.get("assertions"):
        existing["assertions"] = [by_key[(str(a.type), a.pattern, a.target)] for a in merged.assertions]
    raw_confidence = str(existing.get("confidence") or "unverified")
    existing.update(
        tags=merged.tags[:max_merge_tags],
        evidence=merged.evidence,
        impact=merged.importance,
        recurrence=merged.recurrence,
        detail=merged.detail,
        merged_from=merged.merged_from,
        protection_tier=str(merged.protection_tier),
        confidence=raw_confidence if merged.confidence == survivor.confidence else str(merged.confidence),
        type=str(merged.type),
        updated=datetime.now(tz=timezone.utc).date().isoformat(),
    )
    writer.write_yaml(existing_path, existing)
    if merged_out is not None:
        merged_out.update(existing)

    # CORE-042-FR03: re-embed the merged entry into the sqlite-vec index.
    try:
        from trw_mcp.state.memory_store import MemoryStore

        if MemoryStore.available():
            new_embedding = embed(str(existing.get("summary", "")) + " " + merged.detail)
            if new_embedding is not None:
                from trw_mcp.state._paths import resolve_memory_store_path

                store = MemoryStore(resolve_memory_store_path())
                try:
                    store.upsert(str(existing.get("id", "")), new_embedding, {})
                finally:
                    store.close()
    except (ImportError, OSError, ValueError):
        logger.debug("dedup_reindex_skipped", exc_info=True)  # justified: fail-open, best-effort re-indexing
    return existing_path
