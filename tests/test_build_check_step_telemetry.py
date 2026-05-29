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

from typing import Any

import pytest
import structlog.testing

_REQUIRED_KEYS = {
    "persist",
    "run_resolve",
    "log_event",
    "q_learning_dispatch",
    "finalize",
    "total",
}


def test_step_durations_ms_present_on_success(
    build_check_invoke: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FR03: success-path response carries ``step_durations_ms`` dict."""
    monkeypatch.setattr(
        "trw_mcp.scoring.process_outcome_for_event",
        lambda event_type, event_data=None, **_kw: [],
    )
    result = build_check_invoke()

    assert "step_durations_ms" in result, (
        "FR03: every successful trw_build_check MUST emit step_durations_ms. "
        "Pre-fix this telemetry didn't exist; regressions of the 'step "
        "accidentally O(corpus)' class were invisible."
    )
    durations = result["step_durations_ms"]
    assert isinstance(durations, dict)


def test_step_durations_ms_has_required_keys(
    build_check_invoke: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FR03: required key set matches the locked telemetry shape."""
    monkeypatch.setattr(
        "trw_mcp.scoring.process_outcome_for_event",
        lambda event_type, event_data=None, **_kw: [],
    )
    result = build_check_invoke()

    durations = result["step_durations_ms"]
    assert isinstance(durations, dict)
    keys = set(durations.keys())

    assert _REQUIRED_KEYS.issubset(keys), (
        f"FR03: step_durations_ms must contain {_REQUIRED_KEYS}, missing {_REQUIRED_KEYS - keys}, got {keys}"
    )


def test_step_durations_ms_values_non_negative(
    build_check_invoke: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FR03: every recorded duration is ``>= 0.0``."""
    monkeypatch.setattr(
        "trw_mcp.scoring.process_outcome_for_event",
        lambda event_type, event_data=None, **_kw: [],
    )
    result = build_check_invoke()

    durations = result["step_durations_ms"]
    assert isinstance(durations, dict)
    for key, value in durations.items():
        assert isinstance(value, float), f"{key}: expected float, got {type(value).__name__}"
        assert value >= 0.0, f"{key}: negative duration {value}"


def test_step_durations_total_is_sum_of_parts(
    build_check_invoke: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FR03: ``total`` equals the sum of all other steps (within ±5 ms tolerance).

    A divergence >5 ms indicates an instrumented gap (a step that runs
    but is not measured), which is the precise failure mode this
    telemetry guards against.
    """
    monkeypatch.setattr(
        "trw_mcp.scoring.process_outcome_for_event",
        lambda event_type, event_data=None, **_kw: [],
    )
    result = build_check_invoke()

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
    build_check_invoke: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FR03: ``total`` is the largest value in the dict (no step exceeds the call duration)."""
    monkeypatch.setattr(
        "trw_mcp.scoring.process_outcome_for_event",
        lambda event_type, event_data=None, **_kw: [],
    )
    result = build_check_invoke()

    durations = result["step_durations_ms"]
    assert isinstance(durations, dict)
    others = [float(v) for k, v in durations.items() if k != "total"]
    total = float(durations["total"])
    assert total >= max(others), f"FR03: total ({total}ms) must be >= max of other steps ({max(others)}ms)"


def test_step_durations_ms_mirrored_on_log_event(
    build_check_invoke: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FR03 acceptance #2: ``step_durations_ms`` mirrors onto ``build_check_complete`` log event.

    PRD-FIX-088 P1 Fix 1: the log call previously fired BEFORE the
    ``finalize`` and ``total`` steps were recorded, so even if the field
    were attached to the log payload it would be missing the last two
    keys. The fix moves the log emission to AFTER ``_record_step("total",
    ...)`` and passes the full dict, then this test pins the contract.
    """
    monkeypatch.setattr(
        "trw_mcp.scoring.process_outcome_for_event",
        lambda event_type, event_data=None, **_kw: [],
    )

    with structlog.testing.capture_logs() as logs:
        result = build_check_invoke()

    complete_events = [e for e in logs if e.get("event") == "build_check_complete"]
    assert complete_events, "FR03 #2: build_check_complete log event must fire on every success path"
    event = complete_events[-1]

    assert "step_durations_ms" in event, (
        "FR03 #2: build_check_complete log MUST mirror step_durations_ms onto "
        "the structlog event payload (currently missing — the log fired "
        "before the dict was complete)"
    )
    log_durations = event["step_durations_ms"]
    result_durations = result["step_durations_ms"]
    assert log_durations == result_durations, (
        f"FR03 #2: log step_durations_ms ({log_durations!r}) must equal "
        f"result step_durations_ms ({result_durations!r}). Divergence means "
        f"the log payload was assembled before all steps were recorded."
    )


@pytest.fixture(autouse=True)
def _structlog_defaults_for_capture() -> object:
    """File-scoped: reset structlog to defaults so ``capture_logs()`` sees WARN.

    A prior test's ``configure_logging()`` (server import / init_project) installs
    a filtering wrapper that drops WARN before ``capture_logs``'s processor, so
    these warning-assertion tests fail only in full-suite ordering. Save+restore
    (file-scoped, never a global reset — avoids the alphabetical-leak hazard).
    """
    import structlog

    _saved = structlog.get_config()
    structlog.reset_defaults()
    yield
    structlog.configure(**_saved)
