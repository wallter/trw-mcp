"""Missing nudge call sites + the discarded reward signal (UF-042/UF-043/UF-026).

Three defects with the same shape: a mechanism that runs correctly but is not
connected to anything.

* **UF-042** — ``trw_build_check`` and ``trw_deliver`` never called the ceremony
  injector, so ``NudgeContext.build_passed`` had **no production writer
  anywhere**. That alone made the "Build failed -> revert to PLAN" branch of
  ``_reversion_prompt`` and the build-failure bypass in ``_select_nudge_pool``
  unreachable, independent of ledger UF-006.
* **UF-043** — ``trw_recall`` lost its ceremony-status injection in merge
  ``70bb84843f`` (2026-04-11), leaving ``ToolName.RECALL`` with no producer.
* **UF-026** — ``detect_proximal_signals`` produced genuine nudge->action
  records that were assigned to ``result["proximal_signals"]``, a reporting
  field, and never reached ``update_q_value``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from tests.conftest import extract_tool_fn, make_test_server
from trw_mcp.scoring.proximal_reward import ProximalSignal
from trw_mcp.state._ceremony_progress_state import CeremonyState, NudgeContext, write_ceremony_state
from trw_mcp.state._ceremony_state_model import ToolName


@pytest.fixture
def status_spy(monkeypatch: pytest.MonkeyPatch) -> list[NudgeContext | None]:
    """Capture the ``context`` argument reaching ``append_ceremony_status``."""
    seen: list[NudgeContext | None] = []

    def _spy(
        response: dict[str, object],
        trw_dir: Path | None = None,
        context: NudgeContext | None = None,
    ) -> dict[str, object]:
        seen.append(context)
        return response

    monkeypatch.setattr("trw_mcp.tools._ceremony_status.append_ceremony_status", _spy)
    return seen


# ---------------------------------------------------------------------------
# UF-042: build_passed finally has a production writer
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("tests_passed", "static_clean", "expected"),
    [(True, True, True), (False, True, False), (True, False, False)],
)
def test_build_check_writes_build_passed_into_the_context(
    tests_passed: bool,
    static_clean: bool,
    expected: bool,
    tmp_project: Path,
    status_spy: list[NudgeContext | None],
) -> None:
    """The reactive signal reflects the FULL build verdict, not just tests."""
    server = make_test_server("build")
    extract_tool_fn(server, "trw_build_check")(
        tests_passed=tests_passed,
        static_checks_clean=static_clean,
        test_count=4,
        scope="pytest tests/",
    )

    assert len(status_spy) == 1, status_spy
    context = status_spy[0]
    assert context is not None, "trw_build_check still bypasses the injector"
    assert context.tool_name == ToolName.BUILD_CHECK
    assert context.build_passed is expected
    assert context.tool_success is expected


def test_build_check_failure_reaches_the_reversion_prompt_end_to_end(tmp_project: Path) -> None:
    """No spy: the real injector, the real pool, the real response field."""
    trw_dir = tmp_project / ".trw"
    (trw_dir / "context").mkdir(parents=True, exist_ok=True)
    (trw_dir / "config.yaml").write_text("nudge_enabled: true\n", encoding="utf-8")
    write_ceremony_state(trw_dir, CeremonyState(session_started=True, checkpoint_count=1, phase="validate"))

    server = make_test_server("build")
    result = extract_tool_fn(server, "trw_build_check")(
        tests_passed=False,
        static_checks_clean=True,
        test_count=9,
        failure_count=2,
        scope="pytest tests/",
    )

    assert "ceremony_status" in result
    assert "revert to PLAN" in str(result.get("reversion_prompt", ""))
    # The build-failure context also force-selects the reactive pool.
    assert "nudge_content" in result


def test_build_check_pass_carries_no_reversion_prompt(tmp_project: Path) -> None:
    """Negative case: a green build must not suggest reverting to PLAN."""
    trw_dir = tmp_project / ".trw"
    (trw_dir / "context").mkdir(parents=True, exist_ok=True)
    (trw_dir / "config.yaml").write_text("nudge_enabled: true\n", encoding="utf-8")
    write_ceremony_state(trw_dir, CeremonyState(session_started=True, checkpoint_count=1, phase="validate"))

    server = make_test_server("build")
    result = extract_tool_fn(server, "trw_build_check")(
        tests_passed=True,
        static_checks_clean=True,
        test_count=9,
        scope="pytest tests/",
    )
    assert "reversion_prompt" not in result


@pytest.mark.parametrize(
    "raising_target",
    [
        "trw_mcp.tools._ceremony_status.append_ceremony_status",
        "trw_mcp.tools._ceremony_status_context.append_ceremony_status_for_tool",
    ],
    ids=["injector", "seam"],
)
def test_build_check_survives_a_raising_nudge_injection(
    raising_target: str,
    tmp_project: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Nudge injection is telemetry riding the build hot path — it must fail open.

    ``trw_deliver`` wraps its equivalent call in a fail-open try/except. The
    build_check call site added with UF-042 did not, so a filesystem error, a
    concurrent lock, or a serialization failure anywhere inside injection
    propagated out and failed ``trw_build_check`` — telemetry breaking a build
    check that had already completed. Both the seam and the injector underneath
    it are exercised, because either can be the thing that raises.
    """

    def _boom(*args: object, **kwargs: object) -> dict[str, object]:
        raise OSError("nudge state unwritable")

    monkeypatch.setattr(raising_target, _boom)

    server = make_test_server("build")
    result = extract_tool_fn(server, "trw_build_check")(
        tests_passed=True,
        static_checks_clean=True,
        test_count=7,
        scope="pytest tests/",
    )

    # The verdict survives intact; only the decoration is lost.
    assert result["tests_passed"] is True
    assert result["test_count"] == 7
    assert result["scope"] == "pytest tests/"
    assert "ceremony_status" not in result


