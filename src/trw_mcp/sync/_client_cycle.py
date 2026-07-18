"""One-cycle orchestration for :mod:`trw_mcp.sync.client`."""

from __future__ import annotations

import asyncio
import sys
from time import perf_counter
from typing import TYPE_CHECKING, Any

import structlog

from trw_mcp.sync.outcomes import PendingOutcome, write_synced_marker
from trw_mcp.sync.pull import _COMPANY_SYNC_SOURCE

if TYPE_CHECKING:
    from trw_mcp.sync.client import BackendSyncClient

logger = structlog.get_logger(__name__)

_SLOW_LOCAL_WORK_LOG_MS = 1_000.0
_PATHOLOGICAL_LOCAL_WORK_MS = 10_000.0


def _is_company_entry(item: dict[str, Any], company_source: str) -> bool:
    """Return whether a pulled learning belongs to the company cursor."""
    meta = item.get("metadata")
    return isinstance(meta, dict) and meta.get("source") == company_source


def _target_report_status(report: dict[str, object]) -> str:
    """Return the normalized health status for one sync target report."""
    raw_status = report.get("status")
    if raw_status in {"success", "partial_error", "error"}:
        return str(raw_status)
    if report.get("error") is not None:
        return "error"
    raw_failed = report.get("failed", 0)
    failed = int(raw_failed) if isinstance(raw_failed, (int, float)) else 0
    return "partial_error" if failed > 0 else "success"


async def _offload_sync_work(label: str, func: Any, *args: Any, **kwargs: Any) -> Any:
    """Run blocking sync-local work off the FastMCP event loop."""
    facade = sys.modules.get("trw_mcp.sync.client")
    clock = getattr(facade, "perf_counter", perf_counter)
    facade_logger = getattr(facade, "logger", logger)
    started = clock()
    try:
        return await asyncio.to_thread(func, *args, **kwargs)
    finally:
        elapsed_ms = (clock() - started) * 1000
        if elapsed_ms >= _SLOW_LOCAL_WORK_LOG_MS:
            slow = elapsed_ms >= _PATHOLOGICAL_LOCAL_WORK_MS
            log = facade_logger.warning if slow else facade_logger.info
            log(
                "sync_local_work_offloaded",
                label=label,
                duration_ms=round(elapsed_ms, 2),
                slow=slow,
                slow_threshold_ms=int(_PATHOLOGICAL_LOCAL_WORK_MS),
            )


def _write_synced_markers(pending_outcomes: list[PendingOutcome], target_label: str) -> None:
    """Write synced markers for successfully pushed outcome payloads."""
    for item in pending_outcomes:
        if item.run_dir is not None and item.sync_hash:
            write_synced_marker(
                item.run_dir,
                run_id=item.run_id,
                sync_hash=item.sync_hash,
                target_label=target_label,
                run_yaml_hash=item.run_yaml_hash,
            )


