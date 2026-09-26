"""Golden-output regression tests for PRD-QUAL-143 FR06 (R2-011).

Goldens were captured verbatim from the pre-refactor inline case-dispatch in
``_nudge_template_select.py`` (256 lines) before its templates moved to YAML
under ``data/surfaces/`` (read via the existing ``load_pool_message`` loader).
Every branch of ``_select_nudge_template`` and ``_context_reactive_message``
is exercised here so the refactor cannot silently change rendered text.

Also asserts the selector module stays at or under the 60 effective-LOC
budget FR06 sets for "selection and substitution only".
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from trw_mcp.state._ceremony_state_model import CeremonyState, NudgeContext, ToolName
from trw_mcp.state._nudge_template_select import (
    _context_reactive_message,
    _select_nudge_template,
)


def _state(**kwargs: object) -> CeremonyState:
    return CeremonyState(**kwargs)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# _select_nudge_template goldens
# ---------------------------------------------------------------------------


def test_session_start_with_learnings_urgency_tiers() -> None:
    assert _select_nudge_template("session_start", _state(nudge_counts={"session_start": 0}), 5) == (
        "⚡ 5 prior learnings load in 1s for {client_display_name} — "
        "past discoveries become active context. "
        "Call trw_session_start() to begin."
    )
    assert _select_nudge_template("session_start", _state(nudge_counts={"session_start": 3}), 5) == (
        "⚡ 5 prior learnings load in 1s — "
        "each skipped loading costs future agents 5 re-discoveries. "
        "Call trw_session_start() to begin."
    )
    assert _select_nudge_template("session_start", _state(nudge_counts={"session_start": 5}), 5) == (
        "⚡ 5 learnings from prior sessions — "
        "skipping means re-discovering known gotchas from scratch. "
        "trw_session_start() takes <1s."
    )


def test_session_start_no_learnings_urgency_tiers() -> None:
    assert _select_nudge_template("session_start", _state(nudge_counts={"session_start": 0}), 0) == (
        "⚡ Session tracking starts with trw_session_start() — progress, checkpoints, and learnings attach to this run."
    )
    assert _select_nudge_template("session_start", _state(nudge_counts={"session_start": 3}), 0) == (
        "⚡ Session tracking not started — "
        "progress and learnings won't persist without it. "
        "trw_session_start() wires them to this run."
    )
    assert _select_nudge_template("session_start", _state(nudge_counts={"session_start": 5}), 0) == (
        "⚡ Session tracking not started — "
        "progress, checkpoints, and learnings are unattached to this run. "
        "Without it, this session's work is invisible to future agents. "
        "trw_session_start() takes 1s."
    )


def test_checkpoint_with_files_no_elapsed_urgency_tiers() -> None:
    assert _select_nudge_template(
        "checkpoint", _state(nudge_counts={"checkpoint": 0}, files_modified_since_checkpoint=4), 0
    ) == (
        "⚡ 4 files modified since last checkpoint — "
        "context compaction would lose this progress. "
        "trw_checkpoint() saves it in under 2s."
    )
    assert _select_nudge_template(
        "checkpoint", _state(nudge_counts={"checkpoint": 3}, files_modified_since_checkpoint=4), 0
    ) == (
        "⚡ 4 files modified since last checkpoint — "
        "compaction risk: 4 file(s) of progress lost with no recovery path. "
        "trw_checkpoint() saves it in under 2s."
    )
    assert _select_nudge_template(
        "checkpoint", _state(nudge_counts={"checkpoint": 5}, files_modified_since_checkpoint=4), 0
    ) == (
        "⚡ 4 files modified since last checkpoint — "
        "context compaction erases all 4 changes permanently. "
        "trw_checkpoint() saves everything in 2 seconds."
    )


def test_checkpoint_no_files_no_elapsed_urgency_tiers() -> None:
    assert _select_nudge_template("checkpoint", _state(nudge_counts={"checkpoint": 0}), 0) == (
        "⚡ No checkpoint in this session yet — "
        "a checkpoint saves state so context compaction can resume here. "
        "trw_checkpoint() takes under 2s."
    )
    assert _select_nudge_template("checkpoint", _state(nudge_counts={"checkpoint": 3}), 0) == (
        "⚡ No checkpoint yet this session — "
        "context compaction would lose all progress with no recovery path. "
        "trw_checkpoint() takes under 2s."
    )
    assert _select_nudge_template("checkpoint", _state(nudge_counts={"checkpoint": 5}), 0) == (
        "⚡ No checkpoint in this session — "
        "all session progress is unrecoverable if context compacts. "
        "trw_checkpoint() anchors it in 2 seconds."
    )


def test_checkpoint_elapsed_suffix_with_files() -> None:
    ts = (datetime.now(timezone.utc) - timedelta(minutes=7)).isoformat().replace("+00:00", "Z")
    state = _state(nudge_counts={"checkpoint": 3}, files_modified_since_checkpoint=2, last_checkpoint_ts=ts)
    result = _select_nudge_template("checkpoint", state, 0)
    assert result == (
        "⚡ 2 files modified since last checkpoint, 7 min ago — "
        "compaction risk: 2 file(s) of progress lost with no recovery path. "
        "trw_checkpoint() saves it in under 2s."
    )


def test_checkpoint_elapsed_suffix_no_files() -> None:
    ts = (datetime.now(timezone.utc) - timedelta(minutes=7)).isoformat().replace("+00:00", "Z")
    state = _state(nudge_counts={"checkpoint": 3}, files_modified_since_checkpoint=0, last_checkpoint_ts=ts)
    result = _select_nudge_template("checkpoint", state, 0)
    assert result == (
        "⚡ No checkpoint yet this session, 7 min ago — "
        "context compaction would lose all progress with no recovery path. "
        "trw_checkpoint() takes under 2s."
    )


def test_build_check_urgency_tiers() -> None:
    assert _select_nudge_template("build_check", _state(nudge_counts={"build_check": 0}), 0) == (
        "⚡ Verification not run yet — "
        "project-native checks catch integration issues before delivery. "
        "Run the repo's validation command, then record it with "
        "trw_build_check(tests_passed, test_count, failure_count, static_checks_clean, scope)."
    )
    assert _select_nudge_template("build_check", _state(nudge_counts={"build_check": 3}), 0) == (
        "⚡ Verification not run — "
        "test, build, lint, type, or schema failures may be undetected; delivery ships them as-is. "
        "Run project-native validation, then record it with "
        "trw_build_check(tests_passed, test_count, failure_count, static_checks_clean, scope)."
    )
    assert _select_nudge_template("build_check", _state(nudge_counts={"build_check": 5}), 0) == (
        "⚡ Verification not run — "
        "integration issues delivered without verification stay embedded in the result. "
        "Run the narrowest meaningful project-native check now and record the result."
    )


def test_review_urgency_tiers() -> None:
    assert _select_nudge_template("review", _state(nudge_counts={"review": 0}), 0) == (
        "⚡ Independent review not yet called — trw_review() catches spec drift that passing tests miss."
    )
    assert _select_nudge_template("review", _state(nudge_counts={"review": 3}), 0) == (
        "⚡ Review skipped — delivering without review ships unverified changes. trw_review() takes under 1 minute."
    )
    assert _select_nudge_template("review", _state(nudge_counts={"review": 5}), 0) == (
        "⚡ Independent review has not been called — "
        "spec drift and architectural issues go undetected. "
        "trw_review() is required before trw_deliver()."
    )


def test_deliver_static_message() -> None:
    assert _select_nudge_template("deliver", _state(), 0) == (
        "If you have material unfinished work, preserve it in a checkpoint or durable "
        "native handoff with a next-read pointer. Use trw_deliver() for completed work "
        "under existing evidence gates; recorded learnings remain recorded."
    )


def test_unknown_step_returns_empty_string() -> None:
    assert _select_nudge_template("nonexistent", _state(), 0) == ""


# ---------------------------------------------------------------------------
# _context_reactive_message goldens
# ---------------------------------------------------------------------------


def test_context_reactive_suppressed_on_failure_for_non_failure_aware_tool() -> None:
    ctx = NudgeContext(tool_name=ToolName.CHECKPOINT, tool_success=False)
    assert _context_reactive_message(ctx, _state()) is None


def test_context_reactive_build_check_failure_aware_reports_failure() -> None:
    ctx = NudgeContext(tool_name=ToolName.BUILD_CHECK, tool_success=False, build_passed=False)
    assert _context_reactive_message(ctx, _state()) == (
        "Build failed. If failures reveal a design flaw, revert to PLAN "
        "— fixing a plan costs less than patching broken code. "
        "If the work has execution bugs, fix them in-phase and re-run."
    )


def test_context_reactive_build_check_pass_urgency_tiers() -> None:
    ctx = NudgeContext(tool_name=ToolName.BUILD_CHECK, tool_success=True, build_passed=True)
    assert _context_reactive_message(ctx, _state(), urgency="low") == (
        "NEXT: trw_review() — independent verification catches spec drift that passing tests miss"
    )
    assert _context_reactive_message(ctx, _state(), urgency="medium") == (
        "NEXT: trw_review() is recommended — independent verification catches spec drift that passing tests miss"
    )
    assert _context_reactive_message(ctx, _state(), urgency="high") == (
        "NEXT: trw_review() SHOULD be performed — independent verification catches spec drift that passing tests miss"
    )


def test_context_reactive_build_check_none_returns_none() -> None:
    ctx = NudgeContext(tool_name=ToolName.BUILD_CHECK, tool_success=True, build_passed=None)
    assert _context_reactive_message(ctx, _state()) is None


def test_context_reactive_review_p0_found() -> None:
    ctx = NudgeContext(tool_name=ToolName.REVIEW, tool_success=True, review_p0_count=2)
    assert _context_reactive_message(ctx, _state()) == (
        "P0 findings detected. A separate agent MUST remediate "
        "— the reviewer SHALL NOT fix its own findings. "
        "THEN: re-validate with trw_build_check()."
    )


def test_context_reactive_review_no_p0() -> None:
    ctx = NudgeContext(tool_name=ToolName.REVIEW, tool_success=True, review_p0_count=0)
    assert _context_reactive_message(ctx, _state()) == (
        "If the work is complete, use trw_deliver() under existing evidence gates; "
        "otherwise preserve material progress with a next-read pointer."
    )


def test_context_reactive_checkpoint_ok() -> None:
    ctx = NudgeContext(tool_name=ToolName.CHECKPOINT, tool_success=True)
    assert _context_reactive_message(ctx, _state()) == (
        "Progress saved. If you found a non-obvious reusable insight, trw_learn() "
        "persists it across sessions. Do not manufacture a learning for routine work."
    )


def test_context_reactive_learn_ceremony_modes() -> None:
    ctx = NudgeContext(tool_name=ToolName.LEARN, tool_success=True)
    assert _context_reactive_message(ctx, _state(), ceremony_mode="light") == (
        "Learning persisted. Continue the work, then call trw_deliver() when done."
    )
    assert _context_reactive_message(ctx, _state(), ceremony_mode="full") == (
        "Learning persisted. NEXT: trw_checkpoint() at the next milestone."
    )


def test_context_reactive_session_start_ceremony_modes() -> None:
    ctx = NudgeContext(tool_name=ToolName.SESSION_START, tool_success=True)
    assert _context_reactive_message(ctx, _state(), ceremony_mode="light") == (
        "State your approach before editing, then call trw_init() for new work or trw_status() to resume."
    )
    assert _context_reactive_message(ctx, _state(), ceremony_mode="full") == (
        "State your approach after reading FRAMEWORK.md, then call trw_init() for new work or trw_status() to resume."
    )


def test_context_reactive_deliver_learnings_count() -> None:
    ctx = NudgeContext(tool_name=ToolName.DELIVER, tool_success=True)
    assert _context_reactive_message(ctx, _state(learnings_this_session=0)) == (
        "Delivery recorded. No new learnings recorded this session."
    )
    assert _context_reactive_message(ctx, _state(learnings_this_session=3)) == (
        "Delivery recorded. 3 learning(s) recorded this session."
    )


def test_context_reactive_static_tool_messages() -> None:
    cases = {
        ToolName.INIT: "Run bootstrapped. Begin the planned work; checkpoint at the first milestone.",
        ToolName.RECALL: "Learnings recalled. Review them for relevant patterns before proceeding.",
        ToolName.STATUS: "Run status loaded. Resume from last checkpoint rather than re-implementing.",
        ToolName.PRD_VALIDATE: "PRD validated. NEXT: trw_init() to bootstrap the governed run.",
    }
    for tool, expected in cases.items():
        ctx = NudgeContext(tool_name=tool, tool_success=True)
        assert _context_reactive_message(ctx, _state()) == expected


def test_context_reactive_unknown_tool_returns_none() -> None:
    ctx = NudgeContext(tool_name="unknown_tool", tool_success=True)
    assert _context_reactive_message(ctx, _state()) is None


# ---------------------------------------------------------------------------
# FR06 module-size gate: selector keeps only selection + substitution
# ---------------------------------------------------------------------------


def _effective_loc(path: Path) -> int:
    """Count non-blank, non-comment, non-docstring source lines."""
    in_doc = False
    quote = ""
    n = 0
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if in_doc:
            if quote in line:
                in_doc = False
            continue
        matched_docstring_start = False
        for q in ('"""', "'''"):
            if line.startswith(q):
                rest = line[3:]
                if q in rest:
                    matched_docstring_start = True
                else:
                    in_doc = True
                    quote = q
                    matched_docstring_start = True
                break
        if matched_docstring_start:
            continue
        n += 1
    return n


def test_selector_module_is_at_most_60_effective_loc() -> None:
    module_path = Path(__file__).resolve().parent.parent / "src" / "trw_mcp" / "state" / "_nudge_template_select.py"
    eloc = _effective_loc(module_path)
    assert eloc <= 60, (
        f"_nudge_template_select.py is {eloc} effective LOC; FR06 caps selection+substitution "
        "logic at 60 eLOC after templates move to YAML (PRD-QUAL-143 FR06 / R2-011)"
    )
