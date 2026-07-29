"""Nudge attribution + emission-completeness guards (ledger UF-023/UF-024/UF-041/UF-042/UF-043).

Five defects converge on one question: *does the nudge telemetry describe what
the agent actually saw?* Before this module the answer was no.

The headline is UF-023. ``increment_nudge_count`` recorded
``_highest_priority_pending_step(state) or "session_start"`` — "whichever
ceremony step is most overdue right now", not the step the emitted nudge talked
about. In a repo whose ceremony state has every step satisfied (the steady state
of any long-lived project) ``_highest_priority_pending_step`` returns ``None``
and the ``or "session_start"`` default fired on EVERY emission. The live
distribution on this repo at the time of the fix:

    nudge_counts      = {"session_start": 2963, "build_check": 78}   # 97.4%
    session_started   = True          (for the preceding 4,777 tool calls)
    checkpoint_count  = 1374, build_check_result = "passed",
    review_called     = True,  deliver_called = True,  phase = "deliver"

i.e. 2,963 nudges were attributed to a step that had been complete the entire
time. ``test_the_live_2963_session_start_state_is_reproduced_and_fixed`` replays
exactly that state and compares the old rule against the new one on the same
emissions, because "the code changed" is not evidence that the number moved.
"""

from __future__ import annotations

import ast
import json
from contextlib import AbstractContextManager
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest

from trw_mcp.state._ceremony_progress_state import CeremonyState, read_ceremony_state, write_ceremony_state
from trw_mcp.tools._ceremony_nudge_emission import (
    account_nudge_emission,
    attach_reversion_prompt,
    record_emitted_nudge,
    resolve_nudge_target_step,
)
from trw_mcp.tools._ceremony_status_context import append_ceremony_status_for_tool, build_nudge_context

if TYPE_CHECKING:
    from collections.abc import Iterator

_SRC = Path(__file__).resolve().parents[1] / "src" / "trw_mcp"

# The exact live ceremony state that produced 2,963/3,041 "session_start".
LIVE_COMPLETED_STATE = CeremonyState(
    session_started=True,
    checkpoint_count=1374,
    files_modified_since_checkpoint=0,
    build_check_result="passed",
    review_called=True,
    review_verdict="pass",
    deliver_called=True,
    phase="deliver",
    tool_call_counter=4777,
)


def _legacy_attribution(state: CeremonyState) -> str:
    """The pre-fix rule, verbatim, kept as the counterfactual baseline."""
    from trw_mcp.state.ceremony_nudge import _highest_priority_pending_step

    return _highest_priority_pending_step(state) or "session_start"


def _make_trw_dir(tmp_path: Path, state: CeremonyState, *, messenger: str = "standard") -> Path:
    trw_dir = tmp_path / ".trw"
    (trw_dir / "context").mkdir(parents=True, exist_ok=True)
    (trw_dir / "config.yaml").write_text(
        f"nudge_enabled: true\nnudge_messenger: {messenger}\n",
        encoding="utf-8",
    )
    write_ceremony_state(trw_dir, state)
    return trw_dir


def _forced_pool(pool: str) -> AbstractContextManager[object]:
    """Pin the standard messenger's weighted-random draw to one pool.

    Patches the real ``_select_nudge_pool``, which ``select_pool`` imports
    lazily at call time, so everything downstream of selection stays on the
    production path.
    """
    return patch("trw_mcp.state.ceremony_nudge._select_nudge_pool", return_value=pool)


