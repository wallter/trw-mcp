# ruff: noqa: E402
"""Session recall helpers for ceremony.py — live session-start recall logic."""

from __future__ import annotations

from pathlib import Path

import structlog

from trw_mcp.models.config import TRWConfig
from trw_mcp.models.config._defaults import LIGHT_MODE_RECALL_CAP
from trw_mcp.models.typed_dicts import (
    AutoRecalledItemDict,
    SessionRecallExtrasDict,
)
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

_WRITER_PRESSURE_RECALL_CAP = 8
_SESSION_START_COMPACT_FIELDS = ("id", "summary", "impact", "status")


def _is_canary_tamper_error(exc: BaseException) -> bool:
    """Return True when recall failed closed because memory canaries drifted."""
    try:
        from trw_memory.exceptions import CanaryTamperError
    except Exception:  # justified: optional dependency boundary in tests/install variants
        return exc.__class__.__name__ == "CanaryTamperError"
    return isinstance(exc, CanaryTamperError)


def _degraded_canary_recall_extra(exc: BaseException) -> SessionRecallExtrasDict:
    """Build the stable session_start degraded-recall envelope."""
    return {
        "recall_degraded": {
            "reason": "canary_tamper",
            "detail": "Session-start learning recall was skipped because memory canary tamper was detected.",
            "exception_type": exc.__class__.__name__,
        },
        "total_available": 0,
    }


def _compact_session_start_learning(entry: dict[str, object]) -> dict[str, object]:
    """Return the minimal learning payload needed for session-start context."""

    return {field: entry[field] for field in _SESSION_START_COMPACT_FIELDS if field in entry}


def _session_start_writer_pressure(config: TRWConfig, trw_dir: Path) -> tuple[bool, list[int]]:
    """Return whether session_start should prefer a small read-only response."""

    if not config.session_start_defer_under_writer_pressure:
        return False, []
    try:
        from trw_mcp.state.memory_pressure import should_defer_memory_side_effects

        return should_defer_memory_side_effects(
            trw_dir,
            threshold=config.session_start_writer_pressure_threshold,
        )
    except Exception:  # justified: pressure detection is advisory and fail-open
        logger.warning("session_start_response_pressure_check_failed", exc_info=True)
        return False, []


