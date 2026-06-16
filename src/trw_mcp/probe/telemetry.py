"""ProbeEvent telemetry builder (PRD-CORE-144 FR-09).

Belongs to the ``probe`` facade. Re-exported from ``probe/__init__.py``.

Every completed probe (success, timeout, OOM, sandbox kill) emits a
``ProbeEvent`` through the unified ``HPOTelemetryEvent`` envelope from
PRD-HPO-MEAS-001 so the H4 meta-proposer can measure **probe yield** =
(probes producing decisive verdicts) / (probes attempted).

``HPOTelemetryEvent`` is ``extra=forbid`` + frozen, so the probe-specific
payload lives inside ``payload``. The ``ProbeEvent`` subclass (event_type
``probe``) is defined in ``telemetry/event_base.py`` so it is registered in
the global ``EVENT_TYPE_REGISTRY`` regardless of import order — the CI
emitter-coverage parity gate walks ``HPOTelemetryEvent.__subclasses__()`` and
rejects any subclass missing from the registry. It is re-exported here for
back-compat with the historical ``from trw_mcp.probe.telemetry import
ProbeEvent`` import path.
"""

from __future__ import annotations

from typing import Any

from trw_mcp.models.probe import ProbeResult
from trw_mcp.telemetry.event_base import HPOTelemetryEvent, ProbeEvent

#: Canonical payload keys for a probe event (parity with ProbeResult fields
#: the yield metric consumes).
PROBE_EVENT_PAYLOAD_KEYS: tuple[str, ...] = (
    "verdict",
    "hypothesis_id",
    "wall_ms",
    "timed_out",
    "cache_hit",
    "planning_mode",
    "confidence",
    "decisive",
)


def build_probe_event(
    result: ProbeResult,
    *,
    session_id: str,
    planning_mode: str,
    surface_snapshot_id: str = "",
    parent_event_id: str | None = None,
) -> ProbeEvent:
    """Build a :class:`ProbeEvent` from a ``ProbeResult``.

    Fire-and-forget: the caller routes the returned event through its
    telemetry pipeline (FR-09 A2 — emission never blocks on the sink). The
    ``decisive`` payload flag is the probe-yield numerator signal.
    """
    payload: dict[str, Any] = {
        "verdict": result.verdict,
        "hypothesis_id": result.hypothesis_id,
        "wall_ms": result.evidence.wall_ms,
        "timed_out": result.evidence.timed_out,
        "cache_hit": result.cache_hit,
        "planning_mode": planning_mode,
        "confidence": result.confidence,
        "decisive": result.verdict in ("supports", "refutes"),
    }
    return ProbeEvent(
        session_id=session_id,
        run_id=result.run_id,
        surface_snapshot_id=surface_snapshot_id,
        parent_event_id=parent_event_id,
        payload=payload,
    )


__all__ = ["PROBE_EVENT_PAYLOAD_KEYS", "HPOTelemetryEvent", "ProbeEvent", "build_probe_event"]
