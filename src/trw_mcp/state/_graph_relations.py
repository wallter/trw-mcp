"""Does this corpus hold any knowledge-graph relation at all?

PRD-CORE-245 FR07 split the graph in two. Materialised ``memory_graph_edges``
rows still carry the semantic edge types (similarity, consolidation,
co-anchored, cross-validation), but tag co-occurrence — 95.96% of the reference
store's 102,428 edges — is no longer stored. It is DERIVED at query time from
the ``memory_tags`` inverted index by
:func:`trw_memory.retrieval.tag_derivation.derive_tag_neighbours`.

Every health probe that answered "is the graph wired?" with
``SELECT COUNT(*) FROM memory_graph_edges`` therefore became wrong the day that
landed: a corpus whose entries relate to each other purely by shared tags — the
common case, and the only case for a store with no embeddings — reads zero
edges while its graph is entirely healthy. The probes would have fired their
"knowledge graph empty" advisory on every session, forever, and sent the agent
to a backfill that can build nothing.

This module is the one place that knows the graph now has two halves.
"""

from __future__ import annotations

import sqlite3

import structlog
from trw_memory.models.config import MemoryConfig
from trw_memory.retrieval.tag_derivation import derive_tag_neighbours

logger = structlog.get_logger(__name__)


def graph_has_relations(
    conn: sqlite3.Connection,
    *,
    namespace: str,
    config: MemoryConfig,
) -> bool:
    """Return whether *namespace* holds at least one graph relation.

    Answers with the materialised half first because it is a single indexed
    existence check. Only when no edge exists at all does it pay for the derived
    half, and then for exactly ONE root: the most recently updated entry.

    A single root is deliberate. The derivation is documented single-root only
    (a 25-root batch measured 912 ms against 195 ms for the same work looped),
    and probing the newest entry asks the question a health probe actually
    means — "is the graph being maintained for what we are writing now?" —
    rather than scanning a whole namespace to answer it. The cost is one bounded
    query whose p50 is budgeted at 15 ms on a 10,000-entry namespace
    (PRD-CORE-245 NFR01).

    Args:
        conn: The connection serving *namespace* (the MCP singleton's own, or a
            short-lived probe connection over the same file).
        namespace: The namespace to inspect.
        config: Supplies the derivation's typed bounds
            (``graph_tag_min_shared_tags``, ``graph_tag_max_tag_postings``,
            ``graph_tag_derive_top_k``).

    Returns:
        True when a materialised edge exists, or when the newest entry derives
        at least one tag neighbour. False ONLY when the store was read and holds
        no relation — never when it could not be read. False when the namespace
        is empty, and False for the derived half on a store that predates schema 5 — the derivation
        already degrades to an empty list there, and a store with no
        ``memory_tags`` index has no derived relation to find, so the
        materialised answer stands alone exactly as it did before FR07.

    Raises:
        sqlite3.Error: propagated from EITHER query. A probe that cannot read
            ``memory_graph_edges`` cannot answer at all, and neither can one
            that cannot read ``memories`` — the root query used to swallow its
            failure and return ``False``, which is the same value a genuinely
            empty namespace produces, so a store that could not be read was
            reported to the operator as "knowledge graph empty". Both callers
            already own a fail-open wrapper that records the degradation; give
            them the exception rather than a fabricated verdict.
    """
    if conn.execute("SELECT 1 FROM memory_graph_edges LIMIT 1").fetchone() is not None:
        return True
    row = conn.execute(
        "SELECT id FROM memories WHERE namespace = ? ORDER BY updated_at DESC, id DESC LIMIT 1",
        (namespace,),
    ).fetchone()
    if row is None:
        return False
    root_id = str(row[0])
    neighbours = derive_tag_neighbours(conn, root_id, namespace=namespace, config=config)
    logger.debug(
        "graph_relation_probe",
        namespace=namespace,
        root_id=root_id,
        derived_neighbours=len(neighbours),
    )
    return bool(neighbours)