# ---------------------------------------------------------------------------
# UF-023: the attribution rule itself
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("pool", "state", "expected"),
    [
        # ceremony pool renders load_pool_message("ceremony", phase_hint=pending)
        ("ceremony", CeremonyState(session_started=True, phase="implement"), "checkpoint"),
        (
            "ceremony",
            CeremonyState(session_started=True, checkpoint_count=1, phase="validate"),
            "build_check",
        ),
        # everything complete -> the ceremony pool has nothing to target
        ("ceremony", LIVE_COMPLETED_STATE, None),
        # minimal messenger has its OWN ladder (session_start -> deliver -> none)
        ("minimal", CeremonyState(), "session_start"),
        ("minimal", CeremonyState(session_started=True), "deliver"),
        ("minimal", LIVE_COMPLETED_STATE, None),
        # learning / workflow content names no ceremony step
        ("learnings", CeremonyState(session_started=True, phase="validate"), None),
        ("workflow", CeremonyState(session_started=True, phase="validate"), None),
        # context pool with no context cannot target anything
        ("context", CeremonyState(session_started=True, phase="review"), None),
    ],
)
def test_target_step_follows_the_content_not_the_backlog(
    pool: str,
    state: CeremonyState,
    expected: str | None,
) -> None:
    assert resolve_nudge_target_step(pool, state) == expected


def test_context_pool_targets_the_trigger_that_selected_it() -> None:
    """The two reactive triggers that force the context pool are its target."""
    state = CeremonyState(session_started=True, checkpoint_count=1, phase="review")

    build_fail = build_nudge_context("build_check", build_passed=False)
    assert resolve_nudge_target_step("context", state, context=build_fail) == "build_check"

    p0 = build_nudge_context("review", review_verdict="block", review_p0_count=2)
    assert resolve_nudge_target_step("context", state, context=p0) == "review"

    # A reactive nudge for a tool with no ceremony-step remediation stays
    # unattributed rather than borrowing the backlog's most-overdue step.
    assert resolve_nudge_target_step("context", state, context=build_nudge_context("recall")) is None


def test_no_attribution_path_can_default_to_session_start() -> None:
    """Exhaustive: with session_started=True, NO pool may yield "session_start"."""
    contexts = [
        None,
        build_nudge_context("build_check", build_passed=False),
        build_nudge_context("review", review_p0_count=3),
        build_nudge_context("recall"),
    ]
    states = [
        LIVE_COMPLETED_STATE,
        CeremonyState(session_started=True, phase="implement"),
        CeremonyState(session_started=True, checkpoint_count=2, phase="deliver"),
    ]
    for pool in ("minimal", "ceremony", "context", "learnings", "workflow"):
        for state in states:
            for context in contexts:
                assert resolve_nudge_target_step(pool, state, context=context) != "session_start"


# ---------------------------------------------------------------------------
# UF-023: the 97% figure, measured
# ---------------------------------------------------------------------------


def test_the_live_2963_session_start_state_is_reproduced_and_fixed(tmp_path: Path) -> None:
    """Counterfactual on one emission stream: old rule 100% session_start, new rule 0%.

    Runs the REAL ``append_ceremony_status`` path 24 times against the live
    all-complete ceremony state, alternating the pools that actually produced the
    live traffic (``learnings`` 1733 / ``workflow`` 1682 / ``ceremony`` 16).
    """
    trw_dir = _make_trw_dir(tmp_path, LIVE_COMPLETED_STATE)

    # Baseline: the pre-fix rule labels EVERY one of these emissions session_start.
    assert _legacy_attribution(LIVE_COMPLETED_STATE) == "session_start"

    pools = ["learnings", "workflow", "ceremony"]
    for index in range(24):
        with _forced_pool(pools[index % len(pools)]):
            append_ceremony_status_for_tool({}, trw_dir, tool_name="learn")

    final = read_ceremony_state(trw_dir)
    # Only the workflow pool can render content in this state: the ceremony pool
    # correctly produces nothing when no step is pending, and the learnings pool
    # has no candidate corpus in a tmp workspace. Both record an ignore instead.
    emissions = final.pool_nudge_counts.get("workflow", 0)
    assert emissions == 8, final.pool_nudge_counts

    # The counterfactual, on this exact emission stream:
    #   pre-fix  -> 8/8 attributed to "session_start" (100%)
    #   post-fix -> 0/8; nothing is attributed at all, because a phase-keyed
    #               workflow message names no ceremony step.
    assert final.nudge_counts == {}, f"session_start was fabricated again: {final.nudge_counts}"
    assert "session_start" not in final.nudge_counts
    # Emission volume is NOT lost — it lives in the per-pool ledger, which this
    # change extends to every pool rather than the standard messenger only.
    assert sum(final.pool_nudge_counts.values()) == emissions


