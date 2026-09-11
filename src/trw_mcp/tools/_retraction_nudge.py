"""Unretracted-contradiction nudge for the delivery gate (PRD-CORE-244 FR06).

Belongs to the ``_delivery_helpers.py`` facade, which calls
:func:`unretracted_contradiction_nudge` from ``check_delivery_gates``.

``invalidated_by`` was measured non-null on **0 of 9,366 rows** after roughly
four months of daily use. The write path exists and is reachable
(``trw_learn_update(supersedes=...)``); nothing ever asks anyone to use it, so
invalidation depends entirely on unprompted authoring discipline.

The candidate set is derived from existing session-scoped recall receipts and
valid dated unresolved assertion observations. Historical Q outcome labels alone
are not evidence. Invalidation or supersession settles the advisory.

The nudge is ADVISORY and never sets a blocking condition. An assertion can fail
for reasons that are not the learning's fault — an unresolvable project root, a
renamed file — and a blocking gate would convert a false positive into a stopped
delivery, trading a truthfulness gain for a velocity failure of unknown size.
"""

from __future__ import annotations

from pathlib import Path

import structlog

logger = structlog.get_logger(__name__)

__all__ = ["fresh_contradiction_ids", "unretracted_contradiction_nudge", "unsettled_contradiction_ids"]

#: Cap on how many ids the nudge names. Past this the message becomes a wall of
#: text an agent skims, which is the same as not surfacing it at all.
_MAX_NAMED_ENTRIES = 5


def _has_unsettled_contradiction(entry: dict[str, object], *, ttl_seconds: float = 0.0) -> bool:
    """True when *entry* recorded a contradiction and was never superseded.

    ``ttl_seconds=0`` (the default, and what FR06's advisory uses) means "any age":
    ``_observation`` only computes freshness when a positive TTL is supplied, so an
    assertion that failed months ago still counts. That is right for an advisory —
    an unsettled contradiction does not stop mattering because it got old.

    A positive ``ttl_seconds`` additionally requires the failure to be FRESH, and
    that is what FR04's reward path passes. See :func:`fresh_contradiction_ids`.
    """
    if entry.get("invalidated_by") or entry.get("invalid_from"):
        return False
    from trw_mcp.tools._stored_claim_evidence import stored_claim_evidence

    evidence, failure_fraction = stored_claim_evidence(entry, ttl_seconds=ttl_seconds)
    if failure_fraction <= 0:
        return False
    if ttl_seconds <= 0:
        return True
    assertions = evidence.get("assertions")
    rows = assertions if isinstance(assertions, list) else []
    return any(row.get("observation") == "failure" and row.get("freshness") == "fresh" for row in rows)


def unsettled_contradiction_ids(trw_dir: Path, *, ttl_seconds: float = 0.0) -> list[str]:
    """Learnings recalled this session whose stored assertions failed and that
    were never superseded.

    Split out from the nudge text (2026-09-11) because this list is the durable
    verdict and has two consumers, not one: FR06 names the entries for a human,
    and FR04 applies the negative reward to them. Computing it twice would mean
    two traversals that could disagree; formatting it in the same function that
    computes it is what left FR04 with no caller at all.

    Fail-open: any error yields an empty list, matching the nudge's contract that
    an advisory must never raise inside the delivery gate.
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
            return []

        entries_dir = trw_dir / config.learnings_dir / config.entries_dir
        unsettled: list[str] = []
        for learning_id in dict.fromkeys(lid for lid, _discount in correlated):
            _path, data = _default_lookup_entry(learning_id, trw_dir, entries_dir)
            if data is not None and _has_unsettled_contradiction(data, ttl_seconds=ttl_seconds):
                unsettled.append(learning_id)
        return unsettled
    except Exception:  # trw-fail-silent-allow: fail-open by the same contract as the nudge -- neither an advisory nor a reward signal may raise inside the delivery gate; an empty list means "nothing to settle", which is the pre-FR04 behaviour
        logger.debug("unsettled_contradiction_ids_skipped", exc_info=True)
        return []


def fresh_contradiction_ids(trw_dir: Path) -> list[str]:
    """Contradicted entries whose failing assertion was observed RECENTLY (FR04).

    Separate from :func:`unsettled_contradiction_ids` because the two consumers need
    different evidence bars, and collapsing them was a real defect (found in
    pre-release review, 2026-09-11).

    FR06 writes an advisory: naming a months-old unsettled contradiction is useful,
    and the message explicitly tells the reader that current-tree truth is unknown.
    FR04 applies an IRREVERSIBLE negative reward. Driving that from an observation
    the evidence layer itself stamps ``current_tree_verified: False`` means an entry
    keeps being penalised every single day -- the cooldown is one UTC day, but the
    stale observation never expires -- until somebody retracts it. Q is clamped to
    [0, 1], so it converges to 0, utility falls under the delete threshold, and
    ``learning_auto_prune_on_deliver`` (default True) nominates the entry obsolete.
    The more often a memory is recalled, the faster that happens, which inverts the
    whole point of the signal. And FR06's own text asks the user NOT to retract
    until they re-verify, so following the advice is what sustains the decay.

    Bounding it on ``verification_cache_ttl_seconds`` means one verification event
    can produce at most one penalty, which is what FR04's docstring already claims
    its cooldown delivers: one broken assertion is ONE fact about the claim.
    """
    from trw_mcp.models.config import get_config

    return unsettled_contradiction_ids(trw_dir, ttl_seconds=float(get_config().verification_cache_ttl_seconds))


def unretracted_contradiction_nudge(trw_dir: Path) -> str:
    """Return advisory text naming this session's unretracted contradictions.

    Returns the empty string when the session recorded no contradiction, so a
    delivery with nothing to settle produces a gate result identical to today's.

    Fail-open: any error yields an empty string. A missing advisory is a smaller
    harm than a delivery gate that raises.
    """
    try:
        unsettled = unsettled_contradiction_ids(trw_dir)
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
            f"Last-known dated assertion failures remain unresolved for {len(unsettled)} recalled learning(s): "
            f"{', '.join(named)}{suffix}. Current-tree truth is unknown; refresh evidence before deciding to retract. "
            f"Settle each one: {calls}"
        )
    except Exception:  # justified: fail-open, an advisory must never block delivery
        logger.debug("retraction_nudge_skipped", exc_info=True)
        return ""
