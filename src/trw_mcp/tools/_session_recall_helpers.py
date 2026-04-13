"""Session recall helpers for ceremony.py — live session-start recall logic."""

from __future__ import annotations

from pathlib import Path

import structlog

from trw_mcp.models.config import TRWConfig
from trw_mcp.models.config._defaults import LIGHT_MODE_RECALL_CAP
from trw_mcp.models.typed_dicts import (
    AutoRecalledItemDict,
    RunStatusDict,
    SessionRecallExtrasDict,
)
from trw_mcp.scoring import rank_by_utility
from trw_mcp.scoring._recall import RecallContext
from trw_mcp.state.persistence import FileStateReader
from trw_mcp.state.propensity_log import log_ranked_selections
from trw_mcp.state.receipts import log_recall_receipt

logger = structlog.get_logger(__name__)

_PHASE_TAG_MAP: dict[str, list[str]] = {
    "research": ["architecture", "gotcha", "codebase"],
    "plan": ["architecture", "pattern", "dependency"],
    "implement": ["gotcha", "testing", "pattern"],
    "validate": ["testing", "build", "coverage"],
    "review": ["security", "performance", "maintainability"],
    "deliver": ["ceremony", "deployment", "integration"],
}

_ANTIPATTERN_KEYWORDS: tuple[str, ...] = (
    "facade",
    "wiring gap",
    "unwired",
    "dead code",
    "false completion",
    "not wired",
    "integration gap",
)

_SYSTEM_TASK_KEYWORDS: tuple[str, ...] = (
    "model",
    "system",
    "profile",
    "adapter",
    "framework",
    "registry",
)


def _phase_to_tags(phase: str) -> list[str]:
    """Map a framework phase to relevant learning tags (PRD-CORE-049 FR02)."""

    return _PHASE_TAG_MAP.get(phase.lower(), [])


def _apply_antipattern_alerts(
    learnings: list[dict[str, object]],
    query: str,
    is_focused: bool,
) -> list[dict[str, object]]:
    """Prepend anti-pattern alert prefix to matching learning summaries."""

    if not is_focused or not learnings:
        return learnings

    query_lower = query.lower()
    if not any(keyword in query_lower for keyword in _SYSTEM_TASK_KEYWORDS):
        return learnings

    alert_prefix = "\u26a0 ANTI-PATTERN ALERT: "
    result: list[dict[str, object]] = []
    for entry in learnings:
        summary = str(entry.get("summary", "") or "")
        if any(keyword in summary.lower() for keyword in _ANTIPATTERN_KEYWORDS):
            entry = {**entry, "summary": alert_prefix + summary}
        result.append(entry)
    return result


def _log_session_start_surfaces(trw_dir: Path, learning_ids: list[str]) -> None:
    """Best-effort session-start surface logging with structured observability."""

    try:
        from trw_mcp.state.surface_tracking import log_surface_event

        for learning_id in learning_ids:
            log_surface_event(
                trw_dir,
                learning_id=learning_id,
                surface_type="session_start",
            )
    except (ImportError, OSError, RuntimeError, ValueError, TypeError):
        logger.warning(
            "session_start_surface_log_failed",
            op="session_recall",
            outcome="fail_open",
            exc_info=True,
        )


def _dedupe_learning_ids(learning_ids: list[str]) -> list[str]:
    """Preserve order while removing duplicate/empty learning IDs."""

    seen: set[str] = set()
    unique_ids: list[str] = []
    for learning_id in learning_ids:
        if not learning_id or learning_id in seen:
            continue
        seen.add(learning_id)
        unique_ids.append(learning_id)
    return unique_ids


def record_session_start_surfaces(trw_dir: Path, learning_ids: list[str]) -> list[str]:
    """Record shared session-start side effects for surfaced learnings."""

    from trw_mcp.state.memory_adapter import increment_session_counts
    from trw_mcp.state.memory_adapter import update_access_tracking as adapter_update_access

    unique_ids = _dedupe_learning_ids(learning_ids)
    if not unique_ids:
        return []
    increment_session_counts(trw_dir, unique_ids)
    adapter_update_access(trw_dir, unique_ids)
    _log_session_start_surfaces(trw_dir, unique_ids)
    return unique_ids


