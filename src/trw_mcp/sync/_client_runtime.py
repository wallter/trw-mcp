"""Internal helpers for BackendSyncClient runtime state and local bookkeeping."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog

from trw_mcp.state import _store_selection

logger = structlog.get_logger(__name__)

_MIN_HINT_DELAY_SECONDS = 60
_MAX_HINT_DELAY_SECONDS = 7200
_MAX_CONSECUTIVE_IMMEDIATE_REPOLLS = 1
#: Dirty rows pushed per cycle; the rest go on the next cycle, oldest first.
DIRTY_PAGE_SIZE = 500

if TYPE_CHECKING:
    from trw_memory.models.memory import MemoryEntry


def coerce_positive_number(raw: object) -> float | None:
    if not isinstance(raw, (int, float)):
        return None
    value = float(raw)
    return value if value > 0 else None


def parse_sync_hint_timestamp(raw: object) -> datetime | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def apply_sync_hints(
    *,
    client_id: str,
    config_sync_interval_seconds: float,
    sync_hints: dict[str, Any] | None,
    last_applied_schedule_seconds: float,
    consecutive_immediate_repolls: int,
) -> tuple[float, float, float, bool, int]:
    polling_cap_seconds = coerce_positive_number((sync_hints or {}).get("polling_cap_seconds"))
    interval_seconds = coerce_positive_number((sync_hints or {}).get("interval_seconds"))
    delay = interval_seconds if interval_seconds is not None else float(config_sync_interval_seconds)
    recommended_at = (sync_hints or {}).get("next_poll_recommended_at")
    parsed_recommended_at = parse_sync_hint_timestamp(recommended_at)
    if interval_seconds is None and parsed_recommended_at is not None:
        delay = max(0.0, (parsed_recommended_at - datetime.now(tz=timezone.utc)).total_seconds())

    if polling_cap_seconds is not None and delay > 0:
        delay = max(delay, polling_cap_seconds)
    if delay > 0:
        delay = min(max(delay, _MIN_HINT_DELAY_SECONDS), _MAX_HINT_DELAY_SECONDS)

    if (
        sync_hints
        and sync_hints.get("significant_updates_available")
        and consecutive_immediate_repolls < _MAX_CONSECUTIVE_IMMEDIATE_REPOLLS
    ):
        logger.info(
            "sync_hint_applied",
            client_id=client_id,
            mode="immediate_repoll",
            polling_cap_seconds=polling_cap_seconds,
        )
        return 0.0, 0.0, delay, True, consecutive_immediate_repolls + 1

    logger.info(
        "sync_hint_applied",
        client_id=client_id,
        mode="scheduled",
        next_delay_seconds=delay,
        polling_cap_seconds=polling_cap_seconds,
    )
    return delay, delay, delay, False, 0


def reset_poll_schedule(sync_interval_seconds: float) -> tuple[float, float, float, bool, int]:
    delay = float(sync_interval_seconds)
    return delay, delay, delay, False, 0


def restore_poll_schedule(last_applied_schedule_seconds: float) -> tuple[float, float, float, bool, int]:
    delay = float(last_applied_schedule_seconds)
    return delay, delay, delay, False, 0


def consume_next_cycle_force(next_cycle_force: bool) -> tuple[bool, bool]:
    return next_cycle_force, False


def get_dirty_entries(*, client_id: str, trw_dir: Path) -> list[MemoryEntry]:
    """The oldest page of this checkout's project rows not yet pushed (PRD-CORE-298 FR01)."""
    try:
        store, namespace = _store_selection.selected_store(trw_dir)
        return store.page_dirty(namespace, DIRTY_PAGE_SIZE)
    except Exception:  # justified: fail-open, dirty-entry discovery falls back to no-op sync
        # trw-fail-silent-allow: an unreadable page leaves every row dirty, so the next cycle pushes it; nothing is lost.
        logger.debug("sync_get_dirty_failed", client_id=client_id, exc_info=True)
        return []


def mark_synced(*, client_id: str, trw_dir: Path, entries: list[MemoryEntry]) -> None:
    try:
        store, namespace = _store_selection.selected_store(trw_dir)
        store.mark_synced(namespace, entries)
    except Exception:  # justified: fail-open, sync bookkeeping must not break successful pushes
        logger.debug("sync_mark_synced_failed", client_id=client_id, exc_info=True)
