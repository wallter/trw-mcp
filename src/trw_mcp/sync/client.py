"""BackendSyncClient — top-level orchestrator for background sync — PRD-INFRA-051-FR13.

Composes SyncCoordinator + SyncPusher. Runs as an asyncio task in the
MCP server lifespan. The sync loop catches all exceptions at the top level
to never crash the MCP server.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog

from trw_mcp.sync.coordinator import SyncCoordinator
from trw_mcp.sync.push import SyncPusher

if TYPE_CHECKING:
    from trw_mcp.models.config._main import TRWConfig

logger = structlog.get_logger(__name__)


class BackendSyncClient:
    """Orchestrates background push sync."""

    def __init__(self, config: TRWConfig, trw_dir: Path) -> None:
        self._config = config
        self._trw_dir = trw_dir
        self._coordinator = SyncCoordinator(
            trw_dir=trw_dir,
            sync_interval=config.sync_interval_seconds,
        )
        self._pusher = SyncPusher(
            backend_url=config.backend_url,
            api_key=config.backend_api_key,
            batch_size=config.sync_push_batch_size,
            timeout=config.sync_push_timeout_seconds,
        )

    async def run_sync_loop(self) -> None:
        """Main loop: sleep -> check -> lock -> push -> release."""
        logger.info("sync_loop_started", interval=self._config.sync_interval_seconds)
        while True:
            try:
                await asyncio.sleep(self._config.sync_interval_seconds)
                await self._run_one_cycle()
            except asyncio.CancelledError:
                logger.info("sync_loop_cancelled")
                break
            except Exception:
                logger.warning("sync_loop_error", exc_info=True)

    async def trigger_sync(self) -> None:
        """Force an immediate sync cycle (e.g., on deliver)."""
        try:
            await self._run_one_cycle(force=True)
        except Exception:
            logger.warning("sync_trigger_error", exc_info=True)

    async def _run_one_cycle(self, force: bool = False) -> None:
        """Execute one push sync cycle."""
        if not self._config.backend_url:
            return

        if not force and not self._coordinator.should_sync():
            logger.debug("sync_cycle_skipped", reason="too recent")
            return

        with self._coordinator.acquire_sync_lock() as acquired:
            if not acquired:
                return

            # Get dirty entries via DeltaTracker
            dirty = self._get_dirty_entries()
            if not dirty:
                self._coordinator.record_sync_success(pushed=0, pulled=0)
                return

            logger.info("sync_push_started", dirty_count=len(dirty))
            result = self._pusher.push_learnings(dirty)

            if result.failed == 0:
                # Mark successfully pushed entries as synced
                self._mark_synced(dirty[:result.pushed + result.skipped])
                self._coordinator.record_sync_success(pushed=result.pushed, pulled=0)
            else:
                self._coordinator.record_sync_failure(f"push failed: {result.failed} entries")

    def _get_dirty_entries(self) -> list[Any]:
        """Get dirty entries from local storage via DeltaTracker."""
        try:
            from trw_memory.sync.delta import DeltaTracker
            from trw_mcp.state._memory_connection import get_backend as _get_backend

            backend = _get_backend()
            last_seq = self._coordinator.get_last_push_seq()
            return DeltaTracker.get_dirty_entries(backend, since_seq=last_seq)
        except Exception:
            logger.debug("sync_get_dirty_failed", exc_info=True)
            return []

    def _mark_synced(self, entries: list[Any]) -> None:
        """Mark entries as synced in local storage."""
        try:
            from trw_memory.sync.delta import DeltaTracker
            from trw_mcp.state._memory_connection import get_backend as _get_backend

            backend = _get_backend()
            DeltaTracker.mark_synced([e.id for e in entries if hasattr(e, "id")], backend)
        except Exception:
            logger.debug("sync_mark_synced_failed", exc_info=True)
