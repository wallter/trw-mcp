"""Allow ``python -m trw_mcp.server`` to start the MCP server."""

from __future__ import annotations

import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path


def _crash_log(exc: BaseException) -> None:
    """Write crash details to .trw/logs/crash.log AND stderr.

    This runs before structlog is configured, so we use raw I/O.
    The crash log persists across sessions so users can report issues.
    """
    tb = traceback.format_exception(type(exc), exc, exc.__traceback__)
    ts = datetime.now(timezone.utc).isoformat()
    msg = f"\n{'=' * 60}\nTRW MCP CRASH — {ts}\n{'=' * 60}\n{''.join(tb)}\n"

    # Always write to stderr so Claude Code can surface it
    sys.stderr.write(msg)
    sys.stderr.flush()

    # Best-effort write to a log file for debugging. Any failure, a planted-symlink refusal included, is
    # reported on stderr and never raised: the crash above is already there and must stay the story.
    try:
        from trw_mcp._checkout_write import append_checkout_file

        log_dir = Path.cwd() / ".trw" / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        append_checkout_file(Path.cwd(), log_dir / "crash.log", msg)
    except Exception as exc:  # justified: a crash handler must not raise
        sys.stderr.write(f"(crash.log not written: {type(exc).__name__}: {exc})\n")


if __name__ == "__main__":
    try:
        from trw_mcp.server._cli import main

        main()
    except Exception as exc:  # justified: boundary, top-level crash handler for server process
        _crash_log(exc)
        sys.exit(1)
