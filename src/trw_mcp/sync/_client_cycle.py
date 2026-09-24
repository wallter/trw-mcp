"""One-cycle orchestration for :mod:`trw_mcp.sync.client`."""

from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from time import perf_counter
from typing import TYPE_CHECKING, Any

import structlog

from trw_mcp.sync._team_merge_result import TeamMergeResult
from trw_mcp.sync.outcomes import PendingOutcome, write_synced_marker
from trw_mcp.sync.pull import _COMPANY_SYNC_SOURCE, PullResult

if TYPE_CHECKING:
    from trw_mcp.sync._client_push import TargetPushOutcome
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


def _secondary_target_health(report: dict[str, dict[str, object]], primary_label: str) -> dict[str, dict[str, object]]:
    """Return per-secondary health records for the sync-state report surface.

    PRD-FIX-125-FR01: every non-primary target's status is reported separately so
    a diverging best-effort replica is visible without gating the pipeline.
    """
    now = datetime.now(tz=timezone.utc).isoformat()
    health: dict[str, dict[str, object]] = {}
    for label, item in report.items():
        if label == primary_label:
            continue
        status = _target_report_status(item)
        health[label] = {
            "status": status,
            "failed": item.get("failed", 0),
            "last_error": item.get("error"),
            "last_error_at": now if status != "success" else None,
        }
    return health


