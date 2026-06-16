# ruff: noqa: E402
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

import sqlite3
from pathlib import Path

import structlog
from trw_memory.exceptions import CorruptDatabaseUnsalvageableError, StorageError

# PRD-FIX-COMPOUNDING-2 FR01: ``schedule_graph_update`` is imported (and
# re-exported) for the operator backfill runbook + parity with the trw-memory
# MemoryClient store path. The in-process store path uses ``update_entry_graph``
# directly on the singleton connection — see store_learning for the path-
# divergence rationale.
from trw_memory.graph import schedule_graph_update as schedule_graph_update
from trw_memory.graph import update_entry_graph
from trw_memory.models.config import MemoryConfig
from trw_memory.security.runtime import (
    initialize_canaries as initialize_canaries,
)
from trw_memory.security.runtime import (
    prepare_entry_for_store,
    store_quarantined_entry,
)

# ``probe_canaries`` / ``should_halt_recalls`` are re-exported (not used here)
# so the recall path in ``_memory_recall`` can resolve them through this facade
# and existing tests patching ``memory_adapter.<name>`` still take effect
# (PRD-CORE-185 FR06 split preserves the patch seam).
from trw_memory.security.runtime import probe_canaries as probe_canaries
from trw_memory.security.runtime import should_halt_recalls as should_halt_recalls

from trw_mcp.models.config import get_config as get_config
from trw_mcp.state import _memory_connection, _memory_lookups, _memory_queries, _memory_recovery, _memory_transforms
from trw_mcp.state._constants import DEFAULT_NAMESPACE

# Re-export: connection mgmt + embedding ops + query routing + transforms.
_embed_and_store = _memory_connection._embed_and_store
_embed_and_store_returning = _memory_connection._embed_and_store_returning
backfill_embeddings = _memory_connection.backfill_embeddings
embed_text = _memory_connection.embed_text
embed_text_batch = _memory_connection.embed_text_batch
embedding_available = _memory_connection.embedding_available
ensure_migrated = _memory_connection.ensure_migrated
get_backend = _memory_connection.get_backend
get_embed_failure_count = _memory_connection.get_embed_failure_count
get_embedder = _memory_connection.get_embedder
reset_backend = _memory_connection.reset_backend
reset_embedder = _memory_connection.reset_embedder
_apply_entry_filters = _memory_queries._apply_entry_filters
_keyword_search = _memory_queries._keyword_search
_search_entries = _memory_queries._search_entries
_learning_to_memory_entry = _memory_transforms._learning_to_memory_entry
_memory_to_learning_dict = _memory_transforms._memory_to_learning_dict

logger = structlog.get_logger(__name__)

# Preserve module-level constants for backward compatibility with test patches
_NAMESPACE = DEFAULT_NAMESPACE

# Facade-level override for the embed failure counter.  Tests may set this
# attribute directly (``memory_adapter._embed_failures = N``) to inject a
# known count; ``None`` means "read from _memory_connection" (normal path).
_embed_failures: int | None = None


# Embedding-status + corruption-recovery helpers extracted to _memory_recovery
# (PRD-DIST-243 batch 44).
_is_corruption_error = _memory_recovery._is_corruption_error
_log_terminal_recovery = _memory_recovery._log_terminal_recovery
_memory_recovery_in_progress = _memory_recovery._memory_recovery_in_progress
_recover_and_reset_backend = _memory_recovery._recover_and_reset_backend
_schedule_deferred_recovery = _memory_recovery._schedule_deferred_recovery
check_embeddings_status = _memory_recovery.check_embeddings_status
reset_embed_failure_count = _memory_recovery.reset_embed_failure_count
set_embed_failure_count_for_testing = _memory_recovery.set_embed_failure_count_for_testing

# update_learning extracted to _memory_update (PRD-DIST-243 batch 59).
from trw_mcp.state._memory_update import update_learning as update_learning

# PRD-CORE-185 core185-2: re-exported so the user-tier corruption-recovery
# branch in ``store_learning`` (and tests patching ``memory_adapter.<name>``)
# resolve the user-backend singleton reset through this facade.
from trw_mcp.state._user_tier import reset_user_backend as reset_user_backend

# ---------------------------------------------------------------------------
# CRUD operations (return shapes match original YAML tools)
# ---------------------------------------------------------------------------


