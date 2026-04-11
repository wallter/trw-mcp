"""Core recall logic — extracted from learning.py for module-size compliance.

Dependencies that test suites patch at ``trw_mcp.tools.learning.*`` are
injected as parameters by the closure in ``learning.py`` so that patches
remain effective without needing to know about this module.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import structlog

from trw_mcp.models.config import TRWConfig
from trw_mcp.models.typed_dicts import RecallContextDict, RecallResultDict
from trw_mcp.scoring._recall import RecallContext
from trw_mcp.state.persistence import FileStateReader
from trw_mcp.state.surface_tracking import log_surface_event

logger = structlog.get_logger(__name__)

if TYPE_CHECKING:
    from datetime import datetime

    from trw_memory.models.memory import Assertion, AssertionResult


def _detect_surface_phase() -> str:
    """Best-effort detection of the current ceremony phase.

    Returns the phase string (e.g. ``"IMPLEMENT"``) or ``""`` when
    detection fails.  Never raises.
    """
    try:
        from trw_mcp.state._paths import detect_current_phase

        phase = detect_current_phase()
        return phase.upper() if phase else ""
    except (ImportError, OSError, RuntimeError, ValueError, TypeError):
        logger.debug(
            "surface_phase_detection_failed",
            component="recall",
            op="detect_surface_phase",
            outcome="fail_open",
            exc_info=True,
        )
        return ""


def build_recall_context(
    trw_dir: Path,
    query: str,
) -> RecallContext | None:
    """Build a RecallContext from the current session state.

    PRD-CORE-116-FR04: Populates inferred_domains as set[str] and
    threads client_profile/model_family from config.

    Best-effort: returns None if context can't be built.
    """
    from trw_mcp.scoring._recall import RecallContext, infer_domains

    current_phase: str | None = _detect_surface_phase() or None
    modified_files: list[str] = []

    try:
        import subprocess

        git_result = subprocess.run(
            ["git", "diff", "--name-only", "HEAD"],  # noqa: S607
            capture_output=True, text=True, timeout=5,
            cwd=str(trw_dir.parent) if trw_dir.name == ".trw" else str(trw_dir),
        )
        if git_result.returncode == 0:
            modified_files = [f.strip() for f in git_result.stdout.strip().split("\n") if f.strip()]
    except (OSError, subprocess.SubprocessError, ValueError):
        logger.debug(
            "recall_context_git_scan_failed",
            component="recall",
            op="build_recall_context",
            outcome="fail_open",
            exc_info=True,
        )

    inferred_domains = infer_domains(file_paths=modified_files, query=query)

    if not current_phase and not inferred_domains:
        return None

    # Thread client_profile and model_family from config (PRD-CORE-116)
    client_profile = ""
    model_family = ""
    try:
        from trw_mcp.models.config import get_config

        config = get_config()
        profile = config.client_profile
        client_profile = profile.client_id if profile else ""
        model_family = getattr(config, "model_family", "") or ""
    except (ImportError, OSError, RuntimeError, ValueError, TypeError):
        logger.debug(
            "recall_context_config_failed",
            component="recall",
            op="build_recall_context",
            outcome="fail_open",
            exc_info=True,
        )

    # Thread PRD knowledge IDs from artifact scanning (CORE-106/CORE-116)
    prd_knowledge_ids: set[str] = set()
    try:
        from trw_mcp.state._paths import find_active_run

        active_run = find_active_run()
        if active_run:
            kr_path = Path(active_run) / "meta" / "knowledge_requirements.yaml"
            if kr_path.exists():
                reader = FileStateReader()
                kr_data = reader.read_yaml(kr_path)
                raw_ids = kr_data.get("learning_ids", [])
                if isinstance(raw_ids, list):
                    prd_knowledge_ids = {str(lid) for lid in raw_ids}
    except (ImportError, OSError, RuntimeError, ValueError, TypeError):
        logger.debug(
            "recall_context_prd_scan_failed",
            component="recall",
            op="build_recall_context",
            outcome="fail_open",
            exc_info=True,
        )

    logger.debug(
        "recall_context_built",
        phase=current_phase,
        domains=sorted(inferred_domains),
        client_profile=client_profile,
        model_family=model_family,
        prd_knowledge_ids_count=len(prd_knowledge_ids),
    )

    return RecallContext(
        current_phase=current_phase,
        inferred_domains=inferred_domains,
        modified_files=modified_files,
        client_profile=client_profile,
        model_family=model_family,
        prd_knowledge_ids=prd_knowledge_ids,
    )


def _deprioritize_ranked_learnings(
    ranked_learnings: list[dict[str, object]],
    deprioritized_ids: set[str],
) -> list[dict[str, object]]:
    """Move already-in-context learnings behind fresh results while preserving order."""
    if not deprioritized_ids:
        return ranked_learnings

    fresh: list[dict[str, object]] = []
    already_in_context: list[dict[str, object]] = []
    for entry in ranked_learnings:
        if str(entry.get("id", "")) in deprioritized_ids:
            entry["already_in_context"] = True
            already_in_context.append(entry)
        else:
            fresh.append(entry)
    return fresh + already_in_context


def _build_recall_context_safe(trw_dir: Path, query: str) -> RecallContext | None:
    """Build recall context with structured fail-open logging."""

    try:
        return build_recall_context(trw_dir, query)
    except (OSError, RuntimeError, ValueError, TypeError):
        logger.debug(
            "recall_context_build_failed",
            component="recall",
            op="build_recall_context",
            outcome="fail_open",
            exc_info=True,
        )
        return None


def _log_recall_surfaces(trw_dir: Path, ranked_learnings: list[dict[str, object]]) -> None:
    """Best-effort recall surface logging."""

    try:
        phase = _detect_surface_phase()
        for entry in ranked_learnings:
            learning_id = str(entry.get("id", ""))
            if learning_id:
                log_surface_event(
                    trw_dir,
                    learning_id=learning_id,
                    surface_type="recall",
                    phase=phase,
                    files_context=[],
                )
    except (OSError, RuntimeError, ValueError, TypeError):
        logger.debug(
            "surface_logging_failed",
            component="recall",
            op="log_surface_event",
            outcome="fail_open",
            exc_info=True,
        )


def _append_recall_ceremony_status(
    recall_result: RecallResultDict,
    trw_dir: Path,
) -> RecallResultDict:
    """Best-effort ceremony summary injection for recall responses."""

    try:
        from trw_mcp.tools._ceremony_status import append_ceremony_status

        append_ceremony_status(cast("dict[str, object]", recall_result), trw_dir)
    except (ImportError, OSError, RuntimeError, ValueError, TypeError):
        logger.debug(
            "recall_ceremony_status_skipped",
            component="recall",
            op="append_ceremony_status",
            outcome="fail_open",
            exc_info=True,
        )
    return recall_result


def _build_recall_result(
    *,
    query: str,
    ranked_learnings: list[dict[str, object]],
    matching_patterns: list[dict[str, object]],
    context_data: RecallContextDict,
    total_available: int,
    use_compact: bool,
    max_results: int,
    topic_filter_ignored: bool,
    token_budget: int | None,
    tokens_used: int,
    tokens_truncated: bool,
) -> RecallResultDict:
    """Build the final recall response payload."""

    return {
        "query": query,
        "learnings": ranked_learnings,
        "patterns": matching_patterns,
        "context": context_data,
        "total_matches": len(ranked_learnings) + len(matching_patterns),
        "total_available": total_available,
        "compact": use_compact,
        "max_results": max_results,
        "topic_filter_ignored": topic_filter_ignored,
        "tokens_used": tokens_used,
        "tokens_budget": token_budget,
        "tokens_truncated": tokens_truncated,
    }


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
    compact: bool | None = None,
    topic: str | None = None,
    token_budget: int | None = None,
    deprioritized_ids: set[str] | None = None,
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
        topic: Optional topic slug from knowledge topology.
        deprioritized_ids: Learning IDs to push behind fresh results before final caps.
        _adapter_recall: Injected recall function.
        _adapter_update_access: Injected access tracking function.
        _search_patterns: Injected pattern search function.
        _rank_by_utility: Injected ranking function.
        _collect_context: Injected context collector.
    """
    # PRD-CORE-125-FR03: Learning recall gating -- early return when
    # recall is disabled via config/profile.
    if not config.effective_learning_recall_enabled:
        logger.debug("surface_gated", surface="recall")
        return {
            "query": query,
            "learnings": [],
            "patterns": [],
            "context": {},
            "total_matches": 0,
            "total_available": 0,
            "compact": compact if compact is not None else (query.strip() in ("*", "")),
            "max_results": max_results if max_results is not None else config.recall_max_results,
            "topic_filter_ignored": False,
            "tokens_used": 0,
            "tokens_budget": token_budget,
            "tokens_truncated": False,
        }

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

    # Input validation (PRD-QUAL-042-FR06): impact bounds
    min_impact = max(0.0, min(1.0, min_impact))

    reader = FileStateReader()
    if max_results is None:
        max_results = config.recall_max_results
    is_wildcard = query.strip() in ("*", "")
    query_tokens = [] if is_wildcard else query.lower().split()
    use_compact = compact if compact is not None else is_wildcard

    # Build recall context for contextual boosting (PRD-CORE-102)
    recall_context = _build_recall_context_safe(trw_dir, query)

    # Search entries via SQLite adapter
    matching_learnings = recall_fn(
        trw_dir,
        query=query,
        tags=tags,
        min_impact=min_impact,
        status=status,
        max_results=0,
        compact=False,
    )

    # Topic-scoped pre-filter (PRD-CORE-021-FR07)
    topic_filter_ignored = False
    if topic is not None:
        topic_filter_ignored = _apply_topic_filter(trw_dir, config, topic, matching_learnings)

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

    # --- Assertion verification (PRD-CORE-086 FR06) ---
    if not use_compact:
        ranked_learnings = _verify_assertions(
            ranked_learnings, query_tokens, config, rank_fn, context=recall_context
        )

    if deprioritized_ids:
        ranked_learnings = _deprioritize_ranked_learnings(ranked_learnings, deprioritized_ids)

    # --- Token budget filtering (PRD-CORE-123 Phase 2) ---
    from trw_memory.retrieval.token_budget import apply_token_budget, estimate_entry_tokens

    tokens_used = 0
    tokens_truncated = False
    if token_budget is not None:
        if token_budget <= 0:
            raise ValueError(f"token_budget must be positive, got {token_budget}")
        ranked_learnings, tokens_used, tokens_truncated = apply_token_budget(
            ranked_learnings, token_budget
        )
    else:
        tokens_used = sum(estimate_entry_tokens(e) for e in ranked_learnings)

    # Apply result cap after assertion reranking and already-in-context deprioritization.
    if max_results > 0 and len(ranked_learnings) > max_results:
        ranked_learnings = ranked_learnings[:max_results]
        tokens_used = sum(estimate_entry_tokens(e) for e in ranked_learnings)

    # --- Surface event logging (PRD-CORE-103-FR01) ---
    # Log each surfaced learning for telemetry/fatigue detection.
    # Skip compact/wildcard queries (bulk operations, not intentional surfacings).
    if not use_compact:
        _log_recall_surfaces(trw_dir, ranked_learnings)

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

    recall_result = _build_recall_result(
        query=query,
        ranked_learnings=ranked_learnings,
        matching_patterns=matching_patterns,
        context_data=context_data,
        total_available=total_available,
        use_compact=use_compact,
        max_results=max_results,
        topic_filter_ignored=topic_filter_ignored if topic is not None else False,
        token_budget=token_budget,
        tokens_used=tokens_used,
        tokens_truncated=tokens_truncated,
    )

    # Inject ceremony progress summary.
    if not (is_wildcard and use_compact):
        recall_result = _append_recall_ceremony_status(recall_result, trw_dir)

    return recall_result


