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
from trw_mcp.state.deferral_ledger import record_completion, step_deferral_decision
from trw_mcp.state.persistence import FileStateReader
from trw_mcp.state.propensity_log import log_ranked_selections
from trw_mcp.state.receipts import log_recall_receipt
from trw_mcp.tools._session_recall_pressure import (
    DEFERRABLE_SIDE_EFFECTS as DEFERRABLE_SIDE_EFFECTS,
)
from trw_mcp.tools._session_recall_pressure import (
    SurfaceTrackingResult as SurfaceTrackingResult,
)
from trw_mcp.tools._session_recall_pressure import (
    _log_session_start_surfaces as _log_session_start_surfaces,
)
from trw_mcp.tools._session_recall_pressure import (
    record_session_start_surfaces as record_session_start_surfaces,
)
from trw_mcp.tools._session_recall_pressure import (
    session_start_census as _session_start_census,
)
from trw_mcp.tools._session_recall_pressure import (
    session_start_defers as _session_start_defers,
)

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


def _compact_session_start_learning(entry: dict[str, object]) -> dict[str, object]:
    """Return the minimal learning payload needed for session-start context."""

    return {field: entry[field] for field in _SESSION_START_COMPACT_FIELDS if field in entry}


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


def perform_session_recalls(
    trw_dir: Path,
    query: str,
    config: TRWConfig,
    reader: FileStateReader,
) -> tuple[list[dict[str, object]], list[AutoRecalledItemDict], SessionRecallExtrasDict]:
    """Execute focused + baseline recalls, return merged results.

    PRD-CORE-263-FR01 / DEF-01: this used to catch its own ``CanaryTamperError``
    (PRD-CORE-227) and return a degraded-but-empty envelope, which made ``recall``
    — declared ``critical`` in the session-start step table — unreachable for the
    one failure mode most worth stopping on. The step no longer decides. Any
    exception here (canary tamper included) now propagates to
    :func:`trw_mcp.tools._ceremony_session_start_steps.step_recall_learnings`,
    which wraps it in :class:`SessionStartStepError` for the runner's critical
    branch to convert into ``success: False`` with a typed reason. Recall
    failures that are NOT this function's own concern (a corrupt database the
    storage layer has already classified for background recovery) are handled
    one layer down in :mod:`trw_mcp.state._memory_recall` (FR07) and never reach
    here as an exception.
    """

    if config.session_start_recall_enabled is not None and not config.session_start_recall_enabled:
        logger.debug("session_recall_gated", reason="session_start_recall_enabled=False")
        return [], [], {}

    is_focused = query.strip() not in ("", "*")
    extra: SessionRecallExtrasDict = {}
    learnings: list[dict[str, object]] = []

    census = _session_start_census(config, trw_dir)
    compact_for_pressure = _session_start_defers(config, census)
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
        focused_recall_zero_match_advisory,
        recall_baseline_high_impact,
        recall_focused,
        recall_recent_bypass,
    )

    if is_focused:
        focused = recall_focused(trw_dir, query, max_results=effective_max)
        baseline = recall_baseline_high_impact(trw_dir, max_results=effective_max)
        extra["query"] = query
        extra["query_matched"] = len(focused)
        if not focused:
            # ``query_matched: 0`` alone is unreadable: the returned list is
            # then purely the impact-ranked baseline, which is
            # query-INDEPENDENT. Probe here (not later) — subsequent
            # session_start steps can initialize the embedder and would make
            # the probe misreport what this recall actually ran.
            extra["query_advisory"] = focused_recall_zero_match_advisory()
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
                    e for e in fresh if str(e.get("created", "")) >= cutoff and str(e.get("id", "")) not in seen_ids
                ]
                # Fresh entries are highest-priority context for the current
                # session; surface them before the high-impact baseline.
                learnings = fresh_additions + learnings
                learnings = learnings[:effective_max]

    # PRD-CORE-257-FR03: the recall side effects are a bounded ledger step, so a
    # streak that reaches the bound runs them despite pressure.
    decision = step_deferral_decision(
        trw_dir,
        "side_effects",
        under_pressure=compact_for_pressure,
        max_deferral_hours=config.session_start_max_deferral_hours,
    )
    if not decision.defer:
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

    tracking = record_session_start_surfaces(
        trw_dir,
        [str(entry.get("id", "")) for entry in learnings if entry.get("id")],
        defer=decision.defer,
    )
    if tracking.recorded:
        # FR09: the receipt is written for ids that were ACTUALLY recorded. It
        # used to be written unconditionally, from a return value that looked
        # identical whether the write happened or was skipped.
        log_recall_receipt(trw_dir, query if is_focused else "*", tracking.ids)
        record_completion(trw_dir, "side_effects")
    else:
        from trw_mcp.state.memory_pressure import writer_pressure_details

        advisory = writer_pressure_details(census, decision)
        # ``detail`` is the one free-text slot the compact fold admits
        # (``_DEFERRED_SHAPE_KEYS``); any other key here would keep this block
        # from folding and ship it verbatim on every pressured session start
        # (review finding P1, 2026-09-05).
        advisory["detail"] = ", ".join(tracking.deferred_effects)
        extra["side_effects_deferred"] = advisory
        logger.warning(
            "session_start_side_effects_deferred",
            reason="writer_pressure",
            writer_count=census.writer_count,
            peer_writer_count=census.peer_writer_count,
            threshold=census.threshold,
            deferral_age_hours=decision.age_hours,
            deferred_effects=list(tracking.deferred_effects),
            learning_count=len(learnings),
        )

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
            writer_count=census.writer_count,
            peer_writer_count=census.peer_writer_count,
            threshold=census.threshold,
            original_count=pre_compact_count,
            returned_count=len(learnings),
        )

    auto_recalled: list[AutoRecalledItemDict] = []
    return learnings, auto_recalled, extra


from trw_mcp.tools._session_recall_phase import (
    _phase_contextual_recall as _phase_contextual_recall,
)
