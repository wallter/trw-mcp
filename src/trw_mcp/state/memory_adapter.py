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
from trw_memory.models.memory import MemoryEntry
from trw_memory.security.runtime import (
    initialize_canaries as initialize_canaries,
)

# ``probe_canaries`` / ``should_halt_recalls`` are re-exported (not used here)
# so the recall path in ``_memory_recall`` can resolve them through this facade
# and existing tests patching ``memory_adapter.<name>`` still take effect
# (PRD-CORE-185 FR06 split preserves the patch seam).
from trw_memory.security.runtime import probe_canaries as probe_canaries
from trw_memory.security.runtime import should_halt_recalls as should_halt_recalls

# PRD-CORE-251 FR03: THE write path. Entry construction, namespace validation,
# RBAC, input validation, the quarantine decision and the row write all happen
# there now -- ``trw-memory-server`` and this server reach the store through the
# same function. What stays on this side is in ``store_learning``'s docstring.
from trw_memory.tools.store import memory_store_impl

from trw_mcp.models.config import get_config as get_config
from trw_mcp.state import _memory_connection, _memory_lookups, _memory_queries, _memory_recovery, _memory_transforms
from trw_mcp.state._constants import DEFAULT_NAMESPACE
from trw_mcp.state._store_arguments import build_store_arguments

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


#: PRD-CORE-251 FR03: the ONE place the two status vocabularies meet.
#:
#: ``memory_store_impl`` answers in the memory vocabulary
#: (``stored``/``updated``/``quarantined``/``invalid``/``blocked``/``not_found``/
#: ``error``); ``trw_learn`` answers in the learning one
#: (``recorded``/``quarantined``/``error``). The translation is DATA, not an
#: inline conditional, because ``tools/_learn_impl.py`` suppresses the YAML
#: sidecar on exactly ``status == "error"`` -- the D8 dual-write fix. A status
#: that maps to ``recorded`` when the row did NOT land writes an unrecallable
#: YAML-with-no-DB-row, which is the orphan-sidecar data loss that produced the
#: observed "one summary 92x" pathology.
#:
#: ``tests/test_memory_adapter_store_recall.py`` pins BOTH halves: that every
#: status literal ``memory_store_impl`` can return is a key here, and that an
#: UNKNOWN status falls to ``"error"`` rather than to ``"recorded"``.
_STORE_STATUS_TO_LEARNING_STATUS: dict[str, str] = {
    "stored": "recorded",
    "updated": "recorded",
    "quarantined": "quarantined",
    "invalid": "error",
    "blocked": "error",
    "not_found": "error",
    "error": "error",
}


def _learning_status_for(store_status: object) -> str:
    """Translate one ``memory_store_impl`` status into the learning vocabulary.

    An unrecognised status resolves to ``"error"``. That direction is the
    fail-safe one: it suppresses the YAML sidecar and RETAINS the write-ahead
    journal record for replay, so an unmapped status costs a retry rather than
    an orphaned sidecar the dedup check can never suppress.
    """
    return _STORE_STATUS_TO_LEARNING_STATUS.get(str(store_status), "error")


#: How far to follow ``__cause__`` when classifying a store failure. Five is
#: past any real wrap depth (driver -> backend -> tool) and bounds a cyclic or
#: adversarially deep chain.
_MAX_CAUSE_DEPTH = 5