def _store_error_result(learning_id: str, exc: BaseException) -> dict[str, object]:
    """Translate a storage failure into the stable ``store_learning`` shape.

    Seam contract: a non-corruption :class:`StorageError` (e.g. "disk full",
    stale connection) must NOT leak a raw traceback to MCP tool callers — that
    breaks the JSON-RPC response contract. Return the same keys as the success
    path with ``status="error"`` and an ``error`` message so callers can branch
    on the result dict instead of catching an exception across the boundary.
    """
    return {
        "learning_id": learning_id,
        "path": f"sqlite://{learning_id}",
        "status": "error",
        "error": str(exc),
        "distribution_warning": "",
    }


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
    # PRD-DIST-254 §FR02 (cycle 112): policy-relevant metadata.
    metadata: dict[str, str] | None = None,
    # PRD-CORE-185 FR05/FR07: write-tier override ("auto"|"project"|"user").
    scope: str = "auto",
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
        metadata=metadata,
        scope=scope,  # type: ignore[arg-type]
    )

    # PRD-CORE-185 FR05: route to the USER store when the entry was classified
    # portable (and a user-scope store is present). The user store is a distinct
    # DB file rooted at the machine-local user memory dir; dedup/canary/recovery
    # all key on that root so a portable learning de-dupes against the user store
    # (not the project store). When tier == project, behavior is byte-identical
    # to today.
    from trw_mcp.state._tier_routing import tier_of_entry
    from trw_mcp.state._user_paths import resolve_user_memory_dir
    from trw_mcp.state._user_tier import get_user_backend

    is_user_write = tier_of_entry(entry) == "user"
    store_dir = resolve_user_memory_dir() if is_user_write else trw_dir / "memory"

    from trw_memory.storage.sqlite_backend import SQLiteBackend as _SQLiteBackend

    def _store_backend() -> _SQLiteBackend:
        return get_user_backend() if is_user_write else get_backend(trw_dir)

    for attempt in range(2):
        try:
            backend = _store_backend()
            sec_cfg = MemoryConfig(storage_path=str(store_dir))
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
                _log_terminal_recovery(store_dir / "memory.db", exc)
                raise
            if attempt == 0 and not is_user_write and _is_corruption_error(exc):
                # Corruption recovery resets the PROJECT singleton (keyed on
                # trw_dir). The user store is a distinct file; a user-write
                # corruption is handled by the symmetric branch below rather
                # than resetting the unrelated project backend.
                logger.warning(
                    "memory_store_retry_after_corruption",
                    learning_id=learning_id,
                    attempt=attempt + 1,
                    exc_info=True,
                )
                _recover_and_reset_backend(trw_dir)
                continue
            if attempt == 0 and is_user_write and _is_corruption_error(exc):
                # core185-2: a user-tier write hit corruption. Reset the
                # machine-local USER singleton (a distinct file from the project
                # store) and retry once -- symmetric to the project recovery
                # above. Without this branch a corrupted user store would be
                # surfaced as a silent store error and every subsequent
                # user-tier write in the session would fail identically.
                logger.warning(
                    "memory_store_user_retry_after_corruption",
                    learning_id=learning_id,
                    attempt=attempt + 1,
                    exc_info=True,
                )
                reset_user_backend()
                continue
            # Seam: translate a non-corruption StorageError into a stable error
            # dict rather than leaking the exception to MCP callers. The
            # CorruptDatabaseUnsalvageableError subclass is handled above (raise),
            # so only genuine storage failures (disk full, stale connection)
            # reach here. CanaryTamperError etc. are not StorageError → re-raised.
            if isinstance(exc, StorageError):
                logger.warning(
                    "memory_store_storage_error",
                    learning_id=learning_id,
                    error=str(exc),
                    exc_info=True,
                )
                return _store_error_result(learning_id, exc)
            raise

    # Generate and store embedding when enabled. Capture the vector so the
    # graph scheduler can reuse it (FR02 — single embed call per store).
    # Use the SAME backend the entry was stored into (user vs project).
    backend = _store_backend()
    embed_input = f"{summary} {detail}"
    embedding_vec = _embed_and_store_returning(backend, learning_id, embed_input)

    # PRD-FIX-COMPOUNDING-2 FR01: enrich the knowledge graph after a successful
    # store. The MCP store path mirrors MemoryClient.store_impl in trw-memory in
    # every respect EXCEPT this dispatch — which is why memory_graph_edges was 0
    # for the entire project lifespan. Fail-open (NFR02): graph enrichment
    # failure must never fail a store_learning call.
    #
    # NOTE on path divergence (root-caused during FR05 wiring): the MCP server
    # opens its SQLite singleton directly at ``.trw/memory/memory.db`` (NO
    # per-namespace subdirectory), whereas ``schedule_graph_update``'s worker
    # reopens a backend via ``create_backend_from_config`` which ALWAYS resolves
    # ``storage_path/<namespace>/sqlite_db_name`` (e.g. ``.trw/memory/default/
    # memory.db``). The async worker would therefore write edges into a DIFFERENT
    # file than the one the singleton reads — silently producing 0 visible edges
    # (the exact failure the async path would re-introduce). To land edges in the
    # SAME database the singleton serves, enrich SYNCHRONOUSLY on the singleton's
    # own connection via ``update_entry_graph`` (which uses ``backend._conn``).
    # Quality > Velocity (value hierarchy): correct same-DB edges outrank the
    # ~5ms NFR01 async budget.
    try:
        sec_cfg = MemoryConfig(storage_path=str(store_dir))
        update_entry_graph(decision.entry, backend, embedding=embedding_vec, config=sec_cfg)
    except (
        StorageError,
        sqlite3.Error,
        ValueError,
        RuntimeError,
    ):  # justified: fail-open — graph enrichment is best-effort
        logger.warning("graph_update_dispatch_failed", learning_id=learning_id, exc_info=True)

    logger.info(
        "memory_store_ok",
        learning_id=learning_id,
        summary_len=len(summary),
        tags=enriched_tags,
        impact=impact,
        tier="user" if is_user_write else "project",  # PRD-CORE-185 FR05 NFR06
    )
    return {
        "learning_id": learning_id,
        "path": f"sqlite://{learning_id}",
        "status": "recorded",
        "distribution_warning": "",
    }


