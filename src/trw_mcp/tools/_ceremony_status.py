"""Ceremony status helpers for live MCP tool responses."""

from __future__ import annotations

import os
from pathlib import Path

import structlog

from trw_mcp.state._paths import resolve_trw_dir
from trw_mcp.state.ceremony_progress import CeremonyState, read_ceremony_state

logger = structlog.get_logger(__name__)


def build_ceremony_status_line(state: CeremonyState) -> str:
    """Render a compact, deterministic summary of current ceremony progress."""
    parts = [
        "session_started" if state.session_started else "session_start_pending",
        f"phase={state.phase}",
        f"checkpoints={state.checkpoint_count}",
        f"learnings={state.learnings_this_session}",
    ]
    if state.build_check_result:
        parts.append(f"build={state.build_check_result}")
    if state.review_called:
        review_part = f"review={state.review_verdict or 'recorded'}"
        if state.review_p0_count:
            review_part = f"{review_part} p0={state.review_p0_count}"
        parts.append(review_part)
    if state.deliver_called:
        parts.append("deliver_called")
    return "; ".join(parts)


def _try_bandit_nudge_content(trw_dir: Path, state: CeremonyState) -> str | None:
    """Attempt to produce bandit-selected learning nudge content.

    Returns a nudge content string (may be multi-line on phase transition) or
    None when the bandit path is unavailable or produces no output. Always
    fail-open — never raises.
    """
    try:
        from trw_memory.bandit import BanditSelector
        from trw_mcp.state.bandit_policy import (
            WithholdingPolicy,
            render_nudge_content,
            resolve_client_class,
            select_nudge_learning_bandit,
        )
        from trw_mcp.state.memory_adapter import recall_learnings

        # Load bandit state (fail-open on missing file)
        bandit_state_path = trw_dir / "meta" / "bandit_state.json"
        if bandit_state_path.exists():
            raw = bandit_state_path.read_text(encoding="utf-8")
            bandit = BanditSelector.from_json(raw)
        else:
            bandit = BanditSelector()

        # Quick candidate recall (max 10, high-impact only to keep it fast)
        candidates = recall_learnings(
            trw_dir,
            query="*",
            min_impact=0.5,
            max_results=10,
            compact=True,
        )
        if not candidates:
            return None

        # Determine client class from config if available
        client_class = "full_mode"
        try:
            from trw_mcp.models.config import TRWConfig
            cfg = TRWConfig(trw_dir=str(trw_dir))
            client_class = resolve_client_class(cfg.client_profile.name)
        except Exception:  # justified: config may not be available, use default
            pass

        policy = WithholdingPolicy(client_class=client_class)

        selected_learnings, is_transition = select_nudge_learning_bandit(
            candidates,
            bandit,
            policy,
            phase=state.phase,
            previous_phase=state.previous_phase,
        )

        if not selected_learnings:
            return None

        content = render_nudge_content(selected_learnings, is_transition)
        if not content:
            return None

        # Persist updated bandit state atomically (temp-file + rename pattern)
        try:
            bandit_state_path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = bandit_state_path.with_suffix(f".tmp.{os.getpid()}")
            tmp_path.write_text(bandit.to_json(), encoding="utf-8")
            os.rename(tmp_path, bandit_state_path)
        except Exception:  # justified: state persistence must not block nudge
            logger.debug("bandit_state_persist_failed", exc_info=True)

        # Log bandit decision via structlog (interim until PRD-CORE-103 propensity log wired)
        for learning in selected_learnings:
            logger.info(
                "bandit_decision",
                selected=str(learning.get("id", "")),
                phase=state.phase,
                is_transition=is_transition,
                client_class=client_class,
            )

        return content

    except Exception:  # justified: nudge content must never block tool responses
        logger.debug("bandit_nudge_content_failed", exc_info=True)
        return None


def append_ceremony_status(
    response: dict[str, object],
    trw_dir: Path | None = None,
) -> dict[str, object]:
    """Attach a live ceremony progress summary and bandit nudge content to a tool response.

    Sets ``ceremony_status`` (always) and ``nudge_content`` (when bandit
    selection produces learning-backed content).

    Fail-open: if the state cannot be read, the original response is returned.
    """
    try:
        effective_dir = trw_dir if trw_dir is not None else resolve_trw_dir()
        state = read_ceremony_state(effective_dir)
        response["ceremony_status"] = build_ceremony_status_line(state)

        # Attempt bandit-backed learning nudge (PRD-CORE-105 FR04)
        nudge_content = _try_bandit_nudge_content(effective_dir, state)
        if nudge_content:
            response["nudge_content"] = nudge_content

    except Exception:  # justified: status decoration must never break tool responses
        logger.debug("append_ceremony_status_failed", exc_info=True)
    return response