def _apply_topic_filter(
    trw_dir: Path,
    config: TRWConfig,
    topic: str,
    matching_learnings: list[dict[str, object]],
) -> bool:
    """Apply topic-scoped pre-filter. Mutates list in place. Returns True if ignored."""
    clusters_path = trw_dir / config.knowledge_output_dir / "clusters.json"
    try:
        if clusters_path.exists():
            clusters_data = json.loads(clusters_path.read_text(encoding="utf-8"))
            if topic in clusters_data:
                allowed_ids = set(clusters_data[topic])
                matching_learnings[:] = [e for e in matching_learnings if str(e.get("id", "")) in allowed_ids]
                return False
            return True
        return True
    except (json.JSONDecodeError, OSError):
        return True


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
    except (ImportError, OSError, RuntimeError, ValueError, TypeError):
        logger.debug(
            "remote_recall_failed",
            component="recall",
            op="augment_with_remote",
            outcome="fail_open",
            exc_info=True,
        )
    return list(matching_learnings)


def _build_assertion_status(entry_id: str, results: Sequence[AssertionResult]) -> tuple[dict[str, object], int]:
    """Build assertion_status payload and return failing count."""
    passing = 0
    failing = 0
    stale = 0
    detail_rows: list[dict[str, object]] = []

    for index, result in enumerate(results, start=1):
        passed = result.passed
        if passed is True:
            passing += 1
        elif passed is False:
            failing += 1
        else:
            stale += 1
        detail = result.model_dump()
        detail["id"] = f"{entry_id}:{index}"
        detail_rows.append(detail)

    return {
        "passing": passing,
        "failing": failing,
        "stale": stale,
        "details": detail_rows,
    }, failing


