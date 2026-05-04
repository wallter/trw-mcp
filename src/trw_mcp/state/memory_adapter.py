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

import hashlib
from pathlib import Path

import structlog
from trw_memory.exceptions import CorruptDatabaseUnsalvageableError
from trw_memory.models.config import MemoryConfig
from trw_memory.models.memory import MemoryStatus
from trw_memory.security.recall_filter import filter_recall_window
from trw_memory.security.runtime import (
    initialize_canaries,
    prepare_entry_for_store,
    probe_canaries,
    should_halt_recalls,
    store_quarantined_entry,
)

from trw_mcp.models.config import get_config as get_config
from trw_mcp.state._constants import DEFAULT_LIST_LIMIT, DEFAULT_NAMESPACE

# Re-export: connection mgmt + embedding ops + query routing + transforms.
from trw_mcp.state._memory_connection import (
    _embed_and_store as _embed_and_store,
    backfill_embeddings as backfill_embeddings,
    embed_text as embed_text,
    embed_text_batch as embed_text_batch,
    embedding_available as embedding_available,
    ensure_migrated as ensure_migrated,
    get_backend as get_backend,
    get_embed_failure_count as get_embed_failure_count,
    get_embedder as get_embedder,
    get_initialized_embedder as get_initialized_embedder,
    reset_backend as reset_backend,
    reset_embedder as reset_embedder,
)
from trw_mcp.state._memory_queries import (
    _apply_entry_filters as _apply_entry_filters,
    _keyword_search as _keyword_search,
    _lookup_id_tokens as _lookup_id_tokens,
    _search_entries as _search_entries,
    _search_intersect_keywords as _search_intersect_keywords,
)
from trw_mcp.state._memory_transforms import (
    _learning_to_memory_entry as _learning_to_memory_entry,
    _memory_to_learning_dict as _memory_to_learning_dict,
)

logger = structlog.get_logger(__name__)

# Preserve module-level constants for backward compatibility with test patches
_NAMESPACE = DEFAULT_NAMESPACE
_MAX_ENTRIES = DEFAULT_LIST_LIMIT

# Facade-level override for the embed failure counter.  Tests may set this
# attribute directly (``memory_adapter._embed_failures = N``) to inject a
# known count; ``None`` means "read from _memory_connection" (normal path).
_embed_failures: int | None = None


