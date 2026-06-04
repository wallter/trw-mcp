"""Sync-push health surface — PRD-FIX-COMPOUNDING-1.

Belongs to the ``_ceremony_helpers.py`` facade. Re-exported there for
back-compat so ``ceremony.py`` keeps a single import point.

Converts the silently-accumulating ``sync-state.json`` failure counter into an
operator-visible advisory on ``trw_session_start``. The 42-day SYNC-PUSH-DEAD
outage went unnoticed precisely because this read path did not exist.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import structlog

from trw_mcp.models.config import TRWConfig

logger = structlog.get_logger(__name__)


def step_sync_health(trw_dir: Path, config: TRWConfig) -> dict[str, object]:
    """Surface backend sync-push health from ``sync-state.json`` (FR01).

    Reads the failure counter and last-successful-push timestamp written by
    ``SyncCoordinator`` and marks ``degraded`` when consecutive failures reach
    ``config.sync_health_failure_threshold`` OR the last push is older than
    ``config.sync_health_stale_hours`` (missing push => "never" => degraded).

    Fail-open: ANY error (missing file, corrupt JSON, unexpected exception)
    returns the safe default and never raises — matching ``step_embed_health``.

    Returns:
        ``{"degraded": bool, "consecutive_failures": int,
           "last_push_at": str | None, "advisory": str}``. ``advisory`` is the
        empty string when not degraded.
    """
    safe_default: dict[str, object] = {
        "degraded": False,
        "consecutive_failures": 0,
        "last_push_at": None,
        "advisory": "",
    }
    try:
        state_path = trw_dir / "sync-state.json"
        if not state_path.is_file():
            return safe_default

        raw = json.loads(state_path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return safe_default

        failures_raw = raw.get("consecutive_failures", 0)
        consecutive_failures = int(failures_raw) if isinstance(failures_raw, (int, float)) else 0

        last_push_at_raw = raw.get("last_push_at")
        last_push_at: str | None = (
            last_push_at_raw if isinstance(last_push_at_raw, str) and last_push_at_raw else None
        )

        # Age computation — absent/unparseable timestamp is treated as "never".
        last_push_age_hours: float | None = None
        if last_push_at is not None:
            try:
                last_dt = datetime.fromisoformat(last_push_at)
                if last_dt.tzinfo is None:
                    last_dt = last_dt.replace(tzinfo=timezone.utc)
                last_push_age_hours = (datetime.now(tz=timezone.utc) - last_dt).total_seconds() / 3600.0
            except ValueError:
                last_push_age_hours = None  # unparseable => never

        threshold = config.sync_health_failure_threshold
        stale_hours = config.sync_health_stale_hours

        failure_degraded = consecutive_failures >= threshold
        stale_degraded = last_push_age_hours is None or last_push_age_hours > stale_hours
        degraded = failure_degraded or stale_degraded

        advisory = ""
        if degraded:
            push_desc = "never" if last_push_at is None else last_push_at
            advisory = (
                f"Backend sync-push is degraded: {consecutive_failures} consecutive failures; "
                f"last successful push {push_desc}. "
                "Restore platform_urls in .trw/config.yaml and verify the backend is reachable."
            )
            logger.warning(
                "sync_push_degraded_warning",
                consecutive_failures=consecutive_failures,
                last_push_at=last_push_at,
                threshold=threshold,
                stale_hours=stale_hours,
            )

        return {
            "degraded": degraded,
            "consecutive_failures": consecutive_failures,
            "last_push_at": last_push_at,
            "advisory": advisory,
        }
    except Exception:  # justified: fail-open, sync health check must not block session start
        logger.debug("sync_health_check_failed", exc_info=True)
        return safe_default
