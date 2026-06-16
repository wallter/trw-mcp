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

from trw_mcp.models.config import TRWConfig
from trw_mcp.models.typed_dicts import RecallContextDict, RecallResultDict
from trw_mcp.scoring._recall import RecallContext
from trw_mcp.state.persistence import FileStateReader
from trw_mcp.state.propensity_log import log_ranked_selections

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

logger = structlog.get_logger(__name__)

# F-001: prefetch a bounded multiple of max_results from the DB so the backend
# caps BEFORE the full active corpus is deserialized. The post-fetch re-rank +
# dedup still truncate to the real max_results, so a generous multiple keeps
# ranking quality while bounding deserialization cost.
PREFETCH_MULTIPLIER = 5

# F-002: sane default output ceiling (tokens) applied when the caller does not
# pass token_budget, so a single recall can never overflow the context window.
DEFAULT_RECALL_TOKEN_BUDGET = 8000

# F-003: auto-enable compact mode when the result would exceed this many entries,
# so broad recalls don't load full detail fields for every entry.
COMPACT_AUTO_THRESHOLD = 10

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
    shard_id: str | None = None,
    max_results: int | None = None,
    token_budget: int | None = None,
    deprioritized_ids: set[str] | None = None,
    compact: bool | None = None,
    ultra_compact: bool = False,
    topic: str | None = None,
    call_ctx: TRWCallContext | None = None,
    # PRD-CORE-185 FR07: tier-scoping (None -> include user when present).
    include_tiers: list[str] | None = None,
    # PRD-CORE-194 FR03: bi-temporal validity time-travel surface.
    as_of: str | None = None,
    include_superseded: bool = False,
    # Injected deps (patched at trw_mcp.tools.learning.* in tests)
    _adapter_recall: Any = None,
    _adapter_update_access: Any = None,
    _search_patterns: Any = None,
    _rank_by_utility: Any = None,
    _collect_context: Any = None,
) -> RecallResultDict:
    """Execute the core recall workflow: search, rank, verify, format.

    Args:
        query: Search query (keywords matched against summaries/details).
        trw_dir: Resolved .trw directory path.
        config: TRW configuration.
        tags: Optional tag filter.
        min_impact: Minimum impact score filter (0.0-1.0).
        status: Optional status filter.
        shard_id: Optional shard identifier.
        max_results: Maximum learnings to return (default from config, 0 = unlimited).
        compact: When True, return only essential fields per learning.
        ultra_compact: When True, return only learning IDs, compact summaries,
            a result count, and a ceremony hint.
        topic: Optional topic slug from knowledge topology.
        _adapter_recall: Injected recall function.
        _adapter_update_access: Injected access tracking function.
        _search_patterns: Injected pattern search function.
        _rank_by_utility: Injected ranking function.
        _collect_context: Injected context collector.
    """
    # Resolve injected deps with fallbacks
    from trw_mcp.scoring import rank_by_utility as _default_rank
    from trw_mcp.state.memory_adapter import recall_learnings as _default_recall
    from trw_mcp.state.memory_adapter import update_access_tracking as _default_access
    from trw_mcp.state.recall_search import collect_context as _default_collect
    from trw_mcp.state.recall_search import search_patterns as _default_search

    recall_fn = _adapter_recall or _default_recall
    access_fn = _adapter_update_access or _default_access
    search_fn = _search_patterns or _default_search
    rank_fn: Callable[..., list[dict[str, object]]] = _rank_by_utility or _default_rank
    collect_fn = _collect_context or _default_collect

    # FIX-071: Default to active status to exclude obsolete/corrupted entries
    if status is None:
        status = "active"

    # Input validation (PRD-QUAL-042-FR06): impact bounds
    min_impact = max(0.0, min(1.0, min_impact))
    if token_budget is not None and token_budget <= 0:
        raise ValueError(f"token_budget must be positive, got {token_budget}")

    reader = FileStateReader()
    # Track whether the caller explicitly asked for a large result set before we
    # resolve the default cap, so the common default recall keeps full detail.
    caller_max_results = max_results
    if max_results is None:
        max_results = config.recall_max_results
    is_wildcard = query.strip() in ("*", "")
    query_tokens = [] if is_wildcard else query.lower().split()
    # F-003: auto-enable compact for wildcard queries AND when the caller
    # EXPLICITLY requests a broad result set (cap > COMPACT_AUTO_THRESHOLD, or
    # 0 == unlimited). The implicit default cap is left in full mode so a typical
    # single-result recall still returns detail.
    _explicit_broad = caller_max_results is not None and (
        caller_max_results == 0 or caller_max_results > COMPACT_AUTO_THRESHOLD
    )
    _auto_compact = is_wildcard or _explicit_broad
    use_compact = ultra_compact or (compact if compact is not None else _auto_compact)

    # Build recall context for contextual boosting (PRD-CORE-102)
    recall_context: RecallContext | None = None
    try:
        recall_context = build_recall_context(trw_dir, query, call_ctx=call_ctx)
    except Exception:  # justified: fail-open, recall context enrichment must not block recall
        logger.debug("recall_context_build_failed", exc_info=True)

    # Search entries via SQLite adapter.
    # F-001: cap the DB fetch at a bounded multiple of max_results so the backend
    # truncates BEFORE deserializing the whole active corpus. max_results == 0
    # means unlimited, so keep the fetch unbounded in that case.
    fetch_limit = max_results * PREFETCH_MULTIPLIER if max_results > 0 else 0
    # F-003: pass compact through so the backend skips loading the (up to
    # 2000-char) detail field at deserialization rather than stripping it later.
    # PRD-CORE-185 FR07: forward include_tiers only when the caller scoped it, so
    # injected recall doubles without the kwarg stay back-compatible.
    recall_kwargs: dict[str, Any] = {
        "query": query,
        "tags": tags,
        "min_impact": min_impact,
        "status": status,
        "max_results": fetch_limit,
        "compact": use_compact,
    }
    if include_tiers is not None:
        recall_kwargs["include_tiers"] = include_tiers
    # PRD-CORE-194 FR03: forward the validity-prior kwargs only when the caller set
    # them, so injected recall doubles without the params stay back-compatible and
    # the no-as_of / superseded-excluded default is byte-identical to pre-194.
    if as_of is not None:
        recall_kwargs["as_of"] = as_of
    if include_superseded:
        recall_kwargs["include_superseded"] = include_superseded
    matching_learnings = recall_fn(trw_dir, **recall_kwargs)

    # Topic-scoped pre-filter (PRD-CORE-021-FR07)
    topic_filter_warning = ""
    if topic is not None:
        topic_filter_warning = _apply_topic_filter(trw_dir, config, topic, matching_learnings)
    topic_filter_ignored = bool(topic_filter_warning)

    # Update access tracking for recalled IDs
    matched_ids = [str(e.get("id", "")) for e in matching_learnings if e.get("id")]
    access_fn(trw_dir, matched_ids)

    # Track each recalled learning for outcome-based calibration (PRD-CORE-034)
    _track_recall(matched_ids, query)

    # Augment local results with remote shared learnings (PRD-CORE-033)
    if not is_wildcard:
        matching_learnings = _augment_with_remote(query, matching_learnings)

    # Search patterns and rank all results by utility
    matching_patterns = search_fn(
        trw_dir / config.patterns_dir,
        query_tokens,
        reader,
    )
    ranked_learnings: list[dict[str, object]] = rank_fn(
        matching_learnings,
        query_tokens,
        config.recall_utility_lambda,
        context=recall_context,
    )

    # Capture pre-cap counts for the total_available response field
    total_available = len(ranked_learnings) + len(matching_patterns)

    # Move already-in-context learnings behind fresh results before truncation.
    if deprioritized_ids:
        prioritized = [entry for entry in ranked_learnings if str(entry.get("id", "")) not in deprioritized_ids]
        deferred = [entry for entry in ranked_learnings if str(entry.get("id", "")) in deprioritized_ids]
        ranked_learnings = prioritized + deferred

    # F-DEDUP-001: collapse near-duplicate entries on the ranked candidate set
    # BEFORE token budgeting and the max_results cap, so N near-identical copies
    # of one finding can't crowd out distinct findings in the top-K.
    ranked_learnings, duplicates_collapsed = _dedup_ranked_learnings(trw_dir, ranked_learnings)

    from trw_memory.retrieval.token_budget import apply_token_budget, estimate_entry_tokens

    # F-002: apply a sane default token ceiling when the caller gives no budget,
    # so a recall result can never overflow the context window.
    effective_budget = token_budget if token_budget is not None else DEFAULT_RECALL_TOKEN_BUDGET
    ranked_learnings, tokens_used, tokens_truncated = _apply_recall_token_budget(
        ranked_learnings,
        effective_budget,
        apply_token_budget=apply_token_budget,
        estimate_entry_tokens=estimate_entry_tokens,
    )

    # Apply result cap — must happen BEFORE tokens_used is finalised so the
    # reported token count matches the entries actually returned to the caller.
    if max_results > 0 and len(ranked_learnings) > max_results:
        ranked_learnings = ranked_learnings[:max_results]
        # Recompute tokens_used for the capped set; truncation flag stays True
        # if the budget already trimmed the list (that state is unchanged).
        tokens_used = sum(estimate_entry_tokens(entry) for entry in ranked_learnings)

    # --- Surface event logging (PRD-CORE-103-FR01) ---
    # Log each surfaced learning for telemetry/fatigue detection.
    # Skip compact/wildcard queries (bulk operations, not intentional surfacings).
    if not use_compact:
        _log_recall_surface_events(trw_dir, ranked_learnings, recall_context)

    # --- Assertion verification (PRD-CORE-086 FR06) ---
    if not use_compact:
        ranked_learnings = _verify_assertions(ranked_learnings, query_tokens, config, rank_fn, context=recall_context)

    # Strip to compact fields when requested
    if use_compact:
        allowed = config.recall_compact_fields
        ranked_learnings = [{k: v for k, v in entry.items() if k in allowed} for entry in ranked_learnings]

    # Skip context collection for compact wildcard queries (saves I/O)
    context_data: RecallContextDict = {}
    if not (is_wildcard and use_compact):
        context_data = cast("RecallContextDict", collect_fn(trw_dir, config.context_dir, reader))

    _top_impact = float(str(ranked_learnings[0].get("impact", 0.0))) if ranked_learnings else 0.0
    logger.info("recall_ok", query=query[:50], result_count=len(ranked_learnings), top_impact=_top_impact)
    logger.debug("recall_detail", query=query[:80], min_impact=min_impact, tags=tags)
    logger.info(
        "trw_recall_searched",
        query=query,
        learnings_found=len(ranked_learnings),
        patterns_found=len(matching_patterns),
        compact=use_compact,
    )

    if ultra_compact:
        return _build_ultra_compact_recall_result(ranked_learnings)

    recall_result: RecallResultDict = {
        "query": query,
        "learnings": ranked_learnings,
        "patterns": matching_patterns,
        "context": context_data,
        "total_matches": len(ranked_learnings) + len(matching_patterns),
        "total_available": total_available,
        "compact": use_compact,
        "max_results": max_results,
        "topic_filter_ignored": topic_filter_ignored,
        "topic_filter_warning": topic_filter_warning,
        "tokens_used": tokens_used,
        "tokens_budget": effective_budget,
        "tokens_truncated": tokens_truncated,
        "duplicates_collapsed": duplicates_collapsed,
    }

    return recall_result


