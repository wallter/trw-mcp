"""Extracted helper functions for ceremony.py — trw_session_start and trw_deliver.

Modularizes the two longest tool functions into focused, testable helpers:
- perform_session_recalls: execute focused + baseline recalls, return merged results
- run_auto_maintenance: auto-upgrade, stale run close, embeddings backfill
- check_delivery_gates: review/build gates, premature delivery guard
- finalize_run: checkpoint + run status update (placeholder for future expansion)
- step_log_session_event: log session_start event to events.jsonl
- step_telemetry_startup: queue telemetry events and start pipeline
- step_increment_session_counter: increment sessions_tracked counter
- step_sanitize_and_maintain: sanitize ceremony feedback + run auto-maintenance
- step_embed_health: check embeddings health status
- step_mark_session_started: mark session started in ceremony state
- step_ceremony_nudge: inject ceremony nudge into response

Sub-modules (extracted for the 500-line module size gate):
- _session_recall_helpers: recall, nudge injection, phase tags, antipattern alerts
- _delivery_helpers: delivery gates, compliance copy, finalize_run
"""

from __future__ import annotations

import json
from pathlib import Path

import structlog

from trw_mcp.models.config import TRWConfig
from trw_mcp.models.typed_dicts import AutoMaintenanceDict
from trw_mcp.state._paths import resolve_trw_dir
from trw_mcp.state.persistence import (
    FileEventLogger,
    FileStateWriter,
)

# Re-export everything from sub-modules so existing imports continue to work.
# fmt: off
from trw_mcp.tools._delivery_helpers import (
    COMPLEXITY_DRIFT_MULTIPLIER as COMPLEXITY_DRIFT_MULTIPLIER,
)
from trw_mcp.tools._delivery_helpers import (
    REVIEW_SCOPE_FILE_THRESHOLD as REVIEW_SCOPE_FILE_THRESHOLD,
)
from trw_mcp.tools._delivery_helpers import (
    _check_build_and_work_events as _check_build_and_work_events,
)
from trw_mcp.tools._delivery_helpers import (
    _check_checkpoint_blocker_gate as _check_checkpoint_blocker_gate,
)
from trw_mcp.tools._delivery_helpers import (
    _check_complexity_drift as _check_complexity_drift,
)
from trw_mcp.tools._delivery_helpers import (
    _check_integration_review_gate as _check_integration_review_gate,
)
from trw_mcp.tools._delivery_helpers import (
    _check_review_file_count_gate as _check_review_file_count_gate,
)
from trw_mcp.tools._delivery_helpers import (
    _check_review_gate as _check_review_gate,
)
from trw_mcp.tools._delivery_helpers import (
    _check_untracked_files as _check_untracked_files,
)
from trw_mcp.tools._delivery_helpers import (
    _count_file_modified as _count_file_modified,
)
from trw_mcp.tools._delivery_helpers import (
    _count_file_modified_current_session as _count_file_modified_current_session,
)
from trw_mcp.tools._delivery_helpers import (
    _events_since_last_session_start as _events_since_last_session_start,
)
from trw_mcp.tools._delivery_helpers import (
    _read_complexity_class as _read_complexity_class,
)
from trw_mcp.tools._delivery_helpers import (
    _read_run_events as _read_run_events,
)
from trw_mcp.tools._delivery_helpers import (
    _read_run_yaml as _read_run_yaml,
)
from trw_mcp.tools._delivery_helpers import (
    check_delivery_gates as check_delivery_gates,
)
from trw_mcp.tools._delivery_helpers import (
    copy_compliance_artifacts as copy_compliance_artifacts,
)
from trw_mcp.tools._delivery_helpers import (
    finalize_run as finalize_run,
)
from trw_mcp.tools._session_recall_helpers import (
    _ANTIPATTERN_KEYWORDS as _ANTIPATTERN_KEYWORDS,
)
from trw_mcp.tools._session_recall_helpers import (
    _PHASE_TAG_MAP as _PHASE_TAG_MAP,
)
from trw_mcp.tools._session_recall_helpers import (
    _SYSTEM_TASK_KEYWORDS as _SYSTEM_TASK_KEYWORDS,
)
from trw_mcp.tools._session_recall_helpers import (
    _apply_antipattern_alerts as _apply_antipattern_alerts,
)
from trw_mcp.tools._session_recall_helpers import (
    _phase_contextual_recall as _phase_contextual_recall,
)
from trw_mcp.tools._session_recall_helpers import (
    _phase_to_tags as _phase_to_tags,
)
from trw_mcp.tools._session_recall_helpers import (
    append_ceremony_nudge as append_ceremony_nudge,
)
from trw_mcp.tools._session_recall_helpers import (
    perform_session_recalls as perform_session_recalls,
)

