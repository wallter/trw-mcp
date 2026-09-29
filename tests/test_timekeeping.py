"""PRD-CORE-338 FR01/FR02/FR06/NFR01: the pure timekeeping module.

Every test injects ``now``; none reads the wall clock (NFR04).
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

import pytest

from trw_mcp.state import timekeeping as tk

START = "2026-09-26T10:00:00Z"


def _at(hhmm: str) -> datetime:
    return datetime.fromisoformat(f"2026-09-26T{hhmm}:00+00:00")


def _events(*done: tuple[str, str]) -> list[dict[str, object]]:
    events: list[dict[str, object]] = [{"ts": START, "event": "run_init"}]
    for slice_id, hhmm in done:
        events.append({"ts": f"2026-09-26T{hhmm}:00Z", "event": "checkpoint", "slice_done": slice_id})
    return events


def _estimates(*spec: tuple[str, float, float]) -> tuple[tk.SliceEstimate, ...]:
    return tuple(tk.SliceEstimate(id=i, hours_min=lo, hours_max=hi) for i, lo, hi in spec)


SEVEN = _estimates(*[(f"S{i}", 0.5, 1.0) for i in range(1, 8)])
WORKED = _events(("S1", "11:00"), ("S2", "11:30"), ("S3", "12:00"))


def test_forecast_worked_example() -> None:
    """FR01 worked example: 3 of 7 done by 12:00Z gives 14:00Z-14:40Z from 1.5/h and 2.0/h."""
    timeline = tk.load_timeline(WORKED, prd_time=SEVEN)
    result = tk.forecast(timeline, now=_at("12:00"))
    assert result == {
        "basis": "recorded_rates",
        "rate_whole_per_h": 1.5,
        "rate_trailing_per_h": 2.0,
        "n_done": 3,
        "window_k": 2,
        "label": "inferred",
        "remaining": 4,
        "eta_earliest": "2026-09-26T14:00:00Z",
        "eta_latest": "2026-09-26T14:40:00Z",
    }
    assert tk.drift(result, _at("14:30"), now=_at("12:00")) == "at_risk"


def test_insufficient_data_without_estimates() -> None:
    timeline = tk.load_timeline(_events(("S1", "11:00")))
    assert tk.forecast(timeline, now=_at("11:00")) == {"status": "insufficient_data", "n_done": 1}


def test_rates_without_a_declared_total_report_no_total() -> None:
    """n >= 2 with no declared slices: the rates are real, the remaining count is unknown, so no ETA."""
    result = tk.forecast(tk.load_timeline(WORKED), now=_at("12:00"))
    assert result["status"] == "no_total"
    assert "eta_earliest" not in result
    assert result["n_done"] == 3


def test_declared_estimates_branch() -> None:
    estimates = _estimates(("S1", 1, 2), ("S2", 2, 3), ("S3", 0.5, 1))
    before = tk.forecast(tk.load_timeline(_events(), prd_time=estimates), now=_at("10:00"))
    assert before == {
        "basis": "declared_estimates",
        "n_done": 0,
        "window_k": 0,
        "remaining": 3,
        "eta_earliest": "2026-09-26T13:30:00Z",
        "eta_latest": "2026-09-26T16:00:00Z",
        "label": "inferred",
    }
    after = tk.forecast(tk.load_timeline(_events(("S1", "11:00")), prd_time=estimates), now=_at("11:00"))
    assert (after["eta_earliest"], after["eta_latest"], after["n_done"]) == (
        "2026-09-26T13:30:00Z",
        "2026-09-26T15:00:00Z",
        1,
    )


def test_all_declared_slices_done_gives_zero_width_range() -> None:
    """r1 P2: the only declared slice done (n = 1) is a finished forecast, not insufficient_data."""
    result = tk.forecast(
        tk.load_timeline(_events(("S1", "11:00")), prd_time=_estimates(("S1", 1, 2))), now=_at("12:00")
    )
    assert result["basis"] == "declared_estimates"
    assert (result["remaining"], result["eta_earliest"], result["eta_latest"]) == (
        0,
        "2026-09-26T12:00:00Z",
        "2026-09-26T12:00:00Z",
    )


@pytest.mark.parametrize("raw", ["0001-01-01T00:00:00+01:00", "9999-12-31T23:59:59-01:00"])
def test_parse_ts_unrepresentable_instant_is_none(raw: str) -> None:
    """r1 P1: a stamp that overflows on UTC normalisation is invalid, never an exception."""
    assert tk.parse_ts(raw) is None


@pytest.mark.parametrize("second", ["11:40", "10:59"])
def test_repeated_slice_done_counts_once_at_its_first_completion(second: str) -> None:
    """s2 r1 P2: the first recorded completion wins, even over a later duplicate with an earlier ts."""
    events = _events(("S1", "11:00"), ("S1", second))
    assert tk.load_timeline(events).done == (("S1", _at("11:00")),)


def test_malformed_and_naive_ts_are_skipped() -> None:
    events: list[dict[str, object]] = [
        *_events(),
        {"ts": "garbage", "slice_done": "S1"},
        {"ts": "2026-09-26T11:00:00", "slice_done": "S2"},
    ]
    timeline = tk.load_timeline(events)
    assert timeline.done == ()
    assert timeline.last_event_at == _at("10:00")


@pytest.mark.parametrize(
    ("target", "now", "expected"),
    [
        ("14:40", "12:00", "on_track"),  # eta_latest == target
        ("14:00", "12:00", "at_risk"),  # eta_earliest == target
        ("13:59", "12:00", "late"),  # eta_earliest > target
        ("11:59", "12:00", "late"),  # now past target with work remaining
    ],
)
def test_drift_boundaries(target: str, now: str, expected: str) -> None:
    prediction = tk.forecast(tk.load_timeline(WORKED, prd_time=SEVEN), now=_at("12:00"))
    assert tk.drift(prediction, _at(target), now=_at(now)) == expected


def test_drift_without_target_or_eta() -> None:
    assert tk.drift({"status": "insufficient_data"}, None, now=_at("12:00")) == "no_target"
    assert tk.drift({"status": "insufficient_data"}, _at("13:00"), now=_at("12:00")) == "unknown"


def test_timekeeping_ignores_the_process_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    """NFR01: with every clock patched to raise, the module's output is unchanged."""
    now = _at("12:00")
    baseline = tk.time_block(tk.load_timeline(WORKED, prd_time=SEVEN), now=now)

    class _NoClock(datetime):
        @classmethod
        def now(cls, tz: Any = None) -> datetime:  # type: ignore[override]
            raise AssertionError("timekeeping read the process clock")

        @classmethod
        def utcnow(cls) -> datetime:  # type: ignore[override]
            raise AssertionError("timekeeping read the process clock")

    def _no_time() -> float:
        raise AssertionError("timekeeping read time.time")

    monkeypatch.setattr(tk, "datetime", _NoClock)
    monkeypatch.setattr(time, "time", _no_time)
    assert tk.time_block(tk.load_timeline(WORKED, prd_time=SEVEN), now=now) == baseline
    assert tk.clock_mismatch("12:30Z begin", now) is not None


