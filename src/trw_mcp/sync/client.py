"""BackendSyncClient — top-level orchestrator for bidirectional sync.

Targets are derived from :attr:`TRWConfig.resolved_sync_targets`, which fans
out across every configured ``platform_urls`` entry. The legacy accessors
:attr:`TRWConfig.resolved_backend_url` and :attr:`TRWConfig.resolved_backend_api_key`
remain supported and return the first target for backward compatibility.
"""
# ruff: noqa: I001 - facade imports stay grouped for re-export seams and LOC ratchet.

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from time import perf_counter as perf_counter
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

import structlog

from trw_mcp.sync._client_cycle import (
    _is_company_entry as _is_company_entry,
    _offload_sync_work as _offload_sync_work,
    _target_report_status as _target_report_status,
    _write_synced_markers as _write_synced_markers,
    run_one_cycle as _run_one_cycle_impl,
)
from trw_mcp.sync._client_push import (
    _push_to_target as _push_to_target_impl,
    fanout_push as _fanout_push_impl,
)
from trw_mcp.sync._client_runtime import (
    apply_sync_hints as _apply_sync_hints_impl,
    coerce_positive_number as _coerce_positive_number_impl,
    consume_next_cycle_force as _consume_next_cycle_force_impl,
    get_dirty_entries as _get_dirty_entries_impl,
    mark_synced as _mark_synced_impl,
    parse_sync_hint_timestamp as _parse_sync_hint_timestamp_impl,
    reset_poll_schedule as _reset_poll_schedule_impl,
    restore_poll_schedule as _restore_poll_schedule_impl,
)
from trw_mcp.sync.cache import IntelligenceCache
from trw_mcp.sync.coordinator import SyncCoordinator
from trw_mcp.sync.identity import resolve_sync_client_id
from trw_mcp.sync.outcomes import load_pending_outcomes as load_pending_outcomes
from trw_mcp.sync.pull import SyncPuller
from trw_mcp.sync.push import PushResult, SyncPusher

if TYPE_CHECKING:
    from trw_memory.models.memory import MemoryEntry

    from trw_mcp.models.config._main import TRWConfig

logger = structlog.get_logger(__name__)

_SLOW_LOCAL_WORK_LOG_MS = 1_000.0
_PATHOLOGICAL_LOCAL_WORK_MS = 10_000.0
_SYNC_FAILURE_BACKOFF_CAP_SECONDS = 3_600.0


def _failure_backoff_delay_seconds(base_interval_seconds: float, consecutive_failures: int) -> float:
    """Compute bounded exponential backoff for background sync failure churn."""

    base_interval = max(1.0, float(base_interval_seconds))
    failure_count = max(1, int(consecutive_failures))
    multiplier = 2 ** min(failure_count, 4)
    cap = max(base_interval, _SYNC_FAILURE_BACKOFF_CAP_SECONDS)
    return float(min(base_interval * multiplier, cap))


@dataclass(frozen=True)
class SyncTarget:
    """A single (url, api_key, label) fan-out destination."""

    url: str
    api_key: str
    label: str


def _label_for_url(url: str) -> str:
    """Derive a hostname-style label for logs."""
    parsed = urlparse(url if "://" in url else f"http://{url}")
    host = parsed.hostname or url
    return host


def _build_targets(config: TRWConfig) -> list[SyncTarget]:
    """Build the ordered fan-out list from config.

    Uses :attr:`TRWConfig.resolved_sync_targets`. For legacy callers that
    stub config without the new accessor, falls back to a single-target list
    built from ``resolved_backend_url`` / ``resolved_backend_api_key``.
    """
    raw = getattr(config, "resolved_sync_targets", None)
    if raw is None:
        url = getattr(config, "resolved_backend_url", "") or ""
        key = getattr(config, "resolved_backend_api_key", "") or ""
        raw = [(url, key)] if url and key else []
    return [SyncTarget(url=u, api_key=k, label=_label_for_url(u)) for u, k in raw]


