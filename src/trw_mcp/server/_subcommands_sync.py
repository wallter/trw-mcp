"""``trw-mcp sync push | pull | status`` and ``sync pull --full`` (INC-145, PRD-CORE-280 FR02).

``push`` and ``pull`` run one half of the SAME cycle the MCP server loop runs
(:func:`trw_mcp.sync._client_cycle.run_one_cycle`), on demand, so a CLI-only
or headless host shares its learnings without a long-lived server. ``status``
reads local state only and makes no network call. Each prints one JSON line
(the sync status, or the replay report for ``--full``).

Exit codes:

* 0 -- the run completed (learnings the backend rejected are held back and
  named on stderr, not a failure of the run).
* 1 -- the push or pull failed (the server's reason is on stderr), or a
  ``--full`` replay stopped early; run again (``--resume`` for a replay).
* 2 -- refused: a bad bound, the needed opt-in is off, no sync target is
  configured, an unfinished replay exists without ``--resume``, or a sync
  cycle holds the lock (its pid is named).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from trw_mcp.models.config import TRWConfig
    from trw_mcp.sync.client import BackendSyncClient

__all__ = ["run_sync"]

_EXIT_RETRY = 1
_EXIT_REFUSED = 2
#: ``status`` counts pending rows up to the daemon's page limit; past it the count is a floor.
_STATUS_PENDING_CAP = 1000


def _refuse(message: str) -> None:
    sys.stderr.write(f"sync: {message}\n")
    sys.exit(_EXIT_REFUSED)


def _client(config: TRWConfig) -> BackendSyncClient:
    from trw_mcp.state._paths import resolve_trw_dir
    from trw_mcp.sync.client import BackendSyncClient

    return BackendSyncClient(config=config, trw_dir=resolve_trw_dir())


def _status(client: BackendSyncClient) -> dict[str, object]:
    """Last push and pull, failures, pending and held-back counts: local state only."""
    state = client._coordinator._read_state()
    held = client._coordinator.rejected_entries()
    pending = client._get_dirty_entries(page_size=_STATUS_PENDING_CAP)
    return {
        "targets": [target.label for target in client._targets],
        "learning_sharing_enabled": client._learning_sharing_enabled,
        "team_sync_enabled": bool(getattr(client._config, "team_sync_enabled", False)),
        "last_push_at": state.get("last_push_at"),
        "last_content_push_at": state.get("last_content_push_at"),
        "content_pushed_total": state.get("content_pushed_total", 0),
        "last_pull_at": state.get("last_pull_at"),
        "consecutive_failures": state.get("consecutive_failures", 0),
        "last_error": state.get("last_error"),
        "pending": len(pending),
        "pending_capped": len(pending) + len(held) >= _STATUS_PENDING_CAP,
        "rejected": len(held),
        "rejected_entries": [{"id": key, "reason": value.get("reason")} for key, value in held.items()],
    }


def _run_half(client: BackendSyncClient, *, push: bool) -> None:
    """Run the push or the pull half of the server loop's cycle, print the status, exit by outcome."""
    verb = "push" if push else "pull"
    if not client._targets:
        _refuse(f"{verb}: no sync target is configured (platform_urls and an API key)")
    outcome = asyncio.run(client._run_one_cycle(force=True, push=push, pull=not push))
    if outcome == "locked":
        holder = client._coordinator.sync_lock_holder()
        _refuse(f"{verb}: the sync lock is held by pid {holder or 'unknown'}; retry when that cycle ends")
    status = _status(client)
    print(json.dumps(status))
    if status["rejected"]:
        sys.stderr.write(
            f"sync {verb}: {status['rejected']} learning(s) rejected by the backend and held back; "
            "fix the cause, then run `trw-mcp sync push --retry-rejected`\n"
        )
    if outcome in ("push_failed", "pull_failed"):
        sys.stderr.write(f"sync {verb}: {outcome.replace('_', ' ')}: {status['last_error'] or 'unknown error'}\n")
        sys.exit(_EXIT_RETRY)


def _run_full_pull(args: argparse.Namespace, client: BackendSyncClient) -> None:
    from trw_mcp.sync._replay import run_full_pull

    if args.max_pages < 1 or not math.isfinite(args.wait_seconds) or args.wait_seconds < 0:
        _refuse("pull: --max-pages must be at least 1 and --wait-seconds finite and not negative")
    if not client._targets:
        _refuse("pull: no sync target is configured")
    report = asyncio.run(
        run_full_pull(
            client,
            resume=args.resume,
            max_pages=args.max_pages,
            receipt_path=client._trw_dir / "sync-replay.jsonl",
            wait_seconds=args.wait_seconds,
        )
    )
    print(json.dumps(report))
    if report["status"] == "unfinished":
        _refuse("pull: an unfinished replay exists; continue it with --resume")
    if report["status"] == "locked":
        holder = client._coordinator.sync_lock_holder()
        _refuse(f"pull: the sync lock is held by pid {holder or 'unknown'}; retry or pass --wait-seconds")
    if report["status"] != "completed":
        sys.exit(_EXIT_RETRY)


def run_sync(args: argparse.Namespace) -> None:
    """Handle ``sync push | pull [--full] | status``."""
    command = getattr(args, "sync_command", None)
    if command not in ("push", "pull", "status"):
        _refuse("usage: trw-mcp sync push [--retry-rejected] | pull [--full ...] | status")
    from trw_mcp.models.config import get_config

    config = get_config()
    if command == "push" and not getattr(config, "learning_sharing_enabled", False):
        _refuse("push: learning_sharing_enabled is false, so no learning would leave this host")
    if command == "pull" and not getattr(config, "team_sync_enabled", False):
        _refuse("pull: team_sync_enabled is false, so no team learning would be merged")
    client = _client(config)
    if command == "status":
        print(json.dumps(_status(client)))
    elif command == "push":
        if args.retry_rejected:
            client._coordinator.clear_rejected()
        _run_half(client, push=True)
    elif args.full:
        _run_full_pull(args, client)
    else:
        _run_half(client, push=False)
