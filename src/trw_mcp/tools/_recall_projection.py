"""Recall response-field projection — strips internal scoring state.

Belongs to the ``_recall_impl.py`` facade. Re-exported there for back-compat.

``trw_recall`` entries historically carried the FULL stored learning row —
including internal ranking/telemetry state (``outcome_history``,
``q_observations``, access counters, ``combined_score``, …) that is meaningful
to the scoring engine but noise to the calling LLM. Measured 2026-07-12: that
state was ~3x the size of the actual content, inflating a default recall to
~22k tokens while the token budget (which estimates content fields only)
reported ~5.8k.

``strip_internal_response_fields`` removes those keys at the MCP response
boundary only — stored rows, scoring, and access tracking are untouched. The
field set is the ``recall_internal_fields`` config knob; an empty set disables
stripping. Fail-open: malformed entries are returned unchanged.
"""

from __future__ import annotations

import structlog

logger = structlog.get_logger(__name__)


def strip_internal_response_fields(
    entries: list[dict[str, object]],
    internal_fields: frozenset[str],
) -> list[dict[str, object]]:
    """Return copies of *entries* without internal scoring/telemetry keys.

    Applied after ranking/dedup (which may read the internal state) and before
    token budgeting, so the reported ``tokens_used`` reflects what the caller
    actually receives. Fail-open: on any error the original list is returned.
    """
    if not internal_fields:
        return entries
    try:
        return [
            {k: v for k, v in entry.items() if k not in internal_fields} if isinstance(entry, dict) else entry
            for entry in entries
        ]
    except Exception:  # justified: fail-open, projection must never break recall
        logger.debug("recall_projection_failed", exc_info=True)
        return entries