# fmt: on

logger = structlog.get_logger(__name__)


# ── Session lifecycle step functions ─────────────────────────────────────


def step_log_session_event(
    run_dir: Path | None,
    results: dict[str, object],
    query: str,
    is_focused: bool,
) -> None:
    """Log session_start event to events.jsonl (FR01, PRD-CORE-031).

    Writes to run-scoped events file if a run is active, otherwise falls
    back to a session-events file under the context directory.
    """
    from trw_mcp.models.config import get_config

    config = get_config()
    writer = FileStateWriter()
    events = FileEventLogger(writer)

    event_data: dict[str, object] = {
        "learnings_recalled": int(str(results.get("learnings_count", 0))),
        "run_detected": run_dir is not None,
        "query": query if is_focused else "*",
    }
    if run_dir is not None:
        events_path = run_dir / "meta" / "events.jsonl"
        if events_path.parent.exists():
            events.log_event(events_path, "session_start", event_data)
    else:
        trw_dir_path = resolve_trw_dir()
        context_path = trw_dir_path / config.context_dir
        writer.ensure_dir(context_path)
        fallback_path = context_path / "session-events.jsonl"
        events.log_event(fallback_path, "session_start", event_data)


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
    from trw_mcp.telemetry.models import SessionStartEvent

    config = get_config()
    inst_id = resolve_installation_id()
    tel_client = TelemetryClient.from_config()
    tel_client.record_event(SessionStartEvent(
        installation_id=inst_id,
        framework_version=config.framework_version,
        learnings_loaded=int(str(results.get("learnings_count", 0))),
        run_id=str(run_dir.name) if run_dir else None,
    ))
    tel_client.flush()
    # Start the unified telemetry pipeline (periodic background flush
    # replaces the old fire-and-forget BatchSender thread).
    # Note: the TelemetryClient.record_event + flush above already handles
    # the session_start event via the typed path. We only need to start the
    # pipeline here — no separate enqueue, which would create a duplicate event.
    try:
        from trw_mcp.telemetry.pipeline import TelemetryPipeline
        pipeline = TelemetryPipeline.get_instance()
        pipeline.start()
    except Exception:  # justified: fail-open, pipeline start must not block session start
        logger.warning("pipeline_start_failed", exc_info=True)


def step_increment_session_counter() -> None:
    """Increment sessions_tracked counter (FIX-050-FR06)."""
    from trw_mcp.state.analytics.counters import increment_session_start_counter
    increment_session_start_counter(resolve_trw_dir())


def step_sanitize_and_maintain(
    run_dir: Path | None,
) -> AutoMaintenanceDict:
    """Sanitize ceremony feedback, then run auto-maintenance.

    Wraps ``sanitize_ceremony_feedback`` + ``run_auto_maintenance`` in
    a single fail-open step.

    Returns:
        AutoMaintenanceDict with keys for each maintenance operation.
    """
    from trw_mcp.models.config import get_config

    config = get_config()

    # One-time sanitization of test-polluted ceremony feedback (FIX-050-FR07)
    try:
        from trw_mcp.state.ceremony_feedback import sanitize_ceremony_feedback
        sanitize_ceremony_feedback(resolve_trw_dir())
    except Exception:  # justified: fail-open, sanitization must not block session start
        logger.warning("ceremony_feedback_sanitize_failed", exc_info=True)

    return run_auto_maintenance(resolve_trw_dir(), config, run_dir)


def step_embed_health() -> dict[str, object]:
    """Check embeddings health status for agents (FR01, PRD-FIX-053).

    Returns:
        Dict with enabled, available, advisory, recent_failures keys.
        Falls back to a safe default dict if the check fails.
    """
    try:
        from trw_mcp.state.memory_adapter import check_embeddings_status
        embed_status = check_embeddings_status()
        return dict(embed_status)
    except Exception:  # justified: fail-open, embed health check must not block session start
        return {
            "enabled": False,
            "available": False,
            "advisory": "",
            "recent_failures": 0,
        }


def step_mark_session_started() -> None:
    """Mark session started in ceremony state tracker (PRD-CORE-074 FR04)."""
    from trw_mcp.state.ceremony_nudge import mark_session_started
    mark_session_started(resolve_trw_dir())


def step_ceremony_nudge(
    results: dict[str, object],
    learnings_count: int,
) -> None:
    """Inject ceremony nudge into response (PRD-CORE-074 FR01, PRD-CORE-084 FR02).

    Skipped for light ceremony mode (FR07, PRD-CORE-084).
    """
    from trw_mcp.models.config import get_config
    from trw_mcp.state.ceremony_nudge import NudgeContext, ToolName

    config = get_config()
    if config.effective_ceremony_mode == "light":
        return

    ctx = NudgeContext(tool_name=ToolName.SESSION_START)
    append_ceremony_nudge(
        results,
        resolve_trw_dir(),
        available_learnings=learnings_count,
        context=ctx,
    )


