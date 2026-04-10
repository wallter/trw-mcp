"""Session recall helpers for ceremony.py — trw_session_start recall logic.

Extracted from _ceremony_helpers.py to keep modules under the 500-line gate.

Public API (re-exported by _ceremony_helpers.py):
- append_ceremony_nudge: inject ceremony nudge into a tool response
- perform_session_recalls: execute focused + baseline recalls, return merged results
- log_nudge_event: log a nudge_shown event to events.jsonl
- _phase_contextual_recall: phase-tag-aware recall for auto-recall feature
- _phase_to_tags: map framework phase to relevant learning tags
- _apply_antipattern_alerts: prepend alert prefix to matching learning summaries
"""

from __future__ import annotations

import contextlib
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
from trw_mcp.state._paths import resolve_trw_dir
from trw_mcp.state.ceremony_nudge import CeremonyState, NudgeContext, compute_nudge, read_ceremony_state
from trw_mcp.state.persistence import FileStateReader
from trw_mcp.state.receipts import log_recall_receipt

logger = structlog.get_logger(__name__)


# ── Helpers ─────────────────────────────────────────────────────────────


def _hydrate_files_modified(state: CeremonyState, trw_dir: Path) -> None:
    """Count file_modified events since last checkpoint and update state in-memory.

    The PostToolUse shell hook logs ``file_modified`` events to events.jsonl
    but cannot call Python to update ceremony-state.json.  This function
    bridges the gap by counting those events at nudge computation time.

    Mutates *state* in-place (memory only — does NOT write ceremony-state.json).
    Fail-open: on any error, state is left unchanged.
    """
    try:
        from trw_mcp.state._paths import find_active_run

        run_dir = find_active_run()
        if run_dir is None:
            return

        events_path = Path(run_dir) / "meta" / "events.jsonl"
        if not events_path.exists():
            return

        reader = FileStateReader()
        events = reader.read_jsonl(events_path)

        # Count file_modified events after the last checkpoint timestamp
        threshold = state.last_checkpoint_ts or ""
        count = 0
        for event in events:
            if str(event.get("type", "")) == "file_modified":
                event_ts = str(event.get("ts", ""))
                if event_ts > threshold:
                    count += 1

        state.files_modified_since_checkpoint = count
    except Exception:  # justified: fail-open — hydration must never break nudge
        logger.debug("hydrate_files_modified_failed", exc_info=True)


# ── FR01: Ceremony nudge injection ──────────────────────────────────────


