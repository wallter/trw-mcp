"""Pin management helpers — extracted from _paths.py for module-size compliance.

Belongs to the ``_paths.py`` facade. Re-exported there for back-compat.

Three pin-management public helpers writing through to ``.trw/runtime/pins.json``
via the lower-level ``state._pin_store`` module:
- ``pin_active_run`` — pin a run directory as the active run for a session
- ``unpin_active_run`` — remove the run pin for a session
- ``get_pinned_run`` — return the currently pinned run directory, or None
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import structlog

from trw_mcp.state._pin_store import (
    get_pin_entry,
    remove_pin_entry,
    upsert_pin_entry,
)

if TYPE_CHECKING:
    from trw_mcp.state._paths import TRWCallContext

logger = structlog.get_logger(__name__)


def pin_active_run(
    run_dir: Path,
    *,
    context: TRWCallContext | None = None,
    session_id: str | None = None,
) -> None:
    """Pin a run directory as the active run for a session.

    After pinning, ``find_active_run`` returns this directory. Writes
    through to ``.trw/runtime/pins.json`` via ``upsert_pin_entry`` so the
    pin survives MCP server restart (PRD-CORE-141 FR04).

    Args:
        run_dir: Absolute path to the run directory to pin.
        context: TRWCallContext resolved from the FastMCP Context (preferred,
            PRD-CORE-141 FR01).  When provided, its ``session_id`` wins.
        session_id: Legacy kwarg — retained for backward compat with direct
            Python callers.  Ignored when ``context`` is provided.
    """
    from trw_mcp.state._paths import _resolve_session_id

    sid = _resolve_session_id(context, session_id)
    record = upsert_pin_entry(sid, run_dir)
    logger.debug(
        "pin_saved",
        pin_key=sid,
        run_path=record["run_path"],
        pid=record["pid"],
    )


def unpin_active_run(
    *,
    context: TRWCallContext | None = None,
    session_id: str | None = None,
) -> None:
    """Remove the run pin for a session, reverting to filesystem scan.

    Persists the removal to ``.trw/runtime/pins.json`` (PRD-CORE-141 FR04).

    Args:
        context: TRWCallContext resolved from FastMCP Context (preferred).
        session_id: Legacy kwarg; ignored when ``context`` is provided.
    """
    from trw_mcp.state._paths import _resolve_session_id

    sid = _resolve_session_id(context, session_id)
    removed = remove_pin_entry(sid)
    if removed:
        logger.debug("pin_cleared", pin_key=sid)


def get_pinned_run(
    *,
    context: TRWCallContext | None = None,
    session_id: str | None = None,
) -> Path | None:
    """Return the currently pinned run directory for a session, or None.

    Reads through the 1-second-TTL pin-store cache (PRD-CORE-141 FR04).

    Args:
        context: TRWCallContext resolved from FastMCP Context (preferred).
        session_id: Legacy kwarg; ignored when ``context`` is provided.
    """
    from trw_mcp.state._paths import _resolve_session_id

    sid = _resolve_session_id(context, session_id)
    entry = get_pin_entry(sid)
    if entry is None:
        return None
    run_path = entry.get("run_path")
    if isinstance(run_path, str) and run_path:
        return Path(run_path)
    return None