class BackendSyncClient:
    """Orchestrates bidirectional push+pull sync across all configured targets.

    Reads :attr:`TRWConfig.resolved_sync_targets` for fan-out. The legacy
    :attr:`TRWConfig.resolved_backend_url` and
    :attr:`TRWConfig.resolved_backend_api_key` accessors still resolve to the
    first target for backward compatibility with single-target consumers.
    """

    def __init__(self, config: TRWConfig, trw_dir: Path) -> None:
        self._config = config
        self._trw_dir = trw_dir
        self._client_id = resolve_sync_client_id()
        self._coordinator = SyncCoordinator(
            trw_dir=trw_dir,
            sync_interval=config.sync_interval_seconds,
        )
        self._targets: list[SyncTarget] = _build_targets(config)
        # PRD-SEC-004-FR05/FR01: resolve the two independent consent flags once.
        # learning_sharing_enabled (default False) gates learning CONTENT push;
        # platform_telemetry_enabled (default False) gates session-outcome push.
        # getattr-with-default keeps legacy/stub configs working AND is
        # fail-closed for egress when a flag is absent.
        self._learning_sharing_enabled = bool(getattr(config, "learning_sharing_enabled", False))
        self._platform_telemetry_enabled = bool(getattr(config, "platform_telemetry_enabled", False))
        logger.info(
            "sync_targets_resolved",
            count=len(self._targets),
            targets=[t.label for t in self._targets],
            client_id=self._client_id,
            learning_sharing_enabled=self._learning_sharing_enabled,
            platform_telemetry_enabled=self._platform_telemetry_enabled,
        )
        # Pushers and pullers are built per-target; cache a by-label map for reuse.
        self._pushers: dict[str, SyncPusher] = {
            t.label: SyncPusher(
                backend_url=t.url,
                api_key=t.api_key,
                batch_size=config.sync_push_batch_size,
                timeout=config.sync_push_timeout_seconds,
                client_id=self._client_id,
                learning_sharing_enabled=self._learning_sharing_enabled,
                platform_telemetry_enabled=self._platform_telemetry_enabled,
            )
            for t in self._targets
        }
        # Primary puller uses the first target (intel pull is single-source for now).
        primary_url = self._targets[0].url if self._targets else ""
        primary_key = self._targets[0].api_key if self._targets else ""
        self._pusher: SyncPusher = (
            self._pushers[self._targets[0].label]
            if self._targets
            else SyncPusher(
                backend_url=primary_url,
                api_key=primary_key,
                batch_size=config.sync_push_batch_size,
                timeout=config.sync_push_timeout_seconds,
                client_id=self._client_id,
                learning_sharing_enabled=self._learning_sharing_enabled,
                platform_telemetry_enabled=self._platform_telemetry_enabled,
            )
        )
        self._puller = SyncPuller(
            backend_url=primary_url,
            api_key=primary_key,
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
            except asyncio.CancelledError:
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
        """Execute one push+pull sync cycle, fanning out pushes to every target."""
        await _run_one_cycle_impl(self, force=force)

    async def _fanout_push(
        self,
        dirty: list[MemoryEntry],
        outcomes: list[dict[str, object]],
    ) -> tuple[dict[str, dict[str, object]], PushResult]:
        """PRD-FIX-087 FR03: async — awaits the package-level fanout_push helper."""
        return await _fanout_push_impl(
            client_id=self._client_id,
            targets=self._targets,
            primary_pusher=self._pusher,
            pusher_map=self._pushers,
            batch_size=self._config.sync_push_batch_size,
            timeout=self._config.sync_push_timeout_seconds,
            dirty=dirty,
            outcomes=outcomes,
            learning_sharing_enabled=self._learning_sharing_enabled,
            platform_telemetry_enabled=self._platform_telemetry_enabled,
        )

    async def _push_to_target(
        self,
        target: SyncTarget,
        dirty: list[MemoryEntry],
        outcomes: list[dict[str, object]],
    ) -> PushResult:
        """PRD-FIX-087 FR03: async — awaits the package-level _push_to_target helper."""
        return await _push_to_target_impl(
            client_id=self._client_id,
            target=target,
            primary_target_label=self._targets[0].label if self._targets else None,
            primary_pusher=self._pusher,
            pusher_map=self._pushers,
            batch_size=self._config.sync_push_batch_size,
            timeout=self._config.sync_push_timeout_seconds,
            dirty=dirty,
            outcomes=outcomes,
            learning_sharing_enabled=self._learning_sharing_enabled,
            platform_telemetry_enabled=self._platform_telemetry_enabled,
        )

    def _apply_sync_hints(self, sync_hints: dict[str, Any] | None) -> None:
        (
            self._next_sleep_seconds,
            self._scheduled_interval_seconds,
            self._last_applied_schedule_seconds,
            self._next_cycle_force,
            self._consecutive_immediate_repolls,
        ) = _apply_sync_hints_impl(
            client_id=self._client_id,
            config_sync_interval_seconds=float(self._config.sync_interval_seconds),
            sync_hints=sync_hints,
            last_applied_schedule_seconds=self._last_applied_schedule_seconds,
            consecutive_immediate_repolls=self._consecutive_immediate_repolls,
        )

    def _reset_poll_schedule(self) -> None:
        (
            self._next_sleep_seconds,
            self._scheduled_interval_seconds,
            self._last_applied_schedule_seconds,
            self._next_cycle_force,
            self._consecutive_immediate_repolls,
        ) = _reset_poll_schedule_impl(float(self._config.sync_interval_seconds))

    def _restore_poll_schedule(self) -> None:
        (
            self._next_sleep_seconds,
            self._scheduled_interval_seconds,
            self._last_applied_schedule_seconds,
            self._next_cycle_force,
            self._consecutive_immediate_repolls,
        ) = _restore_poll_schedule_impl(self._last_applied_schedule_seconds)

    def _apply_failure_backoff(self, *, reason: str) -> None:
        """Slow future background sync cycles after repeated remote failures."""

        raw_failures = self._coordinator.get_consecutive_failures()
        consecutive_failures = int(raw_failures) if isinstance(raw_failures, (int, float)) else 1
        delay_seconds = _failure_backoff_delay_seconds(
            float(self._config.sync_interval_seconds),
            consecutive_failures,
        )
        self._next_sleep_seconds = delay_seconds
        self._scheduled_interval_seconds = delay_seconds
        self._last_applied_schedule_seconds = delay_seconds
        self._next_cycle_force = False
        self._consecutive_immediate_repolls = 0
        logger.warning(
            "sync_failure_backoff_applied",
            client_id=self._client_id,
            reason=reason,
            consecutive_failures=consecutive_failures,
            next_delay_seconds=delay_seconds,
        )

    def _consume_next_cycle_force(self) -> bool:
        force, self._next_cycle_force = _consume_next_cycle_force_impl(self._next_cycle_force)
        return force

    @staticmethod
    def _parse_sync_hint_timestamp(raw: object) -> datetime | None:
        return _parse_sync_hint_timestamp_impl(raw)

    @staticmethod
    def _coerce_positive_number(raw: object) -> float | None:
        return _coerce_positive_number_impl(raw)

    def _get_dirty_entries(self) -> list[MemoryEntry]:
        return _get_dirty_entries_impl(client_id=self._client_id)

    def _mark_synced(self, entries: list[MemoryEntry]) -> None:
        """Mark entries as synced in local storage."""
        _mark_synced_impl(client_id=self._client_id, entries=entries)
