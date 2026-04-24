"""HPOTelemetryEvent — unified event base class for HPO measurement substrate.

PRD-HPO-MEAS-001 §7 (Naming Resolution) + §5 FR-3/FR-6.

This module introduces ``HPOTelemetryEvent`` (Pydantic v2, strict, frozen,
extra=forbid) as the single base class for all new trw-mcp telemetry
emitters. It intentionally coexists in parallel with the legacy
``trw_mcp.telemetry.models.TelemetryEvent`` (4-field installation-scoped
event) during Phase-1 parallel-emit — the legacy class stays untouched.

Subclasses set only ``event_type`` and a default ``emitter`` so the base
schema remains uniform across surfaces. Sprint-96 keeps subtype-specific
telemetry details payload-backed rather than modeling extra top-level
Pydantic fields; ``EVENT_PAYLOAD_KEY_REGISTRY`` documents the canonical
payload keys that FR-14 truthfully proves today.

FR-6: ``parent_event_id`` is nullable, string-typed, and references another
``event_id`` within the same ``run_id``. Validation is advisory — dangling
references produce a WARN log and a returned list, never an exception.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import structlog
from pydantic import BaseModel, ConfigDict, Field

_log = structlog.get_logger(__name__)


def _evt_id() -> str:
    """Return a new opaque event id with the ``evt_`` prefix."""
    return f"evt_{uuid4().hex}"


def _utc_now() -> datetime:
    """Return the current time as a timezone-aware UTC datetime."""
    return datetime.now(tz=timezone.utc)


class HPOTelemetryEvent(BaseModel):
    """Unified HPO telemetry event base (PRD-HPO-MEAS-001 FR-3).

    All new telemetry emitters subclass this. Subclasses should only set
    class-level defaults for ``event_type`` and ``emitter``; they must not
    add new fields — payload extensions belong in ``payload``.

    ``surface_snapshot_id`` is allowed to be the empty string during Phase 1
    (PRD §9); FR-2 will enforce non-empty values once the artifact registry
    lands in Phase 2.

    ``parent_event_id`` (FR-6) enables DAG-ready causal chains for H3
    investigations; validation is advisory only.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    event_id: str = Field(default_factory=_evt_id)
    session_id: str
    run_id: str | None = None
    ts: datetime = Field(default_factory=_utc_now)
    emitter: str
    event_type: str
    surface_snapshot_id: str = ""
    parent_event_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class CeremonyEvent(HPOTelemetryEvent):
    """Ceremony phase-gate + compliance events."""

    event_type: str = "ceremony"
    emitter: str = "ceremony"


class ContractEvent(HPOTelemetryEvent):
    """Agent-contract validation events."""

    event_type: str = "contract"
    emitter: str = "contract"


class PhaseExposureEvent(HPOTelemetryEvent):
    """Phase-exposure telemetry for H1 sequencing signal."""

    event_type: str = "phase_exposure"
    emitter: str = "phase_exposure"


class ObserverEvent(HPOTelemetryEvent):
    """Observer-layer emissions (oversight hooks)."""

    event_type: str = "observer"
    emitter: str = "observer"


class MCPSecurityEvent(HPOTelemetryEvent):
    """MCP capability-scope + registry security events."""

    event_type: str = "mcp_security"
    emitter: str = "mcp_security"


class MetaTuneEvent(HPOTelemetryEvent):
    """Meta-tune hyperparameter-exposure events."""

    event_type: str = "meta_tune"
    emitter: str = "meta_tune"


class ThrashingEvent(HPOTelemetryEvent):
    """Detected thrashing / retry-churn events."""

    event_type: str = "thrashing"
    emitter: str = "thrashing"


class LLMCallEvent(HPOTelemetryEvent):
    """LLM invocation telemetry (per-call cost/tokens/timing in payload)."""

    event_type: str = "llm_call"
    emitter: str = "llm"


class ToolCallEvent(HPOTelemetryEvent):
    """MCP tool-call timing events."""

    event_type: str = "tool_call"
    emitter: str = "tool_call_timing"


class HPOSessionStartEvent(HPOTelemetryEvent):
    """Emitted at ``trw_session_start``.

    Name-prefixed with ``HPO`` to avoid collision with the legacy
    ``trw_mcp.telemetry.models.SessionStartEvent`` (CORE-031 anonymized
    telemetry). Both classes coexist during Phase 1 parallel-emit; the
    ``telemetry/__init__.py`` re-export keeps the legacy short name.
    """

    event_type: str = "session_start"
    emitter: str = "session"


class HPOSessionEndEvent(HPOTelemetryEvent):
    """Emitted at ``trw_deliver`` (session close).

    Name-prefixed with ``HPO`` to avoid collision with legacy
    ``trw_mcp.telemetry.models.SessionEndEvent`` (CORE-031).
    """

    event_type: str = "session_end"
    emitter: str = "session"


