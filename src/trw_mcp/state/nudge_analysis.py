"""Live nudge-effectiveness analysis (PRD nudge-deep-dive work target #1/#2/#4).

Computes per-session nudge effectiveness facts from the *live* ceremony-state
(``.trw/context/ceremony-state.json``) and surface-event stream
(``.trw/logs/surface_tracking.jsonl``), and writes the result to
``.trw/context/nudge-analysis.json``.

This is the production-side counterpart to trw-eval's campaign-only
``NudgePreAnalysis``: the same questions (did nudged steps get done? which steps
resist? were nudges timely? did nudges pull recalls?) answered in *real*
sessions, not just at the end of an eval campaign.

Three invariants this module preserves:

* **IP boundary** — trw-mcp is the public package and MUST NOT import from the
  proprietary ``trw-eval``. The (generic) step-completion + responsiveness
  arithmetic is reimplemented here rather than imported.
* **Fail-open** — any error degrades to an empty/minimal artifact and is
  swallowed. A nudge surface must never block a tool response (NFR: §brief 2).
* **Aggregate behavioral shift, not per-nudge compliance** — responsiveness is
  "of the steps that were nudged, how many did the agent eventually complete",
  not "did the agent act on each individual nudge impression" (learning
  L-be92b5d7).
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import structlog

from trw_mcp.state._ceremony_progress_state import (
    _STEPS,
    CeremonyState,
    _step_complete,
    read_ceremony_state,
)

logger = structlog.get_logger(__name__)

_ARTIFACT_SCHEMA_VERSION = 1
_ARTIFACT_REL_PATH = ("context", "nudge-analysis.json")

# A step nudged at least this many times but never completed is flagged as
# *structural* resistance — a timing/blocker problem, not a content problem.
DEFAULT_RESISTANCE_THRESHOLD = 3


@dataclass
class TimingSummary:
    """Aggregated live timing-validity counts (sourced from surface events)."""

    timely_count: int = 0
    untimely_count: int = 0
    unknown_count: int = 0
    validity_rate: float = 0.0


@dataclass
class NudgeAnalysis:
    """Live nudge-effectiveness facts for the current session."""

    schema_version: int = _ARTIFACT_SCHEMA_VERSION
    generated_at: str = ""
    session_id: str = ""
    phase: str = ""
    applicable: bool = False
    total_nudges: int = 0
    nudged_step_count: int = 0
    nudge_counts_by_step: dict[str, int] = field(default_factory=dict)
    nudge_step_completed: dict[str, bool] = field(default_factory=dict)
    responded_step_count: int = 0
    nudge_responsiveness: float = 0.0
    resistance_by_step: dict[str, int] = field(default_factory=dict)
    resistance_flags: list[dict[str, object]] = field(default_factory=list)
    resistance_threshold: int = DEFAULT_RESISTANCE_THRESHOLD
    recall_pull_rate: float = 0.0
    recall_nudge_count: int = 0
    timing: TimingSummary = field(default_factory=TimingSummary)
    # Work target #6: live A/B arm distribution (variant label -> nudge count)
    # so an operator can confirm both arms are actually receiving traffic.
    variant_breakdown: dict[str, int] = field(default_factory=dict)


def compute_nudge_timing(step: str, state: CeremonyState) -> tuple[bool, int]:
    """Live timing-validity for a nudge targeting ``step`` (work target #4).

    Computed at *emission* from the in-memory ceremony snapshot — no post-hoc
    ``step_timestamps`` required (the live state does not persist them, which is
    why the eval pipeline's timing-validity is dormant in production).

    Returns ``(is_timely, step_distance_from_call)``:

    * ``is_timely`` — the targeted step is NOT yet satisfactorily complete, so
      the nudge is still actionable. ``False`` is the mistimed case the brief
      wants detectable live: a nudge fired after its step was already done.
    * ``step_distance_from_call`` — index of the furthest-completed ceremony
      step minus the targeted step's index. ``> 0`` => the agent already moved
      past the nudged step (stale); ``<= 0`` => points at current/upcoming work.

    Uses the strict ``_step_complete`` (passing build / fresh checkpoint) rather
    than ``_step_done_at_all`` because timeliness is about whether the action is
    still *worth taking now*, not whether it ever happened.
    """
    try:
        nudged_idx = _STEPS.index(step)
    except ValueError:
        nudged_idx = -1
    completed = [idx for idx, name in enumerate(_STEPS) if _step_complete(name, state)]
    highest_completed = max(completed) if completed else -1
    is_timely = not _step_complete(step, state)
    distance = (highest_completed - nudged_idx) if nudged_idx >= 0 else 0
    return is_timely, distance


def _step_done_at_all(step: str, state: CeremonyState) -> bool:
    """Return whether a ceremony step *eventually happened* this session.

    Deliberately looser than ``_ceremony_progress_state._step_complete`` (which
    additionally requires a passing build / a fresh checkpoint): here we only
    ask "did the agent ever take the nudged action", which is the right notion
    for responsiveness — being nudged toward ``build_check`` and running a
    *failing* build is still a behavioral response to the nudge.
    """
    if step == "session_start":
        return state.session_started
    if step == "checkpoint":
        return state.checkpoint_count > 0
    if step == "build_check":
        return bool(state.build_check_result)
    if step == "review":
        return state.review_called
    if step == "deliver":
        return state.deliver_called
    return False


def _compute_recall_pull(trw_dir: Path, session_id: str | None) -> tuple[float, int]:
    """Best-effort recall-pull-rate via the live surface-tracking helper.

    Returns ``(rate, nudge_count)``; ``(0.0, 0)`` on any failure (fail-open).
    """
    try:
        from trw_mcp.state.surface_tracking import compute_recall_pull_rate

        rate, count, _ = compute_recall_pull_rate(trw_dir, session_id=session_id or None)
        return round(float(rate), 4), int(count)
    except Exception:  # justified: fail-open — recall correlation is advisory
        logger.debug("nudge_analysis_recall_pull_failed", exc_info=True)
        return 0.0, 0


def _aggregate_surface_signals(
    trw_dir: Path,
    session_id: str | None,
) -> tuple[TimingSummary, dict[str, int]]:
    """Aggregate per-nudge ``is_timely`` (#4) + A/B arm distribution (#6) live.

    The eval pipeline derives timing validity post-hoc from ``step_timestamps``,
    which the live ceremony-state does not persist. Instead we read the
    ``is_timely`` flag stamped on each nudge surface event at *emission* time
    (see ``surface_tracking.log_surface_event``). A nudge with no flag (older
    events, non-nudge surfaces) is counted as UNKNOWN. The same pass tallies the
    ``nudge_variant`` arm label so an operator can confirm both A/B arms are
    receiving real traffic.
    """
    summary = TimingSummary()
    variant_counts: dict[str, int] = {}
    try:
        from trw_mcp.state.surface_tracking import read_surface_events

        events = read_surface_events(trw_dir)
    except Exception:  # justified: fail-open
        logger.debug("nudge_analysis_surface_read_failed", exc_info=True)
        return summary, variant_counts

    for event in events:
        if event.get("surface_type") != "nudge":
            continue
        if session_id and event.get("session_id") not in ("", session_id):
            continue
        flag = event.get("is_timely")
        if flag is True:
            summary.timely_count += 1
        elif flag is False:
            summary.untimely_count += 1
        else:
            summary.unknown_count += 1
        variant = event.get("nudge_variant")
        if isinstance(variant, str) and variant:
            variant_counts[variant] = variant_counts.get(variant, 0) + 1

    classified = summary.timely_count + summary.untimely_count
    if classified:
        summary.validity_rate = round(summary.timely_count / classified, 4)
    return summary, variant_counts


def compute_nudge_analysis(
    trw_dir: Path,
    *,
    session_id: str | None = None,
    resistance_threshold: int = DEFAULT_RESISTANCE_THRESHOLD,
) -> NudgeAnalysis:
    """Compute live nudge-effectiveness facts for the session rooted at ``trw_dir``.

    Reads ceremony-state for nudge counts + step completion, the surface stream
    for recall-pull + timing. Never raises — a read failure yields a result with
    ``applicable=False``.
    """
    result = NudgeAnalysis(
        generated_at=datetime.now(timezone.utc).isoformat(),
        session_id=session_id or "",
        resistance_threshold=resistance_threshold,
    )

    try:
        state = read_ceremony_state(trw_dir)
    except Exception:  # justified: fail-open — read_ceremony_state already
        # fails open to defaults, but guard against any future regression so a
        # corrupt state never propagates out of the analysis surface.
        logger.debug("nudge_analysis_state_read_failed", exc_info=True)
        return result

    result.phase = state.phase

    # --- Per-step counts + completion (responsiveness, resistance) -----------
    nudged_steps = {step: count for step, count in state.nudge_counts.items() if count > 0}
    result.nudge_counts_by_step = dict(nudged_steps)
    result.total_nudges = sum(nudged_steps.values())
    result.nudged_step_count = len(nudged_steps)
    result.applicable = result.total_nudges > 0

    for step in nudged_steps:
        completed = _step_done_at_all(step, state)
        result.nudge_step_completed[step] = completed
        if not completed:
            result.resistance_by_step[step] = nudged_steps[step]
            if nudged_steps[step] >= resistance_threshold:
                result.resistance_flags.append(
                    {
                        "step": step,
                        "nudge_count": nudged_steps[step],
                        "phase": state.phase,
                    }
                )

    if result.nudged_step_count:
        result.responded_step_count = sum(1 for done in result.nudge_step_completed.values() if done)
        result.nudge_responsiveness = round(result.responded_step_count / result.nudged_step_count, 4)

    # --- Recall pull + timing + A/B arm distribution (surface-event sourced) -
    result.recall_pull_rate, result.recall_nudge_count = _compute_recall_pull(trw_dir, session_id)
    result.timing, result.variant_breakdown = _aggregate_surface_signals(trw_dir, session_id)

    logger.debug(
        "nudge_analysis_computed",
        total_nudges=result.total_nudges,
        responsiveness=result.nudge_responsiveness,
        resistance_flags=len(result.resistance_flags),
        recall_pull_rate=result.recall_pull_rate,
        timing_validity=result.timing.validity_rate,
    )
    return result


def analysis_artifact_dict(result: NudgeAnalysis) -> dict[str, object]:
    """Serialize a :class:`NudgeAnalysis` to a plain JSON-ready dict."""
    return asdict(result)


def analysis_summary(result: NudgeAnalysis) -> dict[str, object]:
    """Compact summary suitable for embedding in a tool response (deliver).

    When no nudge fired this session (``applicable=False`` — common for short or
    non-interactive sessions) every other field is a zero/empty default that
    conveys nothing beyond the boolean, so the summary collapses to
    ``{"applicable": False}`` to keep the deliver response lean.
    """
    if not result.applicable:
        return {"applicable": False}
    return {
        "applicable": result.applicable,
        "total_nudges": result.total_nudges,
        "responsiveness": result.nudge_responsiveness,
        "recall_pull_rate": result.recall_pull_rate,
        "resistance_steps": sorted(result.resistance_by_step),
        "resistance_flagged": [str(flag.get("step")) for flag in result.resistance_flags],
        "timing_validity_rate": result.timing.validity_rate,
        "variant_breakdown": dict(result.variant_breakdown),
    }


def persist_nudge_analysis(trw_dir: Path, result: NudgeAnalysis) -> Path | None:
    """Atomically write a precomputed analysis to ``.trw/context/nudge-analysis.json``.

    Returns the artifact path on success, ``None`` on any failure (fail-open).
    Split from :func:`write_nudge_analysis` so callers that already hold a
    computed result (e.g. trw_deliver, which also wants a summary) need not
    recompute.
    """
    try:
        path = trw_dir.joinpath(*_ARTIFACT_REL_PATH)
        path.parent.mkdir(parents=True, exist_ok=True)
        content = json.dumps(analysis_artifact_dict(result), separators=(",", ":"))
        fd, tmp_path = tempfile.mkstemp(dir=path.parent, prefix=".nudge-analysis-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(content)
            os.replace(tmp_path, path)
        except OSError:
            logger.warning("nudge_analysis_write_failed", artifact_path=str(path), exc_info=True)
            with contextlib.suppress(OSError):
                os.unlink(tmp_path)
            return None
        return path
    except Exception:  # justified: fail-open — analysis must never block deliver
        logger.debug("nudge_analysis_persist_failed", exc_info=True)
        return None


def write_nudge_analysis(
    trw_dir: Path,
    *,
    session_id: str | None = None,
    resistance_threshold: int = DEFAULT_RESISTANCE_THRESHOLD,
) -> Path | None:
    """Compute and atomically write ``.trw/context/nudge-analysis.json``.

    Convenience for on-demand callers; returns the artifact path or ``None``.
    """
    result = compute_nudge_analysis(
        trw_dir,
        session_id=session_id,
        resistance_threshold=resistance_threshold,
    )
    return persist_nudge_analysis(trw_dir, result)


# Canonical ceremony step order — re-exported so the surface-emission timing
# helper (work target #4) shares one definition with the analysis layer.
CEREMONY_STEP_ORDER: tuple[str, ...] = _STEPS


__all__ = [
    "CEREMONY_STEP_ORDER",
    "DEFAULT_RESISTANCE_THRESHOLD",
    "NudgeAnalysis",
    "TimingSummary",
    "analysis_artifact_dict",
    "analysis_summary",
    "compute_nudge_analysis",
    "compute_nudge_timing",
    "persist_nudge_analysis",
    "write_nudge_analysis",
]
