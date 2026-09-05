"""PRD-CORE-249-FR03 — the ``trw_session_start`` handoff readback.

A session-start step in its own module rather than appended to the already
328-effective-LOC ``_ceremony_session_start_steps.py``, following the
``_ceremony_reconcile_step.py`` precedent.

**Why it exists.** Before this, a run ended and the work it deferred did not
survive with it: the persisted ``deferred_results`` key had zero readers
anywhere in ``trw-mcp/src`` (pattern P12, presence-unconsumed). This is the
first code path that reads project-scoped unfinished work back at the start of
the next session.

**Age is derived, never stored.** ``age_days = (today_utc - first_seen).days``,
compared on UTC calendar dates at both endpoints, so a row written today reports
``0`` and no row can carry a stale age between writes.

**A bound that cannot report a reassuring zero.** Items are oldest-first and
capped at :data:`HANDOFF_READBACK_MAX_ITEMS`, while ``total`` always reports the
UNTRUNCATED count, so a truncated list is distinguishable from a short one. A
managed block over :data:`HANDOFF_BLOCK_MAX_BYTES` is not parsed at all and
reports ``status="not_measured"`` with a reason — absence of a measurement is
not a measurement of absence.

Both bounds are documented module constants rather than public config fields,
following the ``DeliveryLimits`` precedent: they are v1 invariants, not
operator tunables, and the public field budget is already over its NFR04 target.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Final

import structlog

from trw_mcp.models.typed_dicts import (
    OpenHandoffDict as OpenHandoffDict,
)
from trw_mcp.models.typed_dicts import (
    OpenHandoffItemDict as OpenHandoffItemDict,
)
from trw_mcp.tools._project_handoff import find_block_span, parse_rows, resolve_handoff_path

logger = structlog.get_logger(__name__)

#: Oldest-first cap on the items returned. ``total`` is always untruncated.
HANDOFF_READBACK_MAX_ITEMS: Final[int] = 20

#: A managed block above this size is not parsed; the step reports
#: ``not_measured`` rather than an unbounded scan on every session start.
HANDOFF_BLOCK_MAX_BYTES: Final[int] = 256 * 1024


def read_open_handoff() -> OpenHandoffDict:
    """Read the managed block and project its open rows. Never raises."""
    try:
        target = resolve_handoff_path()
    except Exception as exc:  # justified: fail-open — a bad path must not break session start
        logger.warning("handoff_readback_path_unresolved", error=str(exc))
        return OpenHandoffDict(status="not_measured", reason="path_unresolved")
    if not target.is_file():
        return OpenHandoffDict(status="absent", path=str(target))
    try:
        content = target.read_text(encoding="utf-8")
    except OSError as exc:  # justified: fail-open — an unreadable file is not zero items
        logger.warning("handoff_readback_unreadable", path=str(target), error=str(exc))
        return OpenHandoffDict(status="not_measured", reason="unreadable", path=str(target))
    span = find_block_span(content)
    if span is None:
        return OpenHandoffDict(status="absent", path=str(target))
    block = content[span[0] : span[1]]
    if len(block.encode("utf-8")) > HANDOFF_BLOCK_MAX_BYTES:
        logger.info("handoff_block_exceeds_cap", path=str(target), cap=HANDOFF_BLOCK_MAX_BYTES)
        return OpenHandoffDict(status="not_measured", reason="block_exceeds_cap", path=str(target))
    today = datetime.now(timezone.utc).date()
    rows = sorted(parse_rows(block), key=lambda r: (r.first_seen, r.run_id, r.gate_id))
    items: list[OpenHandoffItemDict] = [
        OpenHandoffItemDict(
            gate_id=row.gate_id,
            blocking_class=row.blocking_class,
            owner=row.owner,
            run_id=row.run_id,
            reason=row.reason,
            first_seen=row.first_seen,
            age_days=row.age_days(today),
        )
        for row in rows[:HANDOFF_READBACK_MAX_ITEMS]
    ]
    return OpenHandoffDict(status="measured", total=len(rows), items=items, path=str(target))


def step_handoff_readback() -> OpenHandoffDict:
    """The session-start step. Always returns a block, never raises.

    A result is always present — an absent key would be indistinguishable from a
    step that never ran, which is the ambiguity FR03 exists to remove.
    """
    return read_open_handoff()


__all__ = [
    "HANDOFF_BLOCK_MAX_BYTES",
    "HANDOFF_READBACK_MAX_ITEMS",
    "OpenHandoffDict",
    "OpenHandoffItemDict",
    "read_open_handoff",
    "step_handoff_readback",
]