def test_step_counts_gain_real_diversity_across_a_session(tmp_path: Path) -> None:
    """A session that genuinely moves through phases produces >=3 distinct steps.

    Pre-fix this same walk collapsed onto whatever ``_highest_priority_pending_step``
    happened to return at read time, and onto ``session_start`` the moment nothing
    was pending. Here every count is a step the ceremony pool actually rendered
    content for.
    """
    trw_dir = _make_trw_dir(tmp_path, CeremonyState())
    walk = [
        # (state, expected attributed step)
        (CeremonyState(phase="early"), "session_start"),
        (CeremonyState(session_started=True, phase="implement"), "checkpoint"),
        (
            CeremonyState(session_started=True, checkpoint_count=1, phase="validate"),
            "build_check",
        ),
        (
            CeremonyState(
                session_started=True,
                checkpoint_count=1,
                build_check_result="passed",
                phase="review",
            ),
            "review",
        ),
        (
            CeremonyState(
                session_started=True,
                checkpoint_count=1,
                build_check_result="passed",
                review_called=True,
                phase="deliver",
            ),
            "deliver",
        ),
    ]

    with _forced_pool("ceremony"):
        for state, _expected in walk:
            persisted = read_ceremony_state(trw_dir)
            state.nudge_counts = persisted.nudge_counts
            state.pool_nudge_counts = persisted.pool_nudge_counts
            state.tool_call_counter = persisted.tool_call_counter
            write_ceremony_state(trw_dir, state)
            append_ceremony_status_for_tool({}, trw_dir, tool_name="status")

    counts = read_ceremony_state(trw_dir).nudge_counts
    assert set(counts) == {step for _s, step in walk}, counts
    assert len(counts) >= 3
    # session_start is present ONLY from the one emission where the session had
    # genuinely not started — not as a catch-all default.
    assert counts["session_start"] == 1, counts


def test_a_tool_marking_its_own_step_does_not_steal_the_attribution(tmp_path: Path) -> None:
    """UF-023's second half: mark-before-inject no longer distorts the label.

    ``trw_checkpoint`` marks checkpoint complete BEFORE calling the injector.
    Attribution now comes from the emitted content, so the checkpoint nudge that
    can no longer be rendered simply is not counted — instead of silently
    sliding onto whatever became most-overdue.
    """
    from trw_mcp.state._ceremony_progress_state import mark_checkpoint

    trw_dir = _make_trw_dir(
        tmp_path,
        CeremonyState(session_started=True, phase="implement", files_modified_since_checkpoint=20),
    )
    # Pre-mark, the pending step really is checkpoint.
    assert resolve_nudge_target_step("ceremony", read_ceremony_state(trw_dir)) == "checkpoint"

    mark_checkpoint(trw_dir)  # the tool's own mark_* call

    with _forced_pool("ceremony"):
        append_ceremony_status_for_tool({}, trw_dir, tool_name="checkpoint")

    counts = read_ceremony_state(trw_dir).nudge_counts
    assert "session_start" not in counts, counts
    assert counts.get("checkpoint", 0) == 0, counts


# ---------------------------------------------------------------------------
# UF-024: surface-event coverage for all four pools
# ---------------------------------------------------------------------------


def _surface_events(trw_dir: Path) -> list[dict[str, object]]:
    path = trw_dir / "logs" / "surface_tracking.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