def perform_session_recalls(
    trw_dir: Path,
    query: str,
    config: TRWConfig,
    reader: FileStateReader,
) -> tuple[list[dict[str, object]], list[AutoRecalledItemDict], SessionRecallExtrasDict]:
    """Execute focused + baseline recalls, return merged results."""

    if config.session_start_recall_enabled is not None and not config.session_start_recall_enabled:
        logger.debug("session_recall_gated", reason="session_start_recall_enabled=False")
        return [], [], {}

    from trw_mcp.state.memory_adapter import recall_learnings as adapter_recall

    is_focused = query.strip() not in ("", "*")
    extra: SessionRecallExtrasDict = {}
    learnings: list[dict[str, object]] = []

    effective_max = (
        min(config.recall_max_results, LIGHT_MODE_RECALL_CAP)
        if config.effective_ceremony_mode == "light"
        else config.recall_max_results
    )

    if is_focused:
        focused = adapter_recall(
            trw_dir,
            query=query,
            min_impact=0.3,
            max_results=effective_max,
            compact=True,
        )
        baseline = adapter_recall(
            trw_dir,
            query="*",
            min_impact=0.7,
            max_results=effective_max,
            compact=True,
        )
        extra["query"] = query
        extra["query_matched"] = len(focused)
        seen_ids: set[str] = set()
        for entry in focused + baseline:
            learning_id = str(entry.get("id", ""))
            if learning_id and learning_id not in seen_ids:
                seen_ids.add(learning_id)
                learnings.append(entry)
        learnings = learnings[:effective_max]
    else:
        learnings = adapter_recall(
            trw_dir,
            query="*",
            min_impact=0.7,
            max_results=effective_max,
            compact=True,
        )

    try:
        log_ranked_selections(
            trw_dir,
            learnings,
            context_task_type="session_start",
            context_session_progress="early",
        )
    except (OSError, RuntimeError, ValueError, TypeError):
        logger.warning(
            "session_start_propensity_log_failed",
            op="session_recall",
            outcome="fail_open",
            exc_info=True,
        )

    matched_ids = record_session_start_surfaces(
        trw_dir,
        [str(entry.get("id", "")) for entry in learnings if entry.get("id")],
    )
    log_recall_receipt(trw_dir, query if is_focused else "*", matched_ids)

    extra["total_available"] = len(learnings)
    logger.debug(
        "session_recalls_complete",
        count=len(learnings),
        is_focused=is_focused,
    )

    try:
        learnings = _apply_antipattern_alerts(learnings, query, is_focused)
    except (RuntimeError, ValueError, TypeError):
        logger.warning(
            "antipattern_alert_failed",
            op="session_recall",
            outcome="fail_open",
            exc_info=True,
        )

    auto_recalled: list[AutoRecalledItemDict] = []
    return learnings, auto_recalled, extra


def _phase_contextual_recall(
    trw_dir: Path,
    query: str,
    config: TRWConfig,
    run_dir: Path | None,
    run_status: RunStatusDict | None,
) -> list[AutoRecalledItemDict]:
    """Execute phase-contextual auto-recall (PRD-CORE-049)."""

    from trw_mcp.state.memory_adapter import recall_learnings as adapter_recall_ar

    is_focused = query.strip() not in ("", "*")
    query_tokens: list[str] = []
    if is_focused:
        query_tokens.extend(query.strip().split())

    phase_tags: list[str] | None = None
    phase = ""
    if run_status is not None:
        task_name = str(run_status.get("task_name", ""))
        phase = str(run_status.get("phase", ""))
        if task_name:
            query_tokens.append(task_name)
        if phase:
            query_tokens.append(phase)
            phase_tag_list = _phase_to_tags(phase)
            if phase_tag_list:
                phase_tags = phase_tag_list

    ar_query = " ".join(query_tokens) if query_tokens else "*"
    ar_entries = adapter_recall_ar(
        trw_dir,
        query=ar_query,
        tags=phase_tags,
        min_impact=0.5,
        max_results=config.auto_recall_max_results * 3,
        compact=True,
    )
    if not ar_entries:
        return []

    intel_cache = None
    try:
        from trw_mcp.sync.cache import IntelligenceCache

        cache = IntelligenceCache(
            trw_dir,
            ttl_seconds=getattr(config, "intel_cache_ttl_seconds", 3600),
        )
        if cache.get_bandit_params() is not None:
            intel_cache = cache
    except Exception:  # noqa: S110 — justified: fail-open, auto-recall must work without cache access
        intel_cache = None

    context = RecallContext(
        current_phase=phase.upper() if phase else None,
        intel_cache=intel_cache,
    )
    ranked = rank_by_utility(
        ar_entries,
        query_tokens,
        lambda_weight=config.recall_utility_lambda,
        context=context,
    )
    capped = ranked[: config.auto_recall_max_results]
    try:
        log_ranked_selections(
            trw_dir,
            capped,
            context_phase=phase.upper() if phase else "",
            context_task_type="phase_auto_recall",
            context_session_progress=phase.lower() if phase else "",
        )
    except (OSError, RuntimeError, ValueError, TypeError):
        logger.warning(
            "phase_auto_recall_propensity_log_failed",
            op="session_recall",
            outcome="fail_open",
            exc_info=True,
        )
    return [
        {
            "id": str(entry.get("id", "")),
            "summary": str(entry.get("summary", "")),
            "impact": float(str(entry.get("impact", 0.0))),
        }
        for entry in capped
    ]
