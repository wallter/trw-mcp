"""Learning-injection candidate selectors — extracted from ceremony_nudge.py for module-size compliance.

Belongs to the ``ceremony_nudge.py`` facade. Re-exported there for back-compat
with `_ceremony_status.py` which imports `select_contextual_nudge_content`
via the parent.

Three helpers:
- ``_select_learning_injection_candidate`` — pick a learning entry by repo recall
- ``_contextual_next_step_message`` — action-line composer for contextual nudge
- ``select_contextual_nudge_content`` — public: return (content, learning_id, target_file)

The candidate selector keeps its historical name but serves the live
contextual path only; the ``learning_injection`` messenger it was named for
was retired by PRD-CORE-241-FR07.
"""

from __future__ import annotations

from contextlib import suppress
from pathlib import Path

import structlog

from trw_mcp.state._nudge_state import CeremonyState, NudgeContext

logger = structlog.get_logger(__name__)
_LEARNING_INJECTION_MIN_SCORE = 0.70

# PRD-CORE-294 FR04/FR06: per-session budget and cooldown for the
# transition-nudge selector (distinct from the learning-injection pool
# above, which has only phase-scoped dedup).
_TRANSITION_BUDGET = 6
_TRANSITION_COOLDOWN_CALLS = 3
_TRANSITION_SESSION_CAP = 2048


def nudge_may_recall(context: NudgeContext | None) -> bool:
    """False for the nudge on a ``trw_session_start`` or ``trw_recall`` response (PRD-CORE-294 FR02).

    Either response already carries the call's one recall, so a learning drawn
    by the nudge would repeat it at the cost of a second recall.
    """
    from trw_mcp.state._ceremony_state_model import ToolName

    return context is None or context.tool_name not in (ToolName.SESSION_START, ToolName.RECALL)


def transition_selector_enabled(trw_dir: Path) -> bool:
    """``transition_nudges_enabled`` for this workspace: the one gate over every FR04/FR06 line (FR05's off arm)."""
    from trw_mcp.state._helpers import load_project_config

    return load_project_config(trw_dir).transition_nudges_enabled


def select_transition_line(
    trw_dir: Path,
    *,
    session_key: str,
    candidates: list[tuple[str, str]],
) -> str | None:
    """Pick the first not-yet-shown candidate line for ``session_key``.

    ``candidates`` is an ordered ``(nudge_id, line)`` list — the caller may
    prepend higher-priority ids ahead of lower-priority ones. Returns
    ``None`` when ``transition_nudges_enabled`` is off, when the session's budget (``_TRANSITION_BUDGET`` lines) is
    exhausted, when fewer than ``_TRANSITION_COOLDOWN_CALLS`` tool calls have
    elapsed since this session's last transition line, or when every
    candidate id has already been shown to this session. Fail-open: any
    exception (state I/O, malformed persisted entry) returns ``None``.
    """
    if not session_key or not candidates or not transition_selector_enabled(trw_dir):
        return None
    try:
        from trw_mcp.state._ceremony_progress_state import (
            _state_rmw,
            read_ceremony_state,
            write_ceremony_state,
        )

        with _state_rmw(trw_dir):
            state = read_ceremony_state(trw_dir)
            entry = state.transition_nudges.get(session_key)
            shown_ids_raw = entry.get("shown_ids", []) if entry else []
            shown_ids = (
                {item for item in shown_ids_raw if isinstance(item, str)} if isinstance(shown_ids_raw, list) else set()
            )
            count_raw = entry.get("count", 0) if entry else 0
            count = int(count_raw) if isinstance(count_raw, (int, float)) else 0
            if count >= _TRANSITION_BUDGET:
                return None
            if entry is not None:
                last_counter_raw = entry.get("last_counter", 0)
                last_counter = int(last_counter_raw) if isinstance(last_counter_raw, (int, float)) else 0
                if state.tool_call_counter - last_counter < _TRANSITION_COOLDOWN_CALLS:
                    return None

            selected: tuple[str, str] | None = None
            for nudge_id, line in candidates:
                if nudge_id not in shown_ids:
                    selected = (nudge_id, line)
                    break
            if selected is None:
                return None

            nudge_id, line = selected
            shown_ids.add(nudge_id)
            state.transition_nudges[session_key] = {
                "shown_ids": sorted(shown_ids),
                "count": count + 1,
                "last_counter": state.tool_call_counter,
            }
            while len(state.transition_nudges) > _TRANSITION_SESSION_CAP:
                oldest_key = next(iter(state.transition_nudges))
                state.transition_nudges.pop(oldest_key)
            write_ceremony_state(trw_dir, state)
            return line
    except Exception:  # trw-fail-silent-allow: fail-open -- selector state issues must not break tool responses
        logger.debug("select_transition_line_failed", exc_info=True)
        return None