def _check_version_sentinel(
    trw_dir: Path,
    maintenance: AutoMaintenanceDict,
) -> None:
    """Detect if the installer wrote a newer version since this process started.

    The installer writes ``.trw/installed-version.json`` after upgrading.
    If the on-disk version is newer than the running version, inject an
    ``update_advisory`` telling the user to run ``/mcp`` to reload.
    """
    sentinel = trw_dir / "installed-version.json"
    if not sentinel.is_file():
        return

    try:
        data = json.loads(sentinel.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return

    installed_version = str(data.get("version", ""))
    if not installed_version:
        return

    # Compare with running version
    try:
        from importlib.metadata import version as pkg_version

        running_version = pkg_version("trw-mcp")
    except Exception:  # justified: importlib.metadata may fail in edge cases
        return

    if installed_version != running_version and "update_advisory" not in maintenance:
        maintenance["update_advisory"] = (
            f"TRW v{installed_version} was installed but this MCP server is still "
            f"running v{running_version}. Run /mcp to reload."
        )


def run_auto_maintenance(
    trw_dir: Path,
    config: TRWConfig,
    run_dir: Path | None = None,
) -> AutoMaintenanceDict:
    """Run auto-upgrade check, stale run close, and embeddings backfill.

    Returns a dict with keys for each maintenance operation that produced results.
    All operations are fail-open — individual failures do not affect others.
    """
    maintenance: AutoMaintenanceDict = {}

    # Version sentinel check — detect if installer ran since this process started
    try:
        _check_version_sentinel(trw_dir, maintenance)
    except Exception:  # justified: fail-open, version sentinel check must not block session start
        logger.warning("maintenance_version_sentinel_failed", exc_info=True)

    # Auto-upgrade check (PRD-INFRA-014)
    try:
        from trw_mcp.state.auto_upgrade import check_for_update

        update_info = check_for_update()
        if update_info.get("available"):
            maintenance["update_advisory"] = str(update_info.get("advisory", ""))
            if config.auto_upgrade:
                from trw_mcp.state.auto_upgrade import perform_upgrade

                upgrade_result = perform_upgrade(update_info)
                if upgrade_result.get("applied"):
                    parts: list[str] = []
                    parts.append(
                        f"Auto-upgraded to v{upgrade_result.get('version', '?')}: {upgrade_result.get('details', '')}"
                    )
                    maintenance["auto_upgrade"] = upgrade_result
    except Exception:  # justified: fail-open, auto-upgrade must not block session start
        logger.warning("maintenance_auto_upgrade_failed", exc_info=True)

    # Auto-close stale runs
    try:
        if config.run_auto_close_enabled:
            from trw_mcp.state.analytics.report import auto_close_stale_runs

            close_result = auto_close_stale_runs()
            closed_count = int(str(close_result.get("count", 0)))
            if closed_count > 0:
                maintenance["stale_runs_closed"] = close_result
    except Exception:  # justified: fail-open, stale run cleanup must not block session start
        logger.warning("maintenance_stale_runs_close_failed", exc_info=True)

    # Embeddings status check + backfill
    try:
        from trw_mcp.state.memory_adapter import check_embeddings_status

        emb_status = check_embeddings_status()
        if emb_status.get("advisory"):
            maintenance["embeddings_advisory"] = str(emb_status["advisory"])
        elif emb_status.get("enabled") and emb_status.get("available"):
            from trw_mcp.state.memory_adapter import backfill_embeddings

            backfill = backfill_embeddings(resolve_trw_dir())
            if backfill.get("embedded", 0) > 0:
                maintenance["embeddings_backfill"] = backfill
    except Exception:  # justified: fail-open, embeddings check must not block session start
        logger.warning("maintenance_embeddings_check_failed", exc_info=True)

    # WAL checkpoint (PRD-QUAL-050-FR05)
    try:
        from trw_mcp.state.memory_adapter import maybe_checkpoint_wal

        wal_result = maybe_checkpoint_wal(trw_dir)
        if wal_result.get("checkpointed"):
            maintenance["wal_checkpoint"] = wal_result
    except Exception:  # justified: fail-open, WAL checkpoint must not block session start
        logger.warning("maintenance_wal_checkpoint_failed", exc_info=True)

    logger.debug(
        "auto_maintenance_complete",
        keys=list(maintenance.keys()),
    )
    return maintenance
