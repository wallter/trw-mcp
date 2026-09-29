"""Tool-side adapter for PRD-CORE-338 time tracking.

Belongs to the ``orchestration.py`` facade. :mod:`trw_mcp.state.timekeeping`
is pure (it never reads the clock or config); this module is the one place the
served tools read the machine clock and the project config for it, so every
surface asks the same :func:`~trw_mcp.state.timekeeping.is_tracked` question
before adding any time output (NFR02).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

from trw_mcp.state import timekeeping

if TYPE_CHECKING:
    from trw_mcp.models.config import TRWConfig
    from trw_mcp.state.persistence import FileEventLogger

logger = structlog.get_logger(__name__)


def utc_now() -> datetime:
    """The machine clock; the single seam tests patch to inject ``now`` (NFR04)."""
    return datetime.now(timezone.utc)


def _config() -> TRWConfig:
    from trw_mcp.models.config import get_config

    return get_config()


def prd_time_for(
    run: Mapping[str, object],
) -> tuple[tuple[timekeeping.SliceEstimate, ...], tuple[timekeeping.SliceEstimate, ...]]:
    """Slice estimates declared by the run's scoped PRDs (FR04): ``(all, trigger)``.

    Reads each scoped PRD's frontmatter only when ``prd_scope`` is non-empty
    (``{id}.md`` or a suffixed ``{id}-*.md``). ``all`` feeds the forecast and the
    delivery record for every tracked run; ``trigger`` holds only estimates from a
    PRD declaring 2 or more slices, because FR02's trigger is per PRD (two
    single-slice PRDs do not make a run tracked). Slice ids share one run-scoped namespace, matching ``slice_done``.
    A missing or malformed PRD contributes nothing (logged), never an error.
    """
    scope = run.get("prd_scope")
    if not isinstance(scope, list) or not scope:
        return (), ()
    from trw_mcp.models.requirements import PRDTime
    from trw_mcp.state._paths import resolve_project_root
    from trw_mcp.state.prd_utils import extract_prd_identifier, parse_frontmatter

    prds_dir = resolve_project_root() / _config().prds_relative_path
    estimates: list[timekeeping.SliceEstimate] = []
    trigger: list[timekeeping.SliceEstimate] = []
    for entry in scope:
        prd_id = extract_prd_identifier(str(entry))
        if prd_id is None:
            continue
        try:
            path = prds_dir / f"{prd_id}.md"
            if not path.exists():
                path = next(iter(sorted(prds_dir.glob(f"{prd_id}-*.md"))), path)
            raw = parse_frontmatter(path.read_text(encoding="utf-8")).get("time")
            declared = PRDTime.model_validate(raw) if raw is not None else None
        except Exception:  # justified: fail-open, one unreadable PRD must not break status or checkpoint
            logger.info("prd_time_unreadable", prd_id=prd_id, exc_info=True)
            continue
        if declared is None:
            continue
        own = [timekeeping.SliceEstimate(s.id, s.estimate_hours_min, s.estimate_hours_max) for s in declared.slices]
        estimates.extend(own)
        if len(own) >= 2:
            trigger.extend(own)
    return tuple(estimates), tuple(trigger)


def _tracked_timeline(
    state_data: Mapping[str, object],
    events: Sequence[Mapping[str, object]],
) -> tuple[timekeeping.Timeline, TRWConfig] | None:
    config = _config()
    prd_time, trigger = prd_time_for(state_data)
    if not timekeeping.is_tracked(state_data, events=events, prd_time=trigger, config=config):
        return None
    timeline = timekeeping.load_timeline(events, prd_time=prd_time, target_utc=state_data.get("target_utc"))
    return timeline, config


def status_time_block(
    state_data: Mapping[str, object],
    events: Sequence[Mapping[str, object]],
    events_path: Path | None = None,
) -> dict[str, object] | None:
    """FR05: the ``time`` block for a tracked run, built from the events already read (NFR03).

    FR08: the first ETA a run ever computes is appended once as a
    ``time_forecast`` event, so delivery can compare it with the actual.
    """
    tracked = _tracked_timeline(state_data, events)
    if tracked is None:
        return None
    timeline, config = tracked
    block = timekeeping.time_block(timeline, now=utc_now(), display_timezone=config.display_timezone)
    prediction = block.get("forecast")
    first_eta = isinstance(prediction, dict) and "eta_earliest" in prediction
    if events_path is not None and timeline.first_forecast is None and first_eta:
        _events().log_event(events_path, timekeeping.TIME_FORECAST_EVENT, {"forecast": prediction})
    return block


def deliver_time_record(run_path: Path) -> dict[str, object] | None:
    """FR08: planned vs actual for ``trw_deliver_complete``; None for an untracked run."""
    from trw_mcp.state._helpers import read_jsonl_resilient
    from trw_mcp.state.persistence import FileStateReader

    meta = run_path / "meta"
    state = FileStateReader().read_yaml(meta / "run.yaml")
    events = read_jsonl_resilient(meta / "events.jsonl")
    tracked = _tracked_timeline(state, events)
    if tracked is None:
        return None
    return timekeeping.delivery_record(tracked[0], now=utc_now())


def _events() -> FileEventLogger:
    from trw_mcp.state.persistence import FileEventLogger, FileStateWriter

    return FileEventLogger(FileStateWriter())


def checkpoint_time_fields(
    state_data: Mapping[str, object],
    message: str,
    machine_ts: datetime,
    read_events: Callable[[], Sequence[Mapping[str, object]]],
) -> dict[str, object]:
    """FR06: ``clock_mismatch`` for every run, ``elapsed_seconds`` only for tracked runs.

    ``read_events`` is invoked only when the run is tracked by its run.yaml
    fields, so an untracked checkpoint never reads events.jsonl (NFR03). Runs
    AFTER the checkpoint is persisted, so it is fail-open end to end: an
    advisory field must never turn a recorded checkpoint into a tool error.
    """
    fields: dict[str, object] = {}
    try:
        mismatch = timekeeping.clock_mismatch(message, machine_ts)
        if mismatch is not None:
            fields["clock_mismatch"] = mismatch
        if not timekeeping.is_tracked(state_data, prd_time=prd_time_for(state_data)[1], config=_config()):
            return fields
        elapsed = timekeeping.elapsed_seconds(timekeeping.load_timeline(read_events()), now=machine_ts)
    except Exception:  # justified: fail-open, time fields are advisory and must not fail a recorded checkpoint
        logger.info("checkpoint_time_degraded", exc_info=True)
        return fields
    if elapsed is not None:
        fields["elapsed_seconds"] = elapsed
    return fields
