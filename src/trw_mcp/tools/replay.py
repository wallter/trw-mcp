"""Bounded historical OutcomeSync replay for PRD-CORE-144 FR08."""

from __future__ import annotations

import asyncio
import os
from collections.abc import Awaitable, Callable
from datetime import datetime
from pathlib import Path

import structlog
from fastmcp import FastMCP
from typing_extensions import TypedDict

from trw_mcp.sync.outcomes import PendingOutcome, load_pending_outcomes, write_synced_marker

logger = structlog.get_logger(__name__)
_ENV_GATE = "TRW_ALLOW_REPLAY"


class ReplayOutcomesResult(TypedDict, total=False):
    gated: bool
    configured: bool
    replayed: int
    scanned: int
    failed: int
    since: str


PushOutcomes = Callable[[list[dict[str, object]]], Awaitable[bool]]


def _eligible_since(item: PendingOutcome, cutoff: datetime | None) -> bool:
    """Return whether an unsynced run is old enough for the optional cutoff."""
    if cutoff is None or item.run_dir is None:
        return True
    try:
        return datetime.fromtimestamp(item.run_dir.stat().st_mtime, tz=cutoff.tzinfo) <= cutoff
    except OSError:
        return False


async def _configured_push(payloads: list[dict[str, object]]) -> bool:
    """Push one replay batch through the normal consent-gated OutcomeSync pusher."""
    from trw_mcp.models.config import get_config
    from trw_mcp.sync.push import SyncPusher

    config = get_config()
    targets = config.resolved_sync_targets
    if not targets:
        return False
    url, api_key = targets[0]
    pusher = SyncPusher(
        url,
        api_key,
        batch_size=config.sync_push_batch_size,
        timeout=config.sync_push_timeout_seconds,
        platform_telemetry_enabled=config.platform_telemetry_enabled,
    )
    result = await pusher.push_outcomes(payloads)
    # A backend may legitimately report zero inserts when it deduplicates a
    # payload it has already accepted.  No failed records still means the
    # replay was acknowledged and its local marker can be persisted.
    return result.failed == 0


async def replay_pending_outcomes(
    trw_dir: Path,
    *,
    since: str | None = None,
    push_outcomes: PushOutcomes | None = None,
) -> ReplayOutcomesResult:
    """Push every eligible unsynced delivered run once and persist sync markers.

    ``since`` is an upper cutoff: only runs at or older than the supplied ISO
    timestamp are replayed.  Existing markers remain authoritative, so a second
    invocation returns zero without deleting or rewriting evidence.
    """
    result: ReplayOutcomesResult = {
        "gated": False,
        "configured": True,
        "replayed": 0,
        "scanned": 0,
        "failed": 0,
        "since": since or "",
    }
    if os.environ.get(_ENV_GATE) != "1":
        logger.info("replay_gated", env_var=_ENV_GATE, since=since or "")
        result["gated"] = True
        return result

    cutoff: datetime | None = None
    if since:
        try:
            cutoff = datetime.fromisoformat(since.replace("Z", "+00:00"))
        except ValueError:
            logger.warning("replay_since_parse_failed", since=since)
            result["failed"] = 1
            return result

    pending = load_pending_outcomes(trw_dir)
    result["scanned"] = len(pending)
    eligible = [item for item in pending if _eligible_since(item, cutoff)]
    if not eligible:
        return result

    push = push_outcomes or _configured_push
    if push_outcomes is None:
        from trw_mcp.models.config import get_config

        if not get_config().resolved_sync_targets:
            result["configured"] = False
            return result

    succeeded = await push([item.payload for item in eligible])
    if not succeeded:
        result["failed"] = len(eligible)
        return result

    for item in eligible:
        if item.run_dir is None:
            continue
        write_synced_marker(
            item.run_dir,
            run_id=item.run_id,
            sync_hash=item.sync_hash,
            target_label="historical-replay",
            run_yaml_hash=item.run_yaml_hash,
        )
        result["replayed"] += 1
    logger.info("replay_complete", **result)
    return result


def replay_outcomes(
    trw_dir: Path,
    *,
    since: str | None = None,
    push_outcomes: PushOutcomes | None = None,
) -> ReplayOutcomesResult:
    """Synchronous compatibility facade for scripts and unit tests."""
    return asyncio.run(replay_pending_outcomes(trw_dir, since=since, push_outcomes=push_outcomes))


def register_replay_tools(server: FastMCP) -> None:
    """Register the gated historical OutcomeSync replay tool."""

    @server.tool(output_schema=None)
    async def trw_replay_outcomes(since: str | None = None) -> ReplayOutcomesResult:
        """Replay unsynced delivered-run outcomes once, optionally through an old-run cutoff.

        Use when:
        - Delivered runs may have missed their outcome sync (backend outage, kill).
        - You need pending outcome telemetry flushed before analyzing results.
        """
        from trw_mcp.state._paths import resolve_trw_dir

        return await replay_pending_outcomes(resolve_trw_dir(), since=since)


__all__ = [
    "ReplayOutcomesResult",
    "register_replay_tools",
    "replay_outcomes",
    "replay_pending_outcomes",
]
