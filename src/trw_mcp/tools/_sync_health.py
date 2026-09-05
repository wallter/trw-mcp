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
from trw_mcp.tools._ceremony_degradations import DegradationCollector

logger = structlog.get_logger(__name__)

#: The ``status`` value for a read that could not be performed (PRD-CORE-263-FR02).
#: Distinct from both "healthy" and "degraded": those are verdicts about the
#: sync push, and this says nothing was observed to form a verdict FROM.
NOT_MEASURED = "not_measured"


def _not_measured(reason: str) -> dict[str, object]:
    """The result of a sync-health read that could not be performed.

    Carries NO ``degraded`` key on purpose. The pre-263 shape returned
    ``degraded: False`` for a missing, unparseable or exception-raising state
    file — a verdict about a push nobody looked at, and the exact opposite of
    what the step's own docstring says a missing push means. A reader that
    branches on ``degraded`` now gets a ``KeyError``/``None`` rather than a
    reassuring ``False``, which is the point.
    """
    return {
        "status": NOT_MEASURED,
        "reason": reason,
        "consecutive_failures": 0,
        "last_push_at": None,
        "advisory": f"sync health not measured: {reason}",
    }


def step_sync_health(
    trw_dir: Path,
    config: TRWConfig,
    degradations: DegradationCollector | None = None,
) -> dict[str, object]:
    """Surface backend sync-push health from ``sync-state.json`` (FR01).

    Reads the failure counter and last-successful-push timestamp written by
    ``SyncCoordinator`` and marks ``degraded`` when consecutive failures reach
    ``config.sync_health_failure_threshold`` OR the last push is older than
    ``config.sync_health_stale_hours`` (missing push => "never" => degraded).

    PRD-CORE-263-FR02: a state file that is missing, not a mapping, or that
    raises on read yields :data:`NOT_MEASURED` with a distinct reason — never a
    verdict. It used to return ``degraded: False``, which contradicted the
    paragraph above (a missing push is "never" and therefore degraded) and made
    an unreadable install indistinguishable from a healthy one. The readable
    path is byte-identical to the pre-263 output.

    ``degradations`` (optional): the per-call collector. An unexpected exception
    is recorded there rather than only in a debug log, so the swallow is
    enumerable in the payload (NFR02).

    Returns:
        readable — ``{"degraded": bool, "consecutive_failures": int,
        "last_push_at": str | None, "advisory": str}`` with ``advisory`` empty
        when not degraded; unreadable — see :func:`_not_measured`.
    """
    try:
        state_path = trw_dir / "sync-state.json"
        if not state_path.is_file():
            return _not_measured("sync_state_absent")

        raw = json.loads(state_path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return _not_measured("sync_state_not_a_mapping")

        failures_raw = raw.get("consecutive_failures", 0)
        consecutive_failures = int(failures_raw) if isinstance(failures_raw, (int, float)) else 0

        last_push_at_raw = raw.get("last_push_at")
        last_push_at: str | None = last_push_at_raw if isinstance(last_push_at_raw, str) and last_push_at_raw else None

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
    except Exception as exc:
        # Fail-open, but the failure is REPORTED rather than erased: the step is
        # non-critical (an unreadable sidecar must not block session start), so
        # the swallow is converted into an enumerable degradation instead of a
        # debug line nobody reads (PRD-CORE-263-FR02 / NFR02).
        if degradations is not None:
            degradations.record("sync_health", exc)
        else:
            logger.warning("sync_health_check_failed", error=type(exc).__name__, exc_info=True)
        return _not_measured(f"sync_state_unreadable: {type(exc).__name__}")