def test_deliver_supplies_the_recorded_build_outcome(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    status_spy: list[NudgeContext | None],
) -> None:
    """Deliver reads the session's build result back out of ceremony state."""
    from unittest.mock import patch

    from tests._ceremony_helpers import make_ceremony_server

    tools = make_ceremony_server(monkeypatch, tmp_path)
    trw_dir = tmp_path / ".trw"
    (trw_dir / "learnings" / "entries").mkdir(parents=True, exist_ok=True)
    (trw_dir / "context").mkdir(parents=True, exist_ok=True)
    write_ceremony_state(trw_dir, CeremonyState(session_started=True, build_check_result="failed"))

    with (
        patch("trw_mcp.tools.ceremony.resolve_trw_dir", return_value=trw_dir),
        patch("trw_mcp.tools.ceremony.find_active_run", return_value=None),
        patch(
            "trw_mcp.tools.ceremony._do_instruction_sync",
            return_value={"status": "success", "learnings_promoted": 0, "path": "", "total_lines": 0},
        ),
        patch(
            "trw_mcp.tools._deferred_delivery._do_index_sync",
            return_value={"status": "success", "index": {}, "roadmap": {}},
        ),
        patch("trw_mcp.state._paths.resolve_project_root", return_value=tmp_path),
    ):
        tools["trw_deliver"].fn(
            skip_reflect=True,
            allow_unverified=True,
            unverified_reason="test fixture: synthetic run",
        )

    assert status_spy, "trw_deliver still bypasses the ceremony injector"
    context = status_spy[-1]
    assert context is not None
    assert context.tool_name == ToolName.DELIVER
    assert context.build_passed is False


# ---------------------------------------------------------------------------
# UF-043: ToolName.RECALL has a producer again
# ---------------------------------------------------------------------------


def test_recall_reaches_the_injector_with_a_recall_context(
    tmp_project: Path,
    status_spy: list[NudgeContext | None],
) -> None:
    server = make_test_server("learning")
    extract_tool_fn(server, "trw_recall")(query="nudge attribution")

    assert status_spy, "trw_recall still decorates nothing"
    assert status_spy[-1] is not None
    assert status_spy[-1].tool_name == ToolName.RECALL


def test_recall_response_carries_ceremony_status(tmp_project: Path) -> None:
    """Live path, no spy — the field actually lands on the payload."""
    server = make_test_server("learning")
    result = extract_tool_fn(server, "trw_recall")(query="nudge attribution")
    assert isinstance(result.get("ceremony_status"), str)


def test_ultra_compact_recall_stays_minimal(tmp_project: Path) -> None:
    """The ultra-compact projection is a token-budget contract; do not decorate it."""
    server = make_test_server("learning")
    result = extract_tool_fn(server, "trw_recall")(query="nudge attribution", ultra_compact=True)
    assert set(result) == {"learnings", "count", "ceremony_hint"}


# ---------------------------------------------------------------------------
# UF-026: proximal signals move a q_value
# ---------------------------------------------------------------------------


def _signal(learning_id: str) -> ProximalSignal:
    return ProximalSignal(
        learning_id=learning_id,
        signal_type="test_rerun",
        phase="implement",
        turn_offset=1,
    )


