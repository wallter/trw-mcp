"""Core recall logic — extracted from learning.py for module-size compliance.

Dependencies that test suites patch at ``trw_mcp.tools.learning.*`` are
injected as parameters by the closure in ``learning.py`` so that patches
remain effective without needing to know about this module.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import structlog

from trw_mcp.models.config import TRWConfig
from trw_mcp.models.typed_dicts import RecallContextDict, RecallResultDict
from trw_mcp.scoring._recall import RecallContext
from trw_mcp.state.persistence import FileStateReader
from trw_mcp.state.surface_tracking import log_surface_event

logger = structlog.get_logger(__name__)


def _detect_surface_phase() -> str:
    """Best-effort detection of the current ceremony phase.

    Returns the phase string (e.g. ``"IMPLEMENT"``) or ``""`` when
    detection fails.  Never raises.
    """
    try:
        from trw_mcp.state._paths import detect_current_phase

        phase = detect_current_phase()
        return phase.upper() if phase else ""
    except Exception:  # justified: fail-open, phase detection is optional
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
    except Exception:  # noqa: S110  # fail-open for git
        pass

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
    except Exception:  # noqa: S110 — justified: fail-open, config auto-detection is best-effort
        pass

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
    except Exception:  # noqa: S110 — justified: fail-open, PRD knowledge ID loading is best-effort
        pass

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
    ultra_compact: bool = False,
    topic: str | None = None,
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

    reader = FileStateReader()
    if max_results is None:
        max_results = config.recall_max_results
    is_wildcard = query.strip() in ("*", "")
    query_tokens = [] if is_wildcard else query.lower().split()
    use_compact = ultra_compact or (compact if compact is not None else is_wildcard)

    # Build recall context for contextual boosting (PRD-CORE-102)
    recall_context: RecallContext | None = None
    try:
        recall_context = build_recall_context(trw_dir, query)
    except Exception:
        logger.debug("recall_context_build_failed", exc_info=True)

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

    # Apply result cap
    if max_results > 0:
        ranked_learnings = ranked_learnings[:max_results]

    # --- Surface event logging (PRD-CORE-103-FR01) ---
    # Log each surfaced learning for telemetry/fatigue detection.
    # Skip compact/wildcard queries (bulk operations, not intentional surfacings).
    if not use_compact:
        try:
            phase = _detect_surface_phase()
            for entry in ranked_learnings:
                lid = str(entry.get("id", ""))
                if lid:
                    log_surface_event(
                        trw_dir,
                        learning_id=lid,
                        surface_type="recall",
                        phase=phase,
                        files_context=[],  # No file context in base recall; session_start path adds its own
                    )
        except Exception:  # justified: fail-open, surface logging must not block recall
            logger.debug("surface_logging_failed", exc_info=True)

    # --- Assertion verification (PRD-CORE-086 FR06) ---
    if not use_compact:
        ranked_learnings = _verify_assertions(
            ranked_learnings, query_tokens, config, rank_fn, context=recall_context
        )

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

    recall_result: RecallResultDict = {
        "query": query,
        "learnings": ranked_learnings,
        "patterns": matching_patterns,
        "context": context_data,
        "total_matches": len(ranked_learnings) + len(matching_patterns),
        "total_available": total_available,
        "compact": use_compact,
        "max_results": max_results,
        "topic_filter_ignored": topic_filter_ignored if topic is not None else False,
    }

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
    except Exception:  # justified: fail-open
        logger.debug("assertion_project_root_resolve_failed", exc_info=True)

    if not project_root_path:
        return ranked_learnings

    try:
        from trw_memory.lifecycle.verification import verify_assertions
        from trw_memory.models.memory import Assertion

        now = datetime.now(timezone.utc)
        stale_threshold = now - timedelta(days=config.assertion_stale_threshold_days)

        for learning in ranked_learnings:
            raw_assertions = learning.get("assertions")
            if not raw_assertions or not isinstance(raw_assertions, list):
                continue
            entry_id = str(learning.get("id", ""))
            try:
                assertions_list = [
                    Assertion.model_validate(a) for a in raw_assertions if isinstance(a, dict)
                ]
                results = verify_assertions(assertions_list, project_root_path)

                passing = sum(1 for r in results if r.passed is True)
                failing = sum(1 for r in results if r.passed is False)
                stale = sum(1 for r in results if r.passed is None)

                learning["assertion_status"] = {
                    "passing": passing,
                    "failing": failing,
                    "stale": stale,
                    "details": [r.model_dump() for r in results],
                }

                if failing > 0:
                    penalty = config.assertion_failure_penalty * (failing / len(results))
                    assertion_penalties[entry_id] = penalty

                # FR06: Update assertion fields with verification results
                updated_assertions: list[dict[str, object]] = []
                for assertion, result in zip(assertions_list, results, strict=False):
                    a_dict = assertion.model_dump()
                    a_dict["last_result"] = result.passed
                    a_dict["last_verified_at"] = now.isoformat()
                    a_dict["last_evidence"] = result.evidence
                    # FR08: Track first_failed_at transitions
                    if result.passed is False:
                        # Set first_failed_at if not already set (transition to failure)
                        if assertion.first_failed_at is None:
                            a_dict["first_failed_at"] = now.isoformat()
                    elif result.passed is True:
                        # Clear first_failed_at on transition back to passing
                        a_dict["first_failed_at"] = None
                    updated_assertions.append(a_dict)

                # Persist updated assertions via backend
                try:
                    from trw_mcp.state._paths import resolve_trw_dir
                    from trw_mcp.state.memory_adapter import get_backend

                    trw_dir = resolve_trw_dir()
                    backend = get_backend(trw_dir)
                    backend.update(entry_id, assertions=json.dumps(updated_assertions))
                except Exception:  # justified: persist is best-effort
                    logger.debug("assertion_result_persist_failed", entry_id=entry_id, exc_info=True)

                # FR08: Auto-stale detection — if ALL assertions have been
                # failing for longer than the threshold, mark learning stale
                all_persistently_failing = (
                    len(updated_assertions) > 0
                    and all(
                        a.get("first_failed_at") is not None
                        and datetime.fromisoformat(str(a["first_failed_at"])) < stale_threshold
                        for a in updated_assertions
                    )
                )
                if all_persistently_failing:
                    logger.info(
                        "learning_auto_stale",
                        entry_id=entry_id,
                        threshold_days=config.assertion_stale_threshold_days,
                    )
                    learning["verification_status"] = "stale"

            except Exception:  # justified: scan-resilience
                logger.debug(
                    "assertion_verification_error",
                    entry_id=entry_id,
                    exc_info=True,
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