def append_ceremony_nudge(
    response: dict[str, object],
    trw_dir: Path | None = None,
    available_learnings: int = 0,
    context: NudgeContext | None = None,
) -> dict[str, object]:
    """Append ceremony nudge to a tool response dict.

    Reads ceremony state, computes nudge, adds it under 'ceremony_status' key.
    Also wires nudge dedup (PRD-CORE-103-FR02), surface logging, and propensity
    logging when a learning-backed nudge is present.

    Fail-open: if anything fails, returns response unchanged.

    Args:
        response: The tool response dict to augment.
        trw_dir: Override the .trw directory (defaults to resolve_trw_dir()).
        available_learnings: Number of available learnings for nudge context.
        context: Optional NudgeContext for context-reactive messages (PRD-CORE-084).

    Returns:
        The response dict with 'ceremony_status' key added (or unchanged on error).
    """
    try:
        from trw_mcp.models.config import get_config
        from trw_mcp.state.ceremony_nudge import (
            _highest_priority_pending_step,
            compute_nudge_minimal,
            increment_nudge_count,
        )

        # PRD-CORE-125-FR01: Nudge gating -- skip entire nudge assembly
        # when nudges are disabled via config/profile.
        config = get_config()
        if not config.effective_nudge_enabled:
            return response

        effective_dir = trw_dir if trw_dir is not None else resolve_trw_dir()
        state = read_ceremony_state(effective_dir)

        # Hydrate files_modified_since_checkpoint from events.jsonl (PRD-CORE-124).
        # The PostToolUse hook logs file_modified events but cannot call Python
        # to update ceremony-state.json. We count them here at nudge time.
        _hydrate_files_modified(state, effective_dir)

        if config.effective_ceremony_mode == "light":
            nudge = compute_nudge_minimal(state, available_learnings=available_learnings)
        else:
            nudge = compute_nudge(state, available_learnings=available_learnings, context=context)
        response["ceremony_status"] = nudge
        # Increment nudge count for the pending step (tracks progressive urgency)
        pending = _highest_priority_pending_step(state)
        if pending:
            with contextlib.suppress(Exception):
                increment_nudge_count(effective_dir, pending)

        # PRD-CORE-103-FR02: Learning-backed nudge with dedup + telemetry
        # Embed a relevant learning tip in the nudge response when learnings exist
        if available_learnings > 0 and config.effective_ceremony_mode != "light":
            try:
                from trw_mcp.state._nudge_rules import select_nudge_learning
                from trw_mcp.state.memory_adapter import recall_learnings

                candidates = recall_learnings(
                    effective_dir,
                    query="*",
                    min_impact=0.5,
                    max_results=10,
                    compact=True,
                )

                # Load bandit state if available (CORE-105)
                bandit_instance = None
                try:
                    from trw_memory.bandit import BanditSelector

                    bandit_state_path = effective_dir / "meta" / "bandit_state.json"
                    if bandit_state_path.exists():
                        bandit_instance = BanditSelector.from_json(
                            bandit_state_path.read_text(encoding="utf-8")
                        )
                except (ImportError, Exception):  # noqa: S110  # justified: fail-open, bandit is optional
                    pass

                # Client class for withholding rates — intelligence code
                # (resolve_client_class) was extracted to backend in PRD-INFRA-052
                # and removed from trw-mcp in PRD-INFRA-054.  Use neutral default.
                _client_class = "full_mode"

                burst_items: list[dict[str, object]] = []
                selected, is_fallback = select_nudge_learning(
                    state,
                    candidates,
                    state.phase,
                    bandit=bandit_instance,
                    previous_phase=getattr(state, "previous_phase", ""),
                    client_class=_client_class,
                    burst_items=burst_items,
                )

                if selected:
                    sel_id = str(selected.get("id", ""))
                    sel_summary = str(selected.get("summary", ""))[:80]

                    # Append learning tip to ceremony_status
                    if isinstance(nudge, str) and sel_summary:
                        tip = f"\nTIP: {sel_summary}"
                        if sel_id:
                            tip += f" (id={sel_id})"
                        # PRD-CORE-105 P0: Render burst items for phase transitions
                        for burst_item in burst_items:
                            burst_line = str(
                                burst_item.get("nudge_line")
                                or burst_item.get("summary", "")
                            )[:80]
                            if burst_line:
                                tip += f"\n  - {burst_line}"
                        nudge = nudge + tip
                        response["ceremony_status"] = nudge

                    if sel_id:
                        # Record in nudge history for dedup
                        try:
                            from trw_mcp.state.ceremony_nudge import record_nudge_shown

                            record_nudge_shown(effective_dir, sel_id, state.phase)
                        except Exception:  # justified: fail-open
                            logger.warning("nudge_dedup_record_failed", exc_info=True)

                        # PRD-QUAL-058-FR04: Emit nudge_shown event to session events
                        try:
                            from trw_mcp.state._paths import find_active_run

                            _run = find_active_run()
                            if _run is not None:
                                _evt_path = _run / "meta" / "events.jsonl"
                            else:
                                _evt_path = effective_dir / config.context_dir / "session-events.jsonl"
                            log_nudge_event(
                                _evt_path,
                                learning_id=sel_id,
                                phase=state.phase,
                                is_fallback=is_fallback,
                            )
                        except Exception:  # justified: fail-open
                            logger.warning("nudge_event_emit_failed", exc_info=True)

                        # Surface logging for nudge channel (PRD-CORE-103-FR01)
                        try:
                            from trw_mcp.state.surface_tracking import log_surface_event

                            log_surface_event(
                                effective_dir,
                                learning_id=sel_id,
                                surface_type="nudge",
                                phase=state.phase,
                            )
                        except Exception:  # justified: fail-open
                            logger.warning("nudge_surface_log_failed", exc_info=True)

                        # Propensity logging (PRD-CORE-103-FR03)
                        try:
                            from trw_mcp.state.propensity_log import log_selection

                            candidate_ids = [str(c.get("id", "")) for c in candidates if c.get("id")]
                            log_selection(
                                effective_dir,
                                selected=sel_id,
                                candidate_set=candidate_ids,
                                context_phase=state.phase,
                                exploration=is_fallback,
                            )
                        except Exception:  # justified: fail-open
                            logger.warning("nudge_propensity_log_failed", exc_info=True)
            except Exception:  # justified: fail-open
                logger.warning("learning_nudge_selection_failed", exc_info=True)

        logger.debug(
            "append_ceremony_nudge",
            phase=state.phase,
            has_nudge=len(nudge) > 0,
        )
    except Exception:  # justified: fail-open — nudge injection must never raise or block
        logger.warning("append_ceremony_nudge_failed", exc_info=True)
    return response


