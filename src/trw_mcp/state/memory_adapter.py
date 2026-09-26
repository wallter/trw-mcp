# ruff: noqa: E402
"""Adapter layer between trw-mcp learning tools and the checkout's memory store.

Every read and write goes through ``_store_selection.selected_store`` (the
memory daemon, PRD-CORE-280); the CRUD operations here preserve the return
shapes of the original YAML-based learning tools.

Implementation is split across focused sub-modules:
- ``_memory_transforms``: result transformation between internal/external formats

This module is the public facade -- all external imports should come here.
"""

from __future__ import annotations

from pathlib import Path

import structlog

from trw_mcp.models.config import get_config as get_config
from trw_mcp.state import _memory_lookups, _memory_transforms
from trw_mcp.state._store_arguments import build_store_arguments

# Re-export: transforms. trw-mcp encodes nothing; the daemon owns the model (PRD-CORE-302 FR05).
_memory_to_learning_dict = _memory_transforms._memory_to_learning_dict

logger = structlog.get_logger(__name__)

# update_learning extracted to _memory_update (PRD-DIST-243 batch 59).
from trw_mcp.state._memory_update import update_learning as update_learning

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
    # The write window clears, or a concurrent write to the row lands first: keep the journal
    # record and retry it without spending its budget.
    "rate_limited": "rate_limited",
    "conflict": "rate_limited",
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
# Lookup, list, count and access-tracking helpers extracted to
# _memory_lookups.py (PRD-DIST-243 batch 43).
count_entries = _memory_lookups.count_entries
find_entry_by_id = _memory_lookups.find_entry_by_id
find_yaml_path_for_entry = _memory_lookups.find_yaml_path_for_entry
list_active_learnings = _memory_lookups.list_active_learnings
list_entries_by_status = _memory_lookups.list_entries_by_status
record_surfaced = _memory_lookups.record_surfaced

# PRD-CORE-185 FR06: user-tier federated recall + the wildcard-utility ranker,
# extracted to _memory_recall.py (project ∪ user federation + the 350 eff-LOC
# gate, NFR07). This re-export is THE recall path — it supersedes the historical
# inline recall_learnings, adding the user-tier federation step.
# F5 root-cause B: forced (non-deferrable) knowledge-graph backfill over the
# EXISTING corpus, run by the store (``MemoryStore.graph_backfill``).
from trw_mcp.state._graph_backfill import (
    backfill_graph as backfill_graph,
)
from trw_mcp.state._memory_recall import (
    _rank_wildcard_by_utility as _rank_wildcard_by_utility,
)
from trw_mcp.state._memory_recall import (
    recall_learnings as recall_learnings,
)