def test_time_block_byte_budget() -> None:
    """NFR02: the serialized tracked block stays within 600 bytes, even with a local ETA."""
    events: list[dict[str, object]] = [
        *WORKED,
        {"ts": START, "event": "time_target", "target_utc": "2026-09-26T14:30:00Z"},
    ]
    block = tk.time_block(tk.load_timeline(events, prd_time=SEVEN), now=_at("12:00"), display_timezone="America/Denver")
    assert block["drift"] == "at_risk"
    assert block["eta_local"] == "2026-09-26 08:00-08:40 MDT"
    assert len(json.dumps(block).encode()) <= 600


def test_time_block_utc_display_adds_no_local_key() -> None:
    block = tk.time_block(tk.load_timeline(WORKED, prd_time=SEVEN), now=_at("12:00"))
    assert "eta_local" not in block
    assert block["elapsed_seconds"] == 7200
    assert block["started_at"] == START


def test_unknown_display_timezone_renders_nothing() -> None:
    block = tk.time_block(tk.load_timeline(WORKED, prd_time=SEVEN), now=_at("12:00"), display_timezone="Mars/Olympus")
    assert "eta_local" not in block


# --- FR02 predicate --------------------------------------------------------


def _cfg(enabled: bool = True) -> SimpleNamespace:
    return SimpleNamespace(time_tracking_enabled=enabled)


@pytest.mark.parametrize(
    ("run", "events", "prd_time", "expected"),
    [
        ({"complexity_class": "MINIMAL"}, [], (), False),
        ({"complexity_class": "STANDARD"}, [], (), False),
        ({"complexity_class": "COMPREHENSIVE"}, [], (), True),
        ({"complexity_class": "MINIMAL", "formation_id": "f-1"}, [], (), True),
        ({"complexity_class": "MINIMAL", "target_utc": "2026-09-26T18:00:00Z"}, [], (), True),
        ({"complexity_class": "MINIMAL"}, [{"event": "time_target"}], (), True),
        ({"complexity_class": "STANDARD"}, [], _estimates(("S1", 1, 2), ("S2", 1, 2)), True),
        ({"complexity_class": "STANDARD"}, [], _estimates(("S1", 1, 2)), False),
    ],
)
def test_time_tracking_predicate_is_task_scaled(
    run: dict[str, object], events: list[dict[str, object]], prd_time: tuple[tk.SliceEstimate, ...], expected: bool
) -> None:
    assert tk.is_tracked(run, events=events, prd_time=prd_time, config=_cfg()) is expected