@pytest.mark.parametrize("pool", ["workflow", "ceremony"])
def test_previously_silent_pools_now_emit_a_surface_event(pool: str, tmp_path: Path) -> None:
    """UF-024: the workflow and ceremony branches logged nothing at all."""
    trw_dir = _make_trw_dir(
        tmp_path,
        CeremonyState(session_started=True, checkpoint_count=1, phase="validate"),
    )
    with _forced_pool(pool):
        response = append_ceremony_status_for_tool({}, trw_dir, tool_name="learn")

    assert response.get("nudge_content"), "pool produced no content — test setup is vacuous"
    events = [e for e in _surface_events(trw_dir) if e.get("surface_type") == "nudge"]
    assert len(events) == 1, events
    assert events[0]["messenger"] == "standard"
    assert str(events[0]["learning_id"]).startswith("SYS-nudge-")


def test_surface_stream_and_emission_ledger_stay_joinable(tmp_path: Path) -> None:
    """UF-024's actual consequence: the two ledgers were structurally decoupled.

    Timing/variant data lived only on the two pools that logged surface events,
    so it covered a strict subset of the emission count and the two numbers could
    not be reconciled. One surface event per emission, always.
    """
    trw_dir = _make_trw_dir(
        tmp_path,
        CeremonyState(session_started=True, checkpoint_count=1, phase="validate"),
    )
    for pool in ("workflow", "ceremony", "workflow", "context", "ceremony"):
        with _forced_pool(pool):
            append_ceremony_status_for_tool(
                {},
                trw_dir,
                tool_name="build_check",
                tool_success=False,
                build_passed=False,
            )

    emitted = sum(read_ceremony_state(trw_dir).pool_nudge_counts.values())
    surfaced = len([e for e in _surface_events(trw_dir) if e.get("surface_type") == "nudge"])
    assert emitted == 5, read_ceremony_state(trw_dir).pool_nudge_counts
    assert surfaced == emitted, f"{surfaced} surface events for {emitted} emissions"


def test_analysis_reports_emission_volume_separately_from_step_counts(tmp_path: Path) -> None:
    """A learning/workflow-only session is still ``applicable``.

    ``total_nudges`` narrows to step-targeted nudges (the only ones that
    responsiveness/resistance are defined over), so the artifact would otherwise
    claim nothing fired in a session full of real learning nudges.
    """
    from trw_mcp.state.nudge_analysis import analysis_summary, compute_nudge_analysis

    trw_dir = _make_trw_dir(
        tmp_path,
        CeremonyState(session_started=True, checkpoint_count=1, phase="validate"),
    )
    for _ in range(3):
        with _forced_pool("workflow"):
            append_ceremony_status_for_tool({}, trw_dir, tool_name="learn")

    analysis = compute_nudge_analysis(trw_dir)
    assert analysis.total_nudges == 0
    assert analysis.total_emissions == 3
    assert analysis.emissions_by_pool == {"workflow": 3}
    assert analysis.applicable is True

    summary = analysis_summary(analysis)
    assert summary["total_emissions"] == 3
    # Responsiveness is undefined with no step-targeted nudge — omitting it
    # avoids reporting 0.0, which reads as "the agent ignored every nudge".
    assert "responsiveness" not in summary


def test_unattributed_nudges_omit_timing_instead_of_fabricating_it(tmp_path: Path) -> None:
    """A workflow nudge targets no step, so ``is_timely`` must be absent, not True.

    ``compute_nudge_timing`` returns ``is_timely=True`` for any step name it does
    not recognise, so stamping a placeholder step here would have manufactured a
    "timely" verdict for a nudge with nothing to be timely about.
    """
    trw_dir = _make_trw_dir(tmp_path, CeremonyState(session_started=True, checkpoint_count=1, phase="validate"))
    with _forced_pool("workflow"):
        append_ceremony_status_for_tool({}, trw_dir, tool_name="learn")

    event = [e for e in _surface_events(trw_dir) if e.get("surface_type") == "nudge"][0]
    assert "is_timely" not in event, event
    assert "step_distance_from_call" not in event, event
    assert event.get("nudge_step", "") == ""


