"""BackendSyncClient — top-level orchestrator for bidirectional sync."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog

from trw_mcp.sync.cache import IntelligenceCache
from trw_mcp.sync.coordinator import SyncCoordinator
from trw_mcp.sync.identity import resolve_sync_client_id
from trw_mcp.sync.outcomes import load_pending_outcomes
from trw_mcp.sync.pull import SyncPuller
from trw_mcp.sync.push import PushResult, SyncPusher

if TYPE_CHECKING:
    from trw_memory.models.memory import MemoryEntry

    from trw_mcp.models.config._main import TRWConfig

logger = structlog.get_logger(__name__)

_MIN_HINT_DELAY_SECONDS = 60
_MAX_HINT_DELAY_SECONDS = 7200
_MAX_CONSECUTIVE_IMMEDIATE_REPOLLS = 1


class BackendSyncClient:
    """Orchestrates bidirectional push+pull sync."""

    def __init__(self, config: TRWConfig, trw_dir: Path) -> None:
        self._config = config
        self._trw_dir = trw_dir
        self._client_id = resolve_sync_client_id()
        self._coordinator = SyncCoordinator(
            trw_dir=trw_dir,
            sync_interval=config.sync_interval_seconds,
        )
        self._pusher = SyncPusher(
            backend_url=config.backend_url,
            api_key=config.backend_api_key,
            batch_size=config.sync_push_batch_size,
            timeout=config.sync_push_timeout_seconds,
            client_id=self._client_id,
        )
        self._puller = SyncPuller(
            backend_url=config.backend_url,
            api_key=config.backend_api_key,
            timeout=getattr(config, "sync_pull_timeout_seconds", 5.0),
            client_id=self._client_id,
            trw_dir=trw_dir,
        )
        self._cache = IntelligenceCache(
            trw_dir=trw_dir,
            ttl_seconds=getattr(config, "intel_cache_ttl_seconds", 3600),
        )
        self._next_sleep_seconds = float(config.sync_interval_seconds)
        self._scheduled_interval_seconds = float(config.sync_interval_seconds)
        self._last_applied_schedule_seconds = float(config.sync_interval_seconds)
        self._next_cycle_force = False
        self._consecutive_immediate_repolls = 0

    async def run_sync_loop(self) -> None:
        """Main loop: sleep -> check -> lock -> push -> pull -> release."""
        logger.info(
            "sync_loop_started",
            interval=self._config.sync_interval_seconds,
            client_id=self._client_id,
        )
        while True:
            try:
                await asyncio.sleep(self._next_sleep_seconds)
                force = self._consume_next_cycle_force()
                await self._run_one_cycle(force=force)
            except asyncio.CancelledError:  # noqa: PERF203
                logger.info("sync_loop_cancelled", client_id=self._client_id)
                break
            except Exception:  # justified: fail-open, background sync loop errors must not crash the daemon
                self._reset_poll_schedule()
                logger.warning("sync_loop_error", client_id=self._client_id, exc_info=True)

    async def trigger_sync(self) -> None:
        """Force an immediate sync cycle (e.g., on deliver)."""
        try:
            await self._run_one_cycle(force=True)
        except Exception:  # justified: fail-open, manual sync trigger errors must not break caller workflows
            logger.warning("sync_trigger_error", client_id=self._client_id, exc_info=True)

    async def _run_one_cycle(self, force: bool = False) -> None:
        """Execute one push+pull sync cycle."""
        if not self._config.backend_url:
            return

        if not force and not self._coordinator.should_sync(sync_interval=self._scheduled_interval_seconds):
            logger.debug("sync_cycle_skipped", reason="too_recent", client_id=self._client_id)
            return

        with self._coordinator.acquire_sync_lock() as acquired:
            if not acquired:
                return

            dirty = self._get_dirty_entries()
            push_result = PushResult()
            push_seq = 0
            push_failed = False
            if dirty:
                logger.info("sync_push_started", dirty_count=len(dirty), client_id=self._client_id)
                push_result = self._pusher.push_learnings(dirty)
                if push_result.failed == 0:
                    push_seq = max((entry.sync_seq for entry in dirty), default=0)
                    self._mark_synced(dirty[:push_result.pushed + push_result.skipped])
                else:
                    push_failed = True
                    self._coordinator.record_sync_failure(f"push failed: {push_result.failed} entries")
            else:
                logger.debug("sync_push_skipped", reason="no_dirty_entries", client_id=self._client_id)

            if not push_failed:
                pending_outcomes = load_pending_outcomes(
                    self._trw_dir,
                    since_line=self._coordinator.get_last_outcome_line(),
                )
                if pending_outcomes:
                    outcome_result = self._pusher.push_outcomes([item.payload for item in pending_outcomes])
                    if outcome_result.failed == 0:
                        self._coordinator.record_outcome_push_success(
                            max(item.line_no for item in pending_outcomes)
                        )
                    else:
                        push_failed = True
                        self._coordinator.record_sync_failure(
                            f"outcome push failed: {outcome_result.failed} events"
                        )

            pulled = 0
            merged = 0
            pull_seq = self._coordinator.get_last_pull_seq()
            pull_result = self._puller.pull_intel_state(
                etag=self._cache.etag if self._config.intel_cache_enabled else None,
                since_seq=pull_seq,
                model_family=getattr(self._config, "model_family", ""),
                trw_version=getattr(self._config, "framework_version", ""),
                client_id=self._client_id,
            )
            if pull_result is None:
                self._reset_poll_schedule()
                self._coordinator.record_sync_failure("pull failed")
                return

            if pull_result.not_modified:
                self._restore_poll_schedule()
                if push_failed:
                    self._coordinator.record_pull_success(pull_seq=pull_seq)
                else:
                    self._coordinator.record_sync_success(
                        pushed=push_result.pushed,
                        pulled=0,
                        push_seq=push_seq,
                        pull_seq=pull_seq,
                        pull_completed=True,
                    )
                return

            if self._config.intel_cache_enabled and pull_result.state is not None:
                self._cache.update(pull_result.state, etag=pull_result.etag)
            team_learning_count = len(pull_result.team_learnings or [])
            if self._config.team_sync_enabled:
                merged = self._puller.merge_team_learnings(pull_result.team_learnings)
            pulled = team_learning_count

            next_pull_seq = max([
                pull_seq,
                *[
                    int(item.get("sync_seq", 0))
                    for item in (pull_result.team_learnings or [])
                    if isinstance(item, dict)
                ],
            ])
            self._apply_sync_hints(pull_result.sync_hints)
            logger.info(
                "sync_cycle_completed",
                client_id=self._client_id,
                pushed=push_result.pushed,
                pull_seq=next_pull_seq,
                pulled=pulled,
                merged=merged,
                next_delay_seconds=self._next_sleep_seconds,
                immediate_repoll=self._next_cycle_force,
            )
            if push_failed:
                self._coordinator.record_pull_success(pull_seq=next_pull_seq)
            else:
                self._coordinator.record_sync_success(
                    pushed=push_result.pushed,
                    pulled=pulled,
                    push_seq=push_seq,
                    pull_seq=next_pull_seq,
                    pull_completed=True,
                )

    def _apply_sync_hints(self, sync_hints: dict[str, Any] | None) -> None:
        """Update the next poll schedule from backend hints."""
        polling_cap_seconds = self._coerce_positive_number(
            (sync_hints or {}).get("polling_cap_seconds"),
        )
        interval_seconds = self._coerce_positive_number((sync_hints or {}).get("interval_seconds"))
        delay = interval_seconds
        if delay is None:
            delay = float(self._config.sync_interval_seconds)
        recommended_at = (sync_hints or {}).get("next_poll_recommended_at")
        parsed_recommended_at = self._parse_sync_hint_timestamp(recommended_at)
        if interval_seconds is None and parsed_recommended_at is not None:
            delay = max(0.0, (parsed_recommended_at - datetime.now(tz=timezone.utc)).total_seconds())

        if polling_cap_seconds is not None and delay > 0:
            delay = max(delay, polling_cap_seconds)
        if delay > 0:
            delay = min(max(delay, _MIN_HINT_DELAY_SECONDS), _MAX_HINT_DELAY_SECONDS)

        if sync_hints and sync_hints.get("significant_updates_available") and self._consecutive_immediate_repolls < _MAX_CONSECUTIVE_IMMEDIATE_REPOLLS:
            self._last_applied_schedule_seconds = delay
            self._next_sleep_seconds = 0.0
            self._scheduled_interval_seconds = 0.0
            self._next_cycle_force = True
            self._consecutive_immediate_repolls += 1
            logger.info(
                "sync_hint_applied",
                client_id=self._client_id,
                mode="immediate_repoll",
                polling_cap_seconds=polling_cap_seconds,
            )
            return

        self._last_applied_schedule_seconds = delay
        self._next_sleep_seconds = delay
        self._scheduled_interval_seconds = delay
        self._next_cycle_force = False
        self._consecutive_immediate_repolls = 0
        logger.info(
            "sync_hint_applied",
            client_id=self._client_id,
            mode="scheduled",
            next_delay_seconds=delay,
            polling_cap_seconds=polling_cap_seconds,
        )

    def _reset_poll_schedule(self) -> None:
        self._next_sleep_seconds = float(self._config.sync_interval_seconds)
        self._scheduled_interval_seconds = float(self._config.sync_interval_seconds)
        self._last_applied_schedule_seconds = float(self._config.sync_interval_seconds)
        self._next_cycle_force = False
        self._consecutive_immediate_repolls = 0

    def _restore_poll_schedule(self) -> None:
        self._next_sleep_seconds = self._last_applied_schedule_seconds
        self._scheduled_interval_seconds = self._last_applied_schedule_seconds
        self._next_cycle_force = False
        self._consecutive_immediate_repolls = 0

    def _consume_next_cycle_force(self) -> bool:
        force = self._next_cycle_force
        self._next_cycle_force = False
        return force

    @staticmethod
    def _parse_sync_hint_timestamp(raw: object) -> datetime | None:
        if not isinstance(raw, str) or not raw.strip():
            return None
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(timezone.utc)
        except ValueError:
            return None

    @staticmethod
    def _coerce_positive_number(raw: object) -> float | None:
        if not isinstance(raw, (int, float)):
            return None
        value = float(raw)
        return value if value > 0 else None

    def _get_dirty_entries(self) -> list[MemoryEntry]:
        """Get dirty entries from local storage via DeltaTracker."""
        try:
            from trw_memory.sync.delta import DeltaTracker

            from trw_mcp.state._memory_connection import get_backend as _get_backend

            backend = _get_backend()
            return DeltaTracker.get_dirty_entries(backend, since_seq=0)
        except Exception:  # justified: fail-open, dirty-entry discovery falls back to no-op sync
            logger.debug("sync_get_dirty_failed", client_id=self._client_id, exc_info=True)
            return []

    def _mark_synced(self, entries: list[MemoryEntry]) -> None:
        """Mark entries as synced in local storage."""
        try:
            from trw_memory.sync.delta import DeltaTracker

            from trw_mcp.state._memory_connection import get_backend as _get_backend

            backend = _get_backend()
            DeltaTracker.mark_synced([e.id for e in entries if hasattr(e, "id")], backend)
        except Exception:  # justified: fail-open, sync bookkeeping must not break successful pushes
            logger.debug("sync_mark_synced_failed", client_id=self._client_id, exc_info=True)