# Embedding-status + corruption-recovery helpers extracted to _memory_recovery
# (PRD-DIST-243 batch 44).
from trw_mcp.state._memory_recovery import (
    _is_corruption_error as _is_corruption_error,
    _log_terminal_recovery as _log_terminal_recovery,
    _recover_and_reset_backend as _recover_and_reset_backend,
    check_embeddings_status as check_embeddings_status,
    reset_embed_failure_count as reset_embed_failure_count,
    set_embed_failure_count_for_testing as set_embed_failure_count_for_testing,
)


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
    session_id: str | None = None,
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
            sec_cfg = MemoryConfig(storage_path=str(trw_dir / "memory"))
            initialize_canaries(sec_cfg, backend=backend)
            decision = prepare_entry_for_store(
                entry,
                backend=backend,
                config=sec_cfg,
                session_id=session_id,
                trw_dir=trw_dir,
            )
            if decision.quarantined:
                store_quarantined_entry(sec_cfg, decision.entry)
                return {
                    "learning_id": learning_id,
                    "path": f"sqlite://{learning_id}",
                    "status": "quarantined",
                    "distribution_warning": "",
                }
            backend.store(decision.entry)
            break
        except Exception as exc:  # justified: boundary, corruption recovery retries storage before surfacing failure
            if isinstance(exc, CorruptDatabaseUnsalvageableError):
                _log_terminal_recovery(trw_dir / "memory" / "memory.db", exc)
                raise
            if attempt == 0 and _is_corruption_error(exc):
                logger.warning(
                    "memory_store_retry_after_corruption",
                    learning_id=learning_id,
                    attempt=attempt + 1,
                    exc_info=True,
                )
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
    allow_cold_embedding_init: bool = True,
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
            sec_cfg = MemoryConfig(storage_path=str(trw_dir / "memory"))
            initialize_canaries(sec_cfg, backend=backend)
            if should_halt_recalls(sec_cfg):
                from trw_memory.exceptions import CanaryTamperError

                raise CanaryTamperError("recall halted after canary tamper")
            probe_canaries(sec_cfg, backend=backend)
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
                    allow_cold_embedding_init=allow_cold_embedding_init,
                )
            break
        except Exception as exc:  # justified: boundary, corruption recovery retries recall before surfacing failure
            if isinstance(exc, CorruptDatabaseUnsalvageableError):
                _log_terminal_recovery(trw_dir / "memory" / "memory.db", exc)
                raise
            if attempt == 0 and _is_corruption_error(exc):
                logger.warning(
                    "memory_recall_retry_after_corruption",
                    query=query,
                    attempt=attempt + 1,
                    exc_info=True,
                )
                _recover_and_reset_backend(trw_dir)
                continue
            raise

    public_entries = [entry for entry in entries if entry.metadata.get("system_canary") != "true"]
    filter_result = (
        filter_recall_window(public_entries, mode=sec_cfg.recall_filter_mode) if sec_cfg.enable_recall_filter else None
    )
    filtered_entries = filter_result.accepted if filter_result is not None else public_entries
    results: list[dict[str, object]] = []
    for entry in filtered_entries:
        # Wildcard: apply _apply_entry_filters to match search-path AND semantics.
        # Non-wildcard: search applies tags/status filters; still guard min_impact here.
        if is_wildcard and not _apply_entry_filters(entry, tags, mem_status, min_impact):
            continue
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


def update_learning(
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
    tags: list[str] | None = None,
) -> dict[str, str]:
    """Update a learning entry in SQLite.

    Return shape matches ``trw_learn_update`` output:
    ``{"learning_id", "changes", "status"}``.
    """
    backend = get_backend(trw_dir)
    existing = backend.get(learning_id)
    if existing is None:
        return {"error": f"Learning {learning_id} not found", "status": "not_found"}

    fields: dict[str, str | float | list[str] | dict[str, str]] = {}
    changes: list[str] = []

    if status is not None:
        valid_statuses = {"active", "resolved", "obsolete", "obsolete_poisoned"}
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
    if tags is not None:
        fields["tags"] = tags
        changes.append("tags updated")

    if summary is not None or detail is not None:
        new_content = summary if summary is not None else existing.content
        new_detail = detail if detail is not None else existing.detail
        if existing.metadata.get("provenance_content_hash") or existing.metadata.get("content_hash"):
            new_metadata = dict(existing.metadata)
            new_metadata["provenance_content_hash"] = hashlib.sha256(f"{new_content}{new_detail}".encode()).hexdigest()
            fields["metadata"] = new_metadata

    if not changes:
        return {"learning_id": learning_id, "status": "no_changes"}

    backend.update(learning_id, **fields)

    logger.info("memory_update_learning", learning_id=learning_id, changes=changes)
    return {
        "learning_id": learning_id,
        "changes": ", ".join(changes),
        "status": "updated",
    }



# Lookup, list, count, access tracking, WAL checkpoint helpers extracted to
# _memory_lookups.py (PRD-DIST-243 batch 43).
from trw_mcp.state._memory_lookups import (
    count_entries as count_entries,
    find_entry_by_id as find_entry_by_id,
    find_yaml_path_for_entry as find_yaml_path_for_entry,
    increment_session_counts as increment_session_counts,
    list_active_learnings as list_active_learnings,
    list_entries_by_status as list_entries_by_status,
    maybe_checkpoint_wal as maybe_checkpoint_wal,
    update_access_tracking as update_access_tracking,
)
