"""Recall path for the memory adapter -- project ∪ user tier federation.

Extracted from ``memory_adapter.py`` (PRD-CORE-185 FR06 + the 350 eff-LOC gate,
NFR07). Holds:

* ``recall_learnings`` -- the public recall entry point (re-exported by the
  ``memory_adapter`` facade), now federating across the project store and the
  machine-local user store.
* ``_federate_user_tier`` -- the second-store merge step: query the user backend
  (all ``user:`` entries), cap it (``recall_user_tier_cap``), de-dupe against the
  project hits (exact id), and append. Tier is a re-rank FEATURE only -- a
  precise project hit keeps its rank; user hits are bounded by the cap so a flood
  of low-value user hits cannot bury project precision (D3 / R4 / NFR04).

Performance (NFR01): federation is SKIPPED ENTIRELY when the user tier is
disabled or no user backend has been constructed/has data -- the gating probe
(``peek_user_backend``) never constructs a backend, so the session_start hot
path pays nothing when the user store is absent/empty. Federation fails open to
project-only recall on any error (never breaks recall).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING, cast

import structlog
from trw_memory.exceptions import CorruptDatabaseUnsalvageableError, StorageError
from trw_memory.models.config import MemoryConfig
from trw_memory.models.memory import MemoryStatus
from trw_memory.security.recall_filter import filter_recall_window

from trw_mcp.models.typed_dicts import LearningEntryDict
from trw_mcp.state._constants import DEFAULT_LIST_LIMIT
from trw_mcp.state._memory_queries import _apply_entry_filters, _search_entries
from trw_mcp.state._memory_transforms import _memory_to_learning_dict
from trw_mcp.state._tier_routing import user_scope_present
from trw_mcp.state._user_tier import peek_user_backend

if TYPE_CHECKING:
    from pathlib import Path

    from trw_memory.models.memory import MemoryEntry
    from trw_memory.storage.sqlite_backend import SQLiteBackend

logger = structlog.get_logger(__name__)

# Project namespace forced on the project-store query (unchanged from the
# pre-federation behavior). The facade keeps a module-level ``_NAMESPACE`` that
# tests may patch; we read it lazily through the facade to honor those patches.


def _project_namespace() -> str:
    from trw_mcp.state import memory_adapter

    return memory_adapter._NAMESPACE


def _user_recall_cap() -> int:
    """Per-recall cap on user-tier hits (config, default 5). Fails to 5."""
    try:
        from trw_mcp.models.config import get_config

        return max(0, int(get_config().recall_user_tier_cap))
    except Exception:  # justified: fail-safe to the documented default
        logger.debug("user_tier_cap_read_failed", exc_info=True)
        return 5


def _federate_user_tier(
    project_entries: list[MemoryEntry],
    query: str,
    *,
    tags: list[str] | None,
    mem_status: MemoryStatus | None,
    min_impact: float,
    max_results: int,
    is_wildcard: bool,
    allow_cold_embedding_init: bool,
    as_of: datetime | None = None,
    include_superseded: bool = False,
) -> list[MemoryEntry]:
    """Append capped, de-duped user-tier hits to the project hits.

    Skipped entirely (returns ``project_entries`` unchanged) when the user tier
    is disabled or no user backend exists/has data. Never raises -- any failure
    degrades to project-only (NFR04 fail-open). Tier is a feature, not an
    override: project hits keep their order; user hits are appended (capped),
    so the downstream utility re-rank can still float a high-value project hit
    above the user tail and a precise project hit stays rank 1.
    """
    cap = _user_recall_cap()
    if cap == 0:
        return project_entries
    try:
        if not user_scope_present():
            return project_entries
        # Gate WITHOUT constructing a backend: if none has been built and the
        # store file is absent, federation is a no-op (session_start hot path
        # pays nothing). Only construct when a user store is actually present.
        user_backend = peek_user_backend()
        if user_backend is None:
            from trw_mcp.state._user_paths import resolve_user_memory_dir

            if not (resolve_user_memory_dir(create=False) / "memory.db").exists():
                return project_entries
            from trw_mcp.state._user_tier import get_user_backend

            user_backend = get_user_backend()

        seen = {e.id for e in project_entries}
        user_hits = _query_user_backend(
            user_backend,
            query,
            tags=tags,
            mem_status=mem_status,
            min_impact=min_impact,
            max_results=max_results,
            is_wildcard=is_wildcard,
            allow_cold_embedding_init=allow_cold_embedding_init,
            as_of=as_of,
            include_superseded=include_superseded,
        )
        merged = list(project_entries)
        added = 0
        for entry in user_hits:
            if added >= cap:
                break
            if entry.id in seen:
                continue
            seen.add(entry.id)
            merged.append(entry)
            added += 1
        if added:
            logger.debug("recall_federated_user_tier", user_hits=added, project_hits=len(project_entries))
        return merged
    except Exception:  # justified: fail-open — federation must never break recall
        logger.debug("user_tier_federation_failed", exc_info=True)
        return project_entries


def _query_user_backend(
    user_backend: SQLiteBackend,
    query: str,
    *,
    tags: list[str] | None,
    mem_status: MemoryStatus | None,
    min_impact: float,
    max_results: int,
    is_wildcard: bool,
    allow_cold_embedding_init: bool,
    as_of: datetime | None = None,
    include_superseded: bool = False,
) -> list[MemoryEntry]:
    """Query the user store for ``user:`` entries (namespace=None = all tiers there)."""
    if is_wildcard:
        return user_backend.list_entries(
            status=mem_status,
            namespace=None,
            limit=max_results if max_results > 0 else DEFAULT_LIST_LIMIT,
        )
    top_k = max_results if max_results > 0 else DEFAULT_LIST_LIMIT
    # namespace=None: the user store holds only ``user:<id>`` entries; searching
    # all namespaces returns them without needing to know the exact ``<id>``.
    return _search_entries(
        user_backend,
        query,
        top_k=top_k,
        tags=tags,
        mem_status=mem_status,
        min_impact=min_impact,
        allow_cold_embedding_init=allow_cold_embedding_init,
        namespace=None,
        as_of=as_of,
        include_superseded=include_superseded,
    )


def _user_store_tampered() -> bool:
    """core185-3: return True when the USER store's canary signals tamper.

    The project-tier recall path halt-checks the project canary, but the user
    store is a SEPARATE database whose canaries were never probed before its
    entries were federated into the result. A tampered user store would
    otherwise flow malicious/corrupted entries into recall.

    Prefers a live user backend WITHOUT constructing one (``peek_user_backend``)
    so the session_start hot path pays nothing when no user store exists. When no
    backend has been built yet BUT the user DB file exists on disk, the backend
    is constructed and probed here -- this closes core185-TOCTOU-1: previously the
    peek returned ``None`` on a fresh process, this gate reported "not tampered",
    and ``_federate_user_tier`` then constructed + queried the backend itself with
    NO canary check, leaking a tampered store on the very first federation call.
    The construct-when-present condition mirrors ``_federate_user_tier``'s own
    construction gate so the canary is checked exactly when federation would build
    and query the backend.

    The canary seams are resolved through the ``memory_adapter`` facade so the
    established ``memory_adapter.should_halt_recalls`` / ``.initialize_canaries``
    patch points apply. Fails OPEN (returns False): a probe error must not break
    recall -- it just leaves federation enabled, matching the project path's
    fail-open posture.
    """
    user_backend = peek_user_backend()
    if user_backend is None:
        try:
            from trw_mcp.state._user_paths import resolve_user_memory_dir

            if not (resolve_user_memory_dir(create=False) / "memory.db").exists():
                return False
            from trw_mcp.state._user_tier import get_user_backend

            user_backend = get_user_backend()
        except Exception:  # justified: fail-open — a probe/construct error must not break recall
            logger.debug("user_store_canary_construct_failed", exc_info=True)
            return False
    try:
        from trw_mcp.state import memory_adapter as _facade
        from trw_mcp.state._user_paths import resolve_user_memory_dir

        user_sec_cfg = MemoryConfig(storage_path=str(resolve_user_memory_dir(create=False)))
        _facade.initialize_canaries(user_sec_cfg, backend=user_backend)
        return bool(_facade.should_halt_recalls(user_sec_cfg, backend=user_backend))
    except Exception:  # justified: fail-open — a canary-probe error must not break recall
        logger.debug("user_store_canary_probe_failed", exc_info=True)
        return False


def _parse_as_of(as_of: str | None) -> datetime | None:
    """PRD-CORE-194 FR03: parse an ISO-8601 ``as_of`` to a tz-aware UTC datetime.

    Returns ``None`` for ``None`` (the default, open-only behavior). Accepts a
    trailing ``Z`` (UTC). A naive parse is assumed UTC so the comparison against
    tz-aware entry windows never raises. Raises ``ValueError`` on a malformed
    string so the boundary can surface a clean validation error rather than crash.
    """
    if as_of is None:
        return None
    try:
        parsed = datetime.fromisoformat(as_of.replace("Z", "+00:00"))
    except (ValueError, TypeError) as exc:
        raise ValueError(f"as_of must be an ISO-8601 datetime, got {as_of!r}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


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
    include_tiers: list[str] | None = None,
    as_of: str | None = None,
    include_superseded: bool = False,
) -> list[dict[str, object]]:
    """Search learnings, federating project ∪ user tiers (PRD-CORE-185 FR06/FR07).

    For wildcard queries (``*`` or empty), lists all entries. Otherwise performs
    keyword/hybrid search. When a user-scope store is present, user-tier hits are
    merged in (capped, de-duped); otherwise behavior is project-only and
    byte-identical to the pre-federation path.

    ``include_tiers`` (FR07) scopes ONLY the user-tier federation; project
    entries are ALWAYS included (the project tier is the local source of truth
    and is never excluded). ``None`` (default) and any list containing ``"user"``
    federate the user tier when present; ``["project"]`` (no ``"user"``) yields
    project-only. A user-only query is intentionally not expressible -- passing
    ``["user"]`` still returns project entries plus the federated user tier.

    ``as_of`` / ``include_superseded`` (PRD-CORE-194 FR03) thread the bi-temporal
    validity prior. ``as_of`` is an ISO-8601 string ("what was believed true as of
    T"); a malformed value raises ``ValueError`` (the boundary surfaces it as a
    clean validation error). ``include_superseded=True`` appends superseded records
    AFTER every open one rather than dropping them. The defaults (``as_of=None``,
    ``include_superseded=False``) are byte-identical to the pre-194 path.
    """
    as_of_dt = _parse_as_of(as_of)
    federate_user = include_tiers is None or "user" in include_tiers
    is_wildcard = query.strip() in ("*", "")
    namespace = _project_namespace()

    # Resolve canary/backend seams through the ``memory_adapter`` facade so the
    # established test-patch points (``memory_adapter.get_backend`` /
    # ``.should_halt_recalls`` / ``.probe_canaries`` / ``.initialize_canaries`` /
    # ``._memory_recovery_in_progress``) still take effect after the FR06 split.
    from trw_mcp.state import memory_adapter as _facade

    mem_status: MemoryStatus | None = None
    if status is not None:
        try:
            mem_status = MemoryStatus(status)
        except ValueError:
            logger.debug("invalid_status_ignored", status=status)

    from trw_memory.models.memory import MemoryEntry as _ME

    entries: list[_ME] = []
    if _facade._memory_recovery_in_progress():
        _facade.logger.warning("memory_recall_skipped_recovery_in_progress", query=query[:80])
        return []
    # core185-7: built ONCE before the loop. ``trw_dir`` is loop-invariant so
    # re-constructing inside the loop was dead work; this single binding feeds
    # the canary calls AND the post-loop recall filter.
    sec_cfg = MemoryConfig(storage_path=str(trw_dir / "memory"))
    for attempt in range(2):
        try:
            backend = _facade.get_backend(trw_dir)
            _facade.initialize_canaries(sec_cfg, backend=backend)
            if _facade.should_halt_recalls(sec_cfg, backend=backend):
                from trw_memory.exceptions import CanaryTamperError

                raise CanaryTamperError("recall halted after canary tamper")
            _facade.probe_canaries(sec_cfg, backend=backend)
            if is_wildcard:
                entries = backend.list_entries(
                    status=mem_status,
                    namespace=namespace,
                    limit=max_results if max_results > 0 else DEFAULT_LIST_LIMIT,
                )
            else:
                top_k = max_results if max_results > 0 else DEFAULT_LIST_LIMIT
                entries = _search_entries(
                    backend,
                    query,
                    top_k=top_k,
                    tags=tags,
                    mem_status=mem_status,
                    min_impact=min_impact,
                    allow_cold_embedding_init=allow_cold_embedding_init,
                    as_of=as_of_dt,
                    include_superseded=include_superseded,
                )
            break
        except Exception as exc:  # justified: boundary, corruption recovery retries recall before surfacing failure
            # Recovery seams + the warning logger are resolved through the
            # ``memory_adapter`` facade so existing tests patching
            # ``memory_adapter._schedule_deferred_recovery`` /
            # ``._memory_recovery_in_progress`` / ``.logger`` still apply after
            # the FR06 recall-path split.
            if isinstance(exc, CorruptDatabaseUnsalvageableError):
                _facade._log_terminal_recovery(trw_dir / "memory" / "memory.db", exc)
                raise
            if attempt == 0 and _facade._is_corruption_error(exc):
                _facade.logger.warning(
                    "memory_recall_degraded_recovery_scheduled",
                    query=query,
                    attempt=attempt + 1,
                    exc_info=True,
                )
                _facade._schedule_deferred_recovery(trw_dir, reason="recall_corruption", context={"query": query[:80]})
                return []
            if isinstance(exc, StorageError):
                _facade.logger.warning("memory_recall_storage_error", query=query[:80], error=str(exc), exc_info=True)
                return []
            raise

    # FR06: federate user-tier hits (capped, de-duped, fail-open) BEFORE the
    # canary filter + transform so user entries flow through the same pipeline.
    # FR07: skip federation entirely when the caller excluded the user tier.
    # core185-3: a tampered USER store DISABLES federation (rather than aborting
    # recall) so its entries never enter the result -- project recall survives.
    if federate_user and _user_store_tampered():
        logger.warning("user_tier_federation_disabled_canary_tamper", query=query[:80])
        federate_user = False
    if federate_user:
        entries = _federate_user_tier(
            entries,
            query,
            tags=tags,
            mem_status=mem_status,
            min_impact=min_impact,
            max_results=max_results,
            is_wildcard=is_wildcard,
            allow_cold_embedding_init=allow_cold_embedding_init,
            as_of=as_of_dt,
            include_superseded=include_superseded,
        )

    # PRD-CORE-194 FR03: apply the validity prior on the MCP recall path so a
    # superseded record is EXCLUDED by default here too (the wildcard list_entries
    # branch and the keyword fallback do not pass through hybrid_search's prior).
    # This is the same in-memory post-fetch field compare used by hybrid_search,
    # so the MCP and MemoryClient defaults agree. The ``trw_recall`` tool threads
    # its ``as_of`` / ``include_superseded`` kwargs here (parsed above), reaching
    # parity with ``MemoryClient.recall``'s time-travel surface.
    from trw_memory.retrieval.validity_prior import apply_validity_prior

    entries = apply_validity_prior(entries, as_of=as_of_dt, include_superseded=include_superseded)

    public_entries = [entry for entry in entries if entry.metadata.get("system_canary") != "true"]
    filter_result = (
        filter_recall_window(public_entries, mode=sec_cfg.recall_filter_mode) if sec_cfg.enable_recall_filter else None
    )
    filtered_entries = filter_result.accepted if filter_result is not None else public_entries
    results: list[LearningEntryDict] = []
    for entry in filtered_entries:
        if is_wildcard and not _apply_entry_filters(entry, tags, mem_status, min_impact):
            continue
        if not is_wildcard and entry.importance < min_impact:
            continue
        results.append(_memory_to_learning_dict(entry, compact=compact))

    # R-RANK-002/004: wildcard list_entries orders by updated_at DESC only; route
    # through rank_by_utility so impact/utility drives order (recency is a decay
    # term, not the sole key). The non-wildcard branch is left to execute_recall.
    ranked_results: list[dict[str, object]] = cast("list[dict[str, object]]", results)
    if is_wildcard and ranked_results:
        ranked_results = _rank_wildcard_by_utility(ranked_results)

    logger.info(
        "memory_search_ok",
        query=query[:50],
        result_count=len(ranked_results),
        is_wildcard=is_wildcard,
    )
    return ranked_results


def _rank_wildcard_by_utility(results: list[dict[str, object]]) -> list[dict[str, object]]:
    """Re-rank wildcard recall results so impact/utility drives order.

    R-RANK-002/004: ``backend.list_entries`` returns ``updated_at DESC`` only.
    For a wildcard query every entry has relevance 1.0, so ``rank_by_utility``
    blends ``(1 - lambda) * 1.0 + lambda * utility`` and the utility term
    (impact + Ebbinghaus recency decay) becomes the sole differentiator. Fails
    open: any ranking error returns the recency-ordered list unchanged.
    """
    try:
        from trw_mcp.models.config import get_config
        from trw_mcp.scoring import rank_by_utility

        lambda_weight = get_config().recall_utility_lambda
        return rank_by_utility(results, [], lambda_weight)
    except Exception:  # justified: fail-open, ranking must never block recall
        logger.debug("wildcard_utility_rank_failed", exc_info=True)
        return results
