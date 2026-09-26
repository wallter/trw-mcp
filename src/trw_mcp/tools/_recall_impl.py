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
from trw_memory.retrieval.recall_policy import RECALL_PREFETCH_MULTIPLIER

from trw_mcp.models.config import TRWConfig
from trw_mcp.models.typed_dicts import RecallResultDict
from trw_mcp.scoring._recall import RecallContext
from trw_mcp.state._platform_trust import platform_contact_enabled

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
        _adapter_recall: Injected recall function.
        _rank_by_utility: Injected ranking function.
    """
    from trw_mcp.state.memory_adapter import recall_learnings as _default_recall
    from trw_mcp.tools._recall_assertion_verification import keep_retrieval_order as _default_rank

    recall_fn = _adapter_recall or _default_recall
    # PRD-CORE-125-FR03: off, nothing is retrieved and the envelope is otherwise normal.
    recall_on = learnings_injection_allowed(config, "tool")
    if not recall_on:
        recall_fn = lambda *_a, **_k: []  # noqa: E731
    rank_fn: Callable[..., list[dict[str, object]]] = _rank_by_utility or _default_rank

    # PRD-SEC-015 round-2 audit (Row 3): trw_recall is an allowlisted reviewer
    # tool but otherwise mutates access_count/recall_count in the shared
    # learnings store, appends surface/recall-tracking records, and
    # increments the ceremony tool-call counter. Resolved ONCE and threaded
    # through every write site below rather than re-checked per site.
    from trw_mcp.state._surface_role import reviewer_role_active

    _reviewer = reviewer_role_active()

    # FIX-071: Default to active status to exclude obsolete/corrupted entries
    if status is None:
        status = "active"

    # Input validation (PRD-QUAL-042-FR06): impact bounds
    min_impact = max(0.0, min(1.0, min_impact))
    if max_results is None:
        max_results = config.recall_max_results
    is_wildcard = query.strip() in ("*", "")
    query_tokens = [] if is_wildcard else query.lower().split()

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
    from trw_mcp.state._memory_recall import pop_store_error
    from trw_mcp.state._recall_signals import recall_signal_scope

    pop_store_error()  # report only THIS call's store failure, never one left by an earlier recall
    with recall_signal_scope(query):
        matching_learnings = recall_fn(trw_dir, **recall_kwargs)
        store_error = pop_store_error()

        # Topic-scoped pre-filter (PRD-CORE-021-FR07)
        topic_filter_warning = ""
        if topic is not None:
            topic_filter_warning = _apply_topic_filter(trw_dir, config, topic, matching_learnings)

        # Augment local results with remote shared learnings (PRD-CORE-033)
        remote_recall_status: dict[str, object] | None = None
        if recall_on and not is_wildcard:
            matching_learnings, remote_recall_status = _augment_with_remote(query, matching_learnings)

        # Qualify stored evidence before the one authoritative final ranking.
        ranked_learnings = _verify_assertions(
            matching_learnings, query_tokens, config, rank_fn, context=recall_context, rank_always=True
        )
        candidate_count = len(matching_learnings)

    # Order for the response BEFORE dedup and cap (PRD-CORE-282 FR01):
    # temporal eligibility, then already-in-context, then the ranked score with
    # rows synced from other projects penalized.
    from trw_mcp.tools._recall_order import order_ranked_for_response

    ranked_learnings = order_ranked_for_response(ranked_learnings, deprioritized_ids)

    # F-DEDUP-001: collapse near-duplicate entries on the ranked candidate set
    # BEFORE the cap, so N near-identical copies of one finding can't crowd out
    # distinct findings in the top-K.
    ranked_learnings, duplicates_collapsed = _dedup_ranked_learnings(trw_dir, ranked_learnings)
    if max_results > 0:
        ranked_learnings = ranked_learnings[:max_results]

    recall_result: RecallResultDict = {"query": query, "total_matches": len(ranked_learnings)}
    # Advisories ride only when they carry signal, and are attached BEFORE the
    # presenter so the byte budget covers them.
    if topic_filter_warning:
        recall_result["topic_filter_warning"] = topic_filter_warning
    if remote_recall_status is not None:
        recall_result["remote_recall"] = remote_recall_status
    if store_error:
        # An unopenable store is not an empty one: say so, or zero results read as "nothing learned".
        recall_result["store_unavailable"] = store_error
    if not _reviewer:
        # Row 3: attaching the status line increments the ceremony tool-call counter.
        from trw_mcp.tools._ceremony_status_context import append_ceremony_status_for_tool

        append_ceremony_status_for_tool(cast("dict[str, object]", recall_result), trw_dir, tool_name="recall")

    from trw_mcp.tools._recall_presenter import present

    stubs = present(cast("dict[str, object]", recall_result), ranked_learnings, query_tokens=query_tokens)
    # Exposure means shown to the caller: only rows that made the budget.
    shown = ranked_learnings[: len(stubs)]
    # A wildcard listing is a bulk browse, not an intentional surfacing (PRD-CORE-103-FR01).
    if not _reviewer and not is_wildcard:
        _log_recall_surface_events(trw_dir, shown, recall_context)
    surfaced_ids = [str(entry["id"]) for entry in shown if entry.get("id")]
    if surfaced_ids and not _reviewer:
        from trw_mcp.state import memory_adapter

        # A shared (remote) row is not a local row: its id may collide with one
        # this checkout holds, which was not shown and must not be counted.
        local_ids = [str(entry["id"]) for entry in shown if entry.get("id") and entry.get("source") != "shared"]
        memory_adapter.record_surfaced(trw_dir, local_ids)
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
    if missing:
        result["missing_ids"] = missing
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


def _augment_with_remote(
    query: str,
    matching_learnings: list[dict[str, object]],
) -> tuple[list[dict[str, object]], dict[str, object] | None]:
    """Augment local results with remote shared learnings (PRD-CORE-033).

    Returns the (possibly augmented) learnings plus a ``remote_recall`` status
    payload when the remote leg was incomplete, failed, or returned records
    without temporal validation. ``None`` means no advisory is needed. The caller puts that payload on the response so
    a fetch that raised is not indistinguishable from an empty remote corpus
    (wiring-defect pattern P5: the warning log never reached the agent).

    PRD-CORE-245 FR06: this reaches the platform through trw-memory's
    ``fetch_shared_memories``, which is now the ONE client for the platform
    learning-search endpoint and runs every result through the admission gate
    before returning it. The duplicate client this used to call
    (``trw_mcp.telemetry.remote_recall``) is deleted: it had a divergent
    redaction posture and no gate at all, so unvetted peer text reached agent
    context directly.
    """
    if not platform_contact_enabled():  # the operator's egress switch: the query text never leaves the box
        return matching_learnings, None
    try:
        from trw_memory.models.config import MemoryConfig
        from trw_memory.sync import fetch_shared_memories

        from trw_mcp.state._paths import resolve_trw_dir
        from trw_mcp.state._store_selection import selected_store

        cfg = MemoryConfig()
        # The checkout's store runs the admission gate, in-process or in the daemon
        # (PRD-CORE-280 FR01); a fetch it cannot gate raises and is reported below.
        store, _ = selected_store(resolve_trw_dir())
        remote = fetch_shared_memories(query, cfg, admit=store.admit_shared)
        status: dict[str, object] | None = None
        if remote.status not in {"ok", "disabled"}:
            status = {"status": remote.status, "fetched": remote.fetched, "refused": remote.refused}
            # ``remote.results`` being empty has several causes and they are not
            # interchangeable: nothing matched, nothing was asked, the platform
            # did not answer, or the admission gate refused everything it sent.
            logger.warning(
                "remote_recall_incomplete",
                component="recall",
                op="augment_with_remote",
                outcome=remote.status,
                fetched=remote.fetched,
                refused=remote.refused,
            )
        if remote.results:
            from trw_mcp.state.temporal_order import TEMPORAL_ELIGIBILITY_FIELD

            # Admission verifies content safety, not a peer's claimed temporal
            # eligibility. Only the local producer may supply this marker.
            sanitized = [{k: v for k, v in row.items() if k != TEMPORAL_ELIGIBILITY_FIELD} for row in remote.results]
            status = dict(status or {"status": remote.status, "fetched": remote.fetched, "refused": remote.refused})
            status["temporal_coverage"] = "not_evaluated"
            return list(matching_learnings) + sanitized, status
        return list(matching_learnings), status
    except Exception as exc:  # justified: boundary, remote recall hits network/auth
        logger.warning(
            "remote_recall_failed_unexpected",
            component="recall",
            op="augment_with_remote",
            outcome="fail_open",
            query_excerpt=query[:80],
            exc_info=True,
        )
        return list(matching_learnings), {"status": "failed", "reason": type(exc).__name__}


# Historical facade imports remain compatible; recall now interprets stored
# evidence only. Explicit maintenance owns the actual verifier.
from trw_mcp.tools._recall_assertion_verification import (
    _verify_assertions as _verify_assertions,
)