# recall_learnings + _rank_wildcard_by_utility extracted to _memory_recall.py
# (PRD-CORE-185 FR06 user-tier federation + the 350 eff-LOC gate, NFR07).
# Lookup, list, count, access tracking, WAL checkpoint helpers extracted to
# _memory_lookups.py (PRD-DIST-243 batch 43).
count_entries = _memory_lookups.count_entries
find_entry_by_id = _memory_lookups.find_entry_by_id
find_yaml_path_for_entry = _memory_lookups.find_yaml_path_for_entry
increment_session_counts = _memory_lookups.increment_session_counts
list_active_learnings = _memory_lookups.list_active_learnings
list_entries_by_status = _memory_lookups.list_entries_by_status
maybe_checkpoint_wal = _memory_lookups.maybe_checkpoint_wal
update_access_tracking = _memory_lookups.update_access_tracking

# PRD-CORE-185 FR06: user-tier federated recall + the wildcard-utility ranker,
# extracted to _memory_recall.py (project ∪ user federation + the 350 eff-LOC
# gate, NFR07). This re-export is THE recall path — it supersedes the historical
# inline recall_learnings, adding the user-tier federation step.
# F5 root-cause B: forced (non-deferrable) knowledge-graph backfill over the
# EXISTING corpus, extracted to a focused sibling so the facade stays under the
# 350 eff-LOC gate. Runs update_entry_graph on the singleton's own connection
# (NEVER schedule_graph_update — that hits a divergent per-namespace DB file).
from trw_mcp.state._graph_backfill import (
    backfill_graph as backfill_graph,
)
from trw_mcp.state._memory_recall import (
    _rank_wildcard_by_utility as _rank_wildcard_by_utility,
)
from trw_mcp.state._memory_recall import (
    recall_learnings as recall_learnings,
)

# PRD-CORE-185 FR08: opt-in, non-destructive user-tier backfill (default
# ``dry_run=True``). Re-exported from the focused sibling so the adapter facade
# stays under the 350 gate (NFR07); the FR08 wiring assertion greps
# ``reclassify_to_user_tier`` + ``dry_run`` here.
from trw_mcp.state._user_tier_backfill import (
    reclassify_to_user_tier as reclassify_to_user_tier,
)
