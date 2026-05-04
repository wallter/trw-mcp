"""Internal helpers for BackendSyncClient fan-out push handling."""

from __future__ import annotations

from time import perf_counter
from typing import TYPE_CHECKING, Protocol

import structlog

from trw_mcp.sync.push import PushResult, SyncPusher

logger = structlog.get_logger(__name__)

if TYPE_CHECKING:
    from trw_memory.models.memory import MemoryEntry


class _TargetLike(Protocol):
    url: str
    api_key: str
    label: str


async def _push_to_target(
    *,
    client_id: str,
    target: _TargetLike,
    primary_target_label: str | None,
    primary_pusher: SyncPusher,
    pusher_map: dict[str, SyncPusher],
    batch_size: int,
    timeout: float,
    dirty: list[MemoryEntry],
    outcomes: list[dict[str, object]],
) -> PushResult:
    """PRD-FIX-087 FR03: async — awaits pusher.push_learnings / push_outcomes."""
    started = perf_counter()
    if primary_target_label is not None and target.label == primary_target_label:
        pusher = primary_pusher
    else:
        pusher = pusher_map.get(target.label)
    if pusher is None:
        pusher = SyncPusher(
            backend_url=target.url,
            api_key=target.api_key,
            batch_size=batch_size,
            timeout=timeout,
            client_id=client_id,
        )
        pusher_map[target.label] = pusher

    total = PushResult()
    if dirty:
        logger.info(
            "sync_target_push_start",
            label=target.label,
            kind="learnings",
            client_id=client_id,
        )
        learning_result = await pusher.push_learnings(dirty)
        total = PushResult(
            pushed=total.pushed + learning_result.pushed,
            failed=total.failed + learning_result.failed,
            skipped=total.skipped + learning_result.skipped,
        )
        logger.info(
            "sync_target_push_complete",
            label=target.label,
            kind="learnings",
            pushed=learning_result.pushed,
            skipped=learning_result.skipped,
            failed=learning_result.failed,
            duration_ms=int((perf_counter() - started) * 1000),
            client_id=client_id,
        )
    if outcomes:
        logger.info(
            "sync_target_push_start",
            label=target.label,
            kind="outcomes",
            client_id=client_id,
        )
        outcome_result = await pusher.push_outcomes(outcomes)
        total = PushResult(
            pushed=total.pushed + outcome_result.pushed,
            failed=total.failed + outcome_result.failed,
            skipped=total.skipped + outcome_result.skipped,
        )
        logger.info(
            "sync_target_push_complete",
            label=target.label,
            kind="outcomes",
            pushed=outcome_result.pushed,
            skipped=outcome_result.skipped,
            failed=outcome_result.failed,
            duration_ms=int((perf_counter() - started) * 1000),
            client_id=client_id,
        )
    return total


async def fanout_push(
    *,
    client_id: str,
    targets: list[_TargetLike],
    primary_pusher: SyncPusher,
    pusher_map: dict[str, SyncPusher],
    batch_size: int,
    timeout: float,
    dirty: list[MemoryEntry],
    outcomes: list[dict[str, object]],
) -> tuple[dict[str, dict[str, object]], PushResult, bool]:
    """PRD-FIX-087 FR03: async — awaits _push_to_target per target."""
    report: dict[str, dict[str, object]] = {}
    aggregate: PushResult = PushResult()
    any_success = False
    primary_target_label = targets[0].label if targets else None
    for target in targets:
        try:
            result = await _push_to_target(
                client_id=client_id,
                target=target,
                primary_target_label=primary_target_label,
                primary_pusher=primary_pusher,
                pusher_map=pusher_map,
                batch_size=batch_size,
                timeout=timeout,
                dirty=dirty,
                outcomes=outcomes,
            )
        except Exception as exc:  # justified: boundary, per-target failure is isolated
            logger.warning(
                "sync_target_failed",
                client_id=client_id,
                label=target.label,
                target=target.label,
                error_type=type(exc).__name__,
                error=str(exc)[:200],
                exc_info=True,
            )
            report[target.label] = {
                "pushed": 0,
                "skipped": 0,
                "failed": 1,
                "error": f"{type(exc).__name__}: {str(exc)[:200]}",
            }
            continue
        report[target.label] = {
            "pushed": result.pushed,
            "skipped": result.skipped,
            "failed": result.failed,
            "error": None,
        }
        if result.failed == 0:
            any_success = True
            if aggregate.pushed == 0 and aggregate.skipped == 0:
                aggregate = result
    return report, aggregate, any_success