def _build_updated_assertions(
    assertions_list: Sequence[Assertion],
    results: Sequence[AssertionResult],
    now_iso: str,
) -> list[dict[str, object]]:
    """Apply verification results to serialized assertions."""
    updated_assertions: list[dict[str, object]] = []
    for assertion, result in zip(assertions_list, results, strict=False):
        a_dict = cast("dict[str, object]", assertion.model_dump())
        a_dict["last_result"] = result.passed
        a_dict["last_verified_at"] = now_iso
        a_dict["last_evidence"] = result.evidence
        if result.passed is False:
            if assertion.first_failed_at is None:
                a_dict["first_failed_at"] = now_iso
        elif result.passed is True:
            a_dict["first_failed_at"] = None
        updated_assertions.append(a_dict)
    return updated_assertions


def _persist_assertion_results(entry_id: str, updated_assertions: list[dict[str, object]]) -> None:
    """Best-effort persistence of updated assertion metadata."""
    try:
        from trw_mcp.state._paths import resolve_trw_dir
        from trw_mcp.state.memory_adapter import get_backend

        trw_dir = resolve_trw_dir()
        backend = get_backend(trw_dir)
        backend.update(entry_id, assertions=json.dumps(updated_assertions))
    except (ImportError, OSError, RuntimeError, ValueError, TypeError):
        logger.debug(
            "assertion_result_persist_failed",
            component="recall",
            op="persist_assertion_results",
            outcome="fail_open",
            entry_id=entry_id,
            exc_info=True,
        )


