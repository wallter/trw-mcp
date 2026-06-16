"""Telemetry-startup ceremony step.

Extracted from :mod:`trw_mcp.tools._ceremony_helpers` (PRD-DIST-243
Phase 1 batch 7, cycle 37) to keep that module under the 350-effective-
LOC operator threshold. Holds ``step_telemetry_startup`` (~65 effective
LOC) plus its companion ``_resolve_trw_dir_compat`` resolver helper.
"""

from __future__ import annotations

from pathlib import Path

import structlog

from trw_mcp.state._paths import resolve_trw_dir

__all__ = [
    "_resolve_trw_dir_compat",
    "step_first_session_marker",
    "step_telemetry_startup",
]

logger = structlog.get_logger(__name__)

# PRD-INFRA-142 FR02 — relative path (under the active .trw dir) of the
# once-per-installation first_session sentinel. Created only after a confirmed
# emit so a transient failure re-emits next session rather than double-counting.
_FIRST_SESSION_FLAG_REL = Path("state") / "first_session_emitted"


def _iso_now() -> str:
    """Return the current UTC timestamp as an ISO-8601 string (sentinel body)."""
    from datetime import datetime, timezone

    return datetime.now(tz=timezone.utc).isoformat()


def _resolve_trw_dir_compat() -> Path:
    """Resolve the active ``.trw`` dir while honoring ceremony-module patches."""
    try:
        from trw_mcp.tools import ceremony as ceremony_mod

        # Dynamic lookup — ceremony module may expose resolve_trw_dir via re-export
        # or via test monkeypatch. getattr with default preserves the fallback.
        ceremony_resolver = getattr(ceremony_mod, "resolve_trw_dir", None)
        if ceremony_resolver is not None:
            result = ceremony_resolver()
            if isinstance(result, Path):
                return result
    except Exception:  # justified: fail-open, monkeypatch compatibility probe is best-effort
        logger.debug("ceremony_trw_dir_compat_probe_failed", exc_info=True)
    return resolve_trw_dir()


def step_telemetry_startup(
    results: dict[str, object],
    run_dir: Path | None,
) -> None:
    """Queue SessionStartEvent for telemetry and start the telemetry pipeline.

    Fail-open: exceptions are logged but never propagated.
    """
    from trw_mcp.models.config import get_config
    from trw_mcp.state._paths import resolve_installation_id
    from trw_mcp.telemetry.client import TelemetryClient
    from trw_mcp.telemetry.event_base import HPOSessionStartEvent
    from trw_mcp.telemetry.models import SessionStartEvent

    config = get_config()
    inst_id = resolve_installation_id()
    tel_client = TelemetryClient.from_config()
    # Legacy CORE-031 event — continues through Phase 1 parallel-emit.
    tel_client.record_event(
        SessionStartEvent(
            installation_id=inst_id,
            framework_version=config.framework_version,
            learnings_loaded=int(str(results.get("learnings_count", 0))),
            run_id=str(run_dir.name) if run_dir else None,
        )
    )
    # PRD-HPO-MEAS-001 FR-2/FR-3: parallel-emit the unified-schema
    # HPOSessionStartEvent carrying the resolved surface_snapshot_id so
    # the session's own bootstrap event satisfies "every event carries it".
    snapshot_id = str(results.get("surface_snapshot_id", ""))
    try:
        from trw_mcp.telemetry.unified_events import emit as emit_unified

        hpo_session_event = HPOSessionStartEvent(
            session_id=inst_id,
            run_id=str(run_dir.name) if run_dir else None,
            surface_snapshot_id=snapshot_id,
            payload={
                "learnings_loaded": int(str(results.get("learnings_count", 0))),
                "framework_version": config.framework_version,
            },
        )
        trw_dir_path = _resolve_trw_dir_compat()
        context_dir = trw_dir_path / config.context_dir
        emit_unified(
            hpo_session_event,
            run_dir=run_dir,
            fallback_dir=context_dir if context_dir.exists() else None,
        )
    except Exception:  # justified: fail-open, HPO schema drift must not block session start
        logger.warning("hpo_session_start_event_failed", exc_info=True)
    tel_client.flush()
    # Start the unified telemetry pipeline (periodic background flush
    # replaces the old fire-and-forget BatchSender thread).
    try:
        from trw_mcp.telemetry.pipeline import TelemetryPipeline

        pipeline = TelemetryPipeline.get_instance()
        pipeline.start()
    except Exception:  # justified: fail-open, pipeline start must not block session start
        logger.warning("pipeline_start_failed", exc_info=True)


def step_first_session_marker() -> bool:
    """Emit the PRD-INFRA-142 FR02 ``first_session`` event once per installation.

    Idempotent by a local flag file (``.trw/state/first_session_emitted``): the
    event is recorded only when the flag is ABSENT, and the flag is written only
    AFTER the event is queued. This means a fresh install emits exactly once and
    every subsequent ``trw_session_start`` is a no-op without any backend
    round-trip. Opt-out is honored transitively: ``TelemetryClient.record_event``
    is a no-op when telemetry is disabled (NFR01).

    Returns True when a first_session event was queued this call, else False.
    Fail-open: never raises — the marker must not block session start.
    """
    try:
        from trw_mcp.models.config import get_config
        from trw_mcp.state._paths import resolve_installation_id
        from trw_mcp.telemetry.client import TelemetryClient
        from trw_mcp.telemetry.models import FirstSessionEvent

        trw_dir = _resolve_trw_dir_compat()
        flag_path = trw_dir / _FIRST_SESSION_FLAG_REL
        if flag_path.exists():
            return False

        config = get_config()
        # Resolve the client profile from target_platforms[0] (the same key the
        # installer stamps); falls back to "unknown" for un-configured installs.
        profile = "unknown"
        targets = getattr(config, "target_platforms", None)
        if isinstance(targets, list) and targets:
            profile = str(targets[0])

        tel_client = TelemetryClient.from_config()
        tel_client.record_event(
            FirstSessionEvent(
                installation_id=resolve_installation_id(),
                framework_version=config.framework_version,
                profile=profile,
            )
        )
        tel_client.flush()

        # Write the sentinel only after the event has been queued+flushed, so a
        # crash before this point re-emits next session (no silent data loss);
        # a crash after means at most one duplicate — acceptable, and the
        # backend de-dups by distinct installation_id anyway (FR04).
        flag_path.parent.mkdir(parents=True, exist_ok=True)
        flag_path.write_text(_iso_now() + "\n", encoding="utf-8")
        return True
    except Exception:  # justified: fail-open, first-session marker must not block session start
        logger.warning("first_session_marker_failed", exc_info=True)
        return False