def _dedup_ranked_learnings(
    trw_dir: Path,
    ranked_learnings: list[dict[str, object]],
) -> tuple[list[dict[str, object]], int]:
    """Collapse near-duplicate recall entries (F-DEDUP-001).

    Exact-content collapse always runs; a cosine pass runs additionally when the
    backend exposes stored embeddings. Both fail open — a backend error never
    blocks recall, the entries are returned unchanged.
    """
    from trw_mcp.tools._recall_dedup import dedup_ranked_learnings

    embeddings_fn: Callable[[list[str]], dict[str, list[float]]] | None = None
    try:
        from trw_mcp.state.memory_adapter import get_backend

        backend = get_backend(trw_dir)
        get_stored = getattr(backend, "get_stored_embeddings", None)
        if callable(get_stored):
            embeddings_fn = get_stored
    except Exception:  # justified: fail-open, embedding access must not block recall
        logger.debug("recall_dedup_backend_unavailable", exc_info=True)

    return dedup_ranked_learnings(ranked_learnings, embeddings_fn=embeddings_fn)


def _apply_recall_token_budget(
    ranked_learnings: list[dict[str, object]],
    token_budget: int | None,
    *,
    apply_token_budget: Callable[[list[dict[str, object]], int], tuple[list[dict[str, object]], int, bool]],
    estimate_entry_tokens: Callable[[dict[str, object]], int],
) -> tuple[list[dict[str, object]], int, bool]:
    """Apply token-budget trimming when requested."""
    if token_budget is not None and ranked_learnings:
        return apply_token_budget(ranked_learnings, token_budget)
    return ranked_learnings, sum(estimate_entry_tokens(entry) for entry in ranked_learnings), False