def test_proximal_signal_moves_the_q_value(tmp_path: Path) -> None:
    from trw_mcp.scoring import apply_proximal_rewards

    captured: dict[str, dict[str, object]] = {}

    def _lookup(lid: str, _trw_dir: Path, _entries: Path) -> tuple[Path | None, dict[str, object] | None]:
        if lid != "L-prox":
            return None, None
        data: dict[str, object] = {"id": lid, "q_value": 0.4, "q_observations": 2, "impact": 0.6}
        captured[lid] = data
        return None, data

    updated = apply_proximal_rewards(tmp_path / ".trw", [_signal("L-prox")], lookup_fn=_lookup)

    assert updated == ["L-prox"]
    # tests_passed carries reward 0.8, so the Q-value must move UP from 0.4.
    q_value = captured["L-prox"]["q_value"]
    assert isinstance(q_value, float)
    assert q_value > 0.4
    assert captured["L-prox"]["q_observations"] == 3
    history = captured["L-prox"]["outcome_history"]
    assert isinstance(history, list)
    assert history[-1].endswith(":proximal_tests_passed")


def test_proximal_rewards_skip_ids_with_no_stored_learning(tmp_path: Path) -> None:
    """Synthetic SYS-nudge-* ids have no entry; they must be silently skipped."""
    from trw_mcp.scoring import apply_proximal_rewards

    calls: list[str] = []

    def _lookup(lid: str, _trw_dir: Path, _entries: Path) -> tuple[Path | None, dict[str, object] | None]:
        calls.append(lid)
        return None, None

    updated = apply_proximal_rewards(
        tmp_path / ".trw",
        [_signal("SYS-nudge-standard-workflow-unattributed")],
        lookup_fn=_lookup,
    )
    assert updated == []
    assert calls == ["SYS-nudge-standard-workflow-unattributed"]


def test_proximal_rewards_are_deduplicated_per_learning(tmp_path: Path) -> None:
    """Repeated signals for one learning yield ONE q_observations increment."""
    from trw_mcp.scoring import apply_proximal_rewards

    seen: list[str] = []

    def _lookup(lid: str, _trw_dir: Path, _entries: Path) -> tuple[Path | None, dict[str, object] | None]:
        seen.append(lid)
        return None, {"id": lid, "q_value": 0.5, "q_observations": 0}

    apply_proximal_rewards(
        tmp_path / ".trw",
        [_signal("L-dup"), _signal("L-dup"), _signal("L-dup")],
        lookup_fn=_lookup,
    )
    assert seen == ["L-dup"]


def test_empty_signal_list_is_a_no_op(tmp_path: Path) -> None:
    from trw_mcp.scoring import apply_proximal_rewards

    def _lookup(_lid: str, _t: Path, _e: Path) -> tuple[Path | None, dict[str, object] | None]:
        raise AssertionError("lookup must not run for an empty signal list")

    assert apply_proximal_rewards(tmp_path / ".trw", [], lookup_fn=_lookup) == []


