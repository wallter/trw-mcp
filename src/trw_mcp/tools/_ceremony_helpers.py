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
"""

from __future__ import annotations

import json
from pathlib import Path

import structlog

from trw_mcp.models.config import TRWConfig
from trw_mcp.models.typed_dicts import AutoMaintenanceDict
from trw_mcp.state.persistence import (
    FileEventLogger,
    FileStateWriter,
)
from trw_mcp.tools._ceremony_status import (
    append_ceremony_status as append_ceremony_status,
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


def step_ceremony_status(
    results: dict[str, object],
) -> None:
    """Inject ceremony status into response when full ceremony mode is active.

    Skipped for light ceremony mode (FR07, PRD-CORE-084).
    """
    from trw_mcp.models.config import get_config

    config = get_config()
    if config.effective_ceremony_mode == "light":
        return

    append_ceremony_status(results, _resolve_trw_dir_compat())


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

    # Potemkin defect D (sub_zAfRqZYYq2KtF72d): fire ONLY when the on-disk
    # installed version is genuinely NEWER than the running process — a real
    # pending upgrade that a ``/mcp`` reload would apply. The previous bare
    # ``!=`` check also fired when on-disk was OLDER than (or differently
    # formatted from) the running version, e.g. a stale sentinel left by a
    # downgrade or a server that out-lived the on-disk install. That produced
    # the confusing "vOLD was installed but still running vNEW — reload"
    # advisory the operator reported (reloading would DOWN-grade, not update).
    # Reuse the canonical semver comparator so the direction logic lives in one
    # place; it fails closed (no advisory) on any unparseable version.
    from trw_mcp.state.auto_upgrade import _compare_versions

    if _compare_versions(running_version, installed_version) and "update_advisory" not in maintenance:
        maintenance["update_advisory"] = (
            f"TRW v{installed_version} is installed on disk but this MCP server is still "
            f"running v{running_version}. Run /mcp to reload."
        )


def run_auto_maintenance(
    trw_dir: Path,
    config: TRWConfig,
) -> AutoMaintenanceDict:
    """Run auto-upgrade check, stale run close, and embeddings backfill.

    Returns a dict with keys for each maintenance operation that produced results.
    All operations are fail-open — individual failures do not affect others.
    """
    maintenance: AutoMaintenanceDict = {}
    defer_memory_heavy = False
    writer_pids: list[int] = []
    defer_reason = "writer_pressure"
    if config.session_start_defer_under_writer_pressure:
        try:
            from trw_mcp.state.memory_pressure import should_defer_session_start_optional_work

            defer_memory_heavy, writer_pids, defer_reason = should_defer_session_start_optional_work(
                trw_dir,
                threshold=config.session_start_writer_pressure_threshold,
                pin_ttl_hours=config.pin_ttl_hours,
            )
        except Exception:  # justified: pressure detection must never block maintenance
            logger.warning("maintenance_writer_pressure_check_failed", exc_info=True)

    # Version sentinel check — detect if installer ran since this process started
    try:
        _check_version_sentinel(trw_dir, maintenance)
    except Exception:  # justified: fail-open, version sentinel check must not block session start
        logger.warning("maintenance_version_sentinel_failed", exc_info=True)

    # Auto-upgrade check (PRD-INFRA-014)
    try:
        if defer_memory_heavy:
            maintenance["auto_upgrade_check_deferred"] = _writer_pressure_details(config, defer_reason, writer_pids)
            logger.warning(
                "auto_upgrade_check_deferred",
                reason=defer_reason,
                writer_pids=writer_pids,
                writer_count=len(writer_pids),
                threshold=config.session_start_writer_pressure_threshold,
            )
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
    except Exception:  # justified: fail-open, auto-upgrade must not block session start
        logger.warning("maintenance_auto_upgrade_failed", exc_info=True)

    # Auto-close stale runs
    try:
        if config.run_auto_close_enabled:
            if defer_memory_heavy:
                maintenance["stale_runs_deferred"] = _writer_pressure_details(
                    config,
                    defer_reason,
                    writer_pids,
                    retain_legacy_reason=True,
                )
                logger.warning(
                    "stale_runs_close_deferred",
                    reason=defer_reason,
                    writer_pids=writer_pids,
                    writer_count=len(writer_pids),
                    threshold=config.session_start_writer_pressure_threshold,
                )
            else:
                from trw_mcp.state.analytics._stale_runs import auto_close_stale_runs

                close_result = auto_close_stale_runs()
                closed_count = int(str(close_result.get("count", 0)))
                if closed_count > 0:
                    maintenance["stale_runs_closed"] = close_result
    except Exception:  # justified: fail-open, stale run cleanup must not block session start
        logger.warning("maintenance_stale_runs_close_failed", exc_info=True)

    # Embeddings status check + warm-up + backfill (extracted to sibling to keep
    # this facade under the 350 effective-LOC module gate).
    from trw_mcp.tools._ceremony_embeddings_maintenance import run_embeddings_maintenance

    run_embeddings_maintenance(
        trw_dir,
        config,
        maintenance,
        defer_memory_heavy=defer_memory_heavy,
        defer_reason=defer_reason,
        writer_pids=writer_pids,
    )

    _run_wal_maintenance(
        trw_dir,
        config,
        maintenance,
        defer_memory_heavy=defer_memory_heavy,
        defer_reason=defer_reason,
        writer_pids=writer_pids,
    )

    logger.debug(
        "auto_maintenance_complete",
        keys=list(maintenance.keys()),
    )
    return maintenance


def _writer_pressure_details(
    config: TRWConfig,
    defer_reason: str,
    writer_pids: list[int],
    *,
    retain_legacy_reason: bool = False,
) -> dict[str, object]:
    from trw_mcp.state.memory_pressure import writer_pressure_details

    return writer_pressure_details(
        defer_reason,
        writer_pids,
        threshold=config.session_start_writer_pressure_threshold,
        retain_legacy_reason=retain_legacy_reason,
    )


def _run_wal_maintenance(
    trw_dir: Path,
    config: TRWConfig,
    maintenance: AutoMaintenanceDict,
    *,
    defer_memory_heavy: bool,
    defer_reason: str,
    writer_pids: list[int],
) -> None:
    """Run or defer the WAL checkpoint without coupling its failures to other maintenance."""
    try:
        if defer_memory_heavy:
            maintenance["wal_checkpoint_deferred"] = _writer_pressure_details(config, defer_reason, writer_pids)
            logger.warning(
                "wal_checkpoint_deferred",
                reason=defer_reason,
                writer_pids=writer_pids,
                writer_count=len(writer_pids),
                threshold=config.session_start_writer_pressure_threshold,
            )
        else:
            from trw_mcp.state.memory_adapter import maybe_checkpoint_wal

            wal_result = maybe_checkpoint_wal(trw_dir)
            if wal_result.get("checkpointed"):
                maintenance["wal_checkpoint"] = wal_result
    except Exception:  # justified: fail-open, WAL checkpoint must not block session start
        logger.warning("maintenance_wal_checkpoint_failed", exc_info=True)