def _log_recall_surface_events(
    trw_dir: Path,
    ranked_learnings: list[dict[str, object]],
    recall_context: RecallContext | None,
) -> None:
    """Emit propensity and surface telemetry for surfaced recall results."""
    try:
        log_ranked_selections(
            trw_dir,
            ranked_learnings,
            context_phase=(recall_context.current_phase or "") if recall_context else "",
            context_domain=sorted(recall_context.inferred_domains) if recall_context else [],
            context_agent_type=recall_context.client_profile if recall_context else "",
            context_task_type="recall",
            context_files_modified=len(recall_context.modified_files) if recall_context else 0,
        )
    except (OSError, RuntimeError, ValueError, TypeError):
        logger.debug("propensity_logging_failed", exc_info=True)

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


def _build_ultra_compact_recall_result(ranked_learnings: list[dict[str, object]]) -> RecallResultDict:
    """Build the ultra-compact recall response payload."""
    return {
        "learnings": [
            {
                "id": str(entry.get("id", "")),
                "summary": _truncate_ultra_compact_summary(str(entry.get("summary", ""))),
            }
            for entry in ranked_learnings
        ],
        "count": len(ranked_learnings),
        "ceremony_hint": "Call trw_session_start() first to load prior learnings and active run state.",
    }


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
) -> list[dict[str, object]]:
    """Augment local results with remote shared learnings (PRD-CORE-033)."""
    try:
        from trw_mcp.telemetry.remote_recall import fetch_shared_learnings

        remote = fetch_shared_learnings(query)
        if remote:
            return list(matching_learnings) + [dict(r) for r in remote]
    except Exception:  # justified: boundary, remote recall hits network/auth
        logger.warning(
            "remote_recall_failed_unexpected",
            component="recall",
            op="augment_with_remote",
            outcome="fail_open",
            query_excerpt=query[:80],
            exc_info=True,
        )
    return list(matching_learnings)


def _truncate_ultra_compact_summary(summary: str, token_limit: int = 32) -> str:
    """Trim summaries to a small token budget while preserving a readable suffix."""
    from trw_memory.retrieval.token_budget import estimate_tokens

    normalized = " ".join(summary.split())
    if estimate_tokens(normalized) <= token_limit:
        return normalized

    words = normalized.split()
    while words:
        candidate = " ".join(words) + "…"
        if estimate_tokens(candidate) <= token_limit:
            return candidate
        words.pop()

    return "…"


# Assertion verification helpers extracted to _recall_assertion_verification
# (PRD-DIST-243 batch 8). Re-exported here so existing test imports
# (``from trw_mcp.tools._recall_impl import _verify_assertions``) continue to
# work. Patches against ``trw_mcp.state._paths.*`` /
# ``trw_memory.lifecycle.verification.*`` still apply because internal imports
# happen lazily inside _verify_assertions.
from trw_mcp.tools._recall_assertion_verification import (
    _assertion_result_detail as _assertion_result_detail,
)
from trw_mcp.tools._recall_assertion_verification import (
    _verify_assertions as _verify_assertions,
)