def test_proximal_scan_needs_both_streams_merged(tmp_path: Path) -> None:
    """UF-026 second half: neither stream alone can ever show an adjacency.

    ``nudge_shown`` is only written to ``.trw/context/session-events.jsonl``;
    ``build_check_complete`` is only written to the run's ``meta/events.jsonl``.
    Measured on this repo before the fix: 0 ``nudge_shown`` rows in any run's
    events.jsonl written since 2026-04, and 0 ``build_check_complete`` rows in
    session-events.jsonl ever — so the detector returned ``[]`` in production
    regardless of nudge volume, and bridging it to Q-learning without the merge
    would have wired a permanently-empty signal.
    """
    from trw_mcp.scoring.proximal_reward import detect_proximal_signals, read_proximal_event_window

    trw_dir = tmp_path / ".trw"
    (trw_dir / "context").mkdir(parents=True)
    run_dir = tmp_path / "run"
    (run_dir / "meta").mkdir(parents=True)

    (trw_dir / "context" / "session-events.jsonl").write_text(
        json.dumps(
            {
                "ts": "2026-07-24T10:00:00+00:00",
                "event": "nudge_shown",
                "learning_id": "L-two-stream",
                "data": {"learning_id": "L-two-stream", "phase": "validate"},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (run_dir / "meta" / "events.jsonl").write_text(
        json.dumps({"ts": "2026-07-24T10:00:05+00:00", "event": "build_check_complete"}) + "\n",
        encoding="utf-8",
    )

    # Each stream alone: structurally incapable of yielding a signal.
    from trw_mcp.scoring.proximal_reward import read_recent_events

    assert detect_proximal_signals(read_recent_events(trw_dir / "context" / "session-events.jsonl")) == []
    assert detect_proximal_signals(read_recent_events(run_dir / "meta" / "events.jsonl")) == []

    # Merged and time-ordered: the adjacency is visible.
    signals = detect_proximal_signals(read_proximal_event_window(trw_dir, run_dir))
    assert [s["learning_id"] for s in signals] == ["L-two-stream"]
    assert signals[0]["signal_type"] == "test_rerun"


def test_proximal_window_orders_by_timestamp_not_by_stream(tmp_path: Path) -> None:
    """A build that PRECEDED the nudge must not be read as a response to it."""
    from trw_mcp.scoring.proximal_reward import detect_proximal_signals, read_proximal_event_window

    trw_dir = tmp_path / ".trw"
    (trw_dir / "context").mkdir(parents=True)
    run_dir = tmp_path / "run"
    (run_dir / "meta").mkdir(parents=True)

    (trw_dir / "context" / "session-events.jsonl").write_text(
        json.dumps(
            {
                "ts": "2026-07-24T10:00:09+00:00",
                "event": "nudge_shown",
                "data": {"learning_id": "L-late", "phase": "validate"},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (run_dir / "meta" / "events.jsonl").write_text(
        json.dumps({"ts": "2026-07-24T10:00:01+00:00", "event": "build_check_complete"}) + "\n",
        encoding="utf-8",
    )

    window = read_proximal_event_window(trw_dir, run_dir)
    assert [str(e.get("event")) for e in window] == ["build_check_complete", "nudge_shown"]
    assert detect_proximal_signals(window) == []


def test_real_nudge_then_real_build_check_produces_a_proximal_signal(tmp_project: Path) -> None:
    """End-to-end on the production writers, not hand-written fixtures.

    A real nudge emission (``append_ceremony_status_for_tool`` ->
    ``record_nudge_shown`` -> session-events.jsonl) followed by a real
    ``trw_build_check`` (-> ``_log_build_event`` -> run meta/events.jsonl) is the
    exact sequence UF-026 is meant to reward. Both writers are the live ones.
    """
    from trw_mcp.scoring.proximal_reward import detect_proximal_signals, read_proximal_event_window
    from trw_mcp.state._ceremony_progress_state import record_nudge_shown

    trw_dir = tmp_project / ".trw"
    (trw_dir / "context").mkdir(parents=True, exist_ok=True)
    run_dir = tmp_project / "run"
    (run_dir / "meta").mkdir(parents=True)
    write_ceremony_state(trw_dir, CeremonyState(session_started=True, checkpoint_count=1, phase="validate"))

    # 1. A nudge is shown (production writer, real learning id).
    record_nudge_shown(trw_dir, "L-e2e-prox", "validate", turn=7)

    # 2. The agent responds by running the build (production writer).
    server = make_test_server("build")
    extract_tool_fn(server, "trw_build_check")(
        tests_passed=True,
        static_checks_clean=True,
        test_count=3,
        scope="pytest tests/",
        run_path=str(run_dir),
    )

    signals = detect_proximal_signals(read_proximal_event_window(trw_dir, run_dir))
    assert [s["learning_id"] for s in signals] == ["L-e2e-prox"], read_proximal_event_window(trw_dir, run_dir)


def test_delivery_metrics_feeds_signals_into_the_reward_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """UF-026 wiring: the deferred metrics step must CALL the bridge.

    The signals were previously terminal — assigned to a reporting key and
    dropped. This asserts the call happens with the detected signals, which is
    the seam that was missing, not the arithmetic (covered above).
    """
    from trw_mcp.tools import _deferred_steps_learning as mod

    run_dir = tmp_path / "run"
    (run_dir / "meta").mkdir(parents=True)
    (run_dir / "meta" / "events.jsonl").write_text("", encoding="utf-8")

    detected = [_signal("L-bridge")]
    forwarded: list[Any] = []

    monkeypatch.setattr(
        "trw_mcp.scoring.proximal_reward.detect_proximal_signals",
        lambda *_a, **_kw: detected,
    )

    def _fake_apply(_trw_dir: Path, signals: list[ProximalSignal]) -> list[str]:
        forwarded.append(signals)
        return ["L-bridge"]

    monkeypatch.setattr("trw_mcp.scoring.apply_proximal_rewards", _fake_apply)

    result = mod._step_delivery_metrics(tmp_path / ".trw", run_dir)

    assert forwarded == [detected], "detected signals never reached apply_proximal_rewards"
    assert result["proximal_q_updates"] == ["L-bridge"]
