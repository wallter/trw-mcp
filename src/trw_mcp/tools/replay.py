"""PRD-CORE-144 FR08: historical replay helper for the outcome pusher.

Deletes sibling ``meta/synced.json`` markers (optionally filtered by a
``since`` ISO-8601 cutoff — markers with a ``synced_at`` older than
``since`` are left alone when the filter is provided). After markers are
cleared, the next invocation of the normal outcome pusher re-emits
``OutcomeSync`` payloads for the affected runs.

Gated behind ``TRW_ALLOW_REPLAY=1`` — invocation without the env var is
a no-op with a structured ``replay_gated`` log event.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

import structlog

logger = structlog.get_logger(__name__)

_SYNCED_MARKER = "synced.json"
_ENV_GATE = "TRW_ALLOW_REPLAY"


def replay_outcomes(
    trw_dir: Path,
    *,
    since: str | None = None,
) -> dict[str, object]:
    """Delete ``synced.json`` markers so the next pusher pass re-emits outcomes.

    Args:
        trw_dir: Project ``.trw`` directory.
        since: Optional ISO-8601 timestamp. When provided, only markers
            whose ``synced_at`` is greater than or equal to *since* are
            deleted (i.e. replays recent runs). When omitted, every
            marker under ``trw_dir/runs`` is removed.

    Returns:
        Dict with ``gated``, ``replayed``, and ``scanned`` counts plus
        ``since`` echoed back for operator traceability.
    """
    result: dict[str, object] = {
        "gated": False,
        "replayed": 0,
        "scanned": 0,
        "since": since or "",
    }
    if os.environ.get(_ENV_GATE) != "1":
        logger.info("replay_gated", env_var=_ENV_GATE, since=since or "")
        result["gated"] = True
        return result

    runs_root = trw_dir / "runs"
    if not runs_root.is_dir():
        return result

    cutoff: datetime | None = None
    if since:
        try:
            cutoff = datetime.fromisoformat(since)
        except (ValueError, TypeError):
            logger.warning("replay_since_parse_failed", since=since)
            cutoff = None

    from trw_mcp.state._paths import iter_run_dirs

    scanned = 0
    replayed = 0
    for run_dir, _run_yaml in iter_run_dirs(runs_root):
        scanned += 1
        marker = run_dir / "meta" / _SYNCED_MARKER
        if not marker.exists():
            continue
        if cutoff is not None:
            try:
                payload = json.loads(marker.read_text(encoding="utf-8"))
                synced_at = payload.get("synced_at")
                if isinstance(synced_at, str):
                    marker_ts = datetime.fromisoformat(synced_at)
                    if marker_ts < cutoff:
                        continue
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                # Fall through to delete — unparseable markers are replay candidates.
                pass
        try:
            marker.unlink()
            replayed += 1
        except OSError:
            logger.warning("replay_marker_unlink_failed", run_dir=str(run_dir), exc_info=True)

    result["scanned"] = scanned
    result["replayed"] = replayed
    logger.info(
        "replay_complete",
        scanned=scanned,
        replayed=replayed,
        since=since or "",
    )
    return result
