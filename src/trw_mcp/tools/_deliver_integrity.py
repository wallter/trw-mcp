"""Integrity-on-delivery helper (PRD-INFRA-067 / C2).

Probes the checkout's SELECTED store -- the daemon store since PRD-CORE-298,
never the retired checkout ``memory.db`` -- once per delivery via
:func:`trw_deliver`. Records the result in ``events.jsonl`` and returns it for
inclusion in the deliver response payload.

Observability ONLY. A failed or unreachable probe at deliver time is logged;
it NEVER raises, blocks, or triggers recovery.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import cast

import structlog
from typing_extensions import TypedDict

from trw_mcp.state.persistence import FileEventLogger, FileStateWriter

__all__ = ["DeliverIntegrityResult", "check_memory_integrity_on_deliver"]

logger = structlog.get_logger(__name__)


class DeliverIntegrityResult(TypedDict):
    """Return shape for the deliver-time integrity probe."""

    ok: bool
    detail: str
    namespace: str
    checked_at: str


def check_memory_integrity_on_deliver(
    trw_dir: Path,
    run_dir: Path | None = None,
) -> DeliverIntegrityResult:
    """Probe that the selected store answers for this checkout's namespace.

    ``ok`` means reachable, not integrity-checked: trw-mcp no longer opens the
    database, so it cannot run ``PRAGMA quick_check`` on it.

    Args:
        trw_dir: Resolved ``.trw`` directory.
        run_dir: Optional active-run directory — when provided, the result is
            appended to ``run_dir/meta/events.jsonl`` under event name
            ``db_integrity_check_on_deliver``.

    Returns:
        Dict with ``ok``, ``detail``, ``namespace``, ``checked_at`` keys.
        Always returns — never raises. An unreachable or unpinned store is
        reported as ``ok=False``/``detail="not_measured: ..."`` -- it is never
        conflated with a healthy store the way a missing checkout ``memory.db``
        used to be (that file no longer exists once a checkout is migrated).
    """
    checked_at = datetime.now(timezone.utc).isoformat()
    result: DeliverIntegrityResult = {
        "ok": False,
        "detail": "unknown",
        "namespace": "",
        "checked_at": checked_at,
    }

    from trw_mcp.state._store_selection import StoreUnavailableError, measuring_only, selected_store

    try:
        with measuring_only():
            store, namespace = selected_store(trw_dir)
        result["namespace"] = namespace
        store.health(namespace)
    except StoreUnavailableError as exc:
        logger.debug("deliver_integrity_probe_not_measured", trw_dir=str(trw_dir), error=str(exc))
        result["ok"] = False
        result["detail"] = f"not_measured: {exc}"
    except Exception as exc:  # justified: fail-open observability probe
        logger.debug(
            "deliver_integrity_probe_failed",
            trw_dir=str(trw_dir),
            error=str(exc),
        )
        result["ok"] = False
        result["detail"] = f"not_measured: {exc}"
    else:
        # The probe proves the daemon store answers for this namespace. It does
        # not run an integrity check: the daemon owns its database, and
        # trw-mcp no longer opens it. Say so rather than report "ok".
        result["ok"] = True
        result["detail"] = "reachable; integrity not checked (the memory daemon owns its store)"

    if result["ok"]:
        logger.debug(
            "deliver_db_integrity_ok",
            trw_dir=str(trw_dir),
            namespace=result["namespace"],
            detail=result["detail"],
        )
    else:
        logger.warning(
            "deliver_db_integrity_regression",
            trw_dir=str(trw_dir),
            namespace=result["namespace"],
            detail=result["detail"],
        )

    # Log to events.jsonl when a run is active.
    events_jsonl = run_dir / "meta" / "events.jsonl" if run_dir else None
    if events_jsonl is not None and events_jsonl.parent.exists():
        try:
            events_logger = FileEventLogger(FileStateWriter())
            events_logger.log_event(
                events_jsonl,
                "db_integrity_check_on_deliver",
                cast("dict[str, object]", dict(result)),
            )
        except Exception:  # justified: event logging must never break deliver
            logger.debug("deliver_integrity_event_log_failed", exc_info=True)

    return result
