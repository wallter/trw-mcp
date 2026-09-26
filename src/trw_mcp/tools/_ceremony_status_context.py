"""Reactive nudge-context construction for live ceremony-status injection.

Every live MCP tool that decorates its response via ``append_ceremony_status``
must tell the nudge layer WHICH tool produced the response and what that tool
observed (build outcome, review verdict, P0 count). That ``NudgeContext`` is
the sole activation key for three otherwise-unreachable behaviours:

* the ``context`` nudge pool — ``_ceremony_status_pool.resolve_pool_content``
  returns ``None`` for ``pool == "context"`` when ``context`` is falsy, so every
  draw is wasted into ``record_pool_ignore`` -> cooldown;
* the build-failure / P0 pool bypass in ``_nudge_rules._select_nudge_pool``,
  which force-selects the ``context`` pool regardless of weights;
* the per-tool dispatch in ``_nudge_template_select._context_reactive_message``.

Commit ``093b1fd49e`` (2026-04-10) renamed the injector and dropped the
``NudgeContext`` construction from every call site, so ``context`` silently
defaulted to ``None`` everywhere for months. This module is the ONE place that
builds it, so a future rename cannot re-drop it from eight scattered sites —
and ``tests/test_nudge_context_wiring.py`` asserts each production call site
still routes through here.

Ownership boundary: ``NudgeContext`` belongs to the ceremony-status layer
(``_ceremony_status.py`` / ``_ceremony_status_pool.py`` already import it).
Tool modules stay free of nudge-model imports and pass a tool label instead.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog

from trw_mcp.state._ceremony_progress_state import NudgeContext, ToolName

if TYPE_CHECKING:
    from pathlib import Path

    from trw_mcp.tools._learnings_collector import LearningSummary

__all__ = [
    "KNOWN_TOOL_NAMES",
    "append_ceremony_status_for_tool",
    "build_nudge_context",
    "maybe_attach_edit_hint_transition_nudge",
    "resolve_tool_name",
]

_MAX_LEARNING_SUMMARY_CHARS = 90

logger = structlog.get_logger(__name__)

# PRD-CORE-294 FR06: one trw_assess suggestion per judgment-call transition,
# gated on backend_enablement() and the same budget/cooldown/dedup selector
# as FR04. A future FR04 slice prepends its own learning-anchored
# (nudge_id, line) candidates ahead of these via ``learning_candidates``.
_JEV_BUILD_FAILED = (
    "jev:build_failed",
    "Flaky or real? Ask trw_assess with the failure's facts as state and one "
    "choice question {flaky, real_regression, environment}.",
)
_JEV_REVIEW_FINDINGS = (
    "jev:review_findings",
    "Blocker or follow-up? trw_assess items={finding_id: facts} with one "
    "choice question ranks every finding in one call.",
)
_JEV_SESSION_START = (
    "jev:session_start",
    "Jev is on: at a judgment call (flaky vs real, rank findings, option A vs "
    "B, merge vs hold) ask trw_assess instead of deciding in prose.",
)
# W41-5: a blocked deliver is a release go/no-go judgment call -- exactly the class
# USAGE-7.0.0.md observed getting NO trw_assess calls during release mechanics (08:00Z
# to 13:00Z on 2026-09-24), because the two existing triggers above only fire from
# trw_build_check/trw_review, and a release is often adjudicated at deliver time from
# external CI output neither of those tools ever saw.
_JEV_DELIVER_BLOCKED = (
    "jev:deliver_blocked",
    "Blocked: fix first, override, or escalate? Ask trw_assess with the blocking "
    "reason as state and one choice question {fix_first, override, escalate}.",
)


def _potential_jev_candidates(
    tool_name: str,
    *,
    tool_success: bool,
    build_passed: bool | None,
    review_verdict: str | None,
    review_p0_count: int,
) -> list[tuple[str, str]]:
    """Return the FR06 Jev candidates this transition *could* produce.

    Pure and cheap -- no I/O, no ``backend_enablement()`` call. The caller
    only pays for ``backend_enablement()`` when this returns something,
    which is what keeps the Jev-off path from doing any work on tool calls
    that could never have produced a Jev suggestion anyway.
    """
    resolved = resolve_tool_name(tool_name)
    if resolved == ToolName.BUILD_CHECK and build_passed is False:
        return [_JEV_BUILD_FAILED]
    if resolved == ToolName.REVIEW and (review_p0_count > 0 or bool(review_verdict and review_verdict != "pass")):
        return [_JEV_REVIEW_FINDINGS]
    if resolved == ToolName.SESSION_START:
        return [_JEV_SESSION_START]
    if resolved == ToolName.DELIVER and tool_success is False:
        return [_JEV_DELIVER_BLOCKED]
    return []


def _learning_candidate(learning_id: str, summary: str) -> tuple[str, str]:
    """Build the ``(nudge_id, line)`` pair shared by every learning candidate."""
    clipped = (
        summary if len(summary) <= _MAX_LEARNING_SUMMARY_CHARS else summary[: _MAX_LEARNING_SUMMARY_CHARS - 3] + "..."
    )
    line = (
        f"Related learning {learning_id}: {clipped}. Found the cause? trw_learn "
        f"records it; if {learning_id} is wrong, correct it by id."
    )
    return f"learn:{learning_id}", line


def _build_check_learning_candidates(failure_hints: list[str] | None) -> list[tuple[str, str]]:
    """FR04(a): the top learning anchored to a failed build's failure text.

    Skips ``collect_learnings`` entirely (no recall call) when there is no
    failure text to query on.
    """
    hints = [hint for hint in (failure_hints or []) if isinstance(hint, str) and hint.strip()]
    if not hints:
        return []
    from trw_mcp.tools._learnings_collector import collect_learnings

    try:
        learnings = collect_learnings(hints, top_n=1)
    except Exception:  # trw-fail-silent-allow: fail-open -- a bad recall must not block the build response
        logger.debug("build_check_learning_candidate_failed", exc_info=True)
        return []
    if not learnings:
        return []
    top = learnings[0]
    return [_learning_candidate(top.id, top.summary)] if top.id and top.summary else []


def _deliver_learning_candidates(trw_dir: Path, *, session_key: str) -> list[tuple[str, str]]:
    """FR04(c): learnings on files this session touched, never shown this session.

    Emits ONE candidate naming every fresh id, rather than one candidate per
    id, so it costs at most one selector slot regardless of how many
    anchored-but-unseen learnings the session's diff touches.
    """
    from trw_mcp.state.recall_context import build_recall_context

    recall_context = build_recall_context(trw_dir, "*")
    modified_files_raw = getattr(recall_context, "modified_files", []) if recall_context is not None else []
    modified_files = [str(path).strip() for path in modified_files_raw if str(path).strip()]
    if not modified_files:
        return []

    from trw_mcp.tools._learnings_collector import collect_learnings

    try:
        learnings = collect_learnings(modified_files, top_n=5)
    except Exception:  # trw-fail-silent-allow: fail-open -- a bad recall must not block deliver
        logger.debug("deliver_learning_candidates_failed", exc_info=True)
        return []
    if not learnings:
        return []

    from trw_mcp.state._ceremony_nudge_selectors import get_shown_transition_ids

    shown = get_shown_transition_ids(trw_dir, session_key=session_key)
    fresh_ids = sorted({learning.id for learning in learnings if learning.id and f"learn:{learning.id}" not in shown})
    if not fresh_ids:
        return []
    line = (
        f"Learnings on files you changed that you never saw: {', '.join(fresh_ids)}. "
        "Check them; correct by id if stale."
    )
    return [(f"learn:deliver:{','.join(fresh_ids)}", line)]


# Canonical ``ToolName`` values, used by callers and by the wiring regression
# test to prove a resolved label is a real tool name and not a typo.
KNOWN_TOOL_NAMES: frozenset[str] = frozenset(
    value for name, value in vars(ToolName).items() if not name.startswith("_") and isinstance(value, str)
)


def resolve_tool_name(tool_name: str) -> str:
    """Map a caller-supplied label onto its canonical ``ToolName`` value.

    Accepts both the constant NAME (``"INIT"``, as the orchestration facade
    passes) and the constant VALUE (``"init"``). Unknown labels pass through
    unchanged: downstream dispatch returns ``None`` for them and falls back to
    static messages, so an unrecognised tool degrades rather than raising.
    """
    resolved = getattr(ToolName, tool_name.upper(), None)
    return resolved if isinstance(resolved, str) else tool_name


def build_nudge_context(
    tool_name: str,
    *,
    tool_success: bool = True,
    build_passed: bool | None = None,
    review_verdict: str | None = None,
    review_p0_count: int = 0,
    is_subagent: bool = False,
) -> NudgeContext:
    """Build the reactive nudge context describing a completed tool call."""
    return NudgeContext(
        tool_name=resolve_tool_name(tool_name),
        tool_success=tool_success,
        build_passed=build_passed,
        review_verdict=review_verdict,
        review_p0_count=review_p0_count,
        is_subagent=is_subagent,
    )


def append_ceremony_status_for_tool(
    response: dict[str, object],
    trw_dir: Path | None = None,
    *,
    tool_name: str,
    tool_success: bool = True,
    build_passed: bool | None = None,
    review_verdict: str | None = None,
    review_p0_count: int = 0,
    is_subagent: bool = False,
    failure_hints: list[str] | None = None,
) -> dict[str, object]:
    """Attach ceremony status to ``response`` with reactive context wired in.

    Thin, deliberate seam over :func:`append_ceremony_status`: same fail-open
    contract, but the ``context`` argument is never omitted. ``failure_hints``
    (FR04a) is the raw text ``trw_build_check`` has for a failed run --
    failure strings and/or scope -- used to anchor a learning candidate; it is
    ignored for every other tool.
    """
    from trw_mcp.tools._ceremony_status import append_ceremony_status

    result = append_ceremony_status(
        response,
        trw_dir,
        build_nudge_context(
            tool_name,
            tool_success=tool_success,
            build_passed=build_passed,
            review_verdict=review_verdict,
            review_p0_count=review_p0_count,
            is_subagent=is_subagent,
        ),
    )

    # PRD-CORE-294 FR04/FR06: learning candidates work with Jev disabled;
    # Jev candidates additionally require backend_enablement().
    try:
        _maybe_attach_transition_nudge(
            result,
            trw_dir,
            tool_name=tool_name,
            tool_success=tool_success,
            build_passed=build_passed,
            review_verdict=review_verdict,
            review_p0_count=review_p0_count,
            failure_hints=failure_hints,
        )
    except Exception:  # justified: fail-open -- a transition nudge must never break a tool response
        logger.debug("transition_nudge_failed", exc_info=True)

    return result


def _maybe_attach_transition_nudge(
    response: dict[str, object],
    trw_dir: Path | None,
    *,
    tool_name: str,
    tool_success: bool = True,
    build_passed: bool | None,
    review_verdict: str | None,
    review_p0_count: int,
    failure_hints: list[str] | None = None,
) -> None:
    """Set ``response["transition_nudge"]`` when a line is selected.

    Learning candidates (FR04) are built for the two transitions
    ``append_ceremony_status_for_tool`` reaches -- a failed build and a
    deliver -- and work with Jev entirely disabled. Jev candidates (FR06)
    are gated on ``backend_enablement()``, called only when the transition
    could plausibly produce one, so the Jev-off / non-judgment path never
    pays for the check.

    No candidates on either side -> no selector call, no state write, no
    response key: this is what keeps a no-transition tool call at zero
    added bytes.
    """
    resolved = resolve_tool_name(tool_name)
    effective_dir = _effective_trw_dir(trw_dir)

    learning_candidates: list[tuple[str, str]] = []
    if resolved == ToolName.BUILD_CHECK and build_passed is False:
        learning_candidates = _build_check_learning_candidates(failure_hints)
    elif resolved == ToolName.DELIVER:
        from trw_mcp.state._session_id import resolve_effective_session_id

        learning_candidates = _deliver_learning_candidates(
            effective_dir, session_key=resolve_effective_session_id(effective_dir)
        )

    jev_candidates: list[tuple[str, str]] = []
    potential_jev = _potential_jev_candidates(
        tool_name,
        tool_success=tool_success,
        build_passed=build_passed,
        review_verdict=review_verdict,
        review_p0_count=review_p0_count,
    )
    if potential_jev:
        from trw_mcp.state._paths import resolve_project_root
        from trw_mcp.tools._assess_enablement import backend_enablement

        enabled, _source = backend_enablement(resolve_project_root())
        if enabled:
            jev_candidates = potential_jev

    candidates = [*learning_candidates, *jev_candidates]
    if not candidates:
        return

    from trw_mcp.state._ceremony_nudge_selectors import select_transition_line
    from trw_mcp.state._session_id import resolve_effective_session_id

    session_key = resolve_effective_session_id(effective_dir)
    line = select_transition_line(effective_dir, session_key=session_key, candidates=candidates)
    if line:
        response["transition_nudge"] = line


def maybe_attach_edit_hint_transition_nudge(
    response: dict[str, object],
    trw_dir: Path | None,
    *,
    learnings: list[LearningSummary],
) -> None:
    """FR04(b): attach the top-learning transition line for ``trw_code``'s hint mode.

    Minimal path: skips the whole ``append_ceremony_status`` pipeline (no
    ``NudgeContext``, no phase/status lines) and calls only the transition
    selector, so a file with no anchored learnings costs nothing beyond the
    empty-list check. Fail-open: any exception leaves ``response`` untouched.
    """
    if not learnings:
        return
    top = learnings[0]
    if not top.id or not top.summary:
        return
    line = f"Top learning for this file: {top.id}. If it is wrong or stale, correct it by id instead of adding a duplicate."
    try:
        from trw_mcp.state._ceremony_nudge_selectors import select_transition_line
        from trw_mcp.state._session_id import resolve_effective_session_id

        effective_dir = _effective_trw_dir(trw_dir)
        session_key = resolve_effective_session_id(effective_dir)
        selected = select_transition_line(
            effective_dir, session_key=session_key, candidates=[(f"learn:{top.id}", line)]
        )
    except Exception:  # trw-fail-silent-allow: fail-open -- a transition nudge must never break before_edit_hint
        logger.debug("edit_hint_transition_nudge_failed", exc_info=True)
        return
    if selected:
        response["transition_nudge"] = selected


def transition_selector_state(trw_dir: Path | None = None) -> dict[str, object]:
    """Which transition selector this workspace runs and whether it is on (PRD-CORE-294 FR05).

    Recorded on every ``trw_session_start`` call so the engagement report can
    compare sessions with the selector on and off. ``jev`` says whether FR06's
    Jev candidates join the learning candidates; it is never true while the
    selector is off.
    """
    from trw_mcp.state._ceremony_nudge_selectors import transition_selector_enabled

    enabled = transition_selector_enabled(_effective_trw_dir(trw_dir))
    jev = False
    if enabled:
        from trw_mcp.state._paths import resolve_project_root
        from trw_mcp.tools._assess_enablement import backend_enablement

        jev, _source = backend_enablement(resolve_project_root())
    return {"name": "transition", "enabled": enabled, "jev": jev}


def _effective_trw_dir(trw_dir: Path | None) -> Path:
    if trw_dir is not None:
        return trw_dir
    from trw_mcp.state._paths import resolve_trw_dir

    return resolve_trw_dir()
