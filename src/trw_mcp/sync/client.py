"""BackendSyncClient — top-level orchestrator for bidirectional sync.

Targets are derived from :attr:`TRWConfig.resolved_sync_targets`, which fans
out across every configured ``platform_urls`` entry. The legacy accessors
:attr:`TRWConfig.resolved_backend_url` and :attr:`TRWConfig.resolved_backend_api_key`
remain supported and return the first target for backward compatibility.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

import structlog

from trw_mcp.sync.cache import IntelligenceCache
from trw_mcp.sync.coordinator import SyncCoordinator
from trw_mcp.sync.identity import resolve_sync_client_id
from trw_mcp.sync.outcomes import load_pending_outcomes, write_synced_marker
from trw_mcp.sync.pull import SyncPuller
from trw_mcp.sync.push import PushResult, SyncPusher

if TYPE_CHECKING:
    from trw_memory.models.memory import MemoryEntry

    from trw_mcp.models.config._main import TRWConfig

logger = structlog.get_logger(__name__)

_MIN_HINT_DELAY_SECONDS = 60
_MAX_HINT_DELAY_SECONDS = 7200
_MAX_CONSECUTIVE_IMMEDIATE_REPOLLS = 1


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
        logger.info(
            "sync_targets_resolved",
            count=len(self._targets),
            targets=[t.label for t in self._targets],
            client_id=self._client_id,
        )
        # Pushers and pullers are built per-target; cache a by-label map for reuse.
        self._pushers: dict[str, SyncPusher] = {
            t.label: SyncPusher(
                backend_url=t.url,
                api_key=t.api_key,
                batch_size=config.sync_push_batch_size,
                timeout=config.sync_push_timeout_seconds,
                client_id=self._client_id,
            )
            for t in self._targets
        }
        # Primary puller uses the first target (intel pull is single-source for now).
        primary_url = self._targets[0].url if self._targets else ""
        primary_key = self._targets[0].api_key if self._targets else ""
        self._pusher = self._pushers.get(self._targets[0].label) if self._targets else SyncPusher(
            backend_url=primary_url,
            api_key=primary_key,
            batch_size=config.sync_push_batch_size,
            timeout=config.sync_push_timeout_seconds,
            client_id=self._client_id,
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
        """Execute one push+pull sync cycle, fanning out pushes to every target."""
        if not self._targets:
            logger.debug(
                "sync_cycle_skipped",
                reason="no_targets",
                client_id=self._client_id,
            )
            return

        if not force and not self._coordinator.should_sync(sync_interval=self._scheduled_interval_seconds):
            logger.debug("sync_cycle_skipped", reason="too_recent", client_id=self._client_id)
            return

        with self._coordinator.acquire_sync_lock() as acquired:
            if not acquired:
                return

            dirty = self._get_dirty_entries()
            if dirty:
                logger.info("sync_push_started", dirty_count=len(dirty), client_id=self._client_id)
            else:
                logger.debug("sync_push_skipped", reason="no_dirty_entries", client_id=self._client_id)

            pending_outcomes = load_pending_outcomes(
                self._trw_dir,
                since_line=self._coordinator.get_last_outcome_line(),
            )

            report, push_result, any_target_succeeded = self._fanout_push(
                dirty=dirty,
                outcomes=[item.payload for item in pending_outcomes],
            )
            logger.info(
                "sync_cycle_report",
                client_id=self._client_id,
                targets=len(self._targets),
                successful=sum(1 for r in report.values() if r["error"] is None),
                failed=sum(1 for r in report.values() if r["error"] is not None),
                report=report,
            )

            push_failed = not any_target_succeeded and (bool(dirty) or bool(pending_outcomes))
            push_seq = 0
            if dirty and any_target_succeeded and push_result.failed == 0:
                push_seq = max((entry.sync_seq for entry in dirty), default=0)
                self._mark_synced(dirty[: push_result.pushed + push_result.skipped])
            if push_failed:
                # Preserve legacy single-target failure message format when possible.
                if dirty and len(self._targets) == 1:
                    primary_label = self._targets[0].label
                    raw_failed = report.get(primary_label, {}).get("failed", len(dirty))
                    failed_count = int(raw_failed) if isinstance(raw_failed, (int, float)) else len(dirty)
                    if failed_count == 0:
                        failed_count = len(dirty)
                    # Legacy behaviour reported the backend-side failure count as entry count.
                    self._coordinator.record_sync_failure(f"push failed: {failed_count} entries")
                else:
                    self._coordinator.record_sync_failure(
                        f"all {len(self._targets)} targets failed"
                    )
            if pending_outcomes and any_target_succeeded:
                self._coordinator.record_outcome_push_success(max(item.line_no for item in pending_outcomes))
                # PRD-CORE-144 FR05: stamp sibling synced.json markers so the
                # next pusher pass skips already-synced runs.
                successful_labels = ",".join(
                    lbl for lbl, rep in report.items() if rep.get("error") is None
                )
                for item in pending_outcomes:
                    if item.run_dir is None or not item.sync_hash:
                        continue
                    write_synced_marker(
                        item.run_dir,
                        run_id=item.run_id,
                        sync_hash=item.sync_hash,
                        target_label=successful_labels or "unknown",
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

            next_pull_seq = max(
                [
                    pull_seq,
                    *[
                        int(item.get("sync_seq", 0))
                        for item in (pull_result.team_learnings or [])
                        if isinstance(item, dict)
                    ],
                ]
            )
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

    def _fanout_push(
        self,
        dirty: "list[MemoryEntry]",
        outcomes: list[dict[str, object]],
    ) -> tuple[dict[str, dict[str, object]], PushResult, bool]:
        """Push dirty entries + outcomes to every target with per-target isolation.

        Returns (report, aggregate_push_result, any_target_succeeded). Aggregate
        result reflects the first successful target (sufficient for push_seq).
        """
        report: dict[str, dict[str, object]] = {}
        aggregate: PushResult = PushResult()
        any_success = False
        for target in self._targets:
            try:
                result = self._push_to_target(target, dirty, outcomes)
            except Exception as exc:  # justified: boundary, per-target failure is isolated
                logger.warning(
                    "sync_target_failed",
                    client_id=self._client_id,
                    label=target.label,
                    target=target.label,
                    error_type=type(exc).__name__,
                    error=str(exc)[:200],
                    exc_info=True,
                )
                report[target.label] = {"pushed": 0, "skipped": 0, "failed": 1, "error": f"{type(exc).__name__}: {str(exc)[:200]}"}
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

    def _push_to_target(
        self,
        target: SyncTarget,
        dirty: "list[MemoryEntry]",
        outcomes: list[dict[str, object]],
    ) -> PushResult:
        """Push learnings + outcomes to a single target. Raises on transport failure."""
        started = perf_counter()
        # Primary target uses the canonical self._pusher (tests patch this).
        if self._targets and target.label == self._targets[0].label:
            pusher = self._pusher
        else:
            pusher = self._pushers.get(target.label)
        if pusher is None:
            pusher = SyncPusher(
                backend_url=target.url,
                api_key=target.api_key,
                batch_size=self._config.sync_push_batch_size,
                timeout=self._config.sync_push_timeout_seconds,
                client_id=self._client_id,
            )
            self._pushers[target.label] = pusher

        total = PushResult()
        if dirty:
            logger.info(
                "sync_target_push_start",
                label=target.label,
                kind="learnings",
                client_id=self._client_id,
            )
            learning_result = pusher.push_learnings(dirty)
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
                client_id=self._client_id,
            )
        if outcomes:
            logger.info(
                "sync_target_push_start",
                label=target.label,
                kind="outcomes",
                client_id=self._client_id,
            )
            outcome_result = pusher.push_outcomes(outcomes)
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
                client_id=self._client_id,
            )
        return total

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

        if (
            sync_hints
            and sync_hints.get("significant_updates_available")
            and self._consecutive_immediate_repolls < _MAX_CONSECUTIVE_IMMEDIATE_REPOLLS
        ):
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
