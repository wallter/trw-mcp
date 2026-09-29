"""Task-scaled wall-clock tracking and ETA forecasting (PRD-CORE-338).

One deep module over the run event stream that ``FileEventLogger.log_event``
already writes: every time it reports is either a recorded ``ts`` or the
``now`` its caller injects. Nothing here reads the process clock (NFR01), so
every function is deterministic for a given event list and ``now``.

Interface:

- :func:`is_tracked` decides whether a run gets any time output at all (FR02).
- :func:`load_timeline` folds recorded events into a :class:`Timeline` (FR01).
- :func:`forecast` turns a timeline into an ETA range with its basis and N.
- :func:`drift` compares that range with the declared target.
- :func:`time_block` assembles the ``trw_status`` block (FR05);
  :func:`clock_mismatch` checks a checkpoint message's leading stamp (FR06).

Every ETA is a range labelled ``inferred``: it proves the range spans the two
recorded rates, not that the future rate stays inside it.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Protocol

#: Event name written by ``trw_init`` when ``advanced.target_utc`` is declared.
TIME_TARGET_EVENT = "time_target"
#: Event name written once, the first time ``trw_status`` computes an ETA.
TIME_FORECAST_EVENT = "time_forecast"
#: A leading message stamp further than this from the machine ts is flagged.
CLOCK_TOLERANCE_SECONDS = 300
_TRAILING_WINDOW = 3
_STAMP_SCAN_CHARS = 32
_ISO_STAMP_RE = re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})")
_HHMM_STAMP_RE = re.compile(r"(?<!\d)([01]?\d|2[0-3]):([0-5]\d)(?::[0-5]\d)?\s?(?:Z|UTC)(?![A-Za-z])")


class _TimeConfig(Protocol):
    time_tracking_enabled: bool


@dataclass(frozen=True)
class SliceEstimate:
    """One declared PRD slice estimate, in hours (FR04)."""

    id: str
    hours_min: float
    hours_max: float


@dataclass(frozen=True)
class Timeline:
    """Recorded facts about one run; every instant is a recorded ``ts``."""

    started_at: datetime | None = None
    last_event_at: datetime | None = None
    target: datetime | None = None
    #: First completion per slice id, in completion order (FR03: counts once).
    done: tuple[tuple[str, datetime], ...] = ()
    estimates: tuple[SliceEstimate, ...] = ()
    first_forecast: Mapping[str, object] | None = field(default=None, compare=False)

    @property
    def remaining(self) -> tuple[SliceEstimate, ...]:
        done_ids = {slice_id for slice_id, _ in self.done}
        return tuple(est for est in self.estimates if est.id not in done_ids)


def parse_ts(raw: object) -> datetime | None:
    """Parse an ISO ``ts`` to an aware UTC datetime; None when malformed, naive or unrepresentable in UTC."""
    if not isinstance(raw, str) or not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return None
        return parsed.astimezone(timezone.utc)
    # trw-fail-silent-allow: a malformed or out-of-range ts is skipped by contract (PRD-CORE-338 class A)
    except (ValueError, OverflowError):
        return None


def iso(value: datetime) -> str:
    """Compact UTC rendering used in every time output (second precision, ``Z``)."""
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def is_tracked(
    run: Mapping[str, object],
    *,
    events: Iterable[Mapping[str, object]] = (),
    prd_time: Sequence[SliceEstimate] = (),
    config: _TimeConfig,
) -> bool:
    """FR02: True only for runs where time output is worth its tokens.

    The kill switch ``time_tracking_enabled`` wins. Otherwise a run is tracked
    when it is COMPREHENSIVE, a formation member's run (``formation_id`` was
    stamped at join), has a declared target (``target_utc`` in run.yaml or a
    ``time_target`` event), or scopes a PRD with 2 or more slice estimates.
    """
    if not config.time_tracking_enabled:
        return False
    if str(run.get("complexity_class") or "") == "COMPREHENSIVE":
        return True
    if run.get("formation_id") or run.get("target_utc"):
        return True
    if len(prd_time) >= 2:
        return True
    return any(event.get("event") == TIME_TARGET_EVENT for event in events)


def load_timeline(
    events: Iterable[Mapping[str, object]],
    checkpoints: Iterable[Mapping[str, object]] = (),
    prd_time: Sequence[SliceEstimate] = (),
    *,
    target_utc: object = None,
) -> Timeline:
    """Fold recorded events into a :class:`Timeline`; malformed ``ts`` values are skipped."""
    started_at: datetime | None = None
    last_event_at: datetime | None = None
    target = parse_ts(target_utc)
    first_forecast: Mapping[str, object] | None = None
    done: dict[str, datetime] = {}
    for record in [*events, *checkpoints]:
        ts = parse_ts(record.get("ts"))
        if ts is None:
            continue
        if last_event_at is None or ts > last_event_at:
            last_event_at = ts
        name = record.get("event")
        if name == "run_init" and (started_at is None or ts < started_at):
            started_at = ts
        elif name == TIME_TARGET_EVENT and target is None:
            target = parse_ts(record.get("target_utc"))
        elif name == TIME_FORECAST_EVENT and first_forecast is None:
            raw = record.get("forecast")
            first_forecast = raw if isinstance(raw, Mapping) else None
        slice_id = record.get("slice_done")
        # FR03: the first RECORDED completion per id wins; a later duplicate is ignored even when
        # its ts is earlier (a corrected clock must not rewrite history).
        if isinstance(slice_id, str) and slice_id and slice_id not in done:
            done[slice_id] = ts
    ordered = tuple(sorted(done.items(), key=lambda item: item[1]))
    return Timeline(
        started_at=started_at,
        last_event_at=last_event_at,
        target=target,
        done=ordered,
        estimates=tuple(prd_time),
        first_forecast=first_forecast,
    )


def _rate_per_hour(count: int, start: datetime, end: datetime) -> float | None:
    hours = (end - start).total_seconds() / 3600
    return count / hours if hours > 0 else None


def forecast(timeline: Timeline, *, now: datetime) -> dict[str, object]:
    """FR01: an ETA range from recorded rates, or from declared estimates when n < 2."""
    n_done = len(timeline.done)
    remaining = timeline.remaining
    total_known = bool(timeline.estimates)
    if n_done >= 2 and timeline.started_at is not None:
        stamps = [ts for _, ts in timeline.done]
        window_k = min(_TRAILING_WINDOW, n_done - 1)
        whole = _rate_per_hour(n_done, timeline.started_at, stamps[-1])
        trailing = _rate_per_hour(window_k, stamps[-1 - window_k], stamps[-1])
        rates = [rate for rate in (whole, trailing) if rate is not None]
        if rates:
            result: dict[str, object] = {
                "basis": "recorded_rates",
                "rate_whole_per_h": round(whole, 3) if whole is not None else None,
                "rate_trailing_per_h": round(trailing, 3) if trailing is not None else None,
                "n_done": n_done,
                "window_k": window_k,
                "label": "inferred",
            }
            if not total_known:
                result["status"] = "no_total"
                return result
            count = len(remaining)
            result["remaining"] = count
            result["eta_earliest"] = iso(now + timedelta(hours=count / max(rates)))
            result["eta_latest"] = iso(now + timedelta(hours=count / min(rates)))
            return result
    if total_known:
        # n < 2 (or no usable rate): the declared ranges of the slices not yet done;
        # all done gives the zero-width range [now, now].
        low = sum(est.hours_min for est in remaining)
        high = sum(est.hours_max for est in remaining)
        return {
            "basis": "declared_estimates",
            "n_done": n_done,
            "window_k": 0,
            "remaining": len(remaining),
            "eta_earliest": iso(now + timedelta(hours=low)),
            "eta_latest": iso(now + timedelta(hours=high)),
            "label": "inferred",
        }
    return {"status": "insufficient_data", "n_done": n_done}


def drift(prediction: Mapping[str, object], target: datetime | None, *, now: datetime) -> str:
    """FR01: on_track / at_risk / late against a UTC target, or no_target / unknown."""
    if target is None:
        return "no_target"
    remaining = prediction.get("remaining")
    if now > target and isinstance(remaining, int) and remaining > 0:
        return "late"
    earliest = parse_ts(prediction.get("eta_earliest"))
    latest = parse_ts(prediction.get("eta_latest"))
    if earliest is None or latest is None:
        return "unknown"
    if latest <= target:
        return "on_track"
    if earliest <= target:
        return "at_risk"
    return "late"


def elapsed_seconds(timeline: Timeline, *, now: datetime) -> int | None:
    """Observed seconds since ``run_init``; None when the run has no start event."""
    if timeline.started_at is None:
        return None
    return max(0, int((now - timeline.started_at).total_seconds()))


def time_block(timeline: Timeline, *, now: datetime, display_timezone: str = "UTC") -> dict[str, object]:
    """FR05: the compact ``time`` block for a tracked run (NFR02: at most 600 bytes)."""
    prediction = forecast(timeline, now=now)
    block: dict[str, object] = {
        "started_at": iso(timeline.started_at) if timeline.started_at else None,
        "elapsed_seconds": elapsed_seconds(timeline, now=now),
        "last_event_at": iso(timeline.last_event_at) if timeline.last_event_at else None,
        "label": "observed",
        "forecast": prediction,
        "drift": drift(prediction, timeline.target, now=now),
        "target": iso(timeline.target) if timeline.target else None,
    }
    local = _local_eta(prediction, display_timezone)
    if local:
        block["eta_local"] = local
    return block


def delivery_record(timeline: Timeline, *, now: datetime) -> dict[str, object]:
    """FR08: elapsed, first forecast, target, final drift, per-slice planned vs actual.

    ``forecast_error`` (R8-SCOPE T4(5)) compares the delivery instant with the
    FIRST forecast's range: 0 inside it, otherwise the signed seconds past the
    nearer bound (negative = delivered early). Absent when no forecast was ever
    recorded.
    """
    prediction = forecast(timeline, now=now)
    estimates = {est.id: est for est in timeline.estimates}
    slices: list[dict[str, object]] = []
    previous = timeline.started_at
    for slice_id, done_at in timeline.done:
        est = estimates.get(slice_id)
        actual = round((done_at - previous).total_seconds() / 3600, 3) if previous else None
        slices.append(
            {
                "id": slice_id,
                "estimate_hours_min": est.hours_min if est else None,
                "estimate_hours_max": est.hours_max if est else None,
                "actual_hours": actual,
            }
        )
        previous = done_at
    record: dict[str, object] = {
        "elapsed_seconds": elapsed_seconds(timeline, now=now),
        "first_forecast": dict(timeline.first_forecast) if timeline.first_forecast else None,
        "target": iso(timeline.target) if timeline.target else None,
        "drift": drift(prediction, timeline.target, now=now),
        "slices": slices,
    }
    first = timeline.first_forecast or {}
    earliest, latest = parse_ts(first.get("eta_earliest")), parse_ts(first.get("eta_latest"))
    if earliest is not None and latest is not None:
        if now < earliest:
            error = (now - earliest).total_seconds()
        elif now > latest:
            error = (now - latest).total_seconds()
        else:
            error = 0.0
        record["forecast_error_seconds"] = int(error)
    return record


def _local_eta(prediction: Mapping[str, object], display_timezone: str) -> str:
    """OQ-2 (lead decision): render the ETA range in ``display_timezone`` when it is not UTC."""
    if display_timezone.upper() in {"", "UTC", "Z"}:
        return ""
    from zoneinfo import ZoneInfo

    try:
        zone = ZoneInfo(display_timezone)
    # trw-fail-silent-allow: an unknown zone renders no eta_local (OQ-2); UTC output is unaffected
    except (ValueError, KeyError, OSError):
        return ""
    earliest = parse_ts(prediction.get("eta_earliest"))
    latest = parse_ts(prediction.get("eta_latest"))
    if earliest is None or latest is None:
        return ""
    first = earliest.astimezone(zone)
    last = latest.astimezone(zone)
    return f"{first:%Y-%m-%d %H:%M}-{last:%H:%M} {last:%Z}"


def _leading(pattern: re.Pattern[str], message: str) -> re.Match[str] | None:
    match = pattern.search(message)
    return match if match is not None and match.start() < _STAMP_SCAN_CHARS else None


def clock_mismatch(message: str, machine_ts: datetime) -> dict[str, object] | None:
    """FR06: flag a leading UTC-anchored stamp more than 300 s from the machine ts.

    Only a stamp that STARTS within the first 32 characters is checked: later
    stamps may cite past events legitimately. ``HH:MM`` stamps compare time-of-day with the minimal circular
    difference, so 23:58Z against 00:01Z is 180 s. The stamp is compared, never
    recorded (NFR01).
    """
    iso_match = _leading(_ISO_STAMP_RE, message)
    hhmm_match = _leading(_HHMM_STAMP_RE, message)
    if iso_match and (hhmm_match is None or iso_match.start() <= hhmm_match.start()):
        stamp = parse_ts(iso_match.group(0).replace(" ", "T"))
        if stamp is None:
            return None
        delta = abs((machine_ts - stamp).total_seconds())
        text = iso_match.group(0)
    elif hhmm_match:
        stamp_seconds = int(hhmm_match.group(1)) * 3600 + int(hhmm_match.group(2)) * 60
        utc = machine_ts.astimezone(timezone.utc)
        machine_seconds = utc.hour * 3600 + utc.minute * 60 + utc.second
        raw = abs(machine_seconds - stamp_seconds)
        delta = min(raw, 86400 - raw)
        text = hhmm_match.group(0)
    else:
        return None
    if delta <= CLOCK_TOLERANCE_SECONDS:
        return None
    return {"message_stamp": text, "machine_ts": iso(machine_ts), "delta_seconds": int(delta)}
