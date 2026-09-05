"""TTL-and-liveness expiry for pin-store entries (PRD-CORE-248 FR05).

Parent facade: :mod:`trw_mcp.state._pin_store`, which applies this predicate in
its eviction loop. :mod:`trw_mcp.tools._ceremony_runtime_helpers` applies the
SAME predicate to the candidate-run hints, so a pin that is due for eviction can
never be offered to the agent as an adoptable run.

Why the predicate is a conjunction
----------------------------------
``pin_ttl_hours`` has always been described as "time-to-live (hours) for entries
in the persistent pin store before GC evicts them", and nothing honoured that
for the store itself: the only eviction pass dropped malformed entries and
missing ``run_path``s. Live inspection at authoring time found 15 entries, 2
with a live creator PID and 13 with heartbeats 13.7 to 34.9 days old, all of
them still eligible as ``candidate_runs``.

The fix is NOT "evict by age" and NOT "evict by dead PID". Either alone is
wrong:

- A dead creator PID with a **fresh** heartbeat is a legitimately restarted
  server whose pin must survive — that is exactly what the previous
  "creator PIDs are diagnostic only" contract protected, and it is preserved.
- A live creator PID makes the entry live regardless of heartbeat age.

So an entry expires only when its creator PID is **not** live **and** its
``last_heartbeat_ts`` parses to a timestamp older than the TTL. Ambiguity is
resolved toward retention (NFR02): an absent, non-string, or unparseable
heartbeat is "not provably expired" and the entry stays. Losing a live pin is
worse than keeping a dead one.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import structlog

from trw_mcp.state.memory_pressure import _parse_heartbeat_ts, _pid_is_alive

logger = structlog.get_logger(__name__)

__all__ = ["heartbeat_age_hours", "pin_entry_is_expired", "resolve_pin_ttl_hours"]


def resolve_pin_ttl_hours() -> int:
    """Return the configured ``pin_ttl_hours``, or its field default on failure.

    Fail-open: a config load failure must not turn the eviction pass into a
    purge or a no-op with an unpredictable bound, so the field's own default is
    used. Imported lazily because ``_pin_store`` sits under ``state._paths``,
    which the config model may itself reach.
    """
    try:
        from trw_mcp.models.config import get_config

        return int(get_config().pin_ttl_hours)
    except Exception:  # justified: fail-open, pin hygiene must never break a load
        from trw_mcp.models.config import TRWConfig

        logger.debug("pin_ttl_config_unavailable", exc_info=True)
        return int(TRWConfig.model_fields["pin_ttl_hours"].default)


def heartbeat_age_hours(entry: dict[str, Any], *, now: datetime | None = None) -> float | None:
    """Age in hours of *entry*'s ``last_heartbeat_ts``, or None when unparseable."""
    parsed = _parse_heartbeat_ts(entry.get("last_heartbeat_ts"))
    if parsed is None:
        return None
    reference = now or datetime.now(timezone.utc)
    return (reference - parsed).total_seconds() / 3600.0


def pin_entry_is_expired(
    entry: dict[str, Any],
    *,
    pin_ttl_hours: int,
    now: datetime | None = None,
) -> tuple[bool, float | None]:
    """Return ``(expired, heartbeat_age_hours)`` for one pin-store entry.

    ``expired`` is True only when BOTH hold: the creator PID is not live, and a
    parseable heartbeat is older than *pin_ttl_hours*. The age is returned
    alongside so callers can put it in their eviction telemetry without parsing
    the timestamp a second time.

    A non-positive *pin_ttl_hours* disables expiry entirely (nothing is ever
    provably past a zero-length TTL in a way worth acting on), which keeps an
    operator's ``pin_ttl_hours: 0`` from purging the whole store.
    """
    if pin_ttl_hours <= 0:
        return False, None
    age = heartbeat_age_hours(entry, now=now)
    if age is None or age <= float(pin_ttl_hours):
        return False, age
    pid = entry.get("pid")
    if not isinstance(pid, int) or _pid_is_alive(pid):
        # Not an int -> no evidence the creator is gone; alive -> the session is.
        return False, age
    return True, age
