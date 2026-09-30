"""Learning-nudge content selector — extracted from _ceremony_status.py.

Belongs to the ``_ceremony_status.py`` facade. Re-exported there for
back-compat.

Single helper:
- ``_try_learning_nudge_content`` — produces learning nudge content for live
  MCP responses in recall order, after phase and domain contextualisation.
  Honors the dedup state machine and emits structured surface_event
  telemetry.

Extracted as DIST-243 batch 51 to push parent ``_ceremony_status.py``
closer to the 350 effective-LOC ceiling.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from contextlib import suppress
from pathlib import Path

import structlog

from trw_mcp.state._ceremony_state_model import NudgeContext
from trw_mcp.state._origin_project import is_verified, nudge_eligible_pool
from trw_mcp.state.ceremony_progress import CeremonyState
from trw_mcp.tools._ceremony_status_helpers import (
    _contextualize_candidates,
    _deterministic_fallback_text,
    _select_deterministic_fallback_learning,
)

logger = structlog.get_logger(__name__)

#: ``(needles, excluded ids)``: what the learnings pool must relate to, and what it must never surface.
Relevance = tuple[tuple[str, ...], frozenset[str]]


def nudge_relevance(
    trw_dir: Path,
    context: NudgeContext | None,
    response: Mapping[str, object] | None,
) -> Relevance | None:
    """What the learnings pool must relate to on a ``trw_learn`` or ``trw_status`` response (E2E-INC-010).

    ``None`` leaves the pool unfiltered (every other call). Otherwise a learning is drawn only when
    it names one of ``needles`` by W2's whole-token rule (``_learnings_collector._names_any``: its
    text, tags or anchors), and never when its id is in ``exclude``:

    - ``trw_learn``: the tags and anchored files of the entry just written; that entry is excluded.
    - ``trw_status``: the active run's task and the files modified in the working tree.

    Nothing related means the pool yields no nudge for this call; the other pools still fire.
    """
    from trw_mcp.state._ceremony_state_model import ToolName

    tool = context.tool_name if context is not None else ""
    body = response or {}
    if tool == ToolName.LEARN:
        written = str(body.get("learning_id") or "")
        row = _written_row(trw_dir, written)
        tags = row.get("tags") if row else None
        anchors = row.get("anchors") if row else None
        needles: list[str] = [str(t) for t in tags] if isinstance(tags, list) else []
        for anchor in anchors if isinstance(anchors, list) else []:
            file = str(anchor.get("file", "")) if isinstance(anchor, dict) else str(anchor)
            needles.extend(n for n in (file, os.path.basename(file)) if n)
        return _clean(needles), frozenset({written} if written else ())
    if tool == ToolName.STATUS:
        from trw_mcp.state.recall_context import build_recall_context

        needles = []
        task = str(body.get("task") or "")
        if task and task != "unknown":
            needles.append(task)
        with suppress(Exception):  # justified: fail-open, no working-tree context means fewer needles
            context_obj = build_recall_context(trw_dir, "*")
            needles.extend(str(f).strip() for f in getattr(context_obj, "modified_files", []) or [])
        return _clean(needles), frozenset()
    return None


def _clean(needles: list[str]) -> tuple[str, ...]:
    """Stripped, de-duplicated, non-blank needles: a whitespace tag must not match every learning (codex r1 KI)."""
    return tuple(dict.fromkeys(n.strip() for n in needles if n.strip()))


def _written_row(trw_dir: Path, learning_id: str) -> dict[str, object] | None:
    """The just-written learning's tags and anchored files, read through the store (never the YAML mirror)."""
    if not learning_id:
        return None
    try:
        from trw_mcp.state._recall_admission import fetch_admitted

        rows = fetch_admitted(trw_dir, [learning_id])
    except Exception:  # trw-fail-silent-allow: fail-open, an unreadable row means nothing is related (logged at debug)
        logger.debug("nudge_relevance_row_failed", exc_info=True)
        return None
    if not rows:
        return None
    entry = rows[0]
    return {"tags": list(entry.tags), "anchors": [str(getattr(a, "file", a)) for a in entry.anchors]}


def _related(candidates: list[dict[str, object]], relevance: Relevance) -> list[dict[str, object]]:
    from trw_mcp.tools._learnings_collector import _names_any

    needles, exclude = relevance
    return [c for c in candidates if str(c.get("id", "")) not in exclude and _names_any(c, needles)]


