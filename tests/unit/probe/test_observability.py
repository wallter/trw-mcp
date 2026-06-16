"""FR-01/FR-10 — trw_probe + trw_probe_budget_status MCP tools (PRD-CORE-144).

Proves the tools are registered, the probe tool returns a real ProbeResult
dict through the sandbox, budget is enforced + observable, and the budget
status tool is read-only and reconciles with usage.
"""

from __future__ import annotations

import sys

import pytest
from fastmcp import FastMCP

import trw_mcp.tools.trw_probe as probe_tool_mod
from trw_mcp.tools.trw_probe import register_probe_tools


@pytest.fixture(autouse=True)
def _reset_probe_run_state(monkeypatch: pytest.MonkeyPatch) -> None:
    probe_tool_mod._RUN_STATE.clear()
    # FR-06: trw_probe is gated OFF by default; enable it for the tool-behavior
    # tests below. The flag-state tests override this explicitly.
    monkeypatch.setenv("TRW_PROBE_ENABLED", "1")


def _get(server: FastMCP, name: str):
    from tests.conftest import extract_tool_fn

    return extract_tool_fn(server, name)


def test_both_tools_registered() -> None:
    server = FastMCP("test")
    register_probe_tools(server)
    from tests.conftest import get_tools_sync

    names = set(get_tools_sync(server).keys())
    assert "trw_probe" in names
    assert "trw_probe_budget_status" in names


def test_probe_tool_returns_real_result_dict() -> None:
    server = FastMCP("test")
    register_probe_tools(server)
    probe = _get(server, "trw_probe")
    out = probe(
        hypothesis="prints",
        command=f"{sys.executable} -c \"print('x')\"",
        run_id="run-A",
        timeout_s=10,
    )
    assert out["verdict"] == "supports"
    assert "x" in out["evidence"]["stdout"]


def test_budget_status_reconciles_with_usage() -> None:
    server = FastMCP("test")
    register_probe_tools(server)
    probe = _get(server, "trw_probe")
    status = _get(server, "trw_probe_budget_status")

    probe(
        hypothesis="h",
        command=f'{sys.executable} -c "print(1)"',
        run_id="run-B",
        timeout_s=10,
        hypothesis_id="H1",
        planning_mode="TRIANGULATED",
    )
    snap = status(run_id="run-B", planning_mode="TRIANGULATED")
    # FR-10 A1: counts reconciled with usage.
    assert snap["used"] == 1
    assert snap["remaining"] == 1
    assert snap["total"] == 2
    assert snap["by_hypothesis_id"] == {"H1": 1}


def test_budget_exhaustion_returns_typed_error_dict() -> None:
    server = FastMCP("test")
    register_probe_tools(server)
    probe = _get(server, "trw_probe")
    # DIRECT mode -> budget 0 -> first probe is exhausted.
    out = probe(
        hypothesis="h",
        command=f'{sys.executable} -c "print(1)"',
        run_id="run-C",
        timeout_s=10,
        planning_mode="DIRECT",
    )
    assert out["error"] == "probe_budget_exhausted"
    assert out["remaining"] == 0


def test_budget_status_on_unknown_run_is_read_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """FR-10 — querying status for an unknown run_id does NOT mutate state.

    A status query is observability; it must never insert a run-state entry
    (a write side-effect from a read). The unknown run returns a zero-usage
    view and ``_RUN_STATE`` is unchanged afterwards.
    """
    monkeypatch.setenv("TRW_PROBE_ENABLED", "0")  # gate irrelevant to status
    server = FastMCP("test")
    register_probe_tools(server)
    status = _get(server, "trw_probe_budget_status")

    assert probe_tool_mod._RUN_STATE == {}
    snap = status(run_id="never-probed", planning_mode="TRIANGULATED")
    # Zero-usage view for the unknown run.
    assert snap["used"] == 0
    assert snap["remaining"] == snap["total"] == 2
    assert snap["by_hypothesis_id"] == {}
    # Critical: the read did NOT create run state.
    assert "never-probed" not in probe_tool_mod._RUN_STATE
    assert probe_tool_mod._RUN_STATE == {}