def _outcomes_were_accepted(client: BackendSyncClient, push_result: TargetPushOutcome) -> bool:
    """Whether the PRIMARY durably accepted the whole outcome batch it was sent.

    PRD-FIX-125-FR01. Unlike the learnings path this is a whole-batch verdict,
    not a slice, and the asymmetry is forced by the two endpoints rather than
    chosen:

    * ``push_learnings`` reads ``skipped`` back from the response, so
      ``pushed + skipped`` is a real accepted-count and ``dirty`` can be sliced
      by it.
    * ``push_outcomes`` has no ``skipped`` concept — it counts only
      ``inserted``. An outcome the backend already holds (a re-offer after an
      earlier successful push) reports as neither pushed, skipped, nor failed.
      Slicing by ``pushed`` there would acknowledge nothing forever and
      re-offer the same payload every cycle — measured live on 2026-09-03, when
      the primary returned ``pushed: 0, failed: 0`` for a batch of 8 it already
      had.

    What makes the whole-batch ack safe is that a partial acceptance is not
    representable: ``push_outcomes`` fails a batch as a unit
    (``total_failed += len(batch)``), any failure makes the primary's status
    ``partial_error``, and ``primary_succeeded`` is already False in that case.
    So on this branch every outcome sent was durably handled — inserted or
    already present.

    The remaining hole this closes: ``push_outcomes`` ALSO returns an empty
    zero-failure result without sending anything when telemetry consent is off.
    Acknowledging on that would mark outcomes synced that never left the
    machine, so the consent flag is checked rather than inferred from counts.
    """
    if not client._platform_telemetry_enabled:
        return False
    return push_result.outcomes.failed == 0


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
        # PRD-FIX-125-FR01: the cycle verdict is keyed on the PRIMARY target
        # (resolved_sync_targets[0]) — a best-effort secondary can no longer pin
        # the failure counter. A missing primary entry is NOT success.
        primary_label = client._targets[0].label
        primary_entry = report.get(primary_label)
        primary_status = _target_report_status(primary_entry) if isinstance(primary_entry, dict) else None
        primary_succeeded = primary_status == "success"
        client._coordinator.record_target_health(
            primary_target_label=primary_label,
            secondary_targets=_secondary_target_health(report, primary_label),
        )
        push_incomplete = not primary_succeeded and bool(dirty or pending_outcomes)
        push_seq = 0
        # Each acknowledgement path slices its OWN list by the primary's count
        # for its OWN kind. Summing the two kinds first made an outcome insert
        # count toward the learning slice (PRD-FIX-125-FR01).
        learnings_accepted = push_result.learnings.pushed + push_result.learnings.skipped
        if dirty and primary_succeeded and push_result.learnings.failed == 0:
            push_seq = max((entry.sync_seq for entry in dirty), default=0)
            await facade._offload_sync_work("mark_synced", client._mark_synced, dirty[:learnings_accepted])
        if push_incomplete:
            if dirty and len(client._targets) == 1:
                raw_failed = report.get(primary_label, {}).get("failed", len(dirty))
                failed_count = int(raw_failed) if isinstance(raw_failed, (int, float)) else len(dirty)
                client._coordinator.record_sync_failure(f"push failed: {failed_count or len(dirty)} entries")
            else:
                client._coordinator.record_sync_failure(
                    f"primary target {primary_label} push {primary_status or 'missing'}"
                )
        if pending_outcomes and primary_succeeded and _outcomes_were_accepted(client, push_result):
            client._coordinator.record_outcome_push_success(max(item.line_no for item in pending_outcomes))
            await facade._offload_sync_work(
                "write_synced_markers", facade._write_synced_markers, pending_outcomes, primary_label
            )

        pull_seq = client._coordinator.get_last_pull_seq()
        raw_company_pull_seq = client._coordinator.get_last_company_pull_seq()
        company_pull_seq = int(raw_company_pull_seq) if isinstance(raw_company_pull_seq, (int, float)) else 0
        step = await pull_and_merge(
            client,
            pull_seq=pull_seq,
            company_pull_seq=company_pull_seq,
            etag=client._cache.etag if client._config.intel_cache_enabled else None,
        )
        pull_result, merge_result = step.result, step.merge
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
        # A cycle that pulled 50 and applied 1 is not a completed cycle in the
        # sense the log used to claim. Carry the per-outcome counts into the
        # cycle record so the shortfall is countable at the surface an operator
        # actually reads, and raise the level when anything was rejected.
        pulled, merged, cursor_may_advance, next_pull_seq = (
            step.pulled,
            merge_result.applied,
            step.cursor_may_advance,
            step.next_pull_seq,
        )
        # PRD-FIX-138-FR02: the ETag is cached only when the cursor advanced. A
        # held cursor means this batch must be RE-OFFERED next cycle — but the
        # cache used to record the ETag before the merge ran, so the next pull
        # sent If-None-Match, the server answered 304, and the 304 arm above
        # booked a success. Held batch, cached ETag, permanent stall.
        if client._config.intel_cache_enabled and pull_result.state is not None:
            etag_to_cache = pull_result.etag if cursor_may_advance else None
            if not cursor_may_advance and pull_result.etag:
                facade_logger.info(
                    "sync_pull_etag_withheld",
                    client_id=client._client_id,
                    pull_seq=pull_seq,
                    detail="cursor held; next pull is unconditional so the batch is re-offered",
                )
            client._cache.update(pull_result.state, etag=etag_to_cache)
        client._coordinator.record_company_pull_seq(max(company_pull_seq, pull_result.next_company_seq))
        client._apply_sync_hints(pull_result.sync_hints)
        cycle_emit = facade_logger.warning if merge_result.rejected else facade_logger.info
        cycle_emit(
            "sync_cycle_completed",
            client_id=client._client_id,
            pushed=push_result.pushed,
            pull_seq=next_pull_seq,
            pulled=pulled,
            merged=merged,
            merge_status=merge_result.status,
            merge_rejected=merge_result.rejected,
            merge_skipped_no_id=merge_result.skipped_no_id,
            merge_invalid=merge_result.invalid,
            merge_quarantined=merge_result.quarantined,
            merge_blocked=merge_result.blocked,
            merge_failed=merge_result.failed,
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


@dataclass(frozen=True)
class PullStep:
    """One pull page and its merge, and where the cursor may go (PRD-CORE-280 FR02)."""

    #: ``None`` when the pull failed; ``not_modified`` when the server answered 304.
    result: PullResult | None
    merge: TeamMergeResult
    pulled: int
    #: The cursor after this page: ``pull_seq`` itself when it is held.
    next_pull_seq: int
    cursor_may_advance: bool


async def pull_and_merge(
    client: BackendSyncClient, *, pull_seq: int, company_pull_seq: int, etag: str | None
) -> PullStep:
    """Pull the page after *pull_seq*, merge its team learnings, and judge the cursor.

    The one cursor engine: the periodic cycle and ``sync pull --full`` both call
    it and differ only in where they start, what they do with the ETag and cache,
    and when they stop. It touches no cursor, cache or ETag itself.
    """
    facade_logger = sys.modules["trw_mcp.sync.client"].logger
    pull_result = await client._puller.pull_intel_state(
        etag=etag,
        since_seq=pull_seq,
        model_family=getattr(client._config, "model_family", ""),
        trw_version=getattr(client._config, "framework_version", ""),
        client_id=client._client_id,
        since_company_seq=company_pull_seq,
    )
    if pull_result is None or pull_result.not_modified:
        return PullStep(pull_result, TeamMergeResult(), 0, pull_seq, False)
    pulled = len(pull_result.team_learnings or [])
    merge_result = (
        client._puller.merge_team_learnings(pull_result.team_learnings)
        if client._config.team_sync_enabled
        else TeamMergeResult()
    )
    # THE CURSOR MAY ONLY PASS ITEMS THAT WERE JUDGED. Pulls are since_seq
    # bounded, so anything this cursor steps over is never offered again —
    # advancing past an item the merge never saw discards it permanently, and
    # nothing anywhere reports a number.
    #
    # The arms are NOT alike and are deliberately not treated alike:
    #   invalid / skipped_no_id / quarantined — a DECISION about the item. It
    #     would be judged the same way next time, so holding the cursor makes
    #     a poison-pill loop. Advancing is correct.
    #   failed / unavailable — the item was NEVER JUDGED. `failed` raised
    #     while storing; `unavailable` means the merge could not run at all,
    #     which skips a whole batch at once.
    #   team sync disabled — not a merge outcome. The items were never even
    #     offered, so with team sync off every team learning that arrived used
    #     to advance the cursor past itself and vanish. A user who later
    #     enabled team sync had permanently lost everything that arrived while
    #     it was off.
    #
    # Held at pull_seq the whole batch is re-offered next cycle. That costs a
    # duplicate MERGE, not a duplicate row: `pull.py::find_existing` keys on
    # source_learning_id and updates the existing entry.
    #
    # Counts, not ids: TeamMergeResult carries no per-item sequence numbers,
    # so a precise "advance past the applied ones only" needs a shape change
    # in the merge. This is the conservative version — it can re-offer a few
    # already-applied items, and it can never drop one.
    cursor_may_advance = client._config.team_sync_enabled and not merge_result.unavailable and merge_result.failed == 0
    if cursor_may_advance:
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
    else:
        next_pull_seq = pull_seq
        if pulled:
            facade_logger.warning(
                "sync_pull_cursor_held",
                client_id=client._client_id,
                pulled=pulled,
                reason=(
                    "team_sync_disabled"
                    if not client._config.team_sync_enabled
                    else "merge_unavailable"
                    if merge_result.unavailable
                    else "merge_failed"
                ),
                detail="the cursor did not advance; these items will be re-offered rather than skipped",
            )
    return PullStep(pull_result, merge_result, pulled, next_pull_seq, cursor_may_advance)
