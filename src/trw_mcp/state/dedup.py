"""trw_learn's adapter onto trw-memory's dedup — PRD-CORE-042, PRD-CORE-291, PRD-CORE-302.

The daemon decides skip, merge or store from the new learning's text: it encodes
it, calibrates this project's reference-scale thresholds and chooses its KNN
window or an exhaustive pass (``memory_similar``). trw-mcp loads no model. This
module keeps only what the daemon cannot own: the embedding-independent
exact-content pre-check and the lossless merge into a ``.trw/learnings`` sidecar.

Obsolete entries are checked for skip but NOT for merge, preventing runaway
re-learning of content surfaced by session_start.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path
from typing import cast

import structlog
from trw_memory.lifecycle.dedup import DedupResult, merge_entries
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

from trw_mcp.models.config import TRWConfig
from trw_mcp.state._constants import DEFAULT_NAMESPACE
from trw_mcp.state.persistence import FileStateReader, FileStateWriter

logger = structlog.get_logger(__name__)

__all__ = ["DedupResult", "dedup_verdict", "merge_into_survivor"]

_CONFIDENCES = {c.value for c in Confidence}


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


def _check_exact_content_duplicate(summary: str, detail: str, entries_dir: Path) -> str | None:
    """Id of an ACTIVE entry whose summary and detail match byte-for-byte (fail-open None)."""
    try:
        from trw_mcp.state._store_selection import selected_store

        store, namespace = selected_store(entries_dir.parent.parent)
        return store.find_duplicate(namespace, summary, detail)
    except Exception:  # trw-fail-silent-allow: fail-open; exact dedup must never block storage when the backend is unavailable (logged)
        logger.debug("dedup_exact_content_unavailable", exc_info=True)
        return None


def dedup_verdict(summary: str, detail: str, entries_dir: Path, *, config: TRWConfig | None = None) -> DedupResult:
    """Skip, merge or store for a new learning.

    1. exact-content match (embedding-independent) → ``merge`` at 1.0;
    2. embeddings disabled, no daemon embedder, or empty text → ``store``;
    3. otherwise the daemon's verdict. Any other daemon failure raises: a refusal
       must never read as "no duplicate" (PRD-CORE-302 C4).
    """
    from trw_mcp.state._store_selection import selected_store

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
    store, namespace = selected_store(entries_dir.parent.parent)
    verdict = store.similar(namespace, summary + " " + detail, cfg.dedup_skip_threshold, cfg.dedup_merge_threshold, 10)
    result = verdict if verdict is not None else DedupResult("store", None, 0.0)
    logger.debug(
        "dedup_check_complete",
        duration_ms=round((time.monotonic() - _t0) * 1000, 2),
        action=result.action,
        similarity=round(result.similarity, 4),
        path="daemon" if verdict is not None else "unavailable",
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
    write: bool = True,
) -> Path:
    """Fold *new_entry_data* into the survivor sidecar with trw-memory's lossless merge.

    PRD-FIX-130-FR07: ``existing_data`` is the body the caller already parsed and
    ``merged_out`` receives the merged body, so the file is read at most once.
    ``write=False`` only computes it: a caller that must commit the primary store
    first writes ``merged_out`` itself afterwards.
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
    if write:
        writer.write_yaml(existing_path, existing)
    if merged_out is not None:
        merged_out.update(existing)

    return existing_path
