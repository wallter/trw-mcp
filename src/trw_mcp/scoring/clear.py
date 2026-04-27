"""CLEAR 5-dimensional scorer — PRD-HPO-MEAS-001 FR-5.

Computes a ``ClearScore`` record per closed session from the unified
``HPOTelemetryEvent`` stream using the PRD's derivation formulas:

- **Cost** raw metric: ``sum(ToolCallEvent.usd_cost_est) + sum(LLMCallEvent.usd_cost_est)``
  normalized against a configurable p95 envelope.
- **Latency** raw metric: ``session_end.ts - session_start.ts`` wall-clock,
  normalized against a configurable p95 envelope.
- **Efficacy** raw metric: ``1.0`` for explicit PASS / compliant outcomes,
  else ``0.0`` for explicit FAIL / non-compliant outcomes.
- **Assurance** raw metric: mean of available validation rates
  (contract-schema validity, auth verification, quarantine cleanliness).
- **Reliability** raw metric: ``1 - retry_count / total_tool_calls`` where
  retries count ``ToolCallEvent`` outcomes in ``{retry, error}``.

Exactly one ``ClearScore`` record is produced per closed session;
``compute(session_id, events)`` is pure over the event list so callers
can replay it across historical sessions for trend analysis.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

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

    tool_call_count: int
    retry_or_error_count: int
    total_tool_wall_ms: int
    total_usd: float
    contract_valid_count: int
    contract_total_count: int
    auth_verified_count: int
    auth_total_count: int
    quarantine_clean_sum: float
    quarantine_total_count: int
    session_wall_ms: int
    efficacy_outcome: float | None


def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    """Clamp ``value`` into ``[lo, hi]``."""
    return max(lo, min(hi, value))


def _aggregate_tool_calls(events: Iterable[HPOTelemetryEvent]) -> _ToolCallAggregate:
    """Roll up a session's events into a :class:`_ToolCallAggregate`.

    Resilient to malformed payloads: missing keys default to 0 and the
    event is counted without contributing to the sub-total.
    """
    tool_call_count = 0
    retry_or_error_count = 0
    total_tool_wall_ms = 0
    total_usd = 0.0
    contract_valid_count = 0
    contract_total_count = 0
    auth_verified_count = 0
    auth_total_count = 0
    quarantine_clean_sum = 0.0
    quarantine_total_count = 0
    session_start_ts: datetime | None = None
    session_end_ts: datetime | None = None
    efficacy_outcome: float | None = None

    for ev in events:
        if ev.event_type == "tool_call":
            tool_call_count += 1
            outcome = str(ev.payload.get("outcome", "success")).strip().lower()
            if outcome in {"retry", "error"}:
                retry_or_error_count += 1
            try:
                total_tool_wall_ms += int(ev.payload.get("wall_ms", 0) or 0)
            except (TypeError, ValueError):
                pass
            try:
                total_usd += float(ev.payload.get("usd_cost_est", 0.0) or 0.0)
            except (TypeError, ValueError):
                pass
        elif ev.event_type == "llm_call":
            try:
                total_usd += float(ev.payload.get("usd_cost_est", 0.0) or 0.0)
            except (TypeError, ValueError):
                pass
        elif ev.event_type == "contract":
            schema_valid = ev.payload.get("schema_valid")
            if isinstance(schema_valid, bool):
                contract_total_count += 1
                if schema_valid:
                    contract_valid_count += 1
        elif ev.event_type == "mcp_security":
            verified = ev.payload.get("verified")
            if isinstance(verified, bool):
                auth_total_count += 1
                if verified:
                    auth_verified_count += 1
        elif ev.event_type == "observer":
            clean_rate: object = ev.payload.get("clean_rate")
            try:
                if not isinstance(clean_rate, (int, float, str)):
                    raise TypeError
                clean_value = float(clean_rate)
            except (TypeError, ValueError):
                clean_value = None
            if clean_value is not None:
                quarantine_total_count += 1
                quarantine_clean_sum += clean_value
        elif ev.event_type == "session_start":
            session_start_ts = ev.ts if session_start_ts is None else min(session_start_ts, ev.ts)
        elif ev.event_type == "session_end":
            session_end_ts = ev.ts if session_end_ts is None else max(session_end_ts, ev.ts)
            explicit_outcome = str(ev.payload.get("outcome", "")).strip().upper()
            if explicit_outcome:
                efficacy_outcome = 1.0 if explicit_outcome == "PASS" else 0.0
        elif ev.event_type == "ceremony_compliance":
            compliant = ev.payload.get("compliant")
            if isinstance(compliant, bool):
                efficacy_outcome = 1.0 if compliant else 0.0

    session_wall_ms = 0
    if session_start_ts is not None and session_end_ts is not None:
        session_wall_ms = max(0, int((session_end_ts - session_start_ts).total_seconds() * 1000))

    return _ToolCallAggregate(
        tool_call_count=tool_call_count,
        retry_or_error_count=retry_or_error_count,
        total_tool_wall_ms=total_tool_wall_ms,
        total_usd=total_usd,
        contract_valid_count=contract_valid_count,
        contract_total_count=contract_total_count,
        auth_verified_count=auth_verified_count,
        auth_total_count=auth_total_count,
        quarantine_clean_sum=quarantine_clean_sum,
        quarantine_total_count=quarantine_total_count,
        session_wall_ms=session_wall_ms,
        efficacy_outcome=efficacy_outcome,
    )


def _cost_dim(agg: _ToolCallAggregate, *, cost_p95_usd: float) -> float:
    """Cost dimension: 1 − (usd / p95_envelope), clamped."""
    if cost_p95_usd <= 0.0:
        return 1.0
    return _clamp(1.0 - (agg.total_usd / cost_p95_usd))


def _latency_dim(agg: _ToolCallAggregate, *, latency_p95_ms: int) -> float:
    """Latency dimension from session wall-clock: 1 − (wall_ms / p95_envelope)."""
    if latency_p95_ms <= 0:
        return 1.0
    return _clamp(1.0 - (agg.session_wall_ms / latency_p95_ms))


def _efficacy_dim(agg: _ToolCallAggregate, *, ceremony_score: float | None) -> float:
    """Efficacy dimension from explicit pass/fail outcome signals."""
    if ceremony_score is not None:
        return _clamp(ceremony_score)
    if agg.efficacy_outcome is not None:
        return agg.efficacy_outcome
    return 0.0


def _assurance_dim(agg: _ToolCallAggregate) -> float:
    """Assurance dimension: mean of available validation/verification rates."""
    rates: list[float] = []
    if agg.contract_total_count > 0:
        rates.append(agg.contract_valid_count / agg.contract_total_count)
    if agg.auth_total_count > 0:
        rates.append(agg.auth_verified_count / agg.auth_total_count)
    if agg.quarantine_total_count > 0:
        rates.append(agg.quarantine_clean_sum / agg.quarantine_total_count)
    if not rates:
        return 0.0
    return _clamp(sum(rates) / len(rates))


def _reliability_dim(agg: _ToolCallAggregate) -> float:
    """Reliability dimension: 1 − (retry_or_error_count / total_tool_calls)."""
    if agg.tool_call_count == 0:
        return 1.0
    return _clamp(1.0 - (agg.retry_or_error_count / agg.tool_call_count))


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
        ceremony_compliance_score: Optional sidecar override for the
            efficacy dimension in backfill/replay contexts.
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
        tool_call_count=agg.tool_call_count,
        total_usd_cost=agg.total_usd,
        total_wall_ms=agg.total_tool_wall_ms,
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
            except Exception:  # noqa: S112 — scan-resilience: drift between schema + persisted rows is expected
                continue
            events.append(ev)

    if not events:
        return None
    return compute(session_id, events, ceremony_compliance_score=ceremony_compliance_score)


__all__ = [
    "DEFAULT_COST_P95_USD",
    "DEFAULT_LATENCY_P95_MS",
    "ClearScore",
    "compute",
    "load_and_score_run",
]
