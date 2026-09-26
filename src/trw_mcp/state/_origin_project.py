"""Where a learning came from, and what that means for a project-scoped surface.

PRD-CORE-278 FR08/FR09. On 2026-09-16 this repository's store held 1,343 rows, of
which 1,330 had been pulled from other projects by team sync. Session-start
recall, the ``trw://learnings/summary`` resource, ``trw_code``'s hint mode and the
nudge line all presented them as THIS repository's knowledge — a path-keyed hint
for ``models/config/_loader.py`` returned five learnings about a different
codebase, and the session's single most prominent nudge quoted a claim about a
repository the operator was not working in (learning L-XIhp).

The predicate here is deliberately narrow, and narrow in a specific direction:

- A row with no sync provenance was written in THIS store, so it is attributable.
- A row that arrived through team or company sync was authored in another
  installation's store. Its ``origin_project`` records what the server said —
  and the server currently says nothing, so the value is ``unknown``. Unknown is
  not this project.

It does NOT compare against an installation id. ``resolve_installation_id`` falls
back to a hash of the checkout path, so the same repository on a second machine
would classify its own knowledge as foreign; an identity that is not portable
cannot decide a portability question. When the backend sends a stable project
identity, :func:`origin_project` is where the comparison goes.

Nothing here filters. ``demote_unattributable`` is a stable PARTITION, so a
project whose own store is thin still sees the rest of the world — just after its
own knowledge, and never in the one slot that speaks with the framework's voice.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, TypeVar

import structlog

logger = structlog.get_logger(__name__)

#: Metadata key carrying the project a synced row was authored in.
ORIGIN_PROJECT_KEY = "origin_project"

#: Recorded when the payload does not name one. Never inferred from a namespace,
#: a tag or the content: a guess written into durable metadata is indistinguishable
#: from a fact the next reader will trust.
UNKNOWN_ORIGIN_PROJECT = "unknown"

#: ``source`` / ``source_identity`` values that mean "this row arrived from
#: another installation" (see ``trw_mcp.sync.pull``).
_SYNCED_SOURCES: frozenset[str] = frozenset({"team_sync", "company_sync"})

#: Local id prefix minted by ``SyncPuller._local_team_learning_id``. Checked as a
#: second signal because a row can lose its ``source`` through a re-store while
#: keeping the id it was merged under.
_SYNCED_ID_PREFIX = "team-sync-"

_Row = TypeVar("_Row", bound=Mapping[str, Any])


def _metadata(row: Mapping[str, Any]) -> Mapping[str, Any]:
    raw = row.get("metadata")
    return raw if isinstance(raw, Mapping) else {}


def origin_project(row: Mapping[str, Any]) -> str:
    """Return the recorded origin project, or ``""`` for a locally written row.

    A synced row with no recorded origin reports :data:`UNKNOWN_ORIGIN_PROJECT`
    rather than ``""``: "we do not know where this came from" and "this was
    written here" are different facts and must not collapse into one.
    """
    recorded = str(_metadata(row).get(ORIGIN_PROJECT_KEY, "")).strip()
    if recorded:
        return recorded
    return UNKNOWN_ORIGIN_PROJECT if _is_synced(row) else ""


def _is_synced(row: Mapping[str, Any]) -> bool:
    for field in ("source", "source_type", "source_identity", "client_profile"):
        if str(row.get(field, "")).strip() in _SYNCED_SOURCES:
            return True
    return str(row.get("id", "")).startswith(_SYNCED_ID_PREFIX)


def is_attributable_to_this_project(row: Mapping[str, Any]) -> bool:
    """Return whether *row* is this project's own knowledge.

    Fails OPEN (NFR05): a row that cannot be classified is treated as
    attributable, so a provenance bug can never empty a session-start payload.
    """
    try:
        return origin_project(row) == ""
    except Exception:  # justified: fail-open, provenance must never break a surface
        logger.debug("origin_project_classification_failed", exc_info=True)
        return True


def demote_unattributable(rows: Sequence[_Row]) -> list[_Row]:
    """Partition *rows*: this project's own first, everything else after.

    Stable within each half, so an upstream ranking survives. NOT a filter —
    cross-project knowledge is still reachable, it just stops outranking the
    knowledge that is actually about the checkout the caller is working in.

    Callers must over-fetch BEFORE calling this: partitioning after a cap cannot
    recover a local row the cap already excluded (FR09).
    """
    try:
        local = [row for row in rows if is_attributable_to_this_project(row)]
        if len(local) == len(rows):
            return list(rows)
        foreign = [row for row in rows if not is_attributable_to_this_project(row)]
        return [*local, *foreign]
    except Exception:  # justified: fail-open, ordering is an improvement, not a gate
        logger.debug("origin_project_partition_failed", exc_info=True)
        return list(rows)


def is_verified(row: Mapping[str, Any]) -> bool:
    """Return whether *row* carries a present-tense verified verdict."""
    return str(row.get("verification_status", "")).strip() == "verified"


def nudge_eligible_pool(candidates: Sequence[_Row]) -> list[_Row]:
    """Narrow a nudge pool to the most defensible tier present (FR09).

    The nudge is the single most prominent thing a session is told, so the pool
    is narrowed BEFORE selection — a sort cannot constrain the contextual
    selection that runs after it.

    Precedence, stated because the two rules can conflict (a local unverified
    candidate against a foreign verified one):

    1. attribution — if any candidate is this project's, the pool is those;
    2. verification — if any remaining candidate is ``verified``, the pool is those.

    Attribution outranks verification because a verified claim about a different
    repository is still a claim about a different repository. Returns the input
    unchanged when neither rule can narrow it, so the surface never goes silent.
    """
    if not candidates:
        return list(candidates)
    pool = [row for row in candidates if is_attributable_to_this_project(row)] or list(candidates)
    return [row for row in pool if is_verified(row)] or pool


__all__ = [
    "ORIGIN_PROJECT_KEY",
    "UNKNOWN_ORIGIN_PROJECT",
    "demote_unattributable",
    "is_attributable_to_this_project",
    "is_verified",
    "nudge_eligible_pool",
    "origin_project",
]