def test_ceremony_pool_surface_event_carries_the_real_target_step(tmp_path: Path) -> None:
    trw_dir = _make_trw_dir(tmp_path, CeremonyState(session_started=True, checkpoint_count=1, phase="validate"))
    with _forced_pool("ceremony"):
        append_ceremony_status_for_tool({}, trw_dir, tool_name="learn")

    event = [e for e in _surface_events(trw_dir) if e.get("surface_type") == "nudge"][0]
    assert event["nudge_step"] == "build_check"
    assert event["is_timely"] is True


def test_record_emitted_nudge_returns_a_joinable_id(tmp_path: Path) -> None:
    """The synthetic id encodes messenger/pool/step so the stream can be sliced."""
    from trw_mcp.models.config import TRWConfig

    trw_dir = tmp_path / ".trw"
    (trw_dir / "context").mkdir(parents=True)
    state = CeremonyState(session_started=True, checkpoint_count=1, phase="validate")
    learning_id = record_emitted_nudge(
        trw_dir,
        state=state,
        cfg=TRWConfig(),
        messenger="standard",
        pool="workflow",
        client_id="claude-code",
    )
    assert learning_id == "SYS-nudge-standard-workflow-unattributed"


def test_account_nudge_emission_writes_both_ledgers(tmp_path: Path) -> None:
    trw_dir = tmp_path / ".trw"
    (trw_dir / "context").mkdir(parents=True)
    state = CeremonyState(session_started=True, checkpoint_count=1, phase="validate")

    assert account_nudge_emission(trw_dir, state=state, pool="ceremony") == "build_check"
    assert account_nudge_emission(trw_dir, state=state, pool="workflow") is None

    persisted = read_ceremony_state(trw_dir)
    assert persisted.nudge_counts == {"build_check": 1}
    assert persisted.pool_nudge_counts == {"ceremony": 1, "workflow": 1}


# ---------------------------------------------------------------------------
# UF-041: the reversion prompt reaches a response
# ---------------------------------------------------------------------------


def test_reversion_prompt_reaches_the_response_on_build_failure(tmp_path: Path) -> None:
    trw_dir = _make_trw_dir(tmp_path, CeremonyState(session_started=True, checkpoint_count=1, phase="validate"))
    response = append_ceremony_status_for_tool(
        {},
        trw_dir,
        tool_name="build_check",
        tool_success=False,
        build_passed=False,
    )
    assert "revert to PLAN" in str(response["reversion_prompt"])


def test_reversion_prompt_reaches_the_response_on_p0_findings(tmp_path: Path) -> None:
    trw_dir = _make_trw_dir(tmp_path, CeremonyState(session_started=True, checkpoint_count=1, phase="review"))
    response = append_ceremony_status_for_tool(
        {},
        trw_dir,
        tool_name="review",
        review_verdict="block",
        review_p0_count=2,
    )
    assert "revert to PLAN" in str(response["reversion_prompt"])


def test_reversion_prompt_is_omitted_when_nothing_warrants_it(tmp_path: Path) -> None:
    """Advisory field: absent (zero tokens) on the overwhelmingly common path."""
    trw_dir = _make_trw_dir(tmp_path, CeremonyState(session_started=True, checkpoint_count=1, phase="implement"))
    response = append_ceremony_status_for_tool({}, trw_dir, tool_name="learn", build_passed=True)
    assert "reversion_prompt" not in response


def test_reversion_prompt_never_fires_for_a_subagent() -> None:
    response: dict[str, object] = {}
    attach_reversion_prompt(
        response,
        context=build_nudge_context("build_check", build_passed=False, is_subagent=True),
        state=CeremonyState(session_started=True),
    )
    assert "reversion_prompt" not in response