# ── FR02: Nudge event logging (PRD-CORE-103) ─────────────────────────────


def log_nudge_event(
    events_path: Path,
    learning_id: str,
    phase: str,
    is_fallback: bool,
    turn: int = 0,
    surface_type: str = "nudge",
) -> None:
    """Log a nudge_shown event to events.jsonl for proximal reward detection.

    PRD-QUAL-058-FR04: Emits a structured nudge_shown event with the schema
    expected by proximal_reward.py and the eval pipeline's pre-analyzers.

    Fail-open: if anything fails, silently returns without raising.

    Args:
        events_path: Path to events.jsonl file.
        learning_id: ID of the learning that was surfaced.
        phase: Current ceremony phase when the nudge was shown.
        is_fallback: True if the learning was a fallback (all candidates were dedup-filtered).
        turn: Tool response number when the nudge was shown.
        surface_type: How the learning was surfaced ("nudge", "recall", etc).
    """
    try:
        from trw_mcp.state.persistence import FileEventLogger, FileStateWriter

        writer = FileStateWriter()
        event_logger = FileEventLogger(writer)
        event_logger.log_event(
            events_path,
            "nudge_shown",
            {
                "data": {
                    "learning_id": learning_id,
                    "phase": phase,
                    "turn": turn,
                    "surface_type": surface_type,
                },
                "learning_id": learning_id,
                "phase": phase,
                "fallback": is_fallback,
            },
        )
        logger.debug(
            "nudge_event_logged",
            learning_id=learning_id,
            phase=phase,
            fallback=is_fallback,
        )
    except Exception:  # justified: fail-open — nudge event logging must never raise
        logger.warning("nudge_event_log_failed", exc_info=True)


# ── Phase-contextual tag map (PRD-CORE-049) ──────────────────────────────

_PHASE_TAG_MAP: dict[str, list[str]] = {
    "research": ["architecture", "gotcha", "codebase"],
    "plan": ["architecture", "pattern", "dependency"],
    "implement": ["gotcha", "testing", "pattern"],
    "validate": ["testing", "build", "coverage"],
    "review": ["security", "performance", "maintainability"],
    "deliver": ["ceremony", "deployment", "integration"],
}


def _phase_to_tags(phase: str) -> list[str]:
    """Map a framework phase to relevant learning tags (PRD-CORE-049 FR02)."""
    return _PHASE_TAG_MAP.get(phase.lower(), [])


# ── Anti-pattern alert constants (R-06) ─────────────────────────────────

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


def _apply_antipattern_alerts(
    learnings: list[dict[str, object]],
    query: str,
    is_focused: bool,
) -> list[dict[str, object]]:
    """Prepend anti-pattern alert prefix to matching learning summaries.

    R-06: When the query suggests model/adapter/system work AND a learning's
    summary contains a known anti-pattern keyword, mutate the returned summary
    to include a prominent alert prefix.  This is a read-path mutation only --
    stored learnings are not modified.

    Args:
        learnings: List of learning dicts from recall.
        query: The user's recall query string.
        is_focused: Whether the query is a focused (non-wildcard) query.

    Returns:
        The (potentially mutated) learnings list.
    """
    if not is_focused or not learnings:
        return learnings

    query_lower = query.lower()
    has_system_keyword = any(kw in query_lower for kw in _SYSTEM_TASK_KEYWORDS)
    if not has_system_keyword:
        return learnings

    alert_prefix = "\u26a0 ANTI-PATTERN ALERT: "

    result: list[dict[str, object]] = []
    for entry in learnings:
        summary = str(entry.get("summary", "") or "")
        summary_lower = summary.lower()
        if any(kw in summary_lower for kw in _ANTIPATTERN_KEYWORDS):
            entry = {**entry, "summary": alert_prefix + summary}
        result.append(entry)

    return result


