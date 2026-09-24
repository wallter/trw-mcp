"""PRD-CORE-290-FR01: a per-member usage ledger in ``formation status --json``.

Aggregates EXISTING run events (no parallel ledger store, no new tool parameter):
- MCP serialized response bytes, measured by the tool-call wrapper;
- dispatch children's own token reports, recorded when their result arrives.
Bytes and tokens are separate categories with their own units. A replayed event
counts once, a reconnect's events all count, and unobservable usage is absent,
never zero. A child's tokens count once even when its result is polled twice,
and never inside a member's own byte total. NFR01: no MCP tool response gains a
field; NFR02: the formation total sits next to its outcome checks.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from tests._formation_test_support import FormationFixture, formation_env  # noqa: F401
from trw_mcp.telemetry.event_base import DispatchUsageEvent, ToolCallEvent
from trw_mcp.telemetry.unified_events import emit


def _tool_call(run: Path, response_bytes: int | None, session: str = "s1") -> ToolCallEvent:
    payload: dict[str, Any] = {"tool": "trw_recall", "outcome": "success"}
    if response_bytes is not None:
        payload["response_bytes"] = response_bytes
    event = ToolCallEvent(session_id=session, run_id=run.name, payload=payload)
    assert emit(event, run_dir=run, fallback_dir=None)
    return event


def _child(run: Path, child_id: str, input_tokens: int, output_tokens: int) -> None:
    payload = {"child_id": child_id, "client": "grok", "input_tokens": input_tokens, "output_tokens": output_tokens}
    assert emit(DispatchUsageEvent(session_id="s1", run_id=run.name, payload=payload), run_dir=run, fallback_dir=None)


def _events_file(run: Path) -> Path:
    return run / "meta" / f"events-{datetime.now(tz=timezone.utc).strftime('%Y-%m-%d')}.jsonl"


# ── One member ────────────────────────────────────────────────────────────────


def test_measured_bytes_sum_and_a_replayed_event_counts_once(tmp_path: Path) -> None:
    from trw_mcp.formation._usage import member_usage

    run = tmp_path / "run"
    _tool_call(run, 100)
    _tool_call(run, 50)
    events = _events_file(run)
    first = events.read_text(encoding="utf-8").splitlines()[0]
    events.write_text(events.read_text(encoding="utf-8") + first + "\n", encoding="utf-8")  # a replayed write

    usage = member_usage(run)

    assert usage["mcp_response_bytes"]["value"] == 150
    assert usage["mcp_response_bytes"]["events"] == 2
    assert (usage["mcp_response_bytes"]["unit"], usage["mcp_response_bytes"]["source"]) == ("bytes", "measured")


def test_a_reconnect_keeps_both_sessions_events(tmp_path: Path) -> None:
    from trw_mcp.formation._usage import member_usage

    run = tmp_path / "run"
    _tool_call(run, 70, session="before-reconnect")
    _tool_call(run, 30, session="after-reconnect")

    assert member_usage(run)["mcp_response_bytes"]["value"] == 100


def test_unobservable_usage_is_absent_never_zero(tmp_path: Path) -> None:
    from trw_mcp.formation._usage import member_usage

    empty = tmp_path / "empty"
    (empty / "meta").mkdir(parents=True)
    assert member_usage(empty) == {}

    unmeasured = tmp_path / "unmeasured"
    _tool_call(unmeasured, None)  # an event from before measurement existed
    assert "mcp_response_bytes" not in member_usage(unmeasured)


def test_coverage_says_how_many_calls_were_measured(tmp_path: Path) -> None:
    from trw_mcp.formation._usage import member_usage

    run = tmp_path / "run"
    _tool_call(run, 40)
    _tool_call(run, None)

    block = member_usage(run)["mcp_response_bytes"]
    assert (block["value"], block["events"], block["coverage"]) == (40, 1, "1/2 tool calls measured")


def test_bytes_and_tokens_stay_separate_categories(tmp_path: Path) -> None:
    from trw_mcp.formation._usage import member_usage

    run = tmp_path / "run"
    _tool_call(run, 10)
    _child(run, "job-1", input_tokens=1000, output_tokens=200)

    usage = member_usage(run)
    assert usage["mcp_response_bytes"]["unit"] == "bytes"
    assert usage["child_tokens"] == {
        "input": 1000,
        "output": 200,
        "unit": "tokens",
        "source": "self-reported",
        "children": 1,
        "first": usage["child_tokens"]["first"],
        "last": usage["child_tokens"]["last"],
    }
    assert "total" not in usage, "bytes and tokens are never added together"


# ── Formation: parent / child totals ─────────────────────────────────────────


def test_a_child_polled_twice_counts_once_and_never_inside_member_bytes(formation_env: FormationFixture) -> None:
    from trw_mcp.formation import create, join
    from trw_mcp.formation._usage import formation_usage, member_usage

    create(formation_env.orchestrator_run, formation_env.payload(), prds_dir=None)
    join("release-train", "impl-1", formation_env.member_runs["impl-1"], pin_key="pin-impl-1")
    _tool_call(formation_env.orchestrator_run, 500)
    _child(formation_env.orchestrator_run, "job-7", 900, 100)
    _child(formation_env.orchestrator_run, "job-7", 900, 100)  # the same child's result polled again
    _tool_call(formation_env.member_runs["impl-1"], 300)

    lead = member_usage(formation_env.orchestrator_run)
    assert lead["mcp_response_bytes"]["value"] == 500
    assert lead["child_tokens"]["children"] == 1 and lead["child_tokens"]["input"] == 900

    total = formation_usage([formation_env.orchestrator_run, formation_env.member_runs["impl-1"]])
    assert total["mcp_response_bytes"]["value"] == 800
    assert total["child_tokens"]["input"] == 900


# ── Measurement points ───────────────────────────────────────────────────────


def test_the_tool_wrapper_measures_the_serialized_response(tmp_path: Path) -> None:
    from trw_mcp.formation._usage import member_usage
    from trw_mcp.telemetry.tool_call_timing import wrap_tool

    run = tmp_path / "run"
    response = {"learnings": ["a" * 40], "count": 1}

    def trw_recall() -> dict[str, object]:
        return response

    wrapped = wrap_tool(trw_recall, run_dir_resolver=lambda: run, fallback_dir_resolver=lambda: None)
    assert wrapped() == response, "the response itself is untouched (NFR01)"

    assert member_usage(run)["mcp_response_bytes"]["value"] == len(json.dumps(response, default=str).encode("utf-8"))


def test_a_dispatch_result_records_its_childs_token_report(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from trw_mcp.dispatch._types import DispatchResult
    from trw_mcp.dispatch._usage import record_child_usage
    from trw_mcp.formation._usage import member_usage

    run = tmp_path / "run"
    (run / "meta").mkdir(parents=True)
    monkeypatch.setattr("trw_mcp.dispatch._usage._active_run", lambda: run)

    def _result(structured: dict[str, object] | None) -> DispatchResult:
        return DispatchResult(
            client="grok",
            argv_redacted=["grok"],
            read_only_enforced=True,
            exit_code=0,
            timed_out=False,
            duration_s=0.1,
            text="ok",
            raw_stdout="",
            raw_stderr="",
            structured=structured,
        )

    record_child_usage(
        _result({"usage": {"input_tokens": 12171, "cache_read_input_tokens": 7040, "output_tokens": 158}}), "job-1"
    )
    record_child_usage(_result({"text": "no usage block"}), "job-2")  # unobservable: nothing recorded

    child = member_usage(run)["child_tokens"]
    assert (child["input"], child["output"], child["cache_read"], child["children"]) == (12171, 158, 7040, 1)


# ── The CLI surface, and nothing else ────────────────────────────────────────


def test_formation_status_json_carries_the_ledger_next_to_its_outcomes(
    formation_env: FormationFixture, capsys: pytest.CaptureFixture[str]
) -> None:
    import argparse

    from trw_mcp.formation import create, join
    from trw_mcp.tools._formation_cli import run_formation

    create(formation_env.orchestrator_run, formation_env.payload(), prds_dir=None)
    join("release-train", "impl-1", formation_env.member_runs["impl-1"], pin_key="pin-impl-1")
    _tool_call(formation_env.member_runs["impl-1"], 300)

    with pytest.raises(SystemExit) as exc:
        run_formation(
            argparse.Namespace(formation_command="status", run_path=str(formation_env.orchestrator_run), as_json=True)
        )
    assert exc.value.code in (0, None)
    out = json.loads(capsys.readouterr().out)

    by_id = {m["member_id"]: m for m in out["members"]}
    assert by_id["impl-1"]["usage"]["mcp_response_bytes"]["value"] == 300
    assert "usage" not in by_id["impl-2"] or by_id["impl-2"]["usage"] == {}
    assert out["usage"]["mcp_response_bytes"]["value"] == 300
    # NFR02: the cost total sits beside the outcome checks for the same span.
    assert out["usage"]["outcomes"] == {"members": 2, "builds_passed": 0, "reviews_open": 0}


def test_the_status_row_projection_used_by_trw_status_gains_no_ledger_field() -> None:
    """NFR01: the ledger is read only through ``formation status``."""
    from trw_mcp.formation._status import MemberRow

    assert "usage" not in MemberRow(member_id="m", client="c", role="r", status="joined").as_dict()


def test_trw_dispatch_records_the_childs_usage_in_the_active_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Wiring: the MCP tool, not only the helper, records the child's report."""
    from fastmcp import FastMCP

    from tests.conftest import extract_tool_fn
    from trw_mcp.dispatch._types import DispatchResult
    from trw_mcp.formation._usage import member_usage
    from trw_mcp.tools.dispatch import register_dispatch_tools

    class _Cfg:
        dispatch_enabled_clients = ["grok"]
        dispatch_default_client = "grok"
        dispatch_default_models: dict[str, str] = {}
        dispatch_default_timeout_s = 60
        dispatch_default_read_only = True
        dispatch_role_client: dict[str, str] = {}

    class _Root:
        dispatch = _Cfg()

    run = tmp_path / "run"
    (run / "meta").mkdir(parents=True)
    result = DispatchResult(
        client="grok",
        argv_redacted=["grok"],
        read_only_enforced=True,
        exit_code=0,
        timed_out=False,
        duration_s=0.1,
        text="ok",
        raw_stdout="",
        raw_stderr="",
        structured={"usage": {"input_tokens": 50, "output_tokens": 5}},
    )
    monkeypatch.setattr("trw_mcp.tools.dispatch.get_config", lambda: _Root())
    monkeypatch.setattr("trw_mcp.tools.dispatch.dispatch", lambda _req: result)
    monkeypatch.setattr("trw_mcp.dispatch._usage._active_run", lambda: run)
    server = FastMCP("test")
    register_dispatch_tools(server)

    extract_tool_fn(server, "trw_dispatch")(prompt="review", client="grok", timeout_s=60, wait=True)

    assert member_usage(run)["child_tokens"]["input"] == 50