class HPOCeremonyComplianceEvent(HPOTelemetryEvent):
    """Ceremony-compliance scoring events (per-run rollup).

    Name-prefixed with ``HPO`` to avoid collision with legacy
    ``trw_mcp.telemetry.models.CeremonyComplianceEvent`` (CORE-031).
    """

    event_type: str = "ceremony_compliance"
    emitter: str = "ceremony"


class SurfaceRegistered(HPOTelemetryEvent):
    """Surface-artifact discovery event (PRD-HPO-MEAS-001 FR-10 AC-8).

    Emitted once per newly-discovered governing artifact at
    ``SurfaceRegistry.build()`` call time. Lets cross-session analytics
    answer "when did this CLAUDE.md / FRAMEWORK.md / agent prompt first
    appear in the surface manifest?" without re-walking disk.

    Required ``payload`` keys:
        - ``surface_id``: str — canonical ``<category>:<relpath>``
        - ``content_hash``: str — sha256 hex of artifact contents
        - ``source_path``: str — repo-relative POSIX path
        - ``category``: str — agents / skills / hooks / prompts / config /
          surfaces / claude_md_root / framework_md / sub_claude_md
    """

    event_type: str = "surface_registered"
    emitter: str = "artifact_registry"


class H1ObserveModeWarning(ObserverEvent):
    """Fail-loud shim-fallback event for ``_emit_to_h1`` (PRD-HPO-MEAS-001 FR-9).

    Until all 7 legacy emitters write through :class:`HPOTelemetryEvent`
    directly, any call to the shim ``_emit_to_h1(event)`` — whether it
    forwards successfully OR buffers to a fallback path — MUST emit exactly
    one ``H1ObserveModeWarning`` at WARNING level. Plain
    ``logger.warning(...)`` is insufficient — the signal MUST be a counted
    telemetry event so analytics can answer "how many events are we
    accumulating blind?" without log scraping.

    Required ``payload`` keys (FR-9 §AC-1):
        - ``emitter_name``: str — the legacy emitter that called the shim
        - ``fallback_reason``: str — why the forward degraded
        - ``buffered_event_count_since_start``: int
        - ``activation_gate_blocked_reason``: str — typically
          ``"h1_substrate_not_live"`` during Phase 1/Phase 2 rollout

    Because :class:`HPOTelemetryEvent` is ``extra=forbid``, the payload
    keys above live inside ``payload: dict[str, Any]`` rather than as
    top-level attributes. See :func:`emit_h1_observe_mode_warning`.
    """

    event_type: str = "h1_observe_mode_warning"
    emitter: str = "h1_shim"


def emit_h1_observe_mode_warning(
    *,
    session_id: str,
    run_id: str | None,
    emitter_name: str,
    fallback_reason: str,
    buffered_event_count_since_start: int,
    surface_snapshot_id: str = "",
    activation_gate_blocked_reason: str = "h1_substrate_not_live",
    parent_event_id: str | None = None,
) -> H1ObserveModeWarning:
    """Factory that builds an :class:`H1ObserveModeWarning` with the FR-9 payload shape.

    Use this factory rather than constructing the event directly so the
    required payload keys stay consistent across all shim call sites.
    The returned event is NOT auto-published — callers route it through
    their telemetry pipeline (Phase 2 retrofit wires this into
    ``_emit_to_h1``).
    """
    return H1ObserveModeWarning(
        session_id=session_id,
        run_id=run_id,
        surface_snapshot_id=surface_snapshot_id,
        parent_event_id=parent_event_id,
        payload={
            "emitter_name": emitter_name,
            "fallback_reason": fallback_reason,
            "buffered_event_count_since_start": buffered_event_count_since_start,
            "activation_gate_blocked_reason": activation_gate_blocked_reason,
        },
    )


class DefaultResolutionError(RuntimeError):
    """Raised when a Phase-1 default cannot be resolved to a real resource.

    PRD-HPO-MEAS-001 NFR-12 mandates ``trw_session_start`` run a
    default-resolution audit on first boot per package-install; any
    unresolvable default (e.g. missing ``pricing.yaml``, unknown
    compression algorithm, unavailable hash algorithm) MUST produce a
    typed error with ``file:line + remediation``, BEFORE the session
    writes any events. Fails at install-time or boot-time — never at
    first-production-emission.

    FR-13 enforces this is NOT caught silently; the audit boot gate at
    ``meta_tune/boot_checks.py`` (Wave 2/3) re-raises after logging.
    """


