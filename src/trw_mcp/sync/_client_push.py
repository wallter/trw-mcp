"""Internal helpers for BackendSyncClient fan-out push handling."""

from __future__ import annotations

from collections.abc import Sequence
from time import perf_counter
from typing import TYPE_CHECKING, Protocol

import structlog
from pydantic import BaseModel, Field

from trw_mcp.sync.push import PushResult, SyncPusher

logger = structlog.get_logger(__name__)

if TYPE_CHECKING:
    from trw_memory.models.memory import MemoryEntry

_TARGET_STATUS_SUCCESS = "success"
_TARGET_STATUS_PARTIAL_ERROR = "partial_error"
_TARGET_STATUS_ERROR = "error"


class TargetPushOutcome(BaseModel):
    """One target's push result, kept SPLIT BY KIND.

    PRD-FIX-125-FR01: each acknowledgement path slices its OWN list by its OWN
    count, so a single summed :class:`PushResult` is not enough — it cannot say
    how many LEARNINGS versus how many OUTCOMES a target accepted. Summing them
    made ``dirty[: pushed + skipped]`` count outcome inserts toward the learning
    slice, acknowledging learnings the primary never took whenever both kinds
    were pushed in the same cycle.

    The two endpoints also report differently, which is why they cannot share a
    count: ``push_learnings`` reads ``skipped`` back from the response, so
    ``pushed + skipped`` is a real accepted-count. ``push_outcomes`` has no
    ``skipped`` concept and counts only ``inserted``, so a de-duplicated outcome
    the backend already holds reports as neither pushed, skipped, nor failed.
    See :func:`trw_mcp.sync._client_cycle.run_one_cycle` for how each path
    consumes its own half.

    The summed ``pushed`` / ``failed`` / ``skipped`` accessors are retained so a
    caller that only wants the target's overall verdict reads it directly.
    """

    learnings: PushResult = Field(default_factory=PushResult)
    outcomes: PushResult = Field(default_factory=PushResult)

    @property
    def pushed(self) -> int:
        """Total accepted-and-inserted across both kinds."""
        return self.learnings.pushed + self.outcomes.pushed

    @property
    def failed(self) -> int:
        """Total failures across both kinds — any non-zero makes the target unhealthy."""
        return self.learnings.failed + self.outcomes.failed

    @property
    def skipped(self) -> int:
        """Total de-duplicated learnings (the outcomes endpoint reports no skips)."""
        return self.learnings.skipped + self.outcomes.skipped


class _TargetLike(Protocol):
    @property
    def url(self) -> str: ...

    @property
    def api_key(self) -> str: ...

    @property
    def label(self) -> str: ...


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
    learning_sharing_enabled: bool = False,
    platform_telemetry_enabled: bool = False,
) -> TargetPushOutcome:
    """PRD-FIX-087 FR03: async — awaits pusher.push_learnings / push_outcomes.

    Returns the two kinds SEPARATELY (PRD-FIX-125-FR01); see
    :class:`TargetPushOutcome` for why they must not be summed before the
    acknowledgement paths have taken their slices.
    """
    started = perf_counter()
    pusher: SyncPusher | None
    if primary_target_label is not None and target.label == primary_target_label:
        pusher = primary_pusher
    else:
        pusher = pusher_map.get(target.label)
    if pusher is None:
        # PRD-SEC-004-FR05/FR01: a lazily-built fallback pusher MUST inherit the
        # resolved consent flags — otherwise a target not pre-built in
        # BackendSyncClient.__init__ would default fail-closed and silently drop
        # a consented push.
        pusher = SyncPusher(
            backend_url=target.url,
            api_key=target.api_key,
            batch_size=batch_size,
            timeout=timeout,
            client_id=client_id,
            learning_sharing_enabled=learning_sharing_enabled,
            platform_telemetry_enabled=platform_telemetry_enabled,
        )
        pusher_map[target.label] = pusher

    learning_result = PushResult()
    outcome_result = PushResult()
    if dirty:
        logger.info(
            "sync_target_push_start",
            label=target.label,
            kind="learnings",
            client_id=client_id,
        )
        learning_result = await pusher.push_learnings(dirty)
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
    return TargetPushOutcome(learnings=learning_result, outcomes=outcome_result)


async def fanout_push(
    *,
    client_id: str,
    targets: Sequence[_TargetLike],
    primary_pusher: SyncPusher,
    pusher_map: dict[str, SyncPusher],
    batch_size: int,
    timeout: float,
    dirty: list[MemoryEntry],
    outcomes: list[dict[str, object]],
    learning_sharing_enabled: bool = False,
    platform_telemetry_enabled: bool = False,
) -> tuple[dict[str, dict[str, object]], TargetPushOutcome]:
    """PRD-FIX-087 FR03: async — awaits _push_to_target per target.

    Returns ``(per-target report, the PRIMARY target's split push result)``.

    PRD-FIX-125-FR01: the returned aggregate is the primary's own result — a
    zero-valued :class:`TargetPushOutcome` when the primary raised or is absent. It was
    previously the *first successful* target's result, so a failing primary with
    a succeeding secondary handed the caller the secondary's counts, which is the
    wrong basis for slicing ``dirty`` in the acknowledgement path: the caller
    would have marked entries synced that the primary never accepted.
    """
    report: dict[str, dict[str, object]] = {}
    primary_result = TargetPushOutcome()
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
                learning_sharing_enabled=learning_sharing_enabled,
                platform_telemetry_enabled=platform_telemetry_enabled,
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
                "status": _TARGET_STATUS_ERROR,
            }
            continue
        status = _TARGET_STATUS_PARTIAL_ERROR if result.failed > 0 else _TARGET_STATUS_SUCCESS
        report[target.label] = {
            "pushed": result.pushed,
            "skipped": result.skipped,
            "failed": result.failed,
            "error": None,
            "status": status,
        }
        if target.label == primary_target_label and status == _TARGET_STATUS_SUCCESS:
            primary_result = result
    return report, primary_result