def get_shown_transition_ids(trw_dir: Path, *, session_key: str) -> set[str]:
    """Return the transition-nudge ids already shown to this session.

    Read-only counterpart to :func:`select_transition_line`'s bookkeeping --
    used by PRD-CORE-294 FR04(c) to drop deliver-time learning candidates
    this session already saw via an earlier transition. Fail-open: any
    exception (state I/O, malformed persisted entry) returns an empty set.
    """
    if not session_key:
        return set()
    try:
        from trw_mcp.state._ceremony_progress_state import read_ceremony_state

        state = read_ceremony_state(trw_dir)
        entry = state.transition_nudges.get(session_key)
        shown_ids_raw = entry.get("shown_ids", []) if entry else []
        return {item for item in shown_ids_raw if isinstance(item, str)} if isinstance(shown_ids_raw, list) else set()
    except Exception:  # trw-fail-silent-allow: fail-open -- selector state issues must not break tool responses
        logger.debug("get_shown_transition_ids_failed", exc_info=True)
        return set()


def _select_learning_injection_candidate(
    state: CeremonyState,
    trw_dir: Path,
    *,
    skip_phase_duplicates: bool = False,
    recall: bool = True,
) -> tuple[dict[str, object] | None, str | None]:
    """Return the selected learning entry and active target filename; ``recall=False`` returns only the filename."""
    # Lazy-import parent helpers to avoid circular dep with ceremony_nudge.py.
    from trw_mcp.state.ceremony_nudge import _emit_debug_capture_event
    from trw_mcp.state.learning_injection import infer_domain_tags
    from trw_mcp.state.recall_context import build_recall_context

    recall_context = build_recall_context(trw_dir, "*")
    modified_files_raw = getattr(recall_context, "modified_files", []) if recall_context is not None else []
    modified_files = [str(path).strip() for path in modified_files_raw if str(path).strip()]
    if not modified_files:
        return None, None

    target_path = Path(modified_files[0])
    target_label = target_path.name
    if not recall:
        return None, target_label
    query = " ".join(
        part
        for part in (
            target_path.stem,
            target_path.parent.name,
        )
        if part and part != "."
    ).strip()
    domain_tags = sorted(infer_domain_tags([target_path.as_posix()]))
    attempts = (
        (query or target_label, domain_tags or None),
        ("*", domain_tags or None),
        (query or target_label, None),
    )

    selected_learning: dict[str, object] | None = None
    seen_ids: set[str] = set()

    # PRD-FIX-085 FR05: use named factory.
    from trw_mcp.state.recall_factories import recall_for_nudge_pool

    for attempt_query, attempt_tags in attempts:
        learnings = recall_for_nudge_pool(
            trw_dir,
            query=attempt_query,
            tags=attempt_tags,
            min_impact=0.5,
            max_results=8,
        )
        for learning in learnings:
            learning_id = str(learning.get("id", "")).strip()
            summary = str(learning.get("summary", "")).strip()
            if not learning_id or not summary or learning_id in seen_ids:
                continue
            # Filter LOW-scored candidates only. Entries from the keyword
            # recall path carry no score field at all (the adapter emits
            # combined_score when ranking ran) — a missing score must pass,
            # not default to 0.0, or every unscored entry is silently dropped
            # and the learning-injection pool goes permanently dark.
            score_raw = learning.get("score", learning.get("similarity", learning.get("combined_score")))
            if score_raw is not None:
                try:
                    score = float(score_raw)  # type: ignore[arg-type]
                except (TypeError, ValueError):
                    pass
                else:
                    if score < _LEARNING_INJECTION_MIN_SCORE:
                        continue
            seen_ids.add(learning_id)
            if skip_phase_duplicates and learning_id in state.nudge_history:
                phases_shown = state.nudge_history[learning_id].get("phases_shown", [])
                if state.phase in phases_shown:
                    with suppress(Exception):  # justified: fail-open per NFR02
                        from trw_mcp.state._nudge_rules import _resolve_client_id

                        structlog.get_logger(__name__).debug(
                            "nudge_skipped",
                            reason="phase_dedup",
                            pool="learning_injection",
                            learning_id=learning_id,
                            client_id=_resolve_client_id(),
                        )
                        _emit_debug_capture_event(
                            "nudge_skipped",
                            reason="phase_dedup",
                            pool="learning_injection",
                            learning_id=learning_id,
                            client_id=_resolve_client_id(),
                        )
                    continue
            selected_learning = learning
            break
        if selected_learning is not None:
            break

    return selected_learning, target_label