def _failure_chain(exc: BaseException) -> list[BaseException]:
    """Return *exc* and its ``__cause__`` ancestors, outermost first.

    PRD-CORE-251 FR03 made this necessary: ``memory_store_impl`` re-raises a
    failed row+vector write as a NEW ``StorageError`` ("failed to persist
    entry+vector ...; transaction rolled back") to state its atomicity
    guarantee. That wrapper carries neither the "database disk image is
    malformed" text ``_is_corruption_error`` matches on NOR the
    ``CorruptDatabaseUnsalvageableError`` type the terminal branch matches on,
    so classifying the wrapper alone would silently disable BOTH corruption
    paths: a corrupt store would be reported as an ordinary error and never
    recovered.
    """
    chain: list[BaseException] = []
    current: BaseException | None = exc
    while current is not None and len(chain) < _MAX_CAUSE_DEPTH:
        chain.append(current)
        current = current.__cause__
    return chain


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
    anchor_validity: float | None = None,
    session_id: str | None = None,
    # PRD-DIST-254 §FR02 (cycle 112): policy-relevant metadata.
    metadata: dict[str, str] | None = None,
    # PRD-CORE-185 FR05/FR07: write-tier override ("auto"|"project"|"user").
    scope: str = "auto",
) -> dict[str, object]:
    """Store a learning entry in SQLite and return the tool result dict.

    QUAL-018 FR03: Infers topic tags from the summary before storing.

    PRD-CORE-251 FR03: the write is EXECUTED by
    :func:`trw_memory.tools.store.memory_store_impl`, which is what gives this
    path the namespace validation, the namespace permission check and the input
    validation it never performed. Four things stay here because they are TRW
    concerns rather than memory ones, and each is expressed as an argument or a
    wrapper rather than dropped:

    * topic-tag inference (knowledge topology) enriches the tags passed in;
    * the user-tier routing decision (PRD-CORE-185 FR05) picks the ``backend``
      and the namespace, and the two symmetric corruption-recovery branches
      below stay keyed on WHICH store was written;
    * the post-store embedding + graph enrichment run on THIS server's singleton
      connection (see the path-divergence note below), so the delegated call is
      told the caller owns them;
    * the memory status vocabulary is translated into the learning one by
      :data:`_STORE_STATUS_TO_LEARNING_STATUS`.

    Return shape matches ``trw_learn`` output:
    ``{"learning_id", "path", "status", "distribution_warning"}``.
    """
    # QUAL-018 FR03/FR05: Infer topic tags and append (no duplicates)
    from trw_mcp.state.analytics import infer_topic_tags

    enriched_tags = list(tags) if tags else []
    inferred = infer_topic_tags(summary, enriched_tags)
    if inferred:
        enriched_tags.extend(inferred)

    args = build_store_arguments(
        summary=summary,
        detail=detail,
        tags=enriched_tags,
        shard_id=shard_id,
        source_type=source_type,
        assertions=assertions,
        type=type,
        confidence=confidence,
        domain=domain,
        phase_affinity=phase_affinity,
        protection_tier=protection_tier,
        anchors=anchors,
        impact=impact,
        metadata=metadata,
        scope=scope,  # type: ignore[arg-type]
    )

    # PRD-CORE-185 FR05: route to the USER store when the entry was classified
    # portable (and a user-scope store is present). The user store is a distinct
    # DB file rooted at the machine-local user memory dir; dedup/canary/recovery
    # all key on that root so a portable learning de-dupes against the user store
    # (not the project store). When tier == project, behavior is byte-identical
    # to today.
    from trw_mcp.state._user_paths import resolve_user_memory_dir
    from trw_mcp.state._user_tier import get_user_backend

    is_user_write = args.tier == "user"
    store_dir = resolve_user_memory_dir() if is_user_write else trw_dir / "memory"

    from trw_memory.storage.sqlite_backend import SQLiteBackend as _SQLiteBackend

    def _store_backend() -> _SQLiteBackend:
        return get_user_backend() if is_user_write else get_backend(trw_dir)

    store_result: dict[str, object] = {}
    for attempt in range(2):
        try:
            backend = _store_backend()
            sec_cfg = MemoryConfig(storage_path=str(store_dir))
            initialize_canaries(sec_cfg, backend=backend)
            store_result = memory_store_impl(
                summary,
                args.namespace,
                backend=backend,
                tags=enriched_tags,
                importance=impact,
                detail=detail,
                metadata=args.metadata,
                config=sec_cfg,
                source=args.source,
                source_identity=source_identity,
                session_id=session_id,
                entry_id=learning_id,
                evidence=evidence,
                expires=expires,
                assertions=args.assertions,
                client_profile=client_profile,
                model_id=model_id,
                q_value=args.q_value,
                type=args.type,
                nudge_line=nudge_line,
                confidence=args.confidence,
                task_type=task_type,
                domain=domain or [],
                phase_origin=phase_origin,
                phase_affinity=phase_affinity or [],
                team_origin=team_origin,
                protection_tier=args.protection_tier,
                anchors=args.anchors,
                anchor_validity=anchor_validity,
                trw_dir=trw_dir,
                # This server enriches on its OWN singleton connection below.
                enrich_after_store=False,
                # A security refusal must keep RAISING. The write-ahead journal's
                # replay disposition classifies PIIBlockError / PoisoningError /
                # SchemaValidationError as DETERMINISTIC (dead-letter it) and
                # everything else as retryable; a result dict erases the type and
                # with it that decision, so an unreplayable payload would be
                # retried until it exhausted its budget.
                raise_security_errors=True,
                # The corruption-recovery retry below can only key on the
                # exception type — ``_is_corruption_error`` inspects it.
                raise_storage_errors=True,
            )
            break
        except Exception as exc:  # justified: boundary, corruption recovery retries storage before surfacing failure
            chain = _failure_chain(exc)
            terminal = next((c for c in chain if isinstance(c, CorruptDatabaseUnsalvageableError)), None)
            if terminal is not None:
                _log_terminal_recovery(store_dir / "memory.db", terminal)
                # Surface the TERMINAL error, not the delegated wrapper around
                # it: the caller contract (and the recovery runbook) keys on
                # this type, and the wrapper would degrade it to "some storage
                # error" one frame from the decision that matters.
                if terminal is exc:
                    raise
                raise terminal from exc
            corrupted = any(_is_corruption_error(cause) for cause in chain)
            if attempt == 0 and not is_user_write and corrupted:
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
            if attempt == 0 and is_user_write and corrupted:
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

    status = _learning_status_for(store_result.get("status"))
    if status != "recorded":
        # Quarantined: the entry landed in the quarantine store, not the DB.
        # Anything else: the row did NOT land, so the caller must suppress its
        # YAML sidecar (D8) and keep its journal record for replay.
        result: dict[str, object] = {
            "learning_id": learning_id,
            "path": f"sqlite://{learning_id}",
            "status": status,
            "distribution_warning": "",
        }
        if status == "error":
            logger.warning(
                "memory_store_rejected",
                learning_id=learning_id,
                store_status=store_result.get("status"),
                error=str(store_result.get("error", "")),
            )
            result["error"] = str(store_result.get("error", store_result.get("status", "store rejected")))
        return result

    # Generate and store embedding when enabled. Capture the vector so the
    # graph scheduler can reuse it (FR02 — single embed call per store).
    # Use the SAME backend the entry was stored into (user vs project).
    backend = _store_backend()
    embed_input = f"{summary} {detail}"
    embedding_vec = _embed_and_store_returning(backend, learning_id, embed_input)

    # PRD-FIX-COMPOUNDING-2 FR01: enrich the knowledge graph after a successful
    # store. Fail-open (NFR02): graph enrichment failure must never fail a
    # store_learning call.
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
    # This is also why the delegated store is told ``enrich_after_store=False``.
    # Quality > Velocity (value hierarchy): correct same-DB edges outrank the
    # ~5ms NFR01 async budget.
    #
    # The entry is READ BACK rather than kept from the store call: the row that
    # landed is the post-redaction, post-intake one, and reading it is also the
    # cheapest confirmation that it landed at all.
    # ``isinstance`` rather than ``is not None``: a backend that answers with a
    # placeholder (a test double, a partially-initialised adapter) must not be
    # handed to the graph enricher as if it were the stored row.
    stored_entry = backend.get(learning_id, namespace=args.namespace)
    if isinstance(stored_entry, MemoryEntry):
        try:
            sec_cfg = MemoryConfig(storage_path=str(store_dir))
            update_entry_graph(stored_entry, backend, embedding=embedding_vec, config=sec_cfg)
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
    # PRD-CORE-248 FR04 clause 2: the write-commit evaluation point. Costs one
    # stat when nothing is due; the store path had no checkpoint trigger at all
    # before this, so a server that never ran session-start never checkpointed.
    from trw_mcp.state._wal_idle_sweep import checkpoint_after_commit

    checkpoint_after_commit(trw_dir)
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
