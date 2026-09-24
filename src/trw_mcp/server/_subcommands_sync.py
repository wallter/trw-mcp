"""``trw-mcp sync pull --full`` -- replay every team learning into this checkout (PRD-CORE-280 FR02).

Prints the run's report as one JSON line. Exit codes:

* 0 -- the replay completed.
* 1 -- it stopped early (a held page or the page bound); run again with
  ``--resume``.
* 2 -- refused: a bad bound, team sync is off, no sync target is configured,
  an unfinished replay exists and ``--resume`` was not given, or a sync cycle
  holds the lock (its pid is named).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import sys

__all__ = ["run_sync"]

_EXIT_RETRY = 1
_EXIT_REFUSED = 2


def run_sync(args: argparse.Namespace) -> None:
    """Handle ``sync pull --full``."""
    from trw_mcp.models.config import get_config
    from trw_mcp.state._paths import resolve_trw_dir
    from trw_mcp.sync._replay import run_full_pull
    from trw_mcp.sync.client import BackendSyncClient

    if getattr(args, "sync_command", None) != "pull" or not args.full:
        sys.stderr.write("usage: trw-mcp sync pull --full [--resume] [--max-pages N] [--wait-seconds S]\n")
        sys.exit(_EXIT_REFUSED)
    if args.max_pages < 1 or not math.isfinite(args.wait_seconds) or args.wait_seconds < 0:
        sys.stderr.write("sync pull: --max-pages must be at least 1 and --wait-seconds finite and not negative\n")
        sys.exit(_EXIT_REFUSED)
    config = get_config()
    if not config.team_sync_enabled:
        sys.stderr.write("sync pull: team_sync_enabled is false, so no team learning would be merged\n")
        sys.exit(_EXIT_REFUSED)
    trw_dir = resolve_trw_dir()
    client = BackendSyncClient(config=config, trw_dir=trw_dir)
    if not client._targets:
        sys.stderr.write("sync pull: no sync target is configured\n")
        sys.exit(_EXIT_REFUSED)
    report = asyncio.run(
        run_full_pull(
            client,
            resume=args.resume,
            max_pages=args.max_pages,
            receipt_path=trw_dir / "sync-replay.jsonl",
            wait_seconds=args.wait_seconds,
        )
    )
    print(json.dumps(report))
    if report["status"] == "unfinished":
        sys.stderr.write("sync pull: an unfinished replay exists; continue it with --resume\n")
        sys.exit(_EXIT_REFUSED)
    if report["status"] == "locked":
        holder = client._coordinator.sync_lock_holder()
        sys.stderr.write(
            f"sync pull: the sync lock is held by pid {holder or 'unknown'}; retry or pass --wait-seconds\n"
        )
        sys.exit(_EXIT_REFUSED)
    if report["status"] != "completed":
        sys.exit(_EXIT_RETRY)