def _contextual_next_step_message(
    state: CeremonyState,
    *,
    target_label: str | None,
    context: NudgeContext | None = None,
) -> str:
    """Build the action-oriented line for the contextual messenger."""
    from trw_mcp.state._nudge_messages import _compute_urgency, _context_reactive_message
    from trw_mcp.state.ceremony_nudge import _STEP_RATIONALE, _highest_priority_pending_step

    pending = _highest_priority_pending_step(state)
    urgency = _compute_urgency(state, pending or "checkpoint")

    if context is not None:
        reactive = _context_reactive_message(context, state, urgency=urgency)
        if reactive:
            return reactive

    if pending == "session_start":
        return "NEXT: trw_session_start() — loads prior learnings and run state before more edits."
    if pending == "checkpoint":
        anchor = f" once {target_label} is stable" if target_label else " at the next stable milestone"
        return f"NEXT: trw_checkpoint(){anchor} — {_STEP_RATIONALE['checkpoint']}."
    if pending == "build_check":
        return f"NEXT: trw_build_check() — {_STEP_RATIONALE['build_check']} before review or deliver."
    if pending == "review":
        return f"NEXT: trw_review() — {_STEP_RATIONALE['review']} before deliver."
    if pending == "deliver":
        if state.learnings_this_session > 0:
            return (
                "NEXT: trw_deliver() — "
                f"{_STEP_RATIONALE['deliver']} and preserve {state.learnings_this_session} learning(s)."
            )
        return f"NEXT: trw_deliver() — {_STEP_RATIONALE['deliver']}."

    target_phrase = f" on {target_label}" if target_label else ""
    phase = state.phase or "current"
    return f"NEXT: continue the {phase} work{target_phrase}; trw_checkpoint() at the next stable milestone."


def select_contextual_nudge_content(
    state: CeremonyState,
    trw_dir: Path,
    *,
    context: NudgeContext | None = None,
    skip_phase_duplicates: bool = False,
    include_learning_caution: bool = True,
) -> tuple[str | None, str | None, str | None]:
    """Return contextual nudge content, optional learning id, and target file."""
    from trw_mcp.state._nudge_status_lines import _build_minimal_status_line
    from trw_mcp.state.ceremony_nudge import _MINIMAL_HEADER

    try:
        selected_learning, target_label = _select_learning_injection_candidate(
            state,
            trw_dir,
            skip_phase_duplicates=skip_phase_duplicates,
            recall=nudge_may_recall(context),
        )
        action_line = _contextual_next_step_message(state, target_label=target_label, context=context)
        status_line = _build_minimal_status_line(state)
        lines = [_MINIMAL_HEADER, status_line, action_line]

        learning_id: str | None = None
        if selected_learning is not None and include_learning_caution:
            learning_id = str(selected_learning.get("id", "")).strip() or None
            raw_caution = str(selected_learning.get("nudge_line") or selected_learning.get("summary") or "").strip()
            if raw_caution:
                clipped_caution = raw_caution[:120] + ("..." if len(raw_caution) > 120 else "")
                target_phrase = f" for {target_label}" if target_label else ""
                source_suffix = f" Source: {learning_id}." if learning_id else ""
                lines.append(f"Watch-out{target_phrase}: {clipped_caution}.{source_suffix}".replace("..", "."))

        rendered = "\n".join(line for line in lines if line)
        clipped = rendered if len(rendered) <= 400 else rendered[:397] + "..."
        return clipped, learning_id, target_label
    except Exception:  # justified: fail-open -- recall issues must not break ceremony status
        logger.debug("select_contextual_nudge_content_failed", exc_info=True)
        return None, None, None