def _all_assertions_persistently_failing(
    updated_assertions: list[dict[str, object]],
    stale_threshold: datetime,
) -> bool:
    """Return True when every assertion has been failing past the stale threshold."""
    from datetime import datetime

    return len(updated_assertions) > 0 and all(
        a.get("first_failed_at") is not None
        and datetime.fromisoformat(str(a["first_failed_at"])) < stale_threshold
        for a in updated_assertions
    )


def _verify_assertions(
    ranked_learnings: list[dict[str, object]],
    query_tokens: list[str],
    config: TRWConfig,
    rank_fn: Callable[..., list[dict[str, object]]],
    context: RecallContext | None = None,
) -> list[dict[str, object]]:
    """Run assertion verification on ranked learnings (PRD-CORE-086 FR06).

    Also persists verification results (last_result, last_verified_at,
    first_failed_at) and applies auto-stale detection (FR08).
    """
    from datetime import datetime, timedelta, timezone

    assertion_penalties: dict[str, float] = {}
    project_root_path: Path | None = None
    try:
        from trw_mcp.state._paths import resolve_project_root

        project_root_path = resolve_project_root()
    except (ImportError, OSError, RuntimeError, ValueError, TypeError):
        logger.debug(
            "assertion_project_root_resolve_failed",
            component="recall",
            op="verify_assertions",
            outcome="fail_open",
            exc_info=True,
        )

    try:
        from trw_memory.lifecycle.verification import verify_assertions
        from trw_memory.models.memory import Assertion

        now = datetime.now(timezone.utc)
        stale_threshold = now - timedelta(days=config.assertion_stale_threshold_days)
        loop_start = time.monotonic()
        verified_entries = 0

        for learning in ranked_learnings:
            raw_assertions = learning.get("assertions")
            if not raw_assertions or not isinstance(raw_assertions, list):
                continue
            entry_id = str(learning.get("id", ""))
            try:
                assertions_list = [
                    Assertion.model_validate(a, strict=False) for a in raw_assertions if isinstance(a, dict)
                ]
                results = verify_assertions(assertions_list, project_root_path)
                verified_entries += 1

                status, failing = _build_assertion_status(entry_id, results)
                learning["assertion_status"] = status

                if failing > 0:
                    penalty = config.assertion_failure_penalty * (failing / len(results))
                    assertion_penalties[entry_id] = penalty

                updated_assertions = _build_updated_assertions(assertions_list, results, now.isoformat())
                _persist_assertion_results(entry_id, updated_assertions)

                if _all_assertions_persistently_failing(updated_assertions, stale_threshold):
                    logger.info(
                        "learning_auto_stale",
                        entry_id=entry_id,
                        threshold_days=config.assertion_stale_threshold_days,
                    )
                    learning["verification_status"] = "stale"

            except (RuntimeError, ValueError, TypeError, OSError):
                logger.debug(
                    "assertion_verification_error",
                    component="recall",
                    op="verify_assertions",
                    outcome="fail_open",
                    entry_id=entry_id,
                    exc_info=True,
                )

        if verified_entries:
            logger.info(
                "assertion_recall_verification_complete",
                verified_entries=verified_entries,
                duration_ms=round((time.monotonic() - loop_start) * 1000, 1),
            )

        if assertion_penalties:
            ranked_learnings = rank_fn(
                ranked_learnings, query_tokens, config.recall_utility_lambda,
                assertion_penalties=assertion_penalties,
                context=context,
            )
    except (ImportError, OSError):
        logger.debug("assertion_verification_unavailable", exc_info=True)

    return ranked_learnings
