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


#: How many of the most recently updated entries the derived half samples.
#: PRD-FIX-141-FR02: this was ONE root, which is a very thin basis for a verdict
#: the fail-closed gate escalates at severity error — a single isolated newest
#: entry reported the whole graph dead. The derivation is documented
#: single-root only (a 25-root batch measured 912 ms against 195 ms for the same
#: work looped), so the sample is widened by looping a handful rather than by
#: batching, and :data:`RELATION_SAMPLE_BASIS` states the basis in the advisory
#: so the verdict never reads as a census it is not.
RELATION_SAMPLE_ROOTS: int = 3

#: Human-readable description of what a False answer actually rests on.
RELATION_SAMPLE_BASIS: str = (
    "no materialised edge in this namespace and no derived tag relation "
    f"for its {RELATION_SAMPLE_ROOTS} most recently updated entries"
)


def graph_has_relations(
    conn: sqlite3.Connection,
    *,
    namespace: str,
    config: MemoryConfig,
) -> bool:
    """Return whether *namespace* holds at least one graph relation.

    Answers with the materialised half first because it is a single indexed
    existence check, SCOPED to *namespace* (PRD-FIX-141-FR02: it used to accept
    any edge anywhere in the file, so one other namespace's edge concealed an
    empty project graph). A store whose ``memory_graph_edges`` predates the
    namespace column keeps the unscoped check — that is a schema-version branch,
    not a swallowed failure. Only when no edge exists does it pay for the
    derived half, and then for at most :data:`RELATION_SAMPLE_ROOTS` roots: the
    most recently updated entries.

    The sample is BOUNDED, not exhaustive: a False answer means
    :data:`RELATION_SAMPLE_BASIS`, and every caller renders that basis rather
    than claiming a census. Probing the newest entries asks the question a
    health probe actually means — "is the graph being maintained for what we are
    writing now?" — rather than scanning a whole namespace to answer it. The cost
    is a handful of bounded queries whose per-root p50 is budgeted at 15 ms on a
    10,000-entry namespace (PRD-CORE-245 NFR01).

    Args:
        conn: The connection serving *namespace* (the MCP singleton's own, or a
            short-lived probe connection over the same file).
        namespace: The namespace to inspect.
        config: Supplies the derivation's typed bounds
            (``graph_tag_min_shared_tags``, ``graph_tag_max_tag_postings``,
            ``graph_tag_derive_top_k``).

    Returns:
        True when a materialised edge exists in *namespace*, or when one of the
        sampled roots derives at least one tag neighbour. False ONLY when the
        store was read and the bounded sample found no relation — never when it
        could not be read, and never as a claim that the namespace holds none. False when the namespace
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
    edge_columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(memory_graph_edges)")}
    if "namespace" in edge_columns:
        materialised = conn.execute(
            "SELECT 1 FROM memory_graph_edges WHERE namespace = ? LIMIT 1", (namespace,)
        ).fetchone()
    else:
        materialised = conn.execute("SELECT 1 FROM memory_graph_edges LIMIT 1").fetchone()
    if materialised is not None:
        return True
    roots = conn.execute(
        "SELECT id FROM memories WHERE namespace = ? ORDER BY updated_at DESC, id DESC LIMIT ?",
        (namespace, RELATION_SAMPLE_ROOTS),
    ).fetchall()
    if not roots:
        return False
    for row in roots:
        root_id = str(row[0])
        neighbours = derive_tag_neighbours(conn, root_id, namespace=namespace, config=config)
        logger.debug(
            "graph_relation_probe",
            namespace=namespace,
            root_id=root_id,
            derived_neighbours=len(neighbours),
        )
        if neighbours:
            return True
    return False
