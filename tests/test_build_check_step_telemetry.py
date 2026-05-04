"""PRD-FIX-088 FR03: per-step latency telemetry on ``trw_build_check``.

Mirrors the precedent set by PRD-FIX-084 on ``trw_session_start``: every
``trw_build_check`` success path includes ``step_durations_ms`` with a
fixed key set so future regressions of the "step accidentally O(corpus)"
class are visible from a single log line.

These tests assert the SHAPE of ``step_durations_ms`` — not the
performance budget itself, which is covered by
``test_build_check_latency.py``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

_REQUIRED_KEYS = {
    "persist",
    "run_resolve",
    "log_event",
    "q_learning_dispatch",
    "finalize",
    "total",
}


def _invoke_build_check(tmp_project: Path) -> dict[str, object]:
    from tests.conftest import extract_tool_fn, make_test_server

    server = make_test_server("build")
    fn = extract_tool_fn(server, "trw_build_check")

    import trw_mcp.tools.build._registration as reg_mod

    original_resolve = reg_mod.resolve_trw_dir
    reg_mod.resolve_trw_dir = lambda: tmp_project / ".trw"  # type: ignore[assignment]
    try:
        return fn(tests_passed=True, test_count=1, scope="full")  # type: ignore[no-any-return]
    finally:
        reg_mod.resolve_trw_dir = original_resolve  # type: ignore[assignment]


def test_step_durations_ms_present_on_success(
    tmp_project: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FR03: success-path response carries ``step_durations_ms`` dict."""
    monkeypatch.setattr(
        "trw_mcp.scoring.process_outcome_for_event",
        lambda event_type, event_data=None: [],
    )
    result = _invoke_build_check(tmp_project)

    assert "step_durations_ms" in result, (
        "FR03: every successful trw_build_check MUST emit step_durations_ms. "
        "Pre-fix this telemetry didn't exist; regressions of the 'step "
        "accidentally O(corpus)' class were invisible."
    )
    durations = result["step_durations_ms"]
    assert isinstance(durations, dict)


def test_step_durations_ms_has_required_keys(
    tmp_project: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FR03: required key set matches the locked telemetry shape."""
    monkeypatch.setattr(
        "trw_mcp.scoring.process_outcome_for_event",
        lambda event_type, event_data=None: [],
    )
    result = _invoke_build_check(tmp_project)

    durations = result["step_durations_ms"]
    assert isinstance(durations, dict)
    keys = set(durations.keys())

    assert _REQUIRED_KEYS.issubset(keys), (
        f"FR03: step_durations_ms must contain {_REQUIRED_KEYS}, "
        f"missing {_REQUIRED_KEYS - keys}, got {keys}"
    )


def test_step_durations_ms_values_non_negative(
    tmp_project: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FR03: every recorded duration is ``>= 0.0``."""
    monkeypatch.setattr(
        "trw_mcp.scoring.process_outcome_for_event",
        lambda event_type, event_data=None: [],
    )
    result = _invoke_build_check(tmp_project)

    durations = result["step_durations_ms"]
    assert isinstance(durations, dict)
    for key, value in durations.items():
        assert isinstance(value, float), f"{key}: expected float, got {type(value).__name__}"
        assert value >= 0.0, f"{key}: negative duration {value}"


def test_step_durations_total_is_sum_of_parts(
    tmp_project: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FR03: ``total`` equals the sum of all other steps (within ±5 ms tolerance).

    A divergence >5 ms indicates an instrumented gap (a step that runs
    but is not measured), which is the precise failure mode this
    telemetry guards against.
    """
    monkeypatch.setattr(
        "trw_mcp.scoring.process_outcome_for_event",
        lambda event_type, event_data=None: [],
    )
    result = _invoke_build_check(tmp_project)

    durations = result["step_durations_ms"]
    assert isinstance(durations, dict)

    parts = sum(float(v) for k, v in durations.items() if k != "total")
    total = float(durations["total"])

    assert abs(total - parts) < 5.0, (
        f"FR03 sum-of-parts: total={total}ms, parts={parts}ms, diff={abs(total - parts):.2f}ms. "
        f"Tolerance is ±5 ms (allows un-instrumented init overhead). "
        f"A larger gap indicates a step is running but not being recorded."
    )


def test_step_durations_total_is_max(
    tmp_project: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FR03: ``total`` is the largest value in the dict (no step exceeds the call duration)."""
    monkeypatch.setattr(
        "trw_mcp.scoring.process_outcome_for_event",
        lambda event_type, event_data=None: [],
    )
    result = _invoke_build_check(tmp_project)

    durations = result["step_durations_ms"]
    assert isinstance(durations, dict)
    others = [float(v) for k, v in durations.items() if k != "total"]
    total = float(durations["total"])
    assert total >= max(others), (
        f"FR03: total ({total}ms) must be >= max of other steps ({max(others)}ms)"
    )