# ── Session-start helpers ────────────────────────────────────────────────


def perform_session_recalls(
    trw_dir: Path,
    query: str,
    config: TRWConfig,
    reader: FileStateReader,
) -> tuple[list[dict[str, object]], list[AutoRecalledItemDict], SessionRecallExtrasDict]:
    """Execute focused + baseline recalls, return merged results.

    Returns:
        Tuple of (main_learnings, auto_recalled, extra_fields):
          - main_learnings: merged + deduped list from focused/baseline recall
          - auto_recalled: always empty list (reserved for future use)
          - extra_fields: dict with query_matched, total_available, etc.
    """
    # PRD-CORE-125-FR03: Session-start recall gating -- skip recall when
    # session_start_recall_enabled is explicitly set to False.
    if config.session_start_recall_enabled is not None and not config.session_start_recall_enabled:
        logger.debug("session_recall_gated", reason="session_start_recall_enabled=False")
        return [], [], {}

    from trw_mcp.state.memory_adapter import recall_learnings as adapter_recall
    from trw_mcp.state.memory_adapter import update_access_tracking as adapter_update_access

    is_focused = query.strip() not in ("", "*")
    extra: SessionRecallExtrasDict = {}
    learnings: list[dict[str, object]] = []

    # FR05 (PRD-CORE-084): Cap recall results for light ceremony mode.
    effective_max = (
        min(config.recall_max_results, LIGHT_MODE_RECALL_CAP)
        if config.effective_ceremony_mode == "light"
        else config.recall_max_results
    )

    # Step 1: Core recall
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
            lid = str(entry.get("id", ""))
            if lid and lid not in seen_ids:
                seen_ids.add(lid)
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

    # Update access tracking
    matched_ids = [str(e.get("id", "")) for e in learnings if e.get("id")]
    adapter_update_access(trw_dir, matched_ids)
    log_recall_receipt(trw_dir, query if is_focused else "*", matched_ids)

    # PRD-CORE-103-FR01: Surface logging for session_start channel
    try:
        from trw_mcp.state.surface_tracking import log_surface_event

        for lid in matched_ids:
            log_surface_event(
                trw_dir,
                learning_id=lid,
                surface_type="session_start",
            )
    except Exception:  # justified: fail-open, surface logging must not block session start
        logger.warning("session_start_surface_log_failed", exc_info=True)

    extra["total_available"] = len(learnings)
    logger.debug(
        "session_recalls_complete",
        count=len(learnings),
        is_focused=is_focused,
    )

    # R-06: Anti-pattern alert — surface known wiring/facade/gap learnings
    # prominently when the query suggests model/system/adapter work.
    try:
        learnings = _apply_antipattern_alerts(learnings, query, is_focused)
    except Exception:  # justified: fail-open — anti-pattern alerts must never block recall
        logger.warning("antipattern_alert_failed", exc_info=True)

    # Phase-contextual auto-recall (PRD-CORE-049) — only when caller passes run context
    auto_recalled: list[AutoRecalledItemDict] = []

    return learnings, auto_recalled, extra


def _phase_contextual_recall(
    trw_dir: Path,
    query: str,
    config: TRWConfig,
    run_dir: Path | None,
    run_status: RunStatusDict | None,
) -> list[AutoRecalledItemDict]:
    """Execute phase-contextual auto-recall (PRD-CORE-049).

    Returns a list of auto-recalled learning summaries.
    """
    from trw_mcp.state.memory_adapter import recall_learnings as adapter_recall_ar

    is_focused = query.strip() not in ("", "*")
    query_tokens: list[str] = []
    if is_focused:
        query_tokens.extend(query.strip().split())

    phase_tags: list[str] | None = None
    phase: str = ""
    if run_dir is not None and run_status is not None:
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

    # PRD-CORE-116: Build RecallContext for phase-aware scoring
    context = RecallContext(current_phase=phase.upper() if phase else None)
    ranked = rank_by_utility(
        ar_entries,
        query_tokens,
        lambda_weight=config.recall_utility_lambda,
        context=context,
    )
    capped = ranked[: config.auto_recall_max_results]
    return [
        {
            "id": str(e.get("id", "")),
            "summary": str(e.get("summary", "")),
            "impact": float(str(e.get("impact", 0.0))),
        }
        for e in capped
    ]
