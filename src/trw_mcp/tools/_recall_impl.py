# ruff: noqa: E402
"""Core recall logic — extracted from learning.py for module-size compliance.

Dependencies that test suites patch at ``trw_mcp.tools.learning.*`` are
injected as parameters by the closure in ``learning.py`` so that patches
remain effective without needing to know about this module.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import structlog
from trw_memory.labels import Surface
from trw_memory.retrieval.recall_policy import RECALL_PREFETCH_MULTIPLIER

from trw_mcp.models.config import TRWConfig
from trw_mcp.models.typed_dicts import RecallResultDict
from trw_mcp.scoring._recall import RecallContext
from trw_mcp.state._recall_admission import label_scope
from trw_mcp.state._session_mark import session_mark

# PRD-CORE-146 follow-up: build_recall_context was relocated to
# ``trw_mcp.state.recall_context`` so state/ callers no longer need an
# importlib workaround to dodge the state→tools layer lint. This module
# re-exports the symbol for back-compat; existing test patches against
# ``trw_mcp.tools._recall_impl.build_recall_context`` continue to work
# because patching rebinds the attribute on this module.
from trw_mcp.state.recall_context import (
    _detect_surface_phase as _detect_surface_phase,
)
from trw_mcp.state.recall_context import (
    build_recall_context as build_recall_context,
)
from trw_mcp.state.surface_tracking import log_surface_event
from trw_mcp.tools._recall_gate import learnings_injection_allowed
from trw_mcp.tools._recall_retrieval_note import retrieval_note

logger = structlog.get_logger(__name__)

# F-001: prefetch a bounded multiple of max_results from the DB so the backend
# caps BEFORE the full active corpus is deserialized. The post-fetch re-rank +
# dedup still truncate to the real max_results, so a generous multiple keeps
# ranking quality while bounding deserialization cost. The daemon's memory_recall
# ranks to the same depth, so the value has one owner (PRD-CORE-298 FR05).
PREFETCH_MULTIPLIER = RECALL_PREFETCH_MULTIPLIER

if TYPE_CHECKING:
    from trw_mcp.state._paths import TRWCallContext


def execute_recall(
    query: str,
    trw_dir: Path,
    config: TRWConfig,
    *,
    tags: list[str] | None = None,
    min_impact: float = 0.0,
    status: str | None = None,
    max_results: int | None = None,
    deprioritized_ids: set[str] | None = None,
    topic: str | None = None,
    call_ctx: TRWCallContext | None = None,
    # PRD-CORE-185 FR07: tier-scoping (None -> include user when present).
    include_tiers: list[str] | None = None,
    # PRD-CORE-194 FR03: bi-temporal validity time-travel surface.
    as_of: str | None = None,
    include_superseded: bool = False,
    # PRD-CORE-334 FR02: one MemoryType value; the store filters before its limit.
    record_type: str | None = None,
    # Decision context (trw_code search): False writes nothing, as under the reviewer role.
    track: bool = True,
    # Injected deps (patched at trw_mcp.tools.learning.* in tests)
    _adapter_recall: Any = None,
    _rank_by_utility: Any = None,
) -> RecallResultDict:
    """Search, rank and present learnings as stubs within the FR01 byte budget.

    Args:
        query: Search query (keywords matched against summaries/details).
        trw_dir: Resolved .trw directory path.
        config: TRW configuration.
        tags: Optional tag filter.
        min_impact: Minimum impact score filter (0.0-1.0).
        status: Optional status filter.
        max_results: Maximum learnings to rank (default from config, 0 = unlimited).
        topic: Optional topic slug from knowledge topology.
        track: False skips every write below (access counts, exposure rows,
            receipts, the ceremony counter), exactly as the reviewer role does.
        _adapter_recall: Injected recall function.
        _rank_by_utility: Injected ranking function.
    """
    from trw_mcp.state.memory_adapter import recall_learnings as _default_recall
    from trw_mcp.tools._recall_assertion_verification import keep_retrieval_order as _default_rank

    recall_fn = _adapter_recall or _default_recall
    # PRD-CORE-125-FR03: off, nothing is retrieved and the envelope is otherwise normal.
    if not learnings_injection_allowed(config, "tool"):
        recall_fn = lambda *_a, **_k: []  # noqa: E731
    rank_fn: Callable[..., list[dict[str, object]]] = _rank_by_utility or _default_rank

    # PRD-SEC-015 round-2 audit (Row 3); PRD-CORE-305-FR05 sol round-3: recall
    # is allowlisted (as an MCP reviewer tool AND as the ``local recall`` CLI
    # verb) under EITHER bounded-lane marker, but otherwise mutates
    # access_count/recall_count in the shared learnings store, appends
    # surface/recall-tracking records, and increments the ceremony tool-call
    # counter. Checking only ``reviewer_role_active()`` left a dispatched
    # child (``TRW_DISPATCH_CHILD=1`` without ``TRW_SURFACE_ROLE=reviewer``)
    # free to trigger every one of those writes through ``local recall`` --
    # found by a census test that actually ran the verb under that marker.
    # An untracked recall (``track=False``, the trw_code decision context,
    # PRD-CORE-319) takes the same read-only branch. Resolved ONCE and
    # threaded through every write site below rather than re-checked per site.
    from trw_mcp.dispatch._child_marker import dispatched_child_active
    from trw_mcp.state._surface_role import reviewer_role_active

    _read_only = not track or reviewer_role_active() or dispatched_child_active()

    # FIX-071: Default to active status to exclude obsolete/corrupted entries
    if status is None:
        status = "active"

    # Input validation (PRD-QUAL-042-FR06): impact bounds
    min_impact = max(0.0, min(1.0, min_impact))
    if max_results is None:
        max_results = config.recall_max_results
    is_wildcard = query.strip() in ("*", "")
    query_tokens = [] if is_wildcard else query.lower().split()
    # SHARED-RECALL-LOCAL: learnings from the operator's other hosts arrive by team pull;
    # catch a stale one up first, bounded, and answer from local rows when it runs long.
    fresh_pull = None
    if not is_wildcard and not _read_only:
        from trw_mcp.sync import _fresh_pull

        fresh_pull = _fresh_pull.ensure_fresh(trw_dir, config)

    # Build recall context for contextual boosting (PRD-CORE-102)
    recall_context: RecallContext | None = None
    try:
        recall_context = build_recall_context(trw_dir, query, call_ctx=call_ctx)
    except Exception:  # justified: fail-open, recall context enrichment must not block recall
        logger.debug("recall_context_build_failed", exc_info=True)

    # F-001: cap the DB fetch at a bounded multiple of max_results so the backend
    # truncates BEFORE deserializing the whole active corpus. max_results == 0
    # means unlimited, so keep the fetch unbounded in that case.
    fetch_limit = max_results * PREFETCH_MULTIPLIER if max_results > 0 else 0
    # PRD-CORE-185 FR07: forward include_tiers only when the caller scoped it, so
    # injected recall doubles without the kwarg stay back-compatible.
    recall_kwargs: dict[str, Any] = {
        "query": query,
        "tags": tags,
        "min_impact": min_impact,
        "status": status,
        "max_results": fetch_limit,
        "compact": False,
    }
    if include_tiers is not None:
        recall_kwargs["include_tiers"] = include_tiers
    # PRD-CORE-194 FR03: forward the validity-prior kwargs only when the caller set
    # them, so injected recall doubles without the params stay back-compatible.
    if as_of is not None:
        recall_kwargs["as_of"] = as_of
    if include_superseded:
        recall_kwargs["include_superseded"] = include_superseded
    if record_type is not None:
        recall_kwargs["record_type"] = record_type
    from trw_mcp.state._memory_recall import pop_store_error
    from trw_mcp.state._recall_signals import recall_signal_scope

    pop_store_error()  # report only THIS call's store failure, never one left by an earlier recall
    # PRD-SEC-023 FR03: trw_recall is the one surface where the agent ASKED, so it admits up to agent_max (personal by default); everything
    # else keeps the auto default. The scope counts what the labels withheld, as a number only.
    with recall_signal_scope(query), label_scope(Surface.AGENT) as withheld:
        matching_learnings = recall_fn(trw_dir, **recall_kwargs)
        store_error = pop_store_error()

        # Topic-scoped pre-filter (PRD-CORE-021-FR07)
        topic_filter_warning = ""
        if topic is not None:
            topic_filter_warning = _apply_topic_filter(trw_dir, config, topic, matching_learnings)

        # Qualify stored evidence before the one authoritative final ranking.
        ranked_learnings = _verify_assertions(
            matching_learnings, query_tokens, config, rank_fn, context=recall_context, rank_always=True
        )
        candidate_count = len(matching_learnings)

    # Order for the response BEFORE dedup and cap (PRD-CORE-282 FR01):
    # temporal eligibility, then already-in-context, then the ranked score with
    # rows synced from other projects penalized.
    from trw_mcp.tools._recall_order import order_ranked_for_response

    ranked_learnings = order_ranked_for_response(
        ranked_learnings, deprioritized_ids, all_projects=bool(getattr(config, "team_sync_all_projects", False))
    )

    # F-DEDUP-001: collapse near-duplicate entries on the ranked candidate set
    # BEFORE the cap, so N near-identical copies of one finding can't crowd out
    # distinct findings in the top-K.
    ranked_learnings, duplicates_collapsed = _dedup_ranked_learnings(trw_dir, ranked_learnings)
    # The count BEFORE the cap: a capped call used to report the capped size, so ``max_results=1``
    # said one match however many there were (E2E-INC-010). Bounded by the prefetch, not the corpus.
    total_matches = len(ranked_learnings)
    if max_results > 0:
        ranked_learnings = ranked_learnings[:max_results]

    recall_result: RecallResultDict = {"query": query, "total_matches": total_matches}
    # Advisories ride only when they carry signal, and are attached BEFORE the
    # presenter so the byte budget covers them.
    if topic_filter_warning:
        recall_result["topic_filter_warning"] = topic_filter_warning
    if fresh_pull is not None and fresh_pull.get("status") in ("timeout", "in_flight", "pull_failed", "failed"):
        recall_result["fresh_pull"] = fresh_pull  # only when recall may be missing another host's rows
    retrieval = retrieval_note(trw_dir, query)
    if retrieval is not None:
        recall_result["retrieval_note"] = retrieval
    if store_error:
        # An unopenable store is not an empty one: say so, or zero results read as "nothing learned".
        recall_result["store_unavailable"] = store_error
    if withheld.count:
        recall_result["withheld_by_label"] = withheld.count
    if session_label := session_mark().reported():
        recall_result["session_label"] = session_label  # PRD-SEC-023 FR04: absent while the session is still at team
    if not _read_only:
        # Row 3: attaching the status line increments the ceremony tool-call counter.
        from trw_mcp.tools._ceremony_status_context import append_ceremony_status_for_tool

        append_ceremony_status_for_tool(cast("dict[str, object]", recall_result), trw_dir, tool_name="recall")

    from trw_mcp.tools._recall_presenter import present

    stubs = present(
        cast("dict[str, object]", recall_result),
        ranked_learnings,
        query_tokens=query_tokens,
        provenance=config.recall_provenance_inline,
        home_namespace=config.project_namespace,
    )
    # Exposure means shown to the caller: only rows that made the budget.
    shown = ranked_learnings[: len(stubs)]
    # A wildcard listing is a bulk browse, not an intentional surfacing (PRD-CORE-103-FR01).
    if not _read_only and not is_wildcard:
        _log_recall_surface_events(trw_dir, shown, recall_context)
    surfaced_ids = [str(entry["id"]) for entry in shown if entry.get("id")]
    if surfaced_ids and not _read_only:
        from trw_mcp.state import memory_adapter

        memory_adapter.record_surfaced(trw_dir, surfaced_ids)
        _track_recall(surfaced_ids, query)

    # PRD-CORE-236: counters a caller cannot act on are logged, not returned.
    logger.info(
        "trw_recall_searched",
        query=query[:80],
        candidate_count=candidate_count,
        ranked=len(ranked_learnings),
        shown=len(stubs),
        duplicates_collapsed=duplicates_collapsed,
    )
    return recall_result


def recall_by_ids(
    trw_dir: Path, config: TRWConfig, ids: list[str], *, status: str | None = "active"
) -> RecallResultDict:
    """Full rows for exactly *ids*, admitted by the same predicate search uses (FR01).

    A row search would never return (another namespace, not active, superseded,
    expired, a canary, blocked by the recall filter) is not returned here
    either; it is listed under ``missing_ids`` exactly like an id no store holds.
    Rows carry the same qualified stored evidence ranked recall gives them, and
    internal scoring fields are stripped as on every recall response.
    """
    from trw_mcp.state._memory_transforms import _memory_to_learning_dict
    from trw_mcp.state._recall_admission import fetch_admitted
    from trw_mcp.tools._recall_assertion_verification import qualify_stored_evidence
    from trw_mcp.tools._recall_projection import strip_internal_response_fields

    with label_scope(Surface.AGENT) as withheld:
        admitted = {
            entry.id: entry for entry in fetch_admitted(trw_dir, ids, status=status or "active")
        }  # search's FIX-071 default
    rows: list[dict[str, object]] = []
    missing: list[str] = []
    for learning_id in dict.fromkeys(ids):
        entry = admitted.get(learning_id)
        if entry is None:
            missing.append(learning_id)
        else:
            # The same last-known evidence a ranked row carries, never present-tree proof.
            rows.append(qualify_stored_evidence(cast("dict[str, object]", _memory_to_learning_dict(entry)), config)[0])
    result: RecallResultDict = {
        "query": "",
        "learnings": strip_internal_response_fields(rows, config.recall_internal_fields),
        "total_matches": len(rows),
    }
    if withheld.count:
        result["withheld_by_label"] = withheld.count  # PRD-SEC-023 FR03: a count only; the ids stay under missing_ids
    if session_label := session_mark().reported():
        result["session_label"] = session_label
    if missing:
        # INC-119 b: "missing" also covers a row the requested status filtered out. FR01 keeps a refused row
        # indistinguishable from an absent one, so say what the list means instead of confirming anything.
        result["missing_ids"] = missing
        result["ids_note"] = (
            f"missing_ids: no row with status={status or 'active'} was found for these ids (they may not exist, or may be "
            "closed, expired, superseded or in another namespace). To look for a resolved or obsolete learning pass "
            "status='resolved' or status='obsolete'."
        )
    return result


def _dedup_ranked_learnings(
    trw_dir: Path,
    ranked_learnings: list[dict[str, object]],
) -> tuple[list[dict[str, object]], int]:
    """Collapse near-duplicate recall entries (F-DEDUP-001).

    Exact-content collapse always runs; a cosine pass runs additionally over the
    stored vectors in the daemon's active space, at the threshold the daemon
    calibrated for that space -- both from one ``memory_vectors`` answer
    (PRD-CORE-302 C2). Both fail open: a backend error never blocks recall.
    """
    from trw_mcp.tools._recall_dedup import dedup_ranked_learnings

    vectors_fn = None
    try:
        from trw_mcp.state._store_selection import selected_store

        store, _ = selected_store(trw_dir)
        vectors_fn = store.vectors
    except Exception:  # justified: fail-open, embedding access must not block recall
        logger.debug("recall_dedup_backend_unavailable", exc_info=True)
    return dedup_ranked_learnings(ranked_learnings, vectors_fn=vectors_fn)


def _log_recall_surface_events(
    trw_dir: Path,
    ranked_learnings: list[dict[str, object]],
    recall_context: RecallContext | None,
) -> None:
    """Emit surface telemetry for surfaced recall results."""
    try:
        from trw_mcp.state._session_id import resolve_effective_session_id

        phase = _detect_surface_phase()
        sid = resolve_effective_session_id(trw_dir)
        for entry in ranked_learnings:
            lid = str(entry.get("id", ""))
            if lid:
                log_surface_event(
                    trw_dir,
                    learning_id=lid,
                    surface_type="recall",
                    phase=phase,
                    files_context=[],  # No file context in base recall; session_start path adds its own
                    session_id=sid,
                )
    except Exception:  # justified: fail-open, surface logging must not block recall
        logger.debug("surface_logging_failed", exc_info=True)


def _apply_topic_filter(
    trw_dir: Path,
    config: TRWConfig,
    topic: str,
    matching_learnings: list[dict[str, object]],
) -> str:
    """Apply topic-scoped pre-filter. Mutates list in place.

    Returns an empty string when the filter applied normally, or a non-empty
    warning message when the filter was silently ignored (clusters file missing,
    slug absent, or parse error).  The caller should surface the warning so
    callers are not silently handed unfiltered results.
    """
    clusters_path = trw_dir / config.knowledge_output_dir / "clusters.json"
    try:
        if clusters_path.exists():
            clusters_data = json.loads(clusters_path.read_text(encoding="utf-8"))
            if topic in clusters_data:
                allowed_ids = set(clusters_data[topic])
                matching_learnings[:] = [e for e in matching_learnings if str(e.get("id", "")) in allowed_ids]
                return ""
            warning = f"topic_filter ignored: slug '{topic}' not found in clusters.json — returning unfiltered results"
            logger.warning(
                "topic_filter_ignored",
                topic=topic,
                reason="slug_absent",
                clusters_path=str(clusters_path),
            )
            return warning
        warning = f"topic_filter ignored: clusters.json missing at '{clusters_path}' — returning unfiltered results"
        logger.warning(
            "topic_filter_ignored",
            topic=topic,
            reason="clusters_missing",
            clusters_path=str(clusters_path),
        )
        return warning
    except (json.JSONDecodeError, OSError) as exc:
        warning = f"topic_filter ignored: could not read clusters.json ('{exc}') — returning unfiltered results"
        logger.warning(
            "topic_filter_ignored",
            topic=topic,
            reason="read_error",
            clusters_path=str(clusters_path),
            exc_info=True,
        )
        return warning


def _track_recall(matched_ids: list[str], query: str) -> None:
    """Track each recalled learning for outcome-based calibration (PRD-CORE-034)."""
    try:
        from trw_mcp.state.recall_tracking import record_recall as _record_recall

        for lid in matched_ids:
            _record_recall(lid, query)
    except (ImportError, OSError, RuntimeError, ValueError, TypeError):
        logger.debug("recall_tracking_failed", exc_info=True)


# Historical facade imports remain compatible; recall now interprets stored
# evidence only. Explicit maintenance owns the actual verifier.
from trw_mcp.tools._recall_assertion_verification import (
    _verify_assertions as _verify_assertions,
)
