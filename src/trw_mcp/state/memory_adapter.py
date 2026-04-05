"""Adapter layer between trw-mcp learning tools and trw-memory SQLite backend.

Provides singleton backend access, one-time YAML-to-SQLite migration, and
CRUD operations that preserve the exact return shapes of the original
YAML-based learning tools.

When ``embeddings_enabled=True`` in config, the adapter:
- Generates embeddings on store via :class:`LocalEmbeddingProvider`
- Uses hybrid search (BM25 + dense + RRF fusion) on recall
- Backfills embeddings for existing entries on first activation

Implementation is split across focused sub-modules:
- ``_memory_connection``: singleton management, embedder lifecycle, migration
- ``_memory_queries``: query construction, keyword/hybrid search routing
- ``_memory_transforms``: result transformation between internal/external formats

This module is the public facade -- all external imports should come here.
"""

from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import structlog
from trw_memory.migration.from_trw import migrate_entries_dir as migrate_entries_dir
from trw_memory.models.memory import MemoryStatus

from trw_mcp.models.config import get_config as get_config
from trw_mcp.state._constants import DEFAULT_LIST_LIMIT, DEFAULT_NAMESPACE

# ---------------------------------------------------------------------------
# Re-export: connection management (singletons, embedder, migration)
# ---------------------------------------------------------------------------
from trw_mcp.state._memory_connection import (
    _embed_and_store as _embed_and_store,
)
from trw_mcp.state._memory_connection import (
    backfill_embeddings as backfill_embeddings,
)
from trw_mcp.state._memory_connection import (
    check_embeddings_status as _check_embeddings_status_impl,
)

# --- Embedding operations ---
from trw_mcp.state._memory_connection import (
    embed_text as embed_text,
)
from trw_mcp.state._memory_connection import (
    embed_text_batch as embed_text_batch,
)
from trw_mcp.state._memory_connection import (
    embedding_available as embedding_available,
)
from trw_mcp.state._memory_connection import (
    ensure_migrated as ensure_migrated,
)
from trw_mcp.state._memory_connection import (
    get_backend as get_backend,
)
from trw_mcp.state._memory_connection import (
    get_embed_failure_count as get_embed_failure_count,
)
from trw_mcp.state._memory_connection import (
    get_embedder as get_embedder,
)
from trw_mcp.state._memory_connection import (
    reset_backend as reset_backend,
)
from trw_mcp.state._memory_connection import (
    reset_embed_failure_count as _reset_embed_failure_count_impl,
)
from trw_mcp.state._memory_connection import (
    reset_embedder as reset_embedder,
)

# ---------------------------------------------------------------------------
# Re-export: query routing (keyword search, hybrid search, ID lookup)
# ---------------------------------------------------------------------------
from trw_mcp.state._memory_queries import (
    _apply_entry_filters as _apply_entry_filters,
)
from trw_mcp.state._memory_queries import (
    _keyword_search as _keyword_search,
)
from trw_mcp.state._memory_queries import (
    _lookup_id_tokens as _lookup_id_tokens,
)
from trw_mcp.state._memory_queries import (
    _search_entries as _search_entries,
)
from trw_mcp.state._memory_queries import (
    _search_intersect_keywords as _search_intersect_keywords,
)

# ---------------------------------------------------------------------------
# Re-export: result transformations
# ---------------------------------------------------------------------------
from trw_mcp.state._memory_transforms import (
    _learning_to_memory_entry as _learning_to_memory_entry,
)
from trw_mcp.state._memory_transforms import (
    _memory_to_learning_dict as _memory_to_learning_dict,
)

logger = structlog.get_logger(__name__)

# Preserve module-level constants for backward compatibility with test patches
_NAMESPACE = DEFAULT_NAMESPACE
_MAX_ENTRIES = DEFAULT_LIST_LIMIT
_LEARNING_ID_RE = re.compile(r"^L-[0-9a-zA-Z]{4,}$")

# Facade-level override for the embed failure counter.  Tests may set this
# attribute directly (``memory_adapter._embed_failures = N``) to inject a
# known count; ``None`` means "read from _memory_connection" (normal path).
_embed_failures: int | None = None


