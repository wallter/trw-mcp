"""trw_heartbeat impl — extracted from ceremony.py.

Belongs to the ``ceremony.py`` facade. Re-exported there for back-compat.

Refresh the caller's pin heartbeat and append a heartbeat event with
60s rate-limit guard (state lives in pins.json so it survives restart).

Extracted as DIST-243 batch 67 to push parent ``ceremony.py`` toward
the 350-LOC gate.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

from trw_mcp.models.config import get_config
from trw_mcp.state._paths import TRWCallContext, resolve_pin_key
from trw_mcp.state._pin_store import _iso_now, get_pin_entry, upsert_pin_entry
from trw_mcp.state.persistence import FileEventLogger, FileStateWriter
from trw_mcp.tools._ceremony_runtime_helpers import (
    _compute_run_age_hours,
    _parse_iso_utc,
    _timedelta_hours,
)

if TYPE_CHECKING:
    from fastmcp import Context

    from trw_mcp.models.typed_dicts import TrwHeartbeatResultDict

logger = structlog.get_logger(__name__)

_events = FileEventLogger(FileStateWriter())


def compute_heartbeat_result(
    ctx: Context | None,
    message: str,
) -> TrwHeartbeatResultDict:
    """Compute heartbeat result for the caller, applying writes when not rate-limited.

    Returns ``{"error": "no_active_pin", "hint": ...}`` when no pin is held.
    Otherwise returns a full HeartbeatResultDict — short-circuits when
    ``now - last_heartbeat_ts < 60s`` to avoid spamming events.jsonl.
    """
    pin_key = resolve_pin_key(ctx=ctx, explicit=None)
    raw_session = getattr(ctx, "session_id", None) if ctx is not None else None
    # PRD-CORE-141: construct TRWCallContext for shape parity with other
    # ctx-aware tools.  Reserved for future analytics hooks.
    _ = TRWCallContext(
        session_id=pin_key,
        client_hint=None,
        explicit=False,
        fastmcp_session=raw_session if isinstance(raw_session, str) else None,
    )

    entry = get_pin_entry(pin_key)
    if entry is None:
        logger.warning("trw_heartbeat_no_pin", pin_key=pin_key)
        return {
            "error": "no_active_pin",
            "hint": "call trw_init or trw_adopt_run first",
        }

    config = get_config()
    now_dt = datetime.now(timezone.utc)

    last_ts_str = str(entry.get("last_heartbeat_ts", "") or "")
    last_dt = _parse_iso_utc(last_ts_str)
    rate_limited = last_dt is not None and (now_dt - last_dt).total_seconds() < 60.0

    run_path_str = str(entry.get("run_path", "") or "")
    run_dir = Path(run_path_str) if run_path_str else None
    run_id = run_dir.name if run_dir is not None else ""

    age_hours = _compute_run_age_hours(run_dir)

    if rate_limited:
        stale_after_ts = ""
        if last_dt is not None:
            stale_after_ts = (last_dt + _timedelta_hours(config.run_staleness_hours)).isoformat()
        should_checkpoint = age_hours > float(config.checkpoint_suggest_hours)
        logger.debug(
            "trw_heartbeat_rate_limited",
            pin_key=pin_key,
            run_id=run_id,
            age_hours=age_hours,
        )
        return {
            "run_id": run_id,
            "last_heartbeat_ts": last_ts_str,
            "stale_after_ts": stale_after_ts,
            "age_hours": age_hours,
            "should_checkpoint": should_checkpoint,
            "rate_limited": True,
        }

    new_ts = _iso_now()
    upsert_pin_entry(
        pin_key,
        Path(run_path_str) if run_path_str else Path("."),
        client_hint=entry.get("client_hint") if isinstance(entry.get("client_hint"), str) else None,
    )

    if run_dir is not None and (run_dir / "meta").exists():
        _events.log_event(
            run_dir / "meta" / "events.jsonl",
            "heartbeat",
            {"message": message, "pin_key": pin_key},
        )

    stale_after_ts = (now_dt + _timedelta_hours(config.run_staleness_hours)).isoformat()
    should_checkpoint = age_hours > float(config.checkpoint_suggest_hours)

    logger.debug(
        "trw_heartbeat_applied",
        pin_key=pin_key,
        run_id=run_id,
        age_hours=age_hours,
        should_checkpoint=should_checkpoint,
    )
    return {
        "run_id": run_id,
        "last_heartbeat_ts": new_ts,
        "stale_after_ts": stale_after_ts,
        "age_hours": age_hours,
        "should_checkpoint": should_checkpoint,
        "rate_limited": False,
    }