def test_probe_disabled_when_flag_off_returns_typed_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FR-06 / §9 Phase 1 — TRW_PROBE_ENABLED off returns probe_disabled.

    The tool stays registered (so the surface is stable) but is inert: it
    returns a typed ``probe_disabled`` error WITHOUT spawning a subprocess.
    """
    monkeypatch.setenv("TRW_PROBE_ENABLED", "0")
    server = FastMCP("test")
    register_probe_tools(server)
    # Tool is still registered even when the flag is off.
    from tests.conftest import get_tools_sync

    assert "trw_probe" in set(get_tools_sync(server).keys())

    probe = _get(server, "trw_probe")
    out = probe(
        hypothesis="should not run",
        command=f"{sys.executable} -c \"print('NOPE')\"",
        run_id="run-DISABLED",
        timeout_s=10,
        planning_mode="TRIANGULATED",
    )
    assert out["error"] == "probe_disabled"
    assert "TRW_PROBE_ENABLED" in out["remediation"]
    # No budget was consumed because the gate fired before _state_for.
    assert "run-DISABLED" not in probe_tool_mod._RUN_STATE


def test_probe_enabled_when_flag_on_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    """FR-06 — with TRW_PROBE_ENABLED on, the probe executes normally."""
    monkeypatch.setenv("TRW_PROBE_ENABLED", "true")
    server = FastMCP("test")
    register_probe_tools(server)
    probe = _get(server, "trw_probe")
    out = probe(
        hypothesis="prints",
        command=f"{sys.executable} -c \"print('on')\"",
        run_id="run-ENABLED",
        timeout_s=10,
        planning_mode="TRIANGULATED",
    )
    assert out["verdict"] == "supports"
    assert "on" in out["evidence"]["stdout"]


def test_probe_event_published_to_real_telemetry_pipeline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FR-09 — a completed probe PUBLISHES a ProbeEvent through the pipeline.

    The unified telemetry pipeline (not just a logger line) must receive the
    event so the H4 meta-proposer can compute probe yield. We monkeypatch the
    pipeline singleton's ``enqueue`` and assert the probe-event projection
    lands on it with the decisive-verdict payload.
    """
    import trw_mcp.telemetry.pipeline as pipeline_mod

    enqueued: list[dict[str, object]] = []

    class _FakePipeline:
        def enqueue(self, event: dict[str, object]) -> None:
            enqueued.append(event)

    monkeypatch.setattr(pipeline_mod.TelemetryPipeline, "get_instance", classmethod(lambda cls: _FakePipeline()))

    server = FastMCP("test")
    register_probe_tools(server)
    probe = _get(server, "trw_probe")
    probe(
        hypothesis="prints",
        command=f"{sys.executable} -c \"print('x')\"",
        run_id="run-PUB",
        timeout_s=10,
        planning_mode="TRIANGULATED",
    )

    assert len(enqueued) == 1, f"expected exactly one published probe event, got {enqueued}"
    event = enqueued[0]
    assert event["event_type"] == "probe"
    assert event["emitter"] == "probe_harness"
    assert event["run_id"] == "run-PUB"
    # Payload carries the decisive-verdict signal the yield metric consumes.
    payload = event["payload"]
    assert isinstance(payload, dict)
    assert payload["verdict"] == "supports"
    assert payload["decisive"] is True


def test_probe_event_publish_failure_does_not_break_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FR-09 — a telemetry-pipeline failure never fails the probe (fail-open)."""
    import trw_mcp.telemetry.pipeline as pipeline_mod

    def _boom(cls: object) -> object:
        raise RuntimeError("pipeline down")

    monkeypatch.setattr(pipeline_mod.TelemetryPipeline, "get_instance", classmethod(_boom))

    server = FastMCP("test")
    register_probe_tools(server)
    probe = _get(server, "trw_probe")
    out = probe(
        hypothesis="prints",
        command=f"{sys.executable} -c \"print('x')\"",
        run_id="run-PUB-FAIL",
        timeout_s=10,
        planning_mode="TRIANGULATED",
    )
    # Probe still returns a real result despite the publish blowing up.
    assert out["verdict"] == "supports"


def test_identical_probe_served_from_cache_without_extra_budget() -> None:
    server = FastMCP("test")
    register_probe_tools(server)
    probe = _get(server, "trw_probe")
    status = _get(server, "trw_probe_budget_status")
    cmd = f"{sys.executable} -c \"print('cached')\""
    first = probe(hypothesis="h", command=cmd, run_id="run-D", timeout_s=10, planning_mode="TRIANGULATED")
    second = probe(hypothesis="h", command=cmd, run_id="run-D", timeout_s=10, planning_mode="TRIANGULATED")
    assert first["cache_hit"] is False
    assert second["cache_hit"] is True
    # Cache hit did not consume a second budget slot.
    snap = status(run_id="run-D", planning_mode="TRIANGULATED")
    assert snap["used"] == 1