def check_embeddings_status() -> dict[str, object]:
    """Check embedding readiness and return status for session_start advisory.

    Delegates to :func:`_memory_connection.check_embeddings_status`, but
    supports test patches that set ``memory_adapter._embed_failures`` directly.
    """
    result = _check_embeddings_status_impl()
    # If a test injected _embed_failures on this facade module, honour it.
    if _embed_failures is not None:
        result["recent_failures"] = _embed_failures
    return result


def reset_embed_failure_count() -> None:
    """Reset the embed failure counter to zero (for tests).

    Also clears the facade-level ``_embed_failures`` override so that
    ``check_embeddings_status`` reads the authoritative counter.
    """
    global _embed_failures
    _reset_embed_failure_count_impl()
    _embed_failures = None


def set_embed_failure_count_for_testing(n: int) -> None:
    """Set the facade-level embed failure override (for tests only).

    Prefer calling this over direct attribute assignment when possible.
    """
    global _embed_failures
    _embed_failures = n


# ---------------------------------------------------------------------------
# Corruption recovery
# ---------------------------------------------------------------------------

_MALFORMED_MARKERS = ("malformed", "database disk image", "not a database", "file is not a database")


def _is_corruption_error(exc: BaseException) -> bool:
    """Return True if *exc* indicates SQLite database corruption."""
    msg = str(exc).lower()
    return any(m in msg for m in _MALFORMED_MARKERS)


def _recover_and_reset_backend(trw_dir: Path) -> None:
    """Force-recover the database, backfill from YAML, and reset the singleton."""
    from trw_mcp.state._memory_connection import get_backend as _get_backend
    from trw_mcp.state._memory_connection import reset_backend as _reset

    db_path = trw_dir / "memory" / "memory.db"
    logger.error("runtime_corruption_detected", db=str(db_path), action="recover_and_reset")
    _reset()
    if db_path.exists():
        from trw_memory.storage.sqlite_backend import SQLiteBackend

        conn = SQLiteBackend.recover_db(db_path)
        conn.close()
    # Remove migration sentinel so ensure_migrated re-runs the YAML backfill.
    # This restores any entries that were in YAML but lost from SQLite.
    sentinel = trw_dir / "memory" / ".migrated"
    if sentinel.exists():
        sentinel.unlink()
    # Re-open via the normal singleton path — triggers ensure_migrated (YAML backfill).
    _get_backend(trw_dir)


# ---------------------------------------------------------------------------
# CRUD operations (return shapes match original YAML tools)
# ---------------------------------------------------------------------------


def store_learning(
    trw_dir: Path,
    learning_id: str,
    summary: str,
    detail: str,
    *,
    tags: list[str] | None = None,
    evidence: list[str] | None = None,
    impact: float = 0.5,
    shard_id: str | None = None,
    source_type: str = "agent",
    source_identity: str = "",
    client_profile: str = "",
    model_id: str = "",
    assertions: list[dict[str, str]] | None = None,
    # PRD-CORE-110: Typed learning fields
    type: str = "pattern",
    nudge_line: str = "",
    expires: str = "",
    confidence: str = "unverified",
    task_type: str = "",
    domain: list[str] | None = None,
    phase_origin: str = "",
    phase_affinity: list[str] | None = None,
    team_origin: str = "",
    protection_tier: str = "normal",
    # PRD-CORE-111: Code-grounded anchors
    anchors: list[dict[str, object]] | None = None,
    anchor_validity: float = 1.0,
) -> dict[str, object]:
    """Store a learning entry in SQLite and return the tool result dict.

    QUAL-018 FR03: Infers topic tags from the summary before storing.

    Return shape matches ``trw_learn`` output:
    ``{"learning_id", "path", "status", "distribution_warning"}``.
    """
    # QUAL-018 FR03/FR05: Infer topic tags and append (no duplicates)
    from trw_mcp.state.analytics import infer_topic_tags

    enriched_tags = list(tags) if tags else []
    inferred = infer_topic_tags(summary, enriched_tags)
    if inferred:
        enriched_tags.extend(inferred)

    entry = _learning_to_memory_entry(
        learning_id,
        summary,
        detail,
        tags=enriched_tags,
        evidence=evidence,
        impact=impact,
        shard_id=shard_id,
        source_type=source_type,
        source_identity=source_identity,
        client_profile=client_profile,
        model_id=model_id,
        assertions=assertions,
        type=type,
        nudge_line=nudge_line,
        expires=expires,
        confidence=confidence,
        task_type=task_type,
        domain=domain,
        phase_origin=phase_origin,
        phase_affinity=phase_affinity,
        team_origin=team_origin,
        protection_tier=protection_tier,
        anchors=anchors,
        anchor_validity=anchor_validity,
    )

    for attempt in range(2):
        try:
            backend = get_backend(trw_dir)
            backend.store(entry)
            break
        except Exception as exc:
            if attempt == 0 and _is_corruption_error(exc):
                _recover_and_reset_backend(trw_dir)
                continue
            raise

    # Generate and store embedding when enabled
    backend = get_backend(trw_dir)
    embed_input = f"{summary} {detail}"
    _embed_and_store(backend, learning_id, embed_input)

    logger.info(
        "memory_store_ok",
        learning_id=learning_id,
        summary_len=len(summary),
        tags=enriched_tags,
        impact=impact,
    )
    return {
        "learning_id": learning_id,
        "path": f"sqlite://{learning_id}",
        "status": "recorded",
        "distribution_warning": "",
    }


