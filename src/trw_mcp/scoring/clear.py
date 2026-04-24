"""CLEAR 5-dimensional scorer — PRD-HPO-MEAS-001 FR-5.

Computes a ``ClearScore`` record per closed session joining
:class:`ToolCallEvent` aggregates with session outcome records. Each
dimension is a float in ``[0, 1]`` with a documented derivation:

- **Cost** (higher = cheaper): 1.0 minus the session's total USD cost
  clamped to the p95 cost-per-session envelope (configurable).
- **Latency** (higher = faster): 1.0 minus the total tool wall-time in
  ms, clamped to the p95 latency envelope.
- **Efficacy** (higher = better outcome): session outcome proxy —
  ``ceremony_compliance_score`` when present, else a smoothed function
  of the success/error tool-call ratio.
- **Assurance** (higher = more verified): fraction of tool calls whose
  outcome is ``success`` AND which emitted at least one paired
  ``ContractEvent`` (contract-verified calls). During Phase 1 when
  ContractEvents are sparse, this degrades gracefully to the
  success-ratio.
- **Reliability** (higher = less thrashing): 1.0 minus the share of
  tool calls that appear in a ``ThrashingEvent`` burst.

Exactly one ``ClearScore`` record is produced per closed session;
``compute(session_id, events)`` is pure over the event list so callers
can replay it across historical sessions for trend analysis.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import structlog
from pydantic import BaseModel, ConfigDict, Field

from trw_mcp.telemetry.event_base import HPOTelemetryEvent

logger = structlog.get_logger(__name__)


#: Phase-1 envelopes (configurable via ``config.scoring.clear.*`` in
#: a later wave). They represent the 95th-percentile budget above which
#: the corresponding dimension saturates to zero — a session costing
#: double the p95 still only scores 0, not negative.
DEFAULT_COST_P95_USD: float = 2.00
DEFAULT_LATENCY_P95_MS: int = 120_000  # 2 minutes of tool time per session


class ClearScore(BaseModel):
    """Per-session 5-dimensional score (PRD-HPO-MEAS-001 FR-5)."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    session_id: str
    cost: float = Field(ge=0.0, le=1.0)
    latency: float = Field(ge=0.0, le=1.0)
    efficacy: float = Field(ge=0.0, le=1.0)
    assurance: float = Field(ge=0.0, le=1.0)
    reliability: float = Field(ge=0.0, le=1.0)
    tool_call_count: int = Field(ge=0)
    total_usd_cost: float = Field(ge=0.0)
    total_wall_ms: int = Field(ge=0)


@dataclass(frozen=True)
class _ToolCallAggregate:
    """Internal rollup of a session's ToolCallEvent set."""

    count: int
    success: int
    error: int
    total_wall_ms: int
    total_usd: float
    contract_paired: int  # calls with at least one paired ContractEvent
    thrashing_burst: int  # calls that participated in a ThrashingEvent


def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    """Clamp ``value`` into ``[lo, hi]``."""
    return max(lo, min(hi, value))


def _aggregate_tool_calls(events: Iterable[HPOTelemetryEvent]) -> _ToolCallAggregate:
    """Roll up a session's events into a :class:`_ToolCallAggregate`.

    Resilient to malformed payloads: missing keys default to 0 and the
    event is counted without contributing to the sub-total.
    """
    count = 0
    success = 0
    error = 0
    total_wall_ms = 0
    total_usd = 0.0
    contract_paired = 0
    thrashing_burst = 0
    tool_call_event_ids: set[str] = set()
    contract_parent_ids: set[str] = set()
    thrashing_parent_ids: set[str] = set()

    for ev in events:
        if ev.event_type == "tool_call":
            count += 1
            tool_call_event_ids.add(ev.event_id)
            outcome = str(ev.payload.get("outcome", "success"))
            if outcome == "success":
                success += 1
            else:
                error += 1
            try:
                total_wall_ms += int(ev.payload.get("wall_ms", 0) or 0)
            except (TypeError, ValueError):
                pass
            try:
                total_usd += float(ev.payload.get("usd_cost_est", 0.0) or 0.0)
            except (TypeError, ValueError):
                pass
        elif ev.event_type == "contract" and ev.parent_event_id:
            contract_parent_ids.add(ev.parent_event_id)
        elif ev.event_type == "thrashing" and ev.parent_event_id:
            thrashing_parent_ids.add(ev.parent_event_id)

    contract_paired = len(contract_parent_ids & tool_call_event_ids)
    thrashing_burst = len(thrashing_parent_ids & tool_call_event_ids)

    return _ToolCallAggregate(
        count=count,
        success=success,
        error=error,
        total_wall_ms=total_wall_ms,
        total_usd=total_usd,
        contract_paired=contract_paired,
        thrashing_burst=thrashing_burst,
    )


def _cost_dim(agg: _ToolCallAggregate, *, cost_p95_usd: float) -> float:
    """Cost dimension: 1 − (usd / p95_envelope), clamped."""
    if cost_p95_usd <= 0.0:
        return 1.0
    return _clamp(1.0 - (agg.total_usd / cost_p95_usd))