def _session_start_optional_work_pressure(config: TRWConfig, trw_dir: Path) -> tuple[bool, list[int], str]:
    """Return whether optional session-start side effects should leave the hot path."""

    if not config.session_start_defer_under_writer_pressure:
        return False, [], ""
    try:
        from trw_mcp.state.memory_pressure import should_defer_session_start_optional_work

        return should_defer_session_start_optional_work(
            trw_dir,
            threshold=config.session_start_writer_pressure_threshold,
        )
    except Exception:  # justified: optional-work pressure detection is advisory and fail-open
        logger.warning("session_start_optional_pressure_check_failed", exc_info=True)
        return False, [], ""


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
        from trw_mcp.state._session_id import resolve_effective_session_id
        from trw_mcp.state.surface_tracking import log_surface_event

        sid = resolve_effective_session_id(trw_dir)
        for learning_id in learning_ids:
            log_surface_event(
                trw_dir,
                learning_id=learning_id,
                surface_type="session_start",
                session_id=sid,
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

    from trw_mcp.models.config import get_config
    from trw_mcp.state.memory_adapter import increment_session_counts
    from trw_mcp.state.memory_adapter import update_access_tracking as adapter_update_access
    from trw_mcp.state.memory_pressure import should_defer_memory_side_effects

    unique_ids = _dedupe_learning_ids(learning_ids)
    if not unique_ids:
        return []
    config = get_config()
    defer_tracking = False
    writer_pids: list[int] = []
    if config.session_start_defer_under_writer_pressure:
        defer_tracking, writer_pids = should_defer_memory_side_effects(
            trw_dir,
            threshold=config.session_start_writer_pressure_threshold,
        )
    if defer_tracking:
        logger.warning(
            "session_start_tracking_deferred",
            reason="writer_pressure",
            writer_pids=writer_pids,
            writer_count=len(writer_pids),
            threshold=config.session_start_writer_pressure_threshold,
            learning_count=len(unique_ids),
        )
        logger.warning(
            "session_start_surface_log_deferred",
            reason="writer_pressure",
            writer_pids=writer_pids,
            writer_count=len(writer_pids),
            threshold=config.session_start_writer_pressure_threshold,
            learning_count=len(unique_ids),
        )
    else:
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

    is_focused = query.strip() not in ("", "*")
    extra: SessionRecallExtrasDict = {}
    learnings: list[dict[str, object]] = []

    compact_for_pressure, pressure_writer_pids = _session_start_writer_pressure(config, trw_dir)
    effective_max = (
        min(config.recall_max_results, LIGHT_MODE_RECALL_CAP)
        if not compact_for_pressure and config.effective_ceremony_mode == "light"
        else config.recall_max_results
    )
    if compact_for_pressure:
        effective_max = min(effective_max, _WRITER_PRESSURE_RECALL_CAP)

    # PRD-FIX-085 FR05: use named recall factories instead of direct
    # adapter_recall calls so the call site declares its intent.
    from trw_mcp.state.recall_factories import (
        recall_baseline_high_impact,
        recall_focused,
        recall_recent_bypass,
    )

    try:
        if is_focused:
            focused = recall_focused(trw_dir, query, max_results=effective_max)
            baseline = recall_baseline_high_impact(trw_dir, max_results=effective_max)
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
            baseline = recall_baseline_high_impact(trw_dir, max_results=effective_max)
            # L-fovv fix: union the baseline (high-impact, for cross-session tribal
            # knowledge) with fresh low-impact learnings (for chain-mode + per-
            # project session context). trw_learn defaults new entries to
            # impact=0.5, so without this bypass stateful-chain link 2+ recalls
            # return 0 even when link 1 wrote useful lessons.
            bypass_days = int(getattr(config, "session_start_recent_bypass_days", 0))
            learnings = list(baseline)
            if bypass_days > 0:
                import datetime as _dt

                bypass_min = float(getattr(config, "session_start_recent_bypass_min_impact", 0.3))
                cutoff = (_dt.datetime.now(_dt.timezone.utc).date() - _dt.timedelta(days=bypass_days)).isoformat()
                try:
                    fresh = recall_recent_bypass(
                        trw_dir,
                        max_results=effective_max * 2,
                        min_impact=bypass_min,
                    )
                except Exception:  # justified: fail-open, recent-bypass recall must not block session start
                    logger.warning(
                        "session_recent_bypass_recall_failed",
                        op="session_recall",
                        outcome="fail_open",
                        exc_info=True,
                    )
                else:
                    seen_ids = {str(e.get("id", "")) for e in baseline}
                    fresh_additions = [
                        e
                        for e in fresh
                        if str(e.get("created", "")) >= cutoff and str(e.get("id", "")) not in seen_ids
                    ]
                    # Fresh entries are highest-priority context for the current
                    # session; surface them before the high-impact baseline.
                    learnings = fresh_additions + learnings
                    learnings = learnings[:effective_max]
    except Exception as exc:
        if not _is_canary_tamper_error(exc):
            raise
        logger.warning(
            "session_start_recall_degraded",
            reason="canary_tamper",
            op="session_recall",
            outcome="degraded",
            exc_info=True,
        )
        return [], [], _degraded_canary_recall_extra(exc)

    optional_work_deferred, optional_writer_pids, optional_reason = _session_start_optional_work_pressure(
        config,
        trw_dir,
    )
    if optional_work_deferred:
        compact_for_pressure = True
        pressure_writer_pids = optional_writer_pids
        extra["side_effects_deferred"] = {
            "reason": optional_reason,
            "writer_pids": optional_writer_pids,
            "writer_count": len(optional_writer_pids),
            "threshold": config.session_start_writer_pressure_threshold,
        }
        logger.warning(
            "session_start_side_effects_deferred",
            reason=optional_reason,
            writer_pids=optional_writer_pids,
            writer_count=len(optional_writer_pids),
            threshold=config.session_start_writer_pressure_threshold,
            learning_count=len(learnings),
        )
    else:
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
        post_recall_pressure, post_recall_writer_pids = _session_start_writer_pressure(config, trw_dir)
        if post_recall_pressure:
            compact_for_pressure = True
            pressure_writer_pids = post_recall_writer_pids

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

    if compact_for_pressure:
        pre_compact_count = len(learnings)
        learnings = [_compact_session_start_learning(entry) for entry in learnings[:_WRITER_PRESSURE_RECALL_CAP]]
        extra["response_compacted"] = True
        logger.warning(
            "session_start_response_compacted",
            reason="writer_pressure",
            writer_pids=pressure_writer_pids,
            writer_count=len(pressure_writer_pids),
            threshold=config.session_start_writer_pressure_threshold,
            original_count=pre_compact_count,
            returned_count=len(learnings),
        )

    auto_recalled: list[AutoRecalledItemDict] = []
    return learnings, auto_recalled, extra


from trw_mcp.tools._session_recall_phase import (
    _phase_contextual_recall as _phase_contextual_recall,
)