def recall_learnings(
    trw_dir: Path,
    query: str,
    *,
    tags: list[str] | None = None,
    min_impact: float = 0.0,
    status: str | None = None,
    max_results: int = 25,
    compact: bool = False,
) -> list[dict[str, object]]:
    """Search learnings from SQLite and return dicts matching recall shape.

    For wildcard queries (``*`` or empty), lists all entries.
    Otherwise performs keyword search.
    """
    is_wildcard = query.strip() in ("*", "")

    mem_status: MemoryStatus | None = None
    if status is not None:
        try:
            mem_status = MemoryStatus(status)
        except ValueError:
            logger.debug("invalid_status_ignored", status=status)

    from trw_memory.models.memory import MemoryEntry as _ME

    entries: list[_ME] = []
    for attempt in range(2):
        try:
            backend = get_backend(trw_dir)
            if is_wildcard:
                entries = backend.list_entries(
                    status=mem_status,
                    namespace=_NAMESPACE,
                    limit=max_results if max_results > 0 else _MAX_ENTRIES,
                )
            else:
                top_k = max_results if max_results > 0 else _MAX_ENTRIES
                entries = _search_entries(
                    backend,
                    query,
                    top_k=top_k,
                    tags=tags,
                    mem_status=mem_status,
                    min_impact=min_impact,
                )
            break
        except Exception as exc:
            if attempt == 0 and _is_corruption_error(exc):
                _recover_and_reset_backend(trw_dir)
                continue
            raise

    results: list[dict[str, object]] = []
    for entry in entries:
        # Wildcard path: list_entries doesn't filter by tags/impact, so apply
        # _apply_entry_filters (AND semantics) to match the search path.
        # Search path already applies these filters internally.
        if is_wildcard and not _apply_entry_filters(entry, tags, mem_status, min_impact):
            continue
        # Non-wildcard: still guard min_impact (search may not filter on dict-level impact)
        if not is_wildcard and entry.importance < min_impact:
            continue
        results.append(_memory_to_learning_dict(entry, compact=compact))

    logger.info(
        "memory_search_ok",
        query=query[:50],
        result_count=len(results),
        is_wildcard=is_wildcard,
    )
    return results


