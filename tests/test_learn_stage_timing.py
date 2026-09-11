"""Coarse foreground attribution through the real completion-event writer."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from trw_mcp.models.config import TRWConfig
from trw_mcp.tools import _learn_impl as learn
from trw_mcp.tools import telemetry


def test_decorated_execute_learn_records_content_free_stage_map(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config = TRWConfig(telemetry_enabled=True, platform_telemetry_enabled=False, dedup_enabled=False)
    monkeypatch.setattr(telemetry, "get_config", lambda: config)
    monkeypatch.setattr(telemetry, "_get_cached_run_dir", lambda **kw: None)
    monkeypatch.setattr(telemetry, "resolve_trw_dir", lambda: tmp_path)
    monkeypatch.setattr(telemetry, "_enqueue_to_pipeline", lambda event: None)
    monkeypatch.setattr(telemetry, "emit_tool_span", lambda *a, **kw: None)
    monkeypatch.setattr(learn, "run_accept_gates", lambda *a: {"status": "rejected"})

    from fastmcp import FastMCP

    from tests.conftest import get_tools_sync
    from trw_mcp.tools import learning

    monkeypatch.setattr(learning, "get_config", lambda: config)
    monkeypatch.setattr(learning, "resolve_trw_dir", lambda: tmp_path)
    server = FastMCP("learn-stage-test")
    learning.register_learning_tools(server)
    registered_learn = get_tools_sync(server)["trw_learn"].fn
    assert registered_learn(
        summary="SECRET summary",
        detail="SECRET detail",
        metadata={"client_profile": "", "model_id": ""},
    ) == {"status": "rejected"}
    rows = [
        json.loads(line) for line in (tmp_path / config.context_dir / "session-events.jsonl").read_text().splitlines()
    ]
    assert len(rows) == 1
    event = rows[0]
    assert event["learn_stage_ms"]["preflight"] >= 0
    assert event["tool_call_id"]
    assert event["event_id"]
    assert "SECRET" not in json.dumps(event["learn_stage_ms"])


@pytest.fixture
def timed_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    from trw_mcp.state import _learn_stage_timing as timing

    clock = [0.0]
    config = TRWConfig(telemetry_enabled=True, platform_telemetry_enabled=False, dedup_enabled=False)
    monkeypatch.setattr(timing, "monotonic", lambda: clock[0])
    monkeypatch.setattr(telemetry, "get_config", lambda: config)
    monkeypatch.setattr(telemetry, "_get_cached_run_dir", lambda **kw: None)
    monkeypatch.setattr(telemetry, "resolve_trw_dir", lambda: tmp_path)
    pipeline = []
    monkeypatch.setattr(telemetry, "_enqueue_to_pipeline", pipeline.append)
    monkeypatch.setattr(telemetry, "emit_tool_span", lambda *a, **kw: None)
    return clock, config, pipeline


@pytest.mark.parametrize("outcome", ["recorded", "rejected", "dedup", "error", "quarantined", "exception"])
def test_execute_stage_boundaries_and_cleanup(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, timed_environment, outcome
):
    from trw_mcp.state import _learn_stage_timing as timing
    from trw_mcp.tools import _ceremony_status_context, _learn_metadata

    clock, config, pipeline = timed_environment
    calls = []

    def work(name, seconds, result):
        def execute(*args, **kwargs):
            calls.append(name)
            clock[0] += seconds
            if name == "store" and outcome == "exception":
                raise ValueError("store failure")
            return result

        return execute

    monkeypatch.setattr(
        learn, "run_accept_gates", work("preflight", 1, {"status": "rejected"} if outcome == "rejected" else None)
    )
    monkeypatch.setattr(learn, "journal_accepted", work("journal", 2, None))
    monkeypatch.setattr(learn, "consume_journal", work("consume", 0, None))
    monkeypatch.setattr(_learn_metadata, "resolve_phase_origin", work("metadata", 3, ""))
    monkeypatch.setattr(learn, "resolve_learn_anchors", work("anchors", 5, ([], "unknown")))
    monkeypatch.setattr(learn, "_append_provenance_signed", work("poststore", 7, None))
    monkeypatch.setattr(learn, "_handle_consolidation", lambda *a: [])
    monkeypatch.setattr(learn, "_save_yaml_backup", lambda *a, **kw: tmp_path / "entry.yaml")
    monkeypatch.setattr(learn, "_increment_learning_capture", lambda *a: None)
    monkeypatch.setattr(_ceremony_status_context, "append_ceremony_status_for_tool", lambda *a, **kw: None)

    @telemetry.log_tool_call
    def trw_learn():
        return learn.execute_learn(
            "SECRET title",
            "SECRET body",
            tmp_path,
            config,
            _generate_learning_id=lambda: "L-fixture",
            _list_active_learnings=lambda *a: pytest.fail("quota-only active set loaded"),
            _check_and_handle_dedup=work("dedup", 4, {"status": "deduplicated"} if outcome == "dedup" else None),
            _adapter_store=work("store", 6, {"status": outcome}),
        )

    if outcome == "exception":
        with pytest.raises(ValueError, match="store failure"):
            trw_learn()
    else:
        result = trw_learn()
        assert result["status"] == ("deduplicated" if outcome == "dedup" else outcome)
    assert timing._current.get() is None
    assert len(pipeline) == 1
    event = json.loads((tmp_path / config.context_dir / "session-events.jsonl").read_text())
    stages = event["learn_stage_ms"]
    assert stages == pipeline[0]["learn_stage_ms"]
    assert event["event_id"] == pipeline[0]["event_id"]
    assert event["tool_call_id"] == pipeline[0]["tool_call_id"]
    expected = {"handler": 0.0, "preflight": 1000.0}
    if outcome != "rejected":
        expected.update(journal=2000.0, metadata=3000.0, active_set_dedup=4000.0)
    if outcome not in ("rejected", "dedup"):
        expected.update(anchors=5000.0, store=6000.0)
        if outcome != "exception":
            expected["poststore"] = 7000.0 if outcome == "recorded" else 0.0
    assert stages == expected
    assert sum(stages.values()) == clock[0] * 1000
    assert "SECRET" not in json.dumps(stages)
    assert ("journal" in calls) == (outcome != "rejected")
    assert ("consume" in calls) == (outcome in ("recorded", "dedup", "quarantined"))


def test_nested_calls_have_distinct_exclusive_timing(timed_environment):
    from trw_mcp.state import _learn_stage_timing as timing

    clock, _, pipeline = timed_environment

    @telemetry.log_tool_call
    def trw_learn(depth=0):
        timing.advance("preflight")
        clock[0] += 1
        if depth == 0:
            trw_learn(1)
            clock[0] += 2

    trw_learn()
    inner, outer = pipeline
    assert inner["learn_stage_ms"]["preflight"] == 1000
    assert outer["learn_stage_ms"]["preflight"] == 3000
    assert inner["event_id"] != outer["event_id"]
    assert inner["parent_event_id"] == outer["event_id"]
    assert timing._current.get() is None


@pytest.mark.parametrize("disabled,reviewer", [(True, False), (False, True)])
def test_existing_telemetry_and_reviewer_gates(monkeypatch, timed_environment, disabled, reviewer):
    from trw_mcp.state import _learn_stage_timing as timing
    from trw_mcp.state import _surface_role

    _, config, pipeline = timed_environment
    config.telemetry_enabled = not disabled
    monkeypatch.setattr(_surface_role, "reviewer_role_active", lambda: reviewer)

    @telemetry.log_tool_call
    def trw_learn():
        timing.advance("store")
        return "unchanged"

    assert trw_learn() == "unchanged"
    assert pipeline == []
    assert timing._current.get() is None


def test_clock_failure_does_not_change_primary_result(monkeypatch, timed_environment):
    from trw_mcp.state import _learn_stage_timing as timing

    _, _, pipeline = timed_environment

    def broken():
        raise OSError("clock unavailable")

    @telemetry.log_tool_call
    def trw_learn():
        monkeypatch.setattr(timing, "monotonic", broken)
        timing.advance("store")
        return "unchanged"

    assert trw_learn() == "unchanged"
    assert "learn_stage_ms" not in pipeline[0]
    assert timing._current.get() is None


def test_absent_collector_direct_call_is_noop(monkeypatch):
    from trw_mcp.state import _learn_stage_timing as timing

    monkeypatch.setattr(timing, "monotonic", lambda: pytest.fail("unmeasured call read clock"))
    timing.advance("store")
    assert timing._current.get() is None


def test_copied_contexts_do_not_share_mutable_measurements(timed_environment):
    from contextvars import copy_context

    from trw_mcp.state import _learn_stage_timing as timing

    clock, _, _ = timed_environment
    token = timing.begin("trw_learn")
    child = copy_context()
    clock[0] = 1
    child.run(timing.advance, "store")
    assert timing._current.get().stage == "handler"
    clock[0] = 2
    assert timing.finish() == {"handler": 2000}
    timing.restore(token)


def test_concurrent_decorated_calls_keep_separate_stage_totals(monkeypatch, timed_environment):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier, local

    from trw_mcp.state import _learn_stage_timing as timing

    _, _, pipeline = timed_environment
    clock = local()
    barrier = Barrier(2)
    monkeypatch.setattr(timing, "monotonic", lambda: clock.now)

    @telemetry.log_tool_call
    def trw_learn(seconds):
        timing.advance("store")
        barrier.wait(timeout=5)
        clock.now += seconds
        return seconds

    def run(seconds):
        clock.now = 0
        result = trw_learn(seconds)
        assert timing._current.get() is None
        return result

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(run, [1, 2])) == [1, 2]
    assert sorted(event["learn_stage_ms"]["store"] for event in pipeline) == [1000, 2000]
    assert len({event["tool_call_id"] for event in pipeline}) == 2


@pytest.mark.parametrize("boundary", ["begin", "finish"])
def test_clock_failure_at_wrapper_boundaries_is_fail_open(monkeypatch, timed_environment, boundary):
    from trw_mcp.state import _learn_stage_timing as timing

    _, _, pipeline = timed_environment

    def broken():
        raise OSError("clock unavailable")

    if boundary == "begin":
        monkeypatch.setattr(timing, "monotonic", broken)

    @telemetry.log_tool_call
    def trw_learn():
        monkeypatch.setattr(timing, "monotonic", broken)
        return "unchanged"

    assert trw_learn() == "unchanged"
    assert "learn_stage_ms" not in pipeline[0]
    assert timing._current.get() is None


def test_other_tool_does_not_collect_learn_stages(timed_environment):
    from trw_mcp.state import _learn_stage_timing as timing

    _, _, pipeline = timed_environment

    @telemetry.log_tool_call
    def trw_status():
        timing.advance("store")
        return "unchanged"

    assert trw_status() == "unchanged"
    assert "learn_stage_ms" not in pipeline[0]


def test_nested_completion_sink_time_is_not_parent_stage_work(monkeypatch, timed_environment):
    from trw_mcp.state import _learn_stage_timing as timing

    clock, _, pipeline = timed_environment
    original_write = telemetry._write_tool_event

    def slow_sink(*args, **kwargs):
        original_write(*args, **kwargs)
        clock[0] += 10

    monkeypatch.setattr(telemetry, "_write_tool_event", slow_sink)

    @telemetry.log_tool_call
    def trw_learn(depth=0):
        timing.advance("store")
        clock[0] += 1
        if depth == 0:
            trw_learn(1)
            clock[0] += 2

    trw_learn()
    inner, outer = pipeline
    assert inner["learn_stage_ms"]["store"] == 1000
    assert outer["learn_stage_ms"]["store"] == 3000
    assert timing._current.get() is None
