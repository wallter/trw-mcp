"""trw_adopt_run impl — extracted from ceremony.py.

Belongs to the ``ceremony.py`` facade. Re-exported there for back-compat.

Transfer an existing run's pin to the caller's session, with three guards:
out-of-project containment (no force), terminal-status (force=True
required), and live-owner (force=True required + WARN emission).

Extracted as DIST-243 batch 68 to push parent ``ceremony.py`` toward
the 350-LOC gate.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog

from trw_mcp.exceptions import StateError
from trw_mcp.models.config import get_config
from trw_mcp.state._paths import resolve_pin_key
from trw_mcp.state._pin_store import (
    _iso_now,
    load_pin_store,
    remove_pin_entry,
    upsert_pin_entry,
)
from trw_mcp.state.persistence import FileEventLogger, FileStateReader, FileStateWriter
from trw_mcp.tools._ceremony_runtime_helpers import _parse_iso_utc

if TYPE_CHECKING:
    from fastmcp import Context

    from trw_mcp.models.typed_dicts import TrwAdoptRunResultDict

logger = structlog.get_logger(__name__)

_events = FileEventLogger(FileStateWriter())


def adopt_run(
    ctx: Context | None,
    run_path: str,
    force: bool,
) -> TrwAdoptRunResultDict:
    """Transfer the run at ``run_path`` to the caller's pin.

    Raises :class:`StateError` for out-of-project paths, missing run
    directories, terminal-status runs (without ``force``), and live-owner
    runs (without ``force``). On success returns a TrwAdoptRunResultDict.
    """
    if not run_path:
        raise StateError("run_path is required for trw_adopt_run")

    # Containment check — must be under project root. No force override.
    # Import lazily so conftest's monkeypatch of the source attribute
    # reaches this call site (FR08 containment).
    from trw_mcp.state._paths import resolve_project_root

    resolved = Path(run_path).resolve()
    project_root = resolve_project_root()
    if not resolved.is_relative_to(project_root):
        raise StateError(f"run_path escapes project root: {resolved}", path=str(resolved))
    if not resolved.exists():
        raise StateError(f"run_path does not exist: {resolved}", path=str(resolved))

    caller_pin_key = resolve_pin_key(ctx=ctx, explicit=None)

    run_yaml = resolved / "meta" / "run.yaml"
    target_status = "unknown"
    if run_yaml.exists():
        try:
            reader = FileStateReader()
            data = reader.read_yaml(run_yaml)
            target_status = str(data.get("status", "unknown"))
        except Exception:  # justified: fail-open, unreadable run.yaml treated as unknown
            logger.debug("adopt_run_read_status_failed", run_path=str(resolved), exc_info=True)

    if target_status in ("delivered", "complete", "failed") and not force:
        raise StateError(
            f"cannot adopt terminal-status run (status={target_status}); pass force=True to override",
            path=str(resolved),
            status=target_status,
        )

    # Find existing pin entry for the target run path.
    store = load_pin_store()
    previous_pin_key: str | None = None
    previous_entry: dict[str, Any] | None = None
    target_str = str(resolved)
    for pkey, pentry in store.items():
        if not isinstance(pentry, dict):
            continue
        if str(pentry.get("run_path", "")) == target_str:
            previous_pin_key = pkey
            previous_entry = pentry
            break

    # Live-owner check.
    from_owner_was_live = False
    previous_owner_heartbeat_age_hours: float | None = None
    config = get_config()
    if previous_entry is not None:
        prev_last_ts = _parse_iso_utc(str(previous_entry.get("last_heartbeat_ts", "") or ""))
        if prev_last_ts is not None:
            age_s = (datetime.now(timezone.utc) - prev_last_ts).total_seconds()
            previous_owner_heartbeat_age_hours = age_s / 3600.0
            if age_s < float(config.pin_ttl_hours) * 3600.0:
                from_owner_was_live = True

    if from_owner_was_live and not force:
        raise StateError(
            "run is actively held by a live pin; pass force=True to override",
            path=str(resolved),
            pin_key=previous_pin_key,
        )

    # Atomic-across-two-saves: file lock in the pin store serializes both writes.
    if previous_pin_key is not None and previous_pin_key != caller_pin_key:
        remove_pin_entry(previous_pin_key)
    upsert_pin_entry(caller_pin_key, resolved)
    adopted_ts = _iso_now()

    if from_owner_was_live and force:
        structlog.get_logger(__name__).warning(
            "run_adopted_potential_writer_conflict",
            previous_pin_key=previous_pin_key,
            previous_owner_heartbeat_age_hours=previous_owner_heartbeat_age_hours,
            new_pin_key=caller_pin_key,
            run_path=str(resolved),
        )

    if (resolved / "meta").exists():
        _events.log_event(
            resolved / "meta" / "events.jsonl",
            "run_adopted",
            {
                "from_pin_key": previous_pin_key,
                "to_pin_key": caller_pin_key,
                "force_used": force,
                "previous_owner_heartbeat_age_hours": previous_owner_heartbeat_age_hours,
            },
        )

    logger.info(
        "run_adopted",
        run_path=str(resolved),
        from_pin_key=previous_pin_key,
        to_pin_key=caller_pin_key,
        force_used=force,
        from_owner_was_live=from_owner_was_live,
    )

    return {
        "adopted_run_id": resolved.name,
        "previous_pin_key": previous_pin_key,
        "from_pin_key": previous_pin_key,
        "to_pin_key": caller_pin_key,
        "adopted_ts": adopted_ts,
        "from_owner_was_live": from_owner_was_live,
        "force_used": force,
    }