def update_learning(  # noqa: C901
    trw_dir: Path,
    learning_id: str,
    *,
    status: str | None = None,
    detail: str | None = None,
    impact: float | None = None,
    summary: str | None = None,
    # PRD-CORE-110: Typed learning update fields
    type: str | None = None,
    nudge_line: str | None = None,
    expires: str | None = None,
    confidence: str | None = None,
    task_type: str | None = None,
    domain: list[str] | None = None,
    phase_origin: str | None = None,
    phase_affinity: list[str] | None = None,
    team_origin: str | None = None,
    protection_tier: str | None = None,
) -> dict[str, str]:
    """Update a learning entry in SQLite.

    Return shape matches ``trw_learn_update`` output:
    ``{"learning_id", "changes", "status"}``.
    """
    backend = get_backend(trw_dir)
    existing = backend.get(learning_id)
    if existing is None:
        return {"error": f"Learning {learning_id} not found", "status": "not_found"}

    fields: dict[str, str | float | list[str]] = {}
    changes: list[str] = []

    if status is not None:
        valid_statuses = {"active", "resolved", "obsolete"}
        if status not in valid_statuses:
            return {
                "error": f"Invalid status '{status}'. Must be one of: {valid_statuses}",
                "status": "invalid",
            }
        fields["status"] = status
        changes.append(f"status\u2192{status}")

    if detail is not None:
        fields["detail"] = detail
        changes.append("detail updated")

    if summary is not None:
        fields["content"] = summary
        changes.append("summary updated")

    if impact is not None:
        if not 0.0 <= impact <= 1.0:
            return {"error": f"Impact must be 0.0-1.0, got {impact}", "status": "invalid"}
        fields["importance"] = impact
        changes.append(f"impact\u2192{impact}")

    # PRD-CORE-110: Typed learning fields
    if type is not None:
        valid_types = {"incident", "pattern", "convention", "hypothesis", "workaround"}
        if type not in valid_types:
            return {
                "error": f"Invalid type '{type}'. Must be one of: {valid_types}",
                "status": "invalid",
            }
        fields["type"] = type
        changes.append(f"type\u2192{type}")
    if nudge_line is not None:
        fields["nudge_line"] = nudge_line
        changes.append("nudge_line updated")
    if expires is not None:
        fields["expires"] = expires
        changes.append("expires updated")
    if confidence is not None:
        fields["confidence"] = confidence
        changes.append(f"confidence\u2192{confidence}")
    if task_type is not None:
        fields["task_type"] = task_type
        changes.append(f"task_type\u2192{task_type}")
    if domain is not None:
        fields["domain"] = domain
        changes.append("domain updated")
    if phase_origin is not None:
        fields["phase_origin"] = phase_origin
        changes.append(f"phase_origin\u2192{phase_origin}" if phase_origin else "phase_origin cleared")
    if phase_affinity is not None:
        fields["phase_affinity"] = phase_affinity
        changes.append("phase_affinity updated")
    if team_origin is not None:
        fields["team_origin"] = team_origin
        changes.append(f"team_origin\u2192{team_origin}" if team_origin else "team_origin cleared")
    if protection_tier is not None:
        fields["protection_tier"] = protection_tier
        changes.append(f"protection_tier\u2192{protection_tier}")

    if not changes:
        return {"learning_id": learning_id, "status": "no_changes"}

    backend.update(learning_id, **fields)

    logger.info("memory_update_learning", learning_id=learning_id, changes=changes)
    return {
        "learning_id": learning_id,
        "changes": ", ".join(changes),
        "status": "updated",
    }


def find_entry_by_id(trw_dir: Path, learning_id: str) -> dict[str, object] | None:
    """Look up a single learning entry by ID.

    Returns the dict in learning format, or None if not found.
    """
    backend = get_backend(trw_dir)
    entry = backend.get(learning_id)
    if entry is None:
        return None
    return _memory_to_learning_dict(entry)


def list_active_learnings(
    trw_dir: Path,
    *,
    min_impact: float = 0.0,
    limit: int = DEFAULT_LIST_LIMIT,
) -> list[dict[str, object]]:
    """List all active learning entries from SQLite.

    Used by claude_md.py for CLAUDE.md promotion and analytics.
    """
    backend = get_backend(trw_dir)
    entries = backend.list_entries(
        status=MemoryStatus.ACTIVE,
        namespace=_NAMESPACE,
        limit=limit,
    )
    results: list[dict[str, object]] = [
        _memory_to_learning_dict(entry) for entry in entries if entry.importance >= min_impact
    ]
    return results


def list_entries_by_status(
    trw_dir: Path,
    *,
    status: str = "active",
    min_impact: float = 0.0,
    limit: int = DEFAULT_LIST_LIMIT,
) -> list[dict[str, object]]:
    """Return all entries with the given status as learning dicts.

    PRD-FIX-033-FR01: Single SQLite query for bulk entry retrieval.
    """
    try:
        mem_status = MemoryStatus(status)
    except ValueError:
        return []
    backend = get_backend(trw_dir)
    entries = backend.list_entries(
        status=mem_status,
        namespace=_NAMESPACE,
        limit=limit,
    )
    results: list[dict[str, object]] = [
        _memory_to_learning_dict(entry) for entry in entries if entry.importance >= min_impact
    ]
    return results


