"""PRD-CORE-294 FR04/FR06: the transition-nudge selector plus Jev suggestions.

FR04 gives the selector its budget/cooldown/dedup contract (tested here via
``select_transition_line`` directly, since precise counter control is
easiest without going through a full tool call each time). FR06 wires one
``trw_assess`` suggestion per judgment-call transition into
``append_ceremony_status_for_tool`` when ``backend_enablement()`` is true,
adding zero bytes when it is false. Those wiring tests go through the real
public seam, not private internals, per the task's real-path requirement.

FR04 slice 2 adds learning-anchored candidates on the SAME selector, on THREE
transitions -- a failed ``trw_build_check``, ``trw_before_edit_hint`` on a
file with anchored learnings, and a ``trw_deliver`` whose session touched
files anchored by learnings never shown this session -- all of which MUST
work with Jev disabled (``_build_check_learning_candidates`` /
``_deliver_learning_candidates`` / ``maybe_attach_edit_hint_transition_nudge``
never call ``backend_enablement()``).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from trw_mcp.state._ceremony_nudge_selectors import select_transition_line
from trw_mcp.state._ceremony_progress_state import (
    read_ceremony_state,
    write_ceremony_state,
)
from trw_mcp.tools._ceremony_status_context import (
    append_ceremony_status_for_tool,
    maybe_attach_edit_hint_transition_nudge,
)
from trw_mcp.tools._learnings_collector import LearningSummary


def _make_trw_dir(tmp_path: Path) -> Path:
    trw_dir = tmp_path / ".trw"
    (trw_dir / "context").mkdir(parents=True)
    (trw_dir / "config.yaml").write_text("nudge_enabled: true\n", encoding="utf-8")
    return trw_dir


def _advance_counter(trw_dir: Path, by: int) -> None:
    state = read_ceremony_state(trw_dir)
    state.tool_call_counter += by
    write_ceremony_state(trw_dir, state)


# ---------------------------------------------------------------------------
# FR04: selector budget / cooldown / dedup (direct calls for precise control)
# ---------------------------------------------------------------------------


def test_select_transition_line_never_repeats_shown_id(tmp_path: Path) -> None:
    trw_dir = tmp_path / ".trw"
    candidates = [("id-a", "line-a")]

    first = select_transition_line(trw_dir, session_key="s1", candidates=candidates)
    assert first == "line-a"

    _advance_counter(trw_dir, 10)  # clear cooldown so only dedup is exercised
    second = select_transition_line(trw_dir, session_key="s1", candidates=candidates)
    assert second is None


def test_select_transition_line_picks_first_unseen_candidate(tmp_path: Path) -> None:
    trw_dir = tmp_path / ".trw"
    first = select_transition_line(trw_dir, session_key="s1", candidates=[("id-a", "line-a")])
    assert first == "line-a"

    _advance_counter(trw_dir, 10)
    second = select_transition_line(trw_dir, session_key="s1", candidates=[("id-a", "line-a"), ("id-b", "line-b")])
    assert second == "line-b"


def test_select_transition_line_budget_exhaustion(tmp_path: Path) -> None:
    trw_dir = tmp_path / ".trw"
    for i in range(6):
        _advance_counter(trw_dir, 10)
        line = select_transition_line(trw_dir, session_key="s1", candidates=[(f"id-{i}", f"line-{i}")])
        assert line == f"line-{i}"

    _advance_counter(trw_dir, 10)
    seventh = select_transition_line(trw_dir, session_key="s1", candidates=[("id-6", "line-6")])
    assert seventh is None, "budget of 6 lines per session must be enforced"


def test_select_transition_line_cooldown_blocks_rapid_repeat(tmp_path: Path) -> None:
    trw_dir = tmp_path / ".trw"
    first = select_transition_line(trw_dir, session_key="s1", candidates=[("id-a", "line-a")])
    assert first == "line-a"

    # Fewer than 3 tool calls have elapsed -- even a brand-new id is blocked.
    second = select_transition_line(trw_dir, session_key="s1", candidates=[("id-b", "line-b")])
    assert second is None

    _advance_counter(trw_dir, 3)
    third = select_transition_line(trw_dir, session_key="s1", candidates=[("id-b", "line-b")])
    assert third == "line-b"


def test_select_transition_line_separate_sessions_get_own_budget(tmp_path: Path) -> None:
    trw_dir = tmp_path / ".trw"
    for i in range(6):
        _advance_counter(trw_dir, 10)
        select_transition_line(trw_dir, session_key="s1", candidates=[(f"id-{i}", f"line-{i}")])

    _advance_counter(trw_dir, 10)
    assert select_transition_line(trw_dir, session_key="s1", candidates=[("id-x", "line-x")]) is None
    # A different session key has never been shown anything -- its own budget applies.
    assert select_transition_line(trw_dir, session_key="s2", candidates=[("id-x", "line-x")]) == "line-x"


def test_select_transition_line_fail_open_on_write_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    trw_dir = tmp_path / ".trw"

    def _boom(*_args: object, **_kwargs: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr("trw_mcp.state._ceremony_progress_state.write_ceremony_state", _boom)
    result = select_transition_line(trw_dir, session_key="s1", candidates=[("id-a", "line-a")])
    assert result is None


def test_select_transition_line_empty_inputs_return_none(tmp_path: Path) -> None:
    trw_dir = tmp_path / ".trw"
    assert select_transition_line(trw_dir, session_key="", candidates=[("id-a", "line-a")]) is None
    assert select_transition_line(trw_dir, session_key="s1", candidates=[]) is None


# ---------------------------------------------------------------------------
# FR06: Jev suggestions through the real append_ceremony_status_for_tool seam
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("tool_name", "kwargs"),
    [
        ("build_check", {"build_passed": False}),
        ("review", {"review_verdict": "block", "review_p0_count": 2}),
        ("session_start", {}),
    ],
)
def test_jev_off_adds_no_transition_nudge_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, tool_name: str, kwargs: dict[str, object]
) -> None:
    trw_dir = _make_trw_dir(tmp_path)
    monkeypatch.setattr("trw_mcp.tools._assess_enablement.backend_enablement", lambda *_a, **_kw: (False, ""))

    response: dict[str, Any] = {}
    result = append_ceremony_status_for_tool(response, trw_dir, tool_name=tool_name, **kwargs)

    assert "transition_nudge" not in result
    # No state write either -- Jev-off must not even touch the selector.
    assert read_ceremony_state(trw_dir).transition_nudges == {}


@pytest.mark.parametrize(
    ("tool_name", "kwargs", "expected_substring"),
    [
        ("build_check", {"build_passed": False}, "Flaky or real?"),
        ("review", {"review_verdict": "block", "review_p0_count": 2}, "Blocker or follow-up?"),
        ("session_start", {}, "Jev is on"),
    ],
)
def test_jev_on_emits_the_expected_line_per_transition(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    tool_name: str,
    kwargs: dict[str, object],
    expected_substring: str,
) -> None:
    trw_dir = _make_trw_dir(tmp_path)
    monkeypatch.setattr("trw_mcp.tools._assess_enablement.backend_enablement", lambda *_a, **_kw: (True, "env"))

    result = append_ceremony_status_for_tool({}, trw_dir, tool_name=tool_name, **kwargs)

    assert expected_substring in str(result.get("transition_nudge", ""))


def test_jev_on_build_check_passed_adds_no_candidate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    trw_dir = _make_trw_dir(tmp_path)
    monkeypatch.setattr("trw_mcp.tools._assess_enablement.backend_enablement", lambda *_a, **_kw: (True, "env"))
    result = append_ceremony_status_for_tool({}, trw_dir, tool_name="build_check", build_passed=True)
    assert "transition_nudge" not in result


def test_jev_on_review_pass_with_no_p0_adds_no_candidate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    trw_dir = _make_trw_dir(tmp_path)
    monkeypatch.setattr("trw_mcp.tools._assess_enablement.backend_enablement", lambda *_a, **_kw: (True, "env"))
    result = append_ceremony_status_for_tool({}, trw_dir, tool_name="review", review_verdict="pass", review_p0_count=0)
    assert "transition_nudge" not in result


def test_jev_on_never_repeats_id_for_the_same_session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    trw_dir = _make_trw_dir(tmp_path)
    monkeypatch.setattr("trw_mcp.tools._assess_enablement.backend_enablement", lambda *_a, **_kw: (True, "env"))
    monkeypatch.setattr("trw_mcp.state._session_id.resolve_effective_session_id", lambda *_a: "sess-fixed")

    first = append_ceremony_status_for_tool({}, trw_dir, tool_name="session_start")
    assert "transition_nudge" in first

    # Clear the cooldown so a repeat here can only be explained by dedup.
    _advance_counter(trw_dir, 10)
    second = append_ceremony_status_for_tool({}, trw_dir, tool_name="session_start")
    assert "transition_nudge" not in second


def test_jev_selector_error_fails_open_and_preserves_the_response(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    trw_dir = _make_trw_dir(tmp_path)
    monkeypatch.setattr("trw_mcp.tools._assess_enablement.backend_enablement", lambda *_a, **_kw: (True, "env"))

    def _boom(*_args: object, **_kwargs: object) -> str:
        raise RuntimeError("selector exploded")

    monkeypatch.setattr("trw_mcp.state._ceremony_nudge_selectors.select_transition_line", _boom)

    response: dict[str, Any] = {"pre_existing": "value"}
    result = append_ceremony_status_for_tool(response, trw_dir, tool_name="session_start")

    assert "transition_nudge" not in result
    assert result["pre_existing"] == "value"
    assert "ceremony_status" in result


# ---------------------------------------------------------------------------
# FR04 slice 2: learning-anchored candidates -- work with Jev disabled
# ---------------------------------------------------------------------------

_LEARNING = LearningSummary(id="L-abc123", summary="config must load before startup runs")


def test_build_check_failure_learning_candidate_fires_with_jev_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    trw_dir = _make_trw_dir(tmp_path)
    monkeypatch.setattr("trw_mcp.tools._assess_enablement.backend_enablement", lambda *_a, **_kw: (False, ""))
    monkeypatch.setattr("trw_mcp.tools._learnings_collector.collect_learnings", lambda *_a, **_kw: [_LEARNING])

    result = append_ceremony_status_for_tool(
        {}, trw_dir, tool_name="build_check", build_passed=False, failure_hints=["AssertionError in app.py"]
    )

    assert result.get("transition_nudge") == (
        "Related learning L-abc123: config must load before startup runs. Found the cause? "
        "trw_learn records it; if L-abc123 is wrong, correct it by id."
    )


def test_build_check_failure_no_learning_match_adds_no_key_or_state_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    trw_dir = _make_trw_dir(tmp_path)
    monkeypatch.setattr("trw_mcp.tools._assess_enablement.backend_enablement", lambda *_a, **_kw: (False, ""))
    monkeypatch.setattr("trw_mcp.tools._learnings_collector.collect_learnings", lambda *_a, **_kw: [])

    result = append_ceremony_status_for_tool(
        {}, trw_dir, tool_name="build_check", build_passed=False, failure_hints=["nothing matches"]
    )

    assert "transition_nudge" not in result
    assert read_ceremony_state(trw_dir).transition_nudges == {}


def test_build_check_success_never_calls_collect_learnings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A passing build is not a transition -- no recall call at all."""
    trw_dir = _make_trw_dir(tmp_path)
    monkeypatch.setattr("trw_mcp.tools._assess_enablement.backend_enablement", lambda *_a, **_kw: (False, ""))

    def _boom(*_a: object, **_kw: object) -> list[LearningSummary]:
        raise AssertionError("collect_learnings must not be called for a passing build")

    monkeypatch.setattr("trw_mcp.tools._learnings_collector.collect_learnings", _boom)

    result = append_ceremony_status_for_tool(
        {}, trw_dir, tool_name="build_check", build_passed=True, failure_hints=["irrelevant"]
    )
    assert "transition_nudge" not in result