def _try_learning_nudge_content(trw_dir: Path, state: CeremonyState, relevance: Relevance | None = None) -> str | None:
    """Attempt to produce learning nudge content, in recall order.

    Picks the first candidate with renderable text after the phase and domain
    contextualisation (PRD-CORE-303 FR01: no backend weight reorders it). With a
    *relevance* (see :func:`nudge_relevance`) only learnings related to the call are candidates.
    """
    try:
        from trw_mcp.state._ceremony_progress_state import is_nudge_eligible, record_nudge_shown
        from trw_mcp.state.recall_factories import recall_for_nudge_pool
        from trw_mcp.state.surface_tracking import log_surface_event
        from trw_mcp.tools._recall_impl import build_recall_context

        client_profile_name = ""
        model_family = "generic"
        nudge_variant_label = ""
        try:
            # PRD-FIX-085 FR03: use the per-process get_config() singleton
            from trw_mcp.models.config import get_config

            cfg = get_config()
            client_profile_name = getattr(cfg.client_profile, "client_id", "") or ""
            model_family = cfg.model_family or "generic"
            nudge_variant_label = cfg.nudge_variant or ""
        except Exception:  # justified: config may not be available, use defaults
            logger.debug("ceremony_status_config_defaults", exc_info=True)

        candidates = recall_for_nudge_pool(trw_dir, query="*", min_impact=0.5, max_results=20)
        if relevance is not None:
            candidates = _related(candidates, relevance)
        if not candidates:
            return None

        # Dedup FIRST: filter candidates already shown in current phase (P1 fix).
        # Provenance narrowing runs after it, never before — narrowing to a
        # single already-shown row would empty the unseen set and send the
        # fallback below straight back to the row the dedup exists to avoid.
        eligible_candidates = [c for c in candidates if is_nudge_eligible(state, str(c.get("id", "")), state.phase)]
        if not eligible_candidates:
            eligible_candidates = candidates

        # PRD-CORE-278 FR09: narrow the POOL before any selection runs. A sort
        # cannot constrain the contextual selection below, and this is
        # the one slot that speaks with the framework's voice: on 2026-09-16 it
        # quoted a dead-code claim about a different repository (L-XIhp) and a
        # repo-state claim that had become false (sub_n98TiMz4ioCKf5Lj).
        # Attribution first, then verification.
        wider_pool = eligible_candidates
        eligible_candidates = nudge_eligible_pool(eligible_candidates)

        recall_context = build_recall_context(trw_dir, "*")
        is_transition = bool(state.previous_phase and state.previous_phase != state.phase)
        selection_candidates = _contextualize_candidates(
            eligible_candidates,
            recall_context=recall_context,
            is_transition=is_transition,
        )
        if not selection_candidates:
            selection_candidates = eligible_candidates

        selected_learning = _select_deterministic_fallback_learning(selection_candidates)
        if selected_learning is None:
            return None

        content = _deterministic_fallback_text(selected_learning)
        if not content:
            # The preferred row has no renderable text. Narrowing the pool must
            # not be able to SILENCE a surface that would otherwise have spoken,
            # so retry over every OTHER candidate the dedup left eligible.
            rejected_id = str(selected_learning.get("id", ""))
            remainder = [row for row in wider_pool if str(row.get("id", "")) != rejected_id]
            selected_learning = _select_deterministic_fallback_learning(remainder) if remainder else None
            content = _deterministic_fallback_text(selected_learning) if selected_learning is not None else ""
        if not content or selected_learning is None:
            return None
        if not is_verified(selected_learning):
            # The claim carries its own epistemic status rather than borrowing
            # the framework's. Abstaining instead would silence the surface
            # entirely: every row in every store observed on 2026-09-16 carried
            # verification_status "unknown" (PRD-CORE-278 Open Question 6).
            content = f"Unverified: {content}"

        learning_id = str(selected_learning.get("id", ""))
        if learning_id:
            try:
                record_nudge_shown(trw_dir, learning_id, state.phase, turn=state.tool_call_counter)
            except Exception:  # justified: fail-open
                logger.debug("record_nudge_shown_failed", exc_info=True)

            with suppress(Exception):  # justified: fail-open per NFR02
                logger.info(
                    "nudge_shown",
                    pool="learnings",
                    messenger="standard",
                    learning_id=learning_id,
                    phase=state.phase,
                    client_id=client_profile_name,
                    turn=state.tool_call_counter,
                )

            try:
                from trw_mcp.state._session_id import resolve_effective_session_id

                # Work targets #4/#6: stamp live timing + A/B arm/messenger only on
                # genuine nudges (not phase-transition surfaces). For transitions
                # the nudge-only fields stay empty/None so log_surface_event omits
                # them, keeping phase_transition events shape-compatible.
                #
                # Ledger UF-023: this branch previously stamped
                # ``_highest_priority_pending_step(state) or "session_start"``. The
                # learnings pool renders a prior-learning caution, which targets no
                # ceremony step at all — so the step and the timing fields it feeds
                # are omitted rather than defaulted to a step this nudge never
                # mentioned. ``resolve_nudge_target_step`` is the single rule.
                nudge_step = ""
                is_timely: bool | None = None
                step_distance: int | None = None
                variant_label = ""
                messenger_label = ""
                if not is_transition:
                    variant_label = nudge_variant_label
                    messenger_label = "standard"
                log_surface_event(
                    trw_dir,
                    learning_id=learning_id,
                    surface_type="phase_transition" if is_transition else "nudge",
                    phase=state.phase,
                    client_profile=client_profile_name,
                    model_family=model_family,
                    session_id=resolve_effective_session_id(trw_dir),
                    nudge_step=nudge_step,
                    is_timely=is_timely,
                    step_distance_from_call=step_distance,
                    nudge_variant=variant_label,
                    messenger=messenger_label,
                )
            except Exception:  # justified: fail-open
                logger.debug("surface_event_log_failed", exc_info=True)

        logger.info(
            "learning_nudge_selected",
            selected=learning_id,
            phase=state.phase,
            is_transition=is_transition,
        )
        return content
    except Exception:  # trw-fail-silent-allow: fail-open, an optional nudge must never block the tool response it decorates; None means no nudge line
        logger.debug("learning_nudge_content_failed", exc_info=True)
        return None
