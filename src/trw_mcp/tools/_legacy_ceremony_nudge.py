"""Quarantined legacy nudge helpers kept for offline compatibility only."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import structlog

from trw_mcp.models.config import TRWConfig
from trw_mcp.state.ceremony_nudge import NudgeContext
from trw_mcp.state.ceremony_progress import CeremonyState
from trw_mcp.state.persistence import FileStateReader

logger = structlog.get_logger(__name__)


def _hydrate_files_modified(state: CeremonyState, trw_dir: Path) -> None:
    """Hydrate legacy nudge state from file-modified events."""

    try:
        from trw_mcp.state._paths import find_active_run

        run_dir = find_active_run()
        if run_dir is None:
            return

        events_path = Path(run_dir) / "meta" / "events.jsonl"
        if not events_path.exists():
            return

        events = FileStateReader().read_jsonl(events_path)
        threshold = state.last_checkpoint_ts or ""
        state.files_modified_since_checkpoint = sum(
            1
            for event in events
            if str(event.get("type", "")) == "file_modified" and str(event.get("ts", "")) > threshold
        )
    except (ImportError, OSError, RuntimeError, TypeError, ValueError):
        logger.warning(
            "hydrate_files_modified_failed",
            component="legacy_nudge",
            op="hydrate_files_modified",
            outcome="fail_open",
            exc_info=True,
        )


def log_nudge_event(
    events_path: Path,
    learning_id: str,
    phase: str,
    is_fallback: bool,
    turn: int = 0,
    surface_type: str = "nudge",
) -> None:
    """Log a legacy nudge_shown event."""

    try:
        from trw_mcp.state.persistence import FileEventLogger, FileStateWriter

        writer = FileStateWriter()
        FileEventLogger(writer).log_event(
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
        logger.debug("nudge_event_logged", learning_id=learning_id, phase=phase, fallback=is_fallback)
    except (OSError, RuntimeError, TypeError, ValueError):
        logger.warning(
            "nudge_event_log_failed",
            component="legacy_nudge",
            op="log_nudge_event",
            outcome="fail_open",
            exc_info=True,
        )


def _resolve_bandit_selector(effective_dir: Path) -> object | None:
    """Best-effort bandit loader for legacy nudge selection."""

    try:
        from trw_memory.bandit import BanditSelector

        bandit_state_path = effective_dir / "meta" / "bandit_state.json"
        if not bandit_state_path.exists():
            return None
        return BanditSelector.from_json(bandit_state_path.read_text(encoding="utf-8"))
    except (ImportError, OSError, RuntimeError, ValueError, TypeError):
        logger.debug(
            "legacy_nudge_bandit_unavailable",
            component="legacy_nudge",
            op="resolve_bandit",
            outcome="fail_open",
            exc_info=True,
        )
        return None


def _append_learning_tip(
    response: dict[str, object],
    nudge: str,
    learning_id: str,
    summary: str,
    burst_items: list[dict[str, object]],
) -> None:
    """Append the selected learning tip to the legacy nudge text."""

    if not summary:
        return
    tip = f"\nTIP: {summary}"
    if learning_id:
        tip += f" (id={learning_id})"
    for burst_item in burst_items:
        burst_line = str(burst_item.get("nudge_line") or burst_item.get("summary", ""))[:80]
        if burst_line:
            tip += f"\n  - {burst_line}"
    response["ceremony_status"] = nudge + tip


def _record_nudge_side_effects(
    *,
    config: TRWConfig,
    effective_dir: Path,
    learning_id: str,
    phase: str,
    is_fallback: bool,
    candidates: list[dict[str, object]],
    record_nudge_shown: Callable[[Path, str, str], None],
    find_active_run: Callable[[], Path | None],
) -> None:
    """Best-effort telemetry side-effects for a selected legacy nudge."""

    try:
        record_nudge_shown(effective_dir, learning_id, phase)
    except (OSError, RuntimeError, ValueError, TypeError):
        logger.warning(
            "nudge_dedup_record_failed",
            component="legacy_nudge",
            op="record_nudge_shown",
            outcome="fail_open",
            exc_info=True,
        )

    try:
        run_dir = find_active_run()
        events_path = (
            run_dir / "meta" / "events.jsonl"
            if run_dir is not None
            else effective_dir / config.context_dir / "session-events.jsonl"
        )
        log_nudge_event(
            events_path,
            learning_id=learning_id,
            phase=phase,
            is_fallback=is_fallback,
        )
    except (OSError, RuntimeError, ValueError, TypeError):
        logger.warning(
            "nudge_event_emit_failed",
            component="legacy_nudge",
            op="emit_nudge_event",
            outcome="fail_open",
            exc_info=True,
        )

    try:
        from trw_mcp.state.surface_tracking import log_surface_event

        log_surface_event(
            effective_dir,
            learning_id=learning_id,
            surface_type="nudge",
            phase=phase,
        )
    except (ImportError, OSError, RuntimeError, ValueError, TypeError):
        logger.warning(
            "nudge_surface_log_failed",
            component="legacy_nudge",
            op="surface_log",
            outcome="fail_open",
            exc_info=True,
        )

    try:
        from trw_mcp.state.propensity_log import log_selection

        candidate_ids = [str(candidate.get("id", "")) for candidate in candidates if candidate.get("id")]
        log_selection(
            effective_dir,
            selected=learning_id,
            candidate_set=candidate_ids,
            context_phase=phase,
            exploration=is_fallback,
        )
    except (ImportError, OSError, RuntimeError, ValueError, TypeError):
        logger.warning(
            "nudge_propensity_log_failed",
            component="legacy_nudge",
            op="propensity_log",
            outcome="fail_open",
            exc_info=True,
        )


def append_ceremony_nudge(
    response: dict[str, object],
    trw_dir: Path | None = None,
    available_learnings: int = 0,
    context: NudgeContext | None = None,
) -> dict[str, object]:
    """Append a legacy ceremony nudge to a response dict."""

    try:
        from trw_mcp.models.config import get_config
        from trw_mcp.state._nudge_rules import select_nudge_learning
        from trw_mcp.state._paths import find_active_run, resolve_trw_dir
        from trw_mcp.state.ceremony_nudge import (
            _highest_priority_pending_step,
            compute_nudge,
            compute_nudge_minimal,
            increment_nudge_count,
            read_ceremony_state,
            record_nudge_shown,
        )
        from trw_mcp.state.memory_adapter import recall_learnings

        config = get_config()
        if not config.effective_nudge_enabled:
            logger.debug("surface_gated", surface="nudge")
            return response

        effective_dir = trw_dir if trw_dir is not None else resolve_trw_dir()
        state = read_ceremony_state(effective_dir)
        _hydrate_files_modified(state, effective_dir)

        nudge = (
            compute_nudge_minimal(state, available_learnings=available_learnings)
            if config.effective_ceremony_mode == "light"
            else compute_nudge(state, available_learnings=available_learnings, context=context)
        )
        response["ceremony_status"] = nudge

        pending = _highest_priority_pending_step(state)
        if pending:
            try:
                increment_nudge_count(effective_dir, pending)
            except (OSError, RuntimeError, ValueError, TypeError):
                logger.warning(
                    "legacy_nudge_count_increment_failed",
                    component="legacy_nudge",
                    op="increment_nudge_count",
                    outcome="fail_open",
                    step=pending,
                    exc_info=True,
                )

        if available_learnings <= 0 or config.effective_ceremony_mode == "light":
            logger.debug("append_ceremony_nudge", phase=state.phase, has_nudge=len(str(nudge)) > 0)
            return response

        candidates = recall_learnings(
            effective_dir,
            query="*",
            min_impact=0.5,
            max_results=10,
            compact=True,
        )
        burst_items: list[dict[str, object]] = []
        selected, is_fallback = select_nudge_learning(
            state,
            candidates,
            state.phase,
            bandit=_resolve_bandit_selector(effective_dir),
            previous_phase=getattr(state, "previous_phase", ""),
            client_class="full_mode",
            burst_items=burst_items,
        )

        if not selected:
            logger.debug("append_ceremony_nudge", phase=state.phase, has_nudge=len(str(nudge)) > 0)
            return response

        learning_id = str(selected.get("id", ""))
        summary = str(selected.get("summary", ""))[:80]
        if isinstance(nudge, str) and summary:
            _append_learning_tip(response, nudge, learning_id, summary, burst_items)

        if not learning_id:
            logger.debug("append_ceremony_nudge", phase=state.phase, has_nudge=len(str(nudge)) > 0)
            return response

        _record_nudge_side_effects(
            config=config,
            effective_dir=effective_dir,
            learning_id=learning_id,
            phase=state.phase,
            is_fallback=is_fallback,
            candidates=candidates,
            record_nudge_shown=record_nudge_shown,
            find_active_run=find_active_run,
        )

        logger.debug("append_ceremony_nudge", phase=state.phase, has_nudge=len(str(response["ceremony_status"])) > 0)
    except (ImportError, OSError, RuntimeError, ValueError, TypeError):
        logger.warning(
            "append_ceremony_nudge_failed",
            component="legacy_nudge",
            op="append_ceremony_nudge",
            outcome="fail_open",
            exc_info=True,
        )
    return response