def test_build_check_learning_candidate_wins_over_jev_candidate_when_both_apply(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    trw_dir = _make_trw_dir(tmp_path)
    monkeypatch.setattr("trw_mcp.tools._assess_enablement.backend_enablement", lambda *_a, **_kw: (True, "env"))
    monkeypatch.setattr("trw_mcp.tools._learnings_collector.collect_learnings", lambda *_a, **_kw: [_LEARNING])

    result = append_ceremony_status_for_tool(
        {}, trw_dir, tool_name="build_check", build_passed=False, failure_hints=["AssertionError"]
    )

    assert "Related learning L-abc123" in str(result.get("transition_nudge", ""))
    assert "Flaky or real?" not in str(result.get("transition_nudge", ""))


def test_build_check_learning_candidate_fail_open_on_collect_learnings_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    trw_dir = _make_trw_dir(tmp_path)
    monkeypatch.setattr("trw_mcp.tools._assess_enablement.backend_enablement", lambda *_a, **_kw: (False, ""))

    def _boom(*_a: object, **_kw: object) -> list[LearningSummary]:
        raise RuntimeError("recall backend exploded")

    monkeypatch.setattr("trw_mcp.tools._learnings_collector.collect_learnings", _boom)

    response: dict[str, Any] = {"pre_existing": "value"}
    result = append_ceremony_status_for_tool(
        response, trw_dir, tool_name="build_check", build_passed=False, failure_hints=["boom"]
    )

    assert "transition_nudge" not in result
    assert result["pre_existing"] == "value"


def test_before_edit_hint_learning_candidate_fires_with_jev_off(tmp_path: Path) -> None:
    trw_dir = _make_trw_dir(tmp_path)
    response: dict[str, Any] = {"file_path": "app.py"}

    maybe_attach_edit_hint_transition_nudge(response, trw_dir, learnings=[_LEARNING])

    assert response["transition_nudge"] == (
        "Top learning for this file: L-abc123. If it is wrong or stale, correct it by id instead of adding a duplicate."
    )


def test_before_edit_hint_no_learnings_adds_no_key_or_state_write(tmp_path: Path) -> None:
    trw_dir = _make_trw_dir(tmp_path)
    response: dict[str, Any] = {"file_path": "app.py"}

    maybe_attach_edit_hint_transition_nudge(response, trw_dir, learnings=[])

    assert "transition_nudge" not in response
    assert read_ceremony_state(trw_dir).transition_nudges == {}


def test_before_edit_hint_fail_open_on_selector_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    trw_dir = _make_trw_dir(tmp_path)

    def _boom(*_a: object, **_kw: object) -> str:
        raise RuntimeError("selector exploded")

    monkeypatch.setattr("trw_mcp.state._ceremony_nudge_selectors.select_transition_line", _boom)

    response: dict[str, Any] = {"pre_existing": "value"}
    maybe_attach_edit_hint_transition_nudge(response, trw_dir, learnings=[_LEARNING])

    assert "transition_nudge" not in response
    assert response["pre_existing"] == "value"


def _stub_recall_context(modified_files: list[str]) -> object:
    class _Ctx:
        pass

    ctx = _Ctx()
    ctx.modified_files = modified_files  # type: ignore[attr-defined]
    return ctx


def test_deliver_learning_candidate_fires_with_jev_off_for_unseen_touched_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    trw_dir = _make_trw_dir(tmp_path)
    monkeypatch.setattr("trw_mcp.tools._assess_enablement.backend_enablement", lambda *_a, **_kw: (False, ""))
    monkeypatch.setattr(
        "trw_mcp.state.recall_context.build_recall_context", lambda *_a, **_kw: _stub_recall_context(["app.py"])
    )
    monkeypatch.setattr("trw_mcp.tools._learnings_collector.collect_learnings", lambda *_a, **_kw: [_LEARNING])

    result = append_ceremony_status_for_tool({}, trw_dir, tool_name="deliver")

    assert result.get("transition_nudge") == (
        "Learnings on files you changed that you never saw: L-abc123. Check them; correct by id if stale."
    )


def test_deliver_learning_candidate_drops_ids_already_shown_this_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    trw_dir = _make_trw_dir(tmp_path)
    monkeypatch.setattr("trw_mcp.tools._assess_enablement.backend_enablement", lambda *_a, **_kw: (False, ""))
    monkeypatch.setattr("trw_mcp.state._session_id.resolve_effective_session_id", lambda *_a, **_kw: "sess-deliver")
    monkeypatch.setattr(
        "trw_mcp.state.recall_context.build_recall_context", lambda *_a, **_kw: _stub_recall_context(["app.py"])
    )
    monkeypatch.setattr("trw_mcp.tools._learnings_collector.collect_learnings", lambda *_a, **_kw: [_LEARNING])

    # Mark L-abc123 as already shown this session via an earlier transition.
    select_transition_line(trw_dir, session_key="sess-deliver", candidates=[("learn:L-abc123", "already shown")])

    _advance_counter(trw_dir, 10)
    result = append_ceremony_status_for_tool({}, trw_dir, tool_name="deliver")

    assert "transition_nudge" not in result


def test_deliver_no_modified_files_adds_no_key_or_state_write(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    trw_dir = _make_trw_dir(tmp_path)
    monkeypatch.setattr("trw_mcp.tools._assess_enablement.backend_enablement", lambda *_a, **_kw: (False, ""))
    monkeypatch.setattr(
        "trw_mcp.state.recall_context.build_recall_context", lambda *_a, **_kw: _stub_recall_context([])
    )

    def _boom(*_a: object, **_kw: object) -> list[LearningSummary]:
        raise AssertionError("collect_learnings must not be called with no modified files")

    monkeypatch.setattr("trw_mcp.tools._learnings_collector.collect_learnings", _boom)

    result = append_ceremony_status_for_tool({}, trw_dir, tool_name="deliver")

    assert "transition_nudge" not in result
    assert read_ceremony_state(trw_dir).transition_nudges == {}


def test_transition_dedup_and_budget_hold_across_learning_and_jev_candidates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The shared selector's budget (6) counts every transition line together,
    learning-anchored or Jev, and a learning id shown once is never repeated."""
    trw_dir = _make_trw_dir(tmp_path)
    monkeypatch.setattr("trw_mcp.tools._assess_enablement.backend_enablement", lambda *_a, **_kw: (False, ""))
    monkeypatch.setattr("trw_mcp.tools._learnings_collector.collect_learnings", lambda *_a, **_kw: [_LEARNING])
    monkeypatch.setattr("trw_mcp.state._session_id.resolve_effective_session_id", lambda *_a, **_kw: "sess-budget")

    first = append_ceremony_status_for_tool(
        {}, trw_dir, tool_name="build_check", build_passed=False, failure_hints=["boom"]
    )
    assert "Related learning L-abc123" in str(first.get("transition_nudge", ""))

    _advance_counter(trw_dir, 10)
    second = append_ceremony_status_for_tool(
        {}, trw_dir, tool_name="build_check", build_passed=False, failure_hints=["boom"]
    )
    assert "transition_nudge" not in second, "the same learning id must never repeat for this session"
