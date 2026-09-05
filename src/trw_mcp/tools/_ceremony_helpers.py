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
 - step_ceremony_status: inject ceremony status into response

Sub-modules (extracted for the 500-line module size gate):
 - _session_recall_helpers: recall, phase tags, antipattern alerts
- _delivery_helpers: delivery gates, compliance copy, finalize_run
- _ceremony_maintenance_steps: version sentinel, WAL checkpoint, learn-journal
  drain, writer-pressure details (the fail-open auto-maintenance sub-steps)
"""

from __future__ import annotations

from pathlib import Path

import structlog

from trw_mcp.models.config import TRWConfig
from trw_mcp.models.typed_dicts import AutoMaintenanceDict
from trw_mcp.state.persistence import (
    FileEventLogger,
    FileStateWriter,
)
from trw_mcp.tools._ceremony_maintenance_steps import (
    _census_log_fields as _census_log_fields,
)
from trw_mcp.tools._ceremony_maintenance_steps import (
    _check_version_sentinel as _check_version_sentinel,
)
from trw_mcp.tools._ceremony_maintenance_steps import (
    _record_step_outcome as _record_step_outcome,
)
from trw_mcp.tools._ceremony_maintenance_steps import (
    _run_learn_journal_drain as _run_learn_journal_drain,
)
from trw_mcp.tools._ceremony_maintenance_steps import (
    _run_wal_maintenance as _run_wal_maintenance,
)
from trw_mcp.tools._ceremony_maintenance_steps import (
    _step_pressure_decision as _step_pressure_decision,
)
from trw_mcp.tools._ceremony_maintenance_steps import (
    _writer_pressure_details as _writer_pressure_details,
)
from trw_mcp.tools._ceremony_telemetry import (
    _resolve_trw_dir_compat as _resolve_trw_dir_compat,
)
from trw_mcp.tools._ceremony_telemetry import (
    step_first_session_marker as step_first_session_marker,
)
from trw_mcp.tools._ceremony_telemetry import (
    step_telemetry_startup as step_telemetry_startup,
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
    perform_session_recalls as perform_session_recalls,
)
from trw_mcp.tools._session_recall_helpers import (
    record_session_start_surfaces as record_session_start_surfaces,
)
from trw_mcp.tools._sync_health import (
    step_sync_health as step_sync_health,
)

# fmt: on

logger = structlog.get_logger(__name__)


# ── Session lifecycle step functions ─────────────────────────────────────


def step_log_session_event(
    run_dir: Path | None,
    results: dict[str, object],
    query: str,
    is_focused: bool,
    session_id: str = "",
) -> None:
    """Log session_start event to events.jsonl (FR01, PRD-CORE-031).

    Writes to run-scoped events file if a run is active, otherwise falls
    back to a session-events file under the context directory.
    """
    from trw_mcp.models.config import get_config

    config = get_config()
    writer = FileStateWriter()
    events = FileEventLogger(writer)

    # PRD-HPO-MEAS-001 FR-2: the session's own bootstrap event must carry
    # the resolved surface_snapshot_id. The id is resolved upstream at
    # ceremony.py Step 2c and threaded in via results["surface_snapshot_id"].
    # During Phase 1 the value may be empty-string (fail-open on stamping
    # failure) — we still write the key so parsers can assert presence
    # rather than existence-or-not.
    event_data: dict[str, object] = {
        "learnings_recalled": int(str(results.get("learnings_count", 0))),
        "run_detected": run_dir is not None,
        "query": query if is_focused else "*",
        "surface_snapshot_id": str(results.get("surface_snapshot_id", "")),
        "session_id": session_id,
    }
    if run_dir is not None:
        events_path = run_dir / "meta" / "events.jsonl"
        if events_path.parent.exists():
            events.log_event(events_path, "session_start", event_data)
    else:
        trw_dir_path = _resolve_trw_dir_compat()
        context_path = trw_dir_path / config.context_dir
        writer.ensure_dir(context_path)
        fallback_path = context_path / "session-events.jsonl"
        events.log_event(fallback_path, "session_start", event_data)


def step_increment_session_counter() -> None:
    """Increment sessions_tracked counter (FIX-050-FR06)."""
    from trw_mcp.state.analytics.counters import increment_session_start_counter

    increment_session_start_counter(_resolve_trw_dir_compat())


def step_sanitize_and_maintain() -> AutoMaintenanceDict:
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

        sanitize_ceremony_feedback(_resolve_trw_dir_compat())
    except Exception:  # justified: fail-open, sanitization must not block session start
        logger.warning("ceremony_feedback_sanitize_failed", exc_info=True)

    return run_auto_maintenance(_resolve_trw_dir_compat(), config)


def step_embed_health() -> dict[str, object]:
    """Check embeddings health status for agents (FR01, PRD-FIX-053).

    Returns:
        Dict with enabled, available, advisory, recent_failures keys.

    Failures propagate to the non-critical step-table driver, which records a
    typed degradation without blocking session start.
    """
    from trw_mcp.state.memory_adapter import check_embeddings_status

    embed_status = check_embeddings_status(allow_initialize=False)
    return dict(embed_status)


def step_mark_session_started(session_id: str | None = None) -> None:
    """Mark session started in ceremony state tracker (PRD-CORE-074 FR04)."""
    from trw_mcp.state.ceremony_progress import mark_session_started

    mark_session_started(_resolve_trw_dir_compat(), session_id=session_id)


def step_ceremony_status(results: dict[str, object]) -> None:
    """Inject ceremony status into response when full ceremony mode is active.

    Skipped for light ceremony mode (FR07, PRD-CORE-084). Carries the
    session-start reactive context (PRD-CORE-084 FR03).
    """
    from trw_mcp.models.config import get_config
    from trw_mcp.tools._ceremony_status_context import append_ceremony_status_for_tool

    if get_config().effective_ceremony_mode == "light":
        return

    append_ceremony_status_for_tool(results, _resolve_trw_dir_compat(), tool_name="session_start")


def run_auto_maintenance(
    trw_dir: Path,
    config: TRWConfig,
) -> AutoMaintenanceDict:
    """Run auto-upgrade check, stale run close, and embeddings backfill.

    Returns a dict with keys for each maintenance operation that produced results.
    All operations are fail-open — individual failures do not affect others.
    """
    from trw_mcp.state.memory_pressure import WriterCensus, take_writer_census

    maintenance: AutoMaintenanceDict = {}
    census = WriterCensus(
        writer_pids=(),
        writer_count=0,
        peer_writer_count=0,
        threshold=config.session_start_writer_pressure_threshold,
        under_pressure=False,
        census_state="unreadable",
        identity_state="unverified",
        heartbeat_state="unavailable",
    )
    try:
        census = take_writer_census(
            trw_dir,
            threshold=config.session_start_writer_pressure_threshold,
            pin_ttl_hours=config.pin_ttl_hours,
        )
    except Exception:  # justified: pressure detection must never block maintenance
        logger.warning("maintenance_writer_pressure_check_failed", exc_info=True)
    defer_memory_heavy = config.session_start_defer_under_writer_pressure and census.under_pressure
    # PRD-CORE-257-FR07: exactly ONE census line per trw_session_start, before
    # any step decision, whether or not pressure was detected. The per-site
    # warnings kept their step-specific fields but no longer restate the census,
    # so a healthy-but-loaded machine is now measurable instead of silent and a
    # pressured one logs these numbers once rather than up to six times.
    logger.info(
        "writer_census",
        writer_count=census.writer_count,
        peer_writer_count=census.peer_writer_count,
        threshold=census.threshold,
        under_pressure=census.under_pressure,
        census_state=census.census_state,
        identity_state=census.identity_state,
        heartbeat_state=census.heartbeat_state,
    )
    if census.census_state == "unreadable":
        # "Nothing measured" must be distinguishable from "measured nothing".
        logger.warning("writer_census_unreadable", threshold=census.threshold)

    # Version sentinel check — detect if installer ran since this process started
    try:
        _check_version_sentinel(trw_dir, maintenance)
    except Exception:  # justified: fail-open, version sentinel check must not block session start
        logger.warning("maintenance_version_sentinel_failed", exc_info=True)

    # Auto-upgrade check (PRD-INFRA-014)
    upgrade_decision = _step_pressure_decision(
        trw_dir, config, maintenance, "auto_upgrade_check", defer_memory_heavy=defer_memory_heavy
    )
    try:
        if upgrade_decision.defer:
            maintenance["auto_upgrade_check_deferred"] = _writer_pressure_details(census, upgrade_decision)
            logger.warning("auto_upgrade_check_deferred", **_census_log_fields(census))
        else:
            from trw_mcp.state.auto_upgrade import check_for_update

            update_info = check_for_update()
            if update_info.get("available"):
                maintenance["update_advisory"] = str(update_info.get("advisory", ""))
                if config.auto_upgrade:
                    from trw_mcp.state.auto_upgrade import perform_upgrade

                    upgrade_result = perform_upgrade(update_info)
                    if upgrade_result.get("applied"):
                        maintenance["auto_upgrade"] = upgrade_result
        _record_step_outcome(trw_dir, maintenance, "auto_upgrade_check", upgrade_decision)
    except Exception:  # justified: fail-open, auto-upgrade must not block session start
        logger.warning("maintenance_auto_upgrade_failed", exc_info=True)
        _record_step_outcome(trw_dir, maintenance, "auto_upgrade_check", upgrade_decision, failed=True)

    # Auto-close stale runs. PRD-CORE-257 audit row 9: the ledger decision
    # used to be consulted UNCONDITIONALLY, before checking whether the
    # feature is even enabled — so a disabled auto-close could still open and
    # age a deferral streak under pressure, and enabling the feature later
    # could immediately force a phantom "expired" run. Gate the ledger read
    # itself on ``run_auto_close_enabled``: disabled is a THIRD state, not a
    # deferral, so nothing is consulted and no outcome is recorded.
    if config.run_auto_close_enabled:
        stale_decision = _step_pressure_decision(
            trw_dir, config, maintenance, "stale_runs", defer_memory_heavy=defer_memory_heavy
        )
        try:
            if stale_decision.defer:
                maintenance["stale_runs_deferred"] = _writer_pressure_details(census, stale_decision)
                logger.warning("stale_runs_close_deferred", **_census_log_fields(census))
            else:
                from trw_mcp.state.analytics._stale_runs import auto_close_stale_runs

                close_result = auto_close_stale_runs()
                closed_count = int(str(close_result.get("count", 0)))
                if closed_count > 0:
                    maintenance["stale_runs_closed"] = close_result
            _record_step_outcome(trw_dir, maintenance, "stale_runs", stale_decision)
        except Exception:  # justified: fail-open, stale run cleanup must not block session start
            logger.warning("maintenance_stale_runs_close_failed", exc_info=True)
            _record_step_outcome(trw_dir, maintenance, "stale_runs", stale_decision, failed=True)

    # Embeddings status check + warm-up + backfill (extracted to sibling to keep
    # this facade under the 350 effective-LOC module gate).
    from trw_mcp.tools._ceremony_embeddings_maintenance import run_embeddings_maintenance

    run_embeddings_maintenance(
        trw_dir,
        config,
        maintenance,
        census=census,
        defer_memory_heavy=defer_memory_heavy,
    )

    # PRD-CORE-248 FR04: the checkpoint is called unconditionally. Writer
    # pressure selects its MODE, never whether it runs, so no deferral state is
    # passed here any more.
    _run_wal_maintenance(trw_dir, maintenance)

    _run_learn_journal_drain(
        trw_dir,
        config,
        maintenance,
        census=census,
        defer_memory_heavy=defer_memory_heavy,
    )

    # PRD-CORE-257-FR12: ``auto_maintenance_complete`` fired identically when
    # every one of those keys was a DEFERRAL — completion asserted for a pass in
    # which nothing ran. The aggregate now says only that the pass was
    # evaluated, and carries the per-step outcome map that makes the difference
    # readable. "complete" is reserved for a pass in which every covered step
    # reports ``executed`` or ``expired_ran``.
    logger.info(
        "auto_maintenance_evaluated",
        keys=list(maintenance.keys()),
        step_outcomes=dict(maintenance.get("step_outcomes", {})),
        under_pressure=defer_memory_heavy,
    )
    return maintenance
