"""Embedding backfill implementation for the memory connection facade."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any


def run_backfill_embeddings(
    trw_dir: Path,
    *,
    get_backend: Callable[[Path], Any],
    get_embedder: Callable[[], Any],
    logger: Any,
    namespace: str,
    max_entries: int,
) -> dict[str, int]:
    """Generate embeddings for all entries that don't have one yet.

    Called on first activation of embeddings (session_start with
    embeddings_enabled=True and deps available). Idempotent -- skips
    entries that already have a vector stored.

    Returns counts: ``{"embedded": N, "skipped": N, "failed": N}``.
    """
    embedder = get_embedder()
    if embedder is None:
        return {"embedded": 0, "skipped": 0, "failed": 0}

    backend = get_backend(trw_dir)

    # Idempotency: bulk-fetch already-embedded IDs first. When the vector
    # count covers (or exceeds) the entry count, there is nothing to do --
    # short-circuit BEFORE list_entries(), which loads + Pydantic-validates
    # every MemoryEntry (~27s for 6438 rows). Both checks are O(rows-only)
    # COUNT/SELECT-ID queries, ~ms even on big corpora.
    already_embedded = backend.existing_vector_ids()
    entry_count = backend.count(namespace=namespace)
    missing_count = max(0, entry_count - len(already_embedded))

    if entry_count <= len(already_embedded):
        logger.info(
            "embeddings_backfill_complete",
            embedded=0,
            skipped=entry_count,
            failed=0,
        )
        return {"embedded": 0, "skipped": entry_count, "failed": 0}

    # PRD-FIX-COMPOUNDING-3-FR04: Log at WARNING when vectors are missing so
    # ops logs surface the gap. INFO was silent in agent output.
    logger.warning(
        "embeddings_backfill_start",
        total_entries=entry_count,
        already_embedded=len(already_embedded),
        missing_count=missing_count,
    )

    entries = backend.list_entries(namespace=namespace, limit=max_entries)

    embedded = 0
    skipped = 0
    failed = 0

    for entry in entries:
        if entry.metadata.get("system_canary") == "true":
            continue
        if entry.id in already_embedded:
            skipped += 1
            continue
        try:
            text = f"{entry.content} {entry.detail}"
            if not text.strip():
                skipped += 1
                continue

            vector = embedder.embed(text)
            if vector is None:
                failed += 1
                continue

            backend.upsert_vector(entry.id, vector)
            embedded += 1
        except (OSError, ValueError, RuntimeError):
            failed += 1

    # PRD-FIX-COMPOUNDING-3-FR04: Log at WARNING when vectors were actually embedded
    # so ops logs surface completion and counts are visible in agent output.
    if embedded > 0 or failed > 0:
        logger.warning(
            "embeddings_backfill_complete",
            embedded=embedded,
            skipped=skipped,
            failed=failed,
        )
    else:
        logger.info(
            "embeddings_backfill_complete",
            embedded=embedded,
            skipped=skipped,
            failed=failed,
        )
    return {"embedded": embedded, "skipped": skipped, "failed": failed}


__all__ = ["run_backfill_embeddings"]