async def run_one_cycle(client: BackendSyncClient, *, force: bool = False) -> None:
    """Execute one push+pull sync cycle for ``client``."""
    facade = sys.modules["trw_mcp.sync.client"]
    facade_logger = facade.logger
    if not client._targets:
        facade_logger.debug("sync_cycle_skipped", reason="no_targets", client_id=client._client_id)
        return
    if not force and not client._coordinator.should_sync(sync_interval=client._scheduled_interval_seconds):
        facade_logger.debug("sync_cycle_skipped", reason="too_recent", client_id=client._client_id)
        return

    with client._coordinator.acquire_sync_lock() as acquired:
        if not acquired:
            return
        if client._learning_sharing_enabled:
            dirty = await facade._offload_sync_work("get_dirty_entries", client._get_dirty_entries)
            facade_logger.info(
                "sync_push_started", dirty_count=len(dirty), client_id=client._client_id
            ) if dirty else facade_logger.debug(
                "sync_push_skipped", reason="no_dirty_entries", client_id=client._client_id
            )
        else:
            dirty = []
            facade_logger.debug("sync_push_skipped", reason="learning_sharing_disabled", client_id=client._client_id)

        if client._platform_telemetry_enabled:
            pending_outcomes = await facade._offload_sync_work(
                "load_pending_outcomes",
                facade.load_pending_outcomes,
                client._trw_dir,
                since_line=client._coordinator.get_last_outcome_line(),
            )
        else:
            pending_outcomes = []
            facade_logger.debug(
                "sync_outcome_push_skipped", reason="platform_telemetry_disabled", client_id=client._client_id
            )

        report, push_result = await client._fanout_push(
            dirty=dirty, outcomes=[item.payload for item in pending_outcomes]
        )
        statuses = [_target_report_status(item) for item in report.values()]
        facade_logger.info(
            "sync_cycle_report",
            client_id=client._client_id,
            targets=len(client._targets),
            successful=statuses.count("success"),
            partial_error=statuses.count("partial_error"),
            failed=statuses.count("error"),
            unhealthy=sum(status != "success" for status in statuses),
            report=report,
        )
        all_targets_succeeded = bool(report) and all(status == "success" for status in statuses)
        push_incomplete = not all_targets_succeeded and bool(dirty or pending_outcomes)
        push_seq = 0
        if dirty and all_targets_succeeded and push_result.failed == 0:
            push_seq = max((entry.sync_seq for entry in dirty), default=0)
            await facade._offload_sync_work(
                "mark_synced", client._mark_synced, dirty[: push_result.pushed + push_result.skipped]
            )
        if push_incomplete:
            if dirty and len(client._targets) == 1:
                raw_failed = report.get(client._targets[0].label, {}).get("failed", len(dirty))
                failed_count = int(raw_failed) if isinstance(raw_failed, (int, float)) else len(dirty)
                client._coordinator.record_sync_failure(f"push failed: {failed_count or len(dirty)} entries")
            else:
                unhealthy = sum(status != "success" for status in statuses)
                client._coordinator.record_sync_failure(f"{unhealthy} of {len(client._targets)} targets failed")
        if pending_outcomes and all_targets_succeeded:
            client._coordinator.record_outcome_push_success(max(item.line_no for item in pending_outcomes))
            successful_labels = ",".join(
                label for label, item in report.items() if _target_report_status(item) == "success"
            )
            await facade._offload_sync_work(
                "write_synced_markers", facade._write_synced_markers, pending_outcomes, successful_labels or "unknown"
            )

        pull_seq = client._coordinator.get_last_pull_seq()
        raw_company_pull_seq = client._coordinator.get_last_company_pull_seq()
        company_pull_seq = int(raw_company_pull_seq) if isinstance(raw_company_pull_seq, (int, float)) else 0
        pull_result = await client._puller.pull_intel_state(
            etag=client._cache.etag if client._config.intel_cache_enabled else None,
            since_seq=pull_seq,
            model_family=getattr(client._config, "model_family", ""),
            trw_version=getattr(client._config, "framework_version", ""),
            client_id=client._client_id,
            since_company_seq=company_pull_seq,
        )
        if pull_result is None:
            client._reset_poll_schedule()
            client._coordinator.record_sync_failure("pull failed")
            client._apply_failure_backoff(reason="pull failed")
            return
        if pull_result.not_modified:
            client._restore_poll_schedule()
            if push_incomplete:
                client._coordinator.record_pull_success(pull_seq=pull_seq)
                client._apply_failure_backoff(reason="push failed")
            else:
                client._coordinator.record_sync_success(
                    pushed=push_result.pushed, pulled=0, push_seq=push_seq, pull_seq=pull_seq, pull_completed=True
                )
            return

        if client._config.intel_cache_enabled and pull_result.state is not None:
            client._cache.update(pull_result.state, etag=pull_result.etag)
        pulled = len(pull_result.team_learnings or [])
        merged = (
            client._puller.merge_team_learnings(pull_result.team_learnings) if client._config.team_sync_enabled else 0
        )
        next_pull_seq = max(
            [
                pull_seq,
                *(
                    int(item.get("sync_seq", 0))
                    for item in (pull_result.team_learnings or [])
                    if isinstance(item, dict) and not _is_company_entry(item, _COMPANY_SYNC_SOURCE)
                ),
            ]
        )
        client._coordinator.record_company_pull_seq(max(company_pull_seq, pull_result.next_company_seq))
        client._apply_sync_hints(pull_result.sync_hints)
        facade_logger.info(
            "sync_cycle_completed",
            client_id=client._client_id,
            pushed=push_result.pushed,
            pull_seq=next_pull_seq,
            pulled=pulled,
            merged=merged,
            next_delay_seconds=client._next_sleep_seconds,
            immediate_repoll=client._next_cycle_force,
        )
        if push_incomplete:
            client._coordinator.record_pull_success(pull_seq=next_pull_seq)
            client._apply_failure_backoff(reason="push failed")
        else:
            client._coordinator.record_sync_success(
                pushed=push_result.pushed,
                pulled=pulled,
                push_seq=push_seq,
                pull_seq=next_pull_seq,
                pull_completed=True,
            )