def _latency_dim(agg: _ToolCallAggregate, *, latency_p95_ms: int) -> float:
    """Latency dimension: 1 − (wall_ms / p95_envelope), clamped."""
    if latency_p95_ms <= 0:
        return 1.0
    return _clamp(1.0 - (agg.total_wall_ms / latency_p95_ms))


def _efficacy_dim(agg: _ToolCallAggregate, *, ceremony_score: float | None) -> float:
    """Efficacy dimension: ceremony_score when present, else success/total."""
    if ceremony_score is not None:
        return _clamp(ceremony_score)
    if agg.count == 0:
        return 0.0
    return _clamp(agg.success / agg.count)


def _assurance_dim(agg: _ToolCallAggregate) -> float:
    """Assurance dimension: contract-paired share of successful calls.

    During Phase 1 when contract events are sparse, degrade to the
    success-ratio so the dimension is never stuck at 0 for reasons
    outside the caller's control.
    """
    if agg.count == 0:
        return 0.0
    if agg.contract_paired == 0:
        # Graceful fallback until Phase 2 ContractEvent retrofit lands.
        return _clamp(agg.success / agg.count)
    return _clamp(agg.contract_paired / agg.count)


def _reliability_dim(agg: _ToolCallAggregate) -> float:
    """Reliability dimension: 1 − (thrashing-burst share)."""
    if agg.count == 0:
        return 1.0
    return _clamp(1.0 - (agg.thrashing_burst / agg.count))


def compute(
    session_id: str,
    events: list[HPOTelemetryEvent],
    *,
    ceremony_compliance_score: float | None = None,
    cost_p95_usd: float = DEFAULT_COST_P95_USD,
    latency_p95_ms: int = DEFAULT_LATENCY_P95_MS,
) -> ClearScore:
    """Compute the :class:`ClearScore` for a closed session.

    Pure function of the event list — callers can replay historical
    sessions for trend analysis. Never raises; malformed payloads fall
    back to 0 contributions per dimension.

    Args:
        session_id: Canonical session id stamped on every event.
        events: Full event set for the session (order-independent).
        ceremony_compliance_score: Optional sidecar — the H1 ceremony
            rollup score in ``[0, 1]``. When provided, overrides the
            success-ratio proxy for ``efficacy``.
        cost_p95_usd: P95 cost envelope. Above this, cost dim → 0.
        latency_p95_ms: P95 latency envelope. Above this, latency dim → 0.
    """
    agg = _aggregate_tool_calls(events)

    score = ClearScore(
        session_id=session_id,
        cost=_cost_dim(agg, cost_p95_usd=cost_p95_usd),
        latency=_latency_dim(agg, latency_p95_ms=latency_p95_ms),
        efficacy=_efficacy_dim(agg, ceremony_score=ceremony_compliance_score),
        assurance=_assurance_dim(agg),
        reliability=_reliability_dim(agg),
        tool_call_count=agg.count,
        total_usd_cost=agg.total_usd,
        total_wall_ms=agg.total_wall_ms,
    )
    logger.debug(
        "clear_score_computed",
        session_id=session_id,
        cost=score.cost,
        latency=score.latency,
        efficacy=score.efficacy,
        assurance=score.assurance,
        reliability=score.reliability,
    )
    return score


def load_and_score_run(
    session_id: str,
    run_dir: object,
    *,
    ceremony_compliance_score: float | None = None,
) -> ClearScore | None:
    """Load a run's unified events from disk and compute a :class:`ClearScore`.

    Wave 2c carry-forward wiring: called from ``trw_deliver`` (ceremony.py)
    on session close so every delivered run produces exactly one
    ``session_clear_score.json`` artifact per FR-5's "one record per closed
    session" AC.

    Args:
        session_id: canonical session id stamped on events.
        run_dir: ``<task>/<run_id>/`` directory (``Path`` or str). The
            events file is resolved as ``<run_dir>/meta/events-*.jsonl``.
        ceremony_compliance_score: optional pre-computed ceremony rollup
            to override the success-ratio fallback in the efficacy dim.

    Returns:
        A :class:`ClearScore` or ``None`` when no events can be loaded
        (no run_dir, no meta, no events files).
    """
    from pathlib import Path as _Path

    from trw_mcp.telemetry.event_base import EVENT_TYPE_REGISTRY, ObserverEvent

    run_path = _Path(str(run_dir)) if run_dir is not None else None
    if run_path is None:
        return None
    meta = run_path / "meta"
    if not meta.is_dir():
        return None

    import json as _json

    events: list[HPOTelemetryEvent] = []
    for events_file in sorted(meta.glob("events-*.jsonl")):
        try:
            raw = events_file.read_text(encoding="utf-8")
        except OSError:
            continue
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                record = _json.loads(line)
            except ValueError:
                continue
            if not isinstance(record, dict):
                continue
            et = str(record.get("event_type", ""))
            cls = EVENT_TYPE_REGISTRY.get(et, ObserverEvent)
            try:
                ev = cls.model_validate(record)
            except Exception:  # justified: scan-resilience, drift between schema + persisted rows
                continue
            events.append(ev)

    if not events:
        return None
    return compute(session_id, events, ceremony_compliance_score=ceremony_compliance_score)


__all__ = [
    "ClearScore",
    "DEFAULT_COST_P95_USD",
    "DEFAULT_LATENCY_P95_MS",
    "compute",
    "load_and_score_run",
]