def test_kill_switch_makes_every_run_untracked() -> None:
    run = {"complexity_class": "COMPREHENSIVE", "formation_id": "f-1", "target_utc": "2026-09-26T18:00:00Z"}
    assert tk.is_tracked(run, events=[{"event": "time_target"}], config=_cfg(enabled=False)) is False


# --- FR06 clock mismatch ------------------------------------------------------


@pytest.mark.parametrize(
    ("message", "machine", "expected_delta"),
    [
        ("12:05Z done S1", "12:00:00", None),  # 300 s: within tolerance
        ("12:05:01Z done", "12:00:00", None),  # seconds ignored on HH:MM stamps: 300 s
        ("12:06Z done S1", "12:00:00", 360),
        ("[13:25 UTC] check-in", "12:40:00", 2700),
        ("2026-09-26T12:05:01Z done", "12:00:00", 301),  # ISO: 301 s warns
        ("2026-09-26T12:05:00Z done", "12:00:00", None),  # ISO: 300 s does not
        ("2026-09-26T06:30:00-06:00 MT", "12:00:00", 1800),  # offset-bearing ISO
        ("23:58Z wrap", "00:01:00", None),  # circular: 180 s
        ("23:50Z wrap", "00:01:00", 660),
        ("no stamp here", "12:00:00", None),
        ("09:58 MT local only", "12:00:00", None),  # bare local time is not checked
        ("a long preamble that pushes it past 32 chars 12:40Z", "12:00:00", None),
        # r1 P2: a stamp STARTING inside the window is read whole even when it ends past char 32.
        ("[2026-09-26T12:30:00.123456+00:00] progress", "12:00:00", 1800),
        ("0001-01-01T00:00:00+01:00 progress", "12:00:00", None),  # r1 P1: unrepresentable, no crash
    ],
)
def test_leading_stamp_arms(message: str, machine: str, expected_delta: int | None) -> None:
    day = "2026-09-27" if machine.startswith("00:") else "2026-09-26"
    machine_ts = datetime.fromisoformat(f"{day}T{machine}+00:00").astimezone(timezone.utc)
    result = tk.clock_mismatch(message, machine_ts)
    if expected_delta is None:
        assert result is None
    else:
        assert result is not None
        assert result["delta_seconds"] == expected_delta
        assert result["machine_ts"] == tk.iso(machine_ts)


# --- PRD-CORE-338-FR08 + R8-SCOPE T4(5): delivery record ------------------------


def _delivered(first: dict[str, object] | None) -> tk.Timeline:
    events: list[dict[str, object]] = list(WORKED)
    if first is not None:
        events.append({"ts": "2026-09-26T12:00:00Z", "event": "time_forecast", "forecast": first})
    return tk.load_timeline(events, prd_time=_estimates(("S1", 0.5, 1), ("S2", 0.25, 0.5), ("S3", 1, 2)))


FIRST = {"eta_earliest": "2026-09-26T13:00:00Z", "eta_latest": "2026-09-26T14:00:00Z"}


def test_delivery_record_planned_vs_actual() -> None:
    record = tk.delivery_record(_delivered(FIRST), now=_at("13:30"))
    assert record["elapsed_seconds"] == 12600
    assert record["first_forecast"] == FIRST
    assert record["slices"] == [
        {"id": "S1", "estimate_hours_min": 0.5, "estimate_hours_max": 1, "actual_hours": 1.0},
        {"id": "S2", "estimate_hours_min": 0.25, "estimate_hours_max": 0.5, "actual_hours": 0.5},
        {"id": "S3", "estimate_hours_min": 1, "estimate_hours_max": 2, "actual_hours": 0.5},
    ]
    assert record["forecast_error_seconds"] == 0  # inside the first range


@pytest.mark.parametrize(("now", "error"), [("12:30", -1800), ("13:00", 0), ("14:00", 0), ("15:15", 4500)])
def test_forecast_error_arms(now: str, error: int) -> None:
    """One case per arm: early (negative), both bounds inclusive (0), late (positive)."""
    assert tk.delivery_record(_delivered(FIRST), now=_at(now))["forecast_error_seconds"] == error


def test_forecast_error_absent_without_a_recorded_forecast() -> None:
    record = tk.delivery_record(_delivered(None), now=_at("13:30"))
    assert "forecast_error_seconds" not in record
    assert record["first_forecast"] is None