#: Frozen registry of every :class:`HPOTelemetryEvent` subclass keyed by
#: its ``event_type`` literal (PRD-HPO-MEAS-001 FR-13 §5). Any new
#: subclass MUST be added here AND covered by
#: ``tests/ci/test_event_type_registry_parity.py``.
EVENT_TYPE_REGISTRY: dict[str, type[HPOTelemetryEvent]] = {
    CeremonyEvent.model_fields["event_type"].default: CeremonyEvent,
    ContractEvent.model_fields["event_type"].default: ContractEvent,
    PhaseExposureEvent.model_fields["event_type"].default: PhaseExposureEvent,
    ObserverEvent.model_fields["event_type"].default: ObserverEvent,
    MCPSecurityEvent.model_fields["event_type"].default: MCPSecurityEvent,
    MetaTuneEvent.model_fields["event_type"].default: MetaTuneEvent,
    ThrashingEvent.model_fields["event_type"].default: ThrashingEvent,
    LLMCallEvent.model_fields["event_type"].default: LLMCallEvent,
    ToolCallEvent.model_fields["event_type"].default: ToolCallEvent,
    HPOSessionStartEvent.model_fields["event_type"].default: HPOSessionStartEvent,
    HPOSessionEndEvent.model_fields["event_type"].default: HPOSessionEndEvent,
    HPOCeremonyComplianceEvent.model_fields["event_type"].default: HPOCeremonyComplianceEvent,
    H1ObserveModeWarning.model_fields["event_type"].default: H1ObserveModeWarning,
    SurfaceRegistered.model_fields["event_type"].default: SurfaceRegistered,
}


#: Canonical payload-backed subtype details proven by FR-14 as shipped in
#: Sprint 96. The H1 schema intentionally keeps subtype-specific details in
#: ``payload`` rather than adding extra top-level Pydantic fields per event
#: subclass. Production-path proof exists only for the live emitters called
#: out in the PRD; the remaining entries are schema-level sample contracts.
EVENT_PAYLOAD_KEY_REGISTRY: dict[str, tuple[str, ...]] = {
    "ceremony": ("phase",),
    "contract": ("contract_id", "outcome"),
    "phase_exposure": ("phase", "duration_ms"),
    "observer": ("kind",),
    "mcp_security": ("decision", "scope"),
    "meta_tune": ("proposal_id", "outcome"),
    "thrashing": ("retry_count", "tool"),
    "llm_call": ("model", "input_tokens", "output_tokens"),
    "tool_call": (
        "tool",
        "start_ts",
        "end_ts",
        "wall_ms",
        "input_tokens",
        "output_tokens",
        "usd_cost_est",
        "pricing_version",
        "outcome",
    ),
    "session_start": ("learnings_loaded", "framework_version"),
    "session_end": ("reason", "duration_ms"),
    "ceremony_compliance": ("score",),
    "h1_observe_mode_warning": (
        "emitter_name",
        "fallback_reason",
        "buffered_event_count_since_start",
        "activation_gate_blocked_reason",
    ),
    "surface_registered": ("surface_id", "content_hash", "source_path", "category"),
}


def validate_parent_within_run(
    events: list[HPOTelemetryEvent],
    *,
    run_id: str,
) -> list[str]:
    """Return event_ids whose ``parent_event_id`` does not resolve within ``run_id``.

    FR-6 is advisory: this function NEVER raises. Dangling parent refs are
    logged at WARN level and returned as a list so callers can surface the
    issue without rejecting ingestion.

    Args:
        events: Events to inspect (any subclass of HPOTelemetryEvent).
        run_id: Run scope; only events with matching ``run_id`` are checked,
            and parent resolution is restricted to that same run.

    Returns:
        Sorted list of event_ids with dangling parent references. Empty list
        when all parent_event_ids resolve (or are null).
    """
    in_run = [e for e in events if e.run_id == run_id]
    known_ids = {e.event_id for e in in_run}
    dangling: list[str] = []
    for ev in in_run:
        parent = ev.parent_event_id
        if parent is None:
            continue
        if parent not in known_ids:
            dangling.append(ev.event_id)
            _log.warning(
                "hpo_telemetry_parent_unresolved",
                run_id=run_id,
                event_id=ev.event_id,
                parent_event_id=parent,
                event_type=ev.event_type,
            )
    return sorted(dangling)


__all__ = [
    "HPOTelemetryEvent",
    "CeremonyEvent",
    "ContractEvent",
    "PhaseExposureEvent",
    "ObserverEvent",
    "MCPSecurityEvent",
    "MetaTuneEvent",
    "ThrashingEvent",
    "LLMCallEvent",
    "ToolCallEvent",
    "HPOSessionStartEvent",
    "HPOSessionEndEvent",
    "HPOCeremonyComplianceEvent",
    "H1ObserveModeWarning",
    "SurfaceRegistered",
    "DefaultResolutionError",
    "EVENT_TYPE_REGISTRY",
    "EVENT_PAYLOAD_KEY_REGISTRY",
    "emit_h1_observe_mode_warning",
    "validate_parent_within_run",
]
