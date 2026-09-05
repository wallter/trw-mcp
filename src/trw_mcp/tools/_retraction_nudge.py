"""Unretracted-contradiction nudge for the delivery gate (PRD-CORE-244 FR06).

Belongs to the ``_delivery_helpers.py`` facade, which calls
:func:`unretracted_contradiction_nudge` from ``check_delivery_gates``.

``invalidated_by`` was measured non-null on **0 of 9,366 rows** after roughly
four months of daily use. The write path exists and is reachable
(``trw_learn_update(supersedes=...)``); nothing ever asks anyone to use it, so
invalidation depends entirely on unprompted authoring discipline.

The candidate set is derived, not stored: the session-scoped recall receipts
``correlate_recalls`` already reads, intersected with the entries FR04 actually
penalised — which is durably visible as an ``assertion_contradicted`` entry in
``outcome_history``. An entry whose ``invalid_from``/``invalidated_by`` window
has since been closed drops out.

The nudge is ADVISORY and never sets a blocking condition. An assertion can fail
for reasons that are not the learning's fault — an unresolvable project root, a
renamed file — and a blocking gate would convert a false positive into a stopped
delivery, trading a truthfulness gain for a velocity failure of unknown size.
"""

from __future__ import annotations

from pathlib import Path

import structlog

logger = structlog.get_logger(__name__)

__all__ = ["unretracted_contradiction_nudge"]

#: Cap on how many ids the nudge names. Past this the message becomes a wall of
#: text an agent skims, which is the same as not surfacing it at all.
_MAX_NAMED_ENTRIES = 5


def _has_unsettled_contradiction(entry: dict[str, object]) -> bool:
    """True when *entry* recorded a contradiction and was never superseded."""
    if entry.get("invalidated_by") or entry.get("invalid_from"):
        return False
    history = entry.get("outcome_history")
    if not isinstance(history, list):
        return False
    from trw_mcp.scoring._correlation import CONTRADICTION_EVENT_LABEL

    return any(str(item).endswith(f":{CONTRADICTION_EVENT_LABEL}") for item in history)


def unretracted_contradiction_nudge(trw_dir: Path) -> str:
    """Return advisory text naming this session's unretracted contradictions.

    Returns the empty string when the session recorded no contradiction, so a
    delivery with nothing to settle produces a gate result identical to today's.

    Fail-open: any error yields an empty string. A missing advisory is a smaller
    harm than a delivery gate that raises.
    """
    try:
        from trw_mcp.models.config import get_config
        from trw_mcp.scoring._correlation import _default_lookup_entry
        from trw_mcp.scoring._recall_window import correlate_recalls

        config = get_config()
        correlated = correlate_recalls(
            trw_dir,
            config.learning_outcome_correlation_window_minutes,
            scope=config.learning_outcome_correlation_scope,
        )
        if not correlated:
            return ""

        entries_dir = trw_dir / config.learnings_dir / config.entries_dir
        unsettled: list[str] = []
        for learning_id in dict.fromkeys(lid for lid, _discount in correlated):
            _path, data = _default_lookup_entry(learning_id, trw_dir, entries_dir)
            if data is not None and _has_unsettled_contradiction(data):
                unsettled.append(learning_id)

        if not unsettled:
            return ""

        named = unsettled[:_MAX_NAMED_ENTRIES]
        overflow = len(unsettled) - len(named)
        calls = "; ".join(
            f"trw_learn_update(learning_id='{lid}', status='obsolete')  # or supersedes=<new id>" for lid in named
        )
        suffix = f" (+{overflow} more)" if overflow else ""
        logger.info("unretracted_contradiction_nudge", entry_ids=unsettled)
        return (
            f"This session disproved {len(unsettled)} stored learning(s) whose assertions failed "
            f"against the current tree, and none was retracted: {', '.join(named)}{suffix}. "
            f"Settle each one: {calls}"
        )
    except Exception:  # justified: fail-open, an advisory must never block delivery
        logger.debug("retraction_nudge_skipped", exc_info=True)
        return ""
