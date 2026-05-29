"""Learning-injection candidate selectors — extracted from ceremony_nudge.py for module-size compliance.

Belongs to the ``ceremony_nudge.py`` facade. Re-exported there for back-compat
with `_ceremony_status.py` which imports `select_contextual_nudge_content` and
`select_learning_injection_content` via the parent.

Four helpers:
- ``_select_learning_injection_candidate`` — pick a learning entry by repo recall
- ``_contextual_next_step_message`` — action-line composer for contextual nudge
- ``select_contextual_nudge_content`` — public: return (content, learning_id, target_file)
- ``select_learning_injection_content`` — public: same shape, learning-injection branch
"""

from __future__ import annotations

from pathlib import Path

import structlog

from trw_mcp.state._nudge_state import CeremonyState, NudgeContext

logger = structlog.get_logger(__name__)


def _select_learning_injection_candidate(
    state: CeremonyState,
    trw_dir: Path,
    *,
    skip_phase_duplicates: bool = False,
) -> tuple[dict[str, object] | None, str | None]:
    """Return the selected learning entry and active target filename."""
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
            seen_ids.add(learning_id)
            if skip_phase_duplicates and learning_id in state.nudge_history:
                phases_shown = state.nudge_history[learning_id].get("phases_shown", [])
                if state.phase in phases_shown:
                    try:
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
                    except Exception:  # justified: fail-open per NFR02
                        pass
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


def select_learning_injection_content(
    state: CeremonyState,
    trw_dir: Path,
    *,
    skip_phase_duplicates: bool = False,
) -> tuple[str | None, str | None, str | None]:
    """Return rendered content, learning id, and target file for the injection branch."""
    from trw_mcp.state._nudge_status_lines import _build_minimal_status_line
    from trw_mcp.state.ceremony_nudge import _MINIMAL_HEADER

    try:
        selected_learning, target_label = _select_learning_injection_candidate(
            state,
            trw_dir,
            skip_phase_duplicates=skip_phase_duplicates,
        )
        if selected_learning is None:
            return None, None, target_label

        learning_id = str(selected_learning.get("id", "")).strip()
        summary = str(selected_learning.get("summary", "")).strip()
        clipped_summary = summary[:120] + ("..." if len(summary) > 120 else "")
        status_line = _build_minimal_status_line(state)
        message = (
            f"{_MINIMAL_HEADER}\n"
            f"{status_line}\n"
            f"[!] Past learning on {target_label}: {clipped_summary}. Source: {learning_id}."
        )
        rendered = message if len(message) <= 400 else message[:397] + "..."
        return rendered, learning_id, target_label
    except Exception:  # justified: fail-open -- recall issues must not break ceremony status
        logger.debug("select_learning_injection_content_failed", exc_info=True)
        return None, None, None
