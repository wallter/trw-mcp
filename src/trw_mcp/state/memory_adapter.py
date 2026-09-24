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
- ``_memory_transforms``: result transformation between internal/external formats

This module is the public facade -- all external imports should come here.
"""

from __future__ import annotations

from pathlib import Path

import structlog

# PRD-FIX-COMPOUNDING-2 FR01: ``schedule_graph_update`` is imported (and
# re-exported) for the operator backfill runbook + parity with the trw-memory
# MemoryClient store path. The in-process store path uses ``update_entry_graph``
# directly on the singleton connection — see ``SqliteMemoryStore._enrich`` for
# the path-divergence rationale. Both names stay here as patch seams.
from trw_memory.graph import schedule_graph_update as schedule_graph_update
from trw_memory.graph import update_entry_graph as update_entry_graph
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
# same function (via ``SqliteMemoryStore.put``). What stays on this side is in
# ``store_learning``'s docstring.
from trw_memory.tools.store import memory_store_impl as memory_store_impl

from trw_mcp.models.config import get_config as get_config
from trw_mcp.state import _memory_connection, _memory_lookups, _memory_recovery, _memory_transforms
from trw_mcp.state._constants import DEFAULT_NAMESPACE
from trw_mcp.state._store_arguments import build_store_arguments

# Re-export: connection mgmt + embedding ops + query routing + transforms.
embed_text = _memory_connection.embed_text
embed_text_batch = _memory_connection.embed_text_batch
check_embeddings_status = _memory_connection.check_embeddings_status
embedding_available = _memory_connection.embedding_available
ensure_migrated = _memory_connection.ensure_migrated
get_backend = _memory_connection.get_backend
get_embedder = _memory_connection.get_embedder
reset_backend = _memory_connection.reset_backend
reset_embedder = _memory_connection.reset_embedder
_memory_to_learning_dict = _memory_transforms._memory_to_learning_dict

logger = structlog.get_logger(__name__)

# Preserve module-level constants for backward compatibility with test patches
_NAMESPACE = DEFAULT_NAMESPACE

# Corruption-recovery helpers extracted to _memory_recovery (PRD-DIST-243 batch 44).
_is_corruption_error = _memory_recovery._is_corruption_error
_log_terminal_recovery = _memory_recovery._log_terminal_recovery
_memory_recovery_in_progress = _memory_recovery._memory_recovery_in_progress
_recover_and_reset_backend = _memory_recovery._recover_and_reset_backend
_schedule_deferred_recovery = _memory_recovery._schedule_deferred_recovery

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
#: (``recorded``/``quarantined``/``rejected``/``error``). The translation is DATA,
#: not an inline conditional, because ``tools/_learn_impl.py`` suppresses the YAML
#: sidecar on exactly ``rejected`` and ``error`` -- the D8 dual-write fix -- and the
#: learn journal dead-letters a ``rejected`` record instead of retrying it. A status
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
    # A schema/PII/poisoning refusal of the content itself: replaying it fails identically.
    "invalid": "rejected",
    "blocked": "rejected",
    # The write window clears: keep the journal record and retry it without spending its budget.
    "rate_limited": "rate_limited",
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
    validation it never performed. PRD-CORE-280 FR01: it runs through the
    checkout's :func:`~trw_mcp.state._store_selection.selected_store`, which owns
    backend choice, canaries, corruption recovery and post-store enrichment
    (see ``SqliteMemoryStore``). Three things stay here because they are TRW
    concerns rather than memory ones:

    * topic-tag inference (knowledge topology) enriches the tags passed in;
    * the user-tier routing decision (PRD-CORE-185 FR05) picks the namespace;
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

    # PRD-CORE-280 FR01: the checkout's store owns backend choice, canaries,
    # corruption recovery and post-store enrichment. PRD-CORE-185 FR05: a
    # portable entry goes to the user namespace, which the store routes to the
    # machine-local user store.
    from trw_mcp.state import _store_selection

    store, project_namespace = _store_selection.selected_store(trw_dir)
    is_user_write = args.tier == "user"
    request: _store_selection.StoreRequest = {
        "tags": enriched_tags,
        "importance": impact,
        "detail": detail,
        "metadata": args.metadata,
        "source": args.source,
        "source_identity": source_identity,
        "session_id": session_id,
        "entry_id": learning_id,
        "evidence": evidence or [],
        "expires": expires,
        "assertions": args.assertions,
        "client_profile": client_profile,
        "model_id": model_id,
        "type": args.type,
        "nudge_line": nudge_line,
        "confidence": args.confidence,
        "task_type": task_type,
        "domain": domain or [],
        "phase_origin": phase_origin,
        "phase_affinity": phase_affinity or [],
        "team_origin": team_origin,
        "protection_tier": args.protection_tier,
        "anchors": args.anchors,
        "anchor_validity": anchor_validity,
    }
    store_result = store.put(summary, args.namespace if is_user_write else project_namespace, request)

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
        if status in ("error", "rate_limited"):
            logger.warning(
                "memory_store_rejected",
                learning_id=learning_id,
                store_status=store_result.get("status"),
                error=str(store_result.get("error", "")),
            )
            result["error"] = str(store_result.get("error", store_result.get("status", "store rejected")))
        elif status == "rejected":
            result["reason"] = str(store_result.get("status"))
            result["message"] = str(store_result.get("error", "the memory store refused the learning"))
        return result

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
list_active_learnings = _memory_lookups.list_active_learnings
list_entries_by_status = _memory_lookups.list_entries_by_status
maybe_checkpoint_wal = _memory_lookups.maybe_checkpoint_wal
record_surfaced = _memory_lookups.record_surfaced

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