def test_reversion_prompt_survives_every_messenger_branch(tmp_path: Path) -> None:
    """Two of three messenger branches return before pool dispatch.

    A per-branch write would have been reachable on one path only; this pins the
    placement so a later refactor cannot quietly re-strand the output.
    """
    for messenger in ("minimal", "contextual", "standard"):
        trw_dir = _make_trw_dir(
            tmp_path / messenger,
            CeremonyState(session_started=True, checkpoint_count=1, phase="validate"),
            messenger=messenger,
        )
        response = append_ceremony_status_for_tool(
            {},
            trw_dir,
            tool_name="build_check",
            tool_success=False,
            build_passed=False,
        )
        assert "reversion_prompt" in response, f"messenger={messenger}"


# ---------------------------------------------------------------------------
# Static invariants — behavioural tests missed the original regression for
# 3.5 months, so the shape of each fix is pinned in the AST too.
# ---------------------------------------------------------------------------

# Every module in the nudge surface that could reintroduce the fabricated label.
_ATTRIBUTION_MODULES = (
    _SRC / "tools" / "_ceremony_status.py",
    _SRC / "tools" / "_ceremony_status_nudge.py",
    _SRC / "tools" / "_ceremony_status_helpers.py",
    _SRC / "tools" / "_ceremony_nudge_emission.py",
    _SRC / "tools" / "_ceremony_status_pool.py",
)


def _or_session_start_defaults(module_path: Path) -> Iterator[int]:
    """Yield line numbers of any ``<expr> or "session_start"`` in the module."""
    tree = ast.parse(module_path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.BoolOp) or not isinstance(node.op, ast.Or):
            continue
        for value in node.values[1:]:
            if isinstance(value, ast.Constant) and value.value == "session_start":
                yield node.lineno


def test_no_module_may_reintroduce_the_or_session_start_default() -> None:
    """AST invariant: this exact expression IS defect UF-023.

    It survived every behavioural test in the suite because a fabricated step
    label is still a valid string — nothing raises, nothing looks wrong, and the
    resulting 97% distribution reads as a finding rather than a bug.
    """
    offenders = [
        f"{path.name}:{lineno}" for path in _ATTRIBUTION_MODULES for lineno in _or_session_start_defaults(path)
    ]
    assert not offenders, f're-fabricated "session_start" default at: {offenders}'


def _keywords_of_calls(module_path: Path, func_name: str) -> Iterator[tuple[int, set[str]]]:
    tree = ast.parse(module_path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
        if name == func_name:
            yield node.lineno, {kw.arg for kw in node.keywords if kw.arg}


@pytest.mark.parametrize(
    ("module", "tool_label"),
    [
        (_SRC / "tools" / "build" / "_registration.py", "build_check"),
        (_SRC / "tools" / "_ceremony_deliver_tool.py", "deliver"),
    ],
)
def test_build_outcome_call_sites_must_supply_build_passed(module: Path, tool_label: str) -> None:
    """AST invariant for UF-042: ``build_passed`` has exactly two writers.

    ``NudgeContext.build_passed`` had NO production writer anywhere, which alone
    made the "Build failed -> revert to PLAN" branch unreachable. Dropping the
    keyword from either site restores that dead state while every behavioural
    test keeps passing — so it is asserted structurally.
    """
    calls = list(_keywords_of_calls(module, "append_ceremony_status_for_tool"))
    assert calls, f"{module.name} no longer calls the ceremony injector at all"
    assert any("build_passed" in keywords for _lineno, keywords in calls), (
        f"{module.name} calls the injector without build_passed: {calls}"
    )
    source = module.read_text(encoding="utf-8")
    assert f'tool_name="{tool_label}"' in source, f"{module.name} lost its {tool_label} label"


def test_recall_supplies_a_tool_name() -> None:
    """UF-043: ``ToolName.RECALL`` needs a production producer."""
    module = _SRC / "tools" / "_recall_impl.py"
    calls = list(_keywords_of_calls(module, "append_ceremony_status_for_tool"))
    assert calls, "_recall_impl.py no longer decorates its response"
    assert all("tool_name" in keywords for _lineno, keywords in calls), calls
