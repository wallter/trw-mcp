"""The ``sync_health`` doctor row: WARN/SKIP/PASS over the existing sync-push read.

Belongs to the ``_subcommands_doctor.py`` catalogue; kept in a sibling for the
eLOC gate, following the ``_doctor_backend_connectivity.py`` /
``_doctor_pipeline_health.py`` split pattern (PRD-CORE-311-FR07).

This row adds NO new detection. It calls the existing
``trw_mcp.tools._sync_health.step_sync_health`` unchanged -- the same read
``trw_session_start`` already performs -- and only maps its readable/
NOT_MEASURED verdict onto the doctor's status vocabulary. ``step_sync_health``
does exactly one local file read (``sync-state.json``); this row makes no
network call of its own.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    from trw_mcp.models.config import TRWConfig

__all__ = ["sync_health_row"]


def sync_health_row(trw_dir: Path, config: TRWConfig) -> tuple[str, str]:
    """Return ``(status, message)`` for the ``sync_health`` row.

    Maps ``step_sync_health``'s three possible outcomes:

    * ``degraded`` -> ``WARN``, message is the same advisory text
      ``step_sync_health`` already computes (naming the failing push reason).
    * :data:`NOT_MEASURED <trw_mcp.tools._sync_health.NOT_MEASURED>` (state
      file absent, unreadable, or not a mapping) -> ``SKIP`` with the
      NOT_MEASURED reason -- never a false ``PASS``.
    * healthy -> ``PASS``.
    """
    from trw_mcp.tools._sync_health import NOT_MEASURED, step_sync_health

    health = step_sync_health(trw_dir, config)

    if health.get("status") == NOT_MEASURED:
        reason = str(health.get("reason") or "unknown")
        return "SKIP", f"sync health not measured: {reason}"

    if bool(health.get("degraded")):
        advisory = str(health.get("advisory") or "sync push degraded")
        return "WARN", advisory

    return "PASS", "sync push healthy: no degraded signal"