def find_yaml_path_for_entry(trw_dir: Path, entry_id: str) -> Path | None:
    """Resolve the YAML file path for a given entry_id.

    PRD-FIX-033-FR05: YAML path resolution for cold archive calls.
    """
    import re as _re

    cfg = get_config()
    entries_dir = trw_dir / cfg.learnings_dir / cfg.entries_dir
    if not entries_dir.exists():
        return None

    sanitized = _re.sub(r"[^a-zA-Z0-9_\-]", "-", entry_id)

    # Try exact match first
    candidate = entries_dir / f"{sanitized}.yaml"
    if candidate.exists():
        return candidate

    # Fall back to partial match
    for yaml_file in entries_dir.glob("*.yaml"):
        if yaml_file.name == "index.yaml":
            continue
        if sanitized in yaml_file.stem or entry_id in yaml_file.stem:
            return yaml_file

    return None


def count_entries(trw_dir: Path) -> int:
    """Return total number of entries in the SQLite store."""
    backend = get_backend(trw_dir)
    return backend.count(namespace=_NAMESPACE)


def update_access_tracking(trw_dir: Path, learning_ids: list[str]) -> None:
    """Increment access_count and last_accessed_at for recalled entries."""
    backend = get_backend(trw_dir)
    now = datetime.now(timezone.utc)
    for lid in learning_ids:
        try:
            entry = backend.get(lid)
            if entry is not None:
                backend.update(
                    lid,
                    access_count=entry.access_count + 1,
                    last_accessed_at=now,
                )
        except Exception:  # per-item error handling: access tracking is best-effort, one failure must not break recall results  # noqa: PERF203
            logger.warning(
                "access_tracking_update_failed",
                exc_info=True,
                entry_id=lid,
            )
            continue


# ---------------------------------------------------------------------------
# WAL checkpoint management (PRD-QUAL-050-FR05)
# ---------------------------------------------------------------------------


def maybe_checkpoint_wal(trw_dir: Path) -> dict[str, object]:
    """Checkpoint the SQLite WAL file if it exceeds a configurable size threshold.

    PRD-QUAL-050-FR05: During ``trw_session_start()`` auto-maintenance, if the
    WAL file exceeds ``wal_checkpoint_threshold_mb`` (default 10 MB), runs
    ``PRAGMA wal_checkpoint(TRUNCATE)`` to reclaim space.

    Fail-open: checkpoint failure is logged but never propagated. Returns a
    result dict describing what happened.

    Args:
        trw_dir: Path to the ``.trw`` directory.

    Returns:
        Dict with either ``{"skipped": True, "reason": ...}`` when under
        threshold, ``{"checkpointed": True, ...}`` on success, or
        ``{"error": True, "reason": ...}`` on failure.
    """
    try:
        config = get_config()
        threshold_bytes = config.wal_checkpoint_threshold_mb * 1024 * 1024

        # memory.db is the primary SQLite store (distinct from vectors.db)
        db_path = trw_dir / "memory" / "memory.db"
        wal_path = db_path.with_suffix(".db-wal")

        if not wal_path.exists():
            return {"skipped": True, "reason": "no_wal_file"}

        wal_size = wal_path.stat().st_size
        if wal_size < threshold_bytes:
            return {"skipped": True, "reason": "under_threshold"}

        wal_size_mb = round(wal_size / (1024 * 1024), 1)
        logger.info(
            "wal_checkpoint_starting",
            wal_size_mb=wal_size_mb,
            threshold_mb=config.wal_checkpoint_threshold_mb,
        )

        # Use a fresh connection with busy_timeout for the checkpoint.
        # PASSIVE instead of TRUNCATE: TRUNCATE requires exclusive lock
        # across ALL connections (including other processes), which fails
        # under concurrent multi-process access and can corrupt the DB.
        # PASSIVE checkpoints what it can without blocking writers.
        conn = sqlite3.connect(str(db_path), timeout=5.0)
        try:
            conn.execute("PRAGMA busy_timeout = 5000")
            row = conn.execute("PRAGMA wal_checkpoint(PASSIVE)").fetchone()
            pages_checkpointed = row[1] if row else 0
        finally:
            conn.close()

        logger.info(
            "wal_checkpoint_complete",
            wal_size_before_mb=wal_size_mb,
            pages_checkpointed=pages_checkpointed,
        )
        return {
            "checkpointed": True,
            "wal_size_before_mb": wal_size_mb,
            "pages_checkpointed": pages_checkpointed,
        }
    except Exception:  # justified: fail-open, WAL checkpoint must not block session start
        logger.warning("wal_checkpoint_failed", exc_info=True)
        return {"error": True, "reason": "checkpoint_failed"}
