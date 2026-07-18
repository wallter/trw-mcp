"""PRD-FIX-084: per-step latency telemetry on trw_session_start.

The five regressions of the "step in step_sanitize_and_maintain
accidentally O(corpus)" class (commits c7ff20f84, ba328b177, d67ad5651,
27e4e4562, a65427847) were each only diagnosable via py-spy on a live
server because end-to-end latency was visible but per-step duration was
not. session_start_ok now carries step_durations_ms so the slow step
name is in the log line.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest

from tests.conftest import extract_tool_fn, make_test_server


def _get_session_start_fn() -> Any:
    """Extract the trw_session_start tool function via shared conftest helpers."""
    return extract_tool_fn(make_test_server("ceremony"), "trw_session_start")


def test_session_start_emits_step_durations_ms(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """trw_session_start result includes step_durations_ms with all named keys."""
    fn = _get_session_start_fn()

    # Run the function. Many steps may exit fast; what matters is that
    # the result includes the step_durations_ms key with float values.
    # verbose=True: step_durations_ms is a diagnostic sub-block that
    # compact-by-default (PRD-IMPROVE-MCP-04) folds into health_summary; the
    # full per-step telemetry is only returned in verbose mode.
    result: dict[str, Any] = fn(ctx=None, query="*", verbose=True)

    assert "step_durations_ms" in result, (
        f"trw_session_start result must include step_durations_ms; got keys: {sorted(result.keys())}"
    )
    durations = result["step_durations_ms"]
    assert isinstance(durations, dict)

    # Every named step that did not partial-fail should have a duration.
    expected_keys = {
        "recall",
        "run_resolve",
        "surface_stamp",
        "log_event",
        "telemetry",
        "counter",
        "sanitize_maintain",
        "phase_recall",
        "embed_health",
        "assertion_health",
        "finalize",
        "total",
    }
    present_keys = set(durations.keys())
    assert "total" in present_keys, "total must always be recorded"
    # Most other steps may legitimately be absent under partial-failure;
    # but in a clean run with no errors, all named steps run.
    if not result.get("errors"):
        missing = expected_keys - present_keys
        assert not missing, f"Clean session_start should record all named steps; missing: {missing}"

    # Every duration is a non-negative float.
    for key, value in durations.items():
        assert isinstance(value, (int, float)), f"{key} duration not numeric: {value!r}"
        assert float(value) >= 0.0, f"{key} duration negative: {value}"


def test_session_start_total_is_at_least_sum_of_named_steps(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """total_ms must be >= sum of the named step durations (within tolerance).

    They should be approximately equal in a clean run; total may be
    slightly larger because some bookkeeping happens outside the
    timed blocks (e.g. assertion health, ceremony status injection,
    deferral checks). It must never be smaller.
    """
    fn = _get_session_start_fn()
    # verbose=True so the diagnostic step_durations_ms block is returned
    # (compact-by-default folds it into health_summary — PRD-IMPROVE-MCP-04).
    result: dict[str, Any] = fn(ctx=None, query="*", verbose=True)
    durations = result["step_durations_ms"]
    if "total" not in durations:
        pytest.skip("total not recorded (partial failure path)")

    total = float(durations["total"])
    named_sum = sum(
        float(durations[k])
        for k in (
            "recall",
            "run_resolve",
            "surface_stamp",
            "log_event",
            "telemetry",
            "counter",
            "sanitize_maintain",
            "phase_recall",
        )
        if k in durations
    )
    # total must include the named steps. Allow 1ms slack for floating point.
    assert total + 1.0 >= named_sum, (
        f"total ({total:.2f} ms) < sum of named steps ({named_sum:.2f} ms); durations: {durations}"
    )


def test_session_start_step_durations_logged_with_event(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """session_start_ok event payload includes step_durations_ms field."""
    from structlog.testing import capture_logs

    fn = _get_session_start_fn()

    with capture_logs() as logs:
        fn(ctx=None, query="*")

    ok_events = [e for e in logs if e.get("event") == "session_start_ok"]
    assert ok_events, "session_start_ok event must fire on success"
    payload = ok_events[-1]
    assert "step_durations_ms" in payload, (
        f"session_start_ok event must include step_durations_ms; got keys: {sorted(payload.keys())}"
    )
    assert isinstance(payload["step_durations_ms"], dict)
    assert "total" in payload["step_durations_ms"]


def test_finalize_and_payload_trim_are_included_in_latency(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from trw_mcp.tools import ceremony

    real_finalize = ceremony.finalize_session_start
    real_trim = ceremony.trim_session_start_payload

    def slow_finalize(*args: Any, **kwargs: Any) -> None:
        time.sleep(0.03)
        real_finalize(*args, **kwargs)

    def slow_trim(*args: Any, **kwargs: Any) -> Any:
        time.sleep(0.03)
        return real_trim(*args, **kwargs)

    monkeypatch.setattr(ceremony, "finalize_session_start", slow_finalize)
    monkeypatch.setattr(ceremony, "trim_session_start_payload", slow_trim)

    result: dict[str, Any] = _get_session_start_fn()(ctx=None, query="*", verbose=True)
    durations = result["step_durations_ms"]
    assert float(durations["finalize"]) >= 25.0
    assert float(durations["total"]) >= float(durations["finalize"]) + 25.0


def test_session_start_warm_p95_under_5_seconds(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SLO regression test: warm trw_session_start p95 < 5000 ms.

    Calls session_start 10 times; asserts the p95 of calls 2-10 is under
    5 s. Calls 2-10 are "warm" because call 1 paid embedder cold-load
    and any one-time maintenance. This is the durable defense against
    the regression class that ate 2026-05-03.

    The threshold is intentionally generous (5 s) -- in steady state the
    measured value is ~1 s. The test fires on regressions that push the
    warm budget into the danger zone.
    """
    fn = _get_session_start_fn()

    call_total_ms: list[float] = []
    for _ in range(10):
        # verbose=True surfaces step_durations_ms in the result (compact mode
        # folds it into health_summary — PRD-IMPROVE-MCP-04).
        result: dict[str, Any] = fn(ctx=None, query="warm-perf", verbose=True)
        durations = result.get("step_durations_ms", {})
        if "total" in durations:
            call_total_ms.append(float(durations["total"]))

    # Use calls 2..10 as warm. Sort and pick p95.
    warm = sorted(call_total_ms[1:])
    assert len(warm) >= 5, f"need at least 5 warm samples; got {len(warm)}"
    p95_index = max(0, int(0.95 * len(warm)) - 1)
    p95 = warm[p95_index]
    assert p95 < 5000.0, (
        f"warm p95 trw_session_start latency = {p95:.1f} ms (cap 5000). "
        f"Recent regression suspected. All warm samples: {warm}"
    )
