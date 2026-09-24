"""Embedding-space gate for trw-mcp's dense comparisons.

A cosine between two vectors means something only when one encoder produced
both. trw-memory records the embedding space of every vector it stores and
scores only vectors from the active embedder's space
(``trw_memory.embeddings._space_gate``). trw-mcp reads the same stored vectors
directly (learn-time dedup, recall dedup), so it applies that same gate here
instead of re-deriving it: after a model change (all-MiniLM-L6-v2 ->
bge-small-en-v1.5) an old-space vector is never compared with a new-space one.

Fail closed: with no known active space, nothing is admitted. The active space
comes from the embedder that is already loaded; nothing here cold-loads a model.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING, Any

import structlog
from trw_memory.embeddings._similarity_calibration import calibrated_threshold
from trw_memory.embeddings._space_gate import active_embedding_space, select_space_vectors

if TYPE_CHECKING:
    from collections.abc import Mapping

    from trw_memory.embeddings.provenance import EmbeddingSpace, StoredVector

__all__ = [
    "REEMBED_STATUS",
    "admit_in_space",
    "admitted_vectors",
    "loaded_embedding_space",
    "loaded_space_threshold",
]

logger = structlog.get_logger(__name__)

#: What happens to a vector outside the active space. The in-process re-embed pass
#: was deleted with PRD-CORE-298 FR01 (a checkout's rows live in the daemon); the
#: daemon-side pass is a post-6.0 follow-up, so no surface may name a remedy yet.
REEMBED_STATUS = "nothing in trw-mcp re-encodes them in this release, so they rank by keyword only"


def admit_in_space(
    records: Mapping[str, StoredVector], space: EmbeddingSpace | None, *, namespace: str, surface: str
) -> dict[str, list[float]]:
    """trw-memory's ``admit_space_vectors``, with trw-mcp's own hint.

    Same selection (``select_space_vectors``) and the same structured
    ``dense_vectors_excluded_embedding_space`` warning; only the hint differs,
    because trw-memory's names a command that does not reach a trw-mcp checkout.
    """
    selection = select_space_vectors(records, space)
    if selection.excluded:
        logger.warning(
            "dense_vectors_excluded_embedding_space",
            surface=surface,
            namespace=namespace,
            excluded=selection.excluded,
            mismatched_space=selection.mismatched,
            no_provenance=selection.unqualified,
            admitted=len(selection.vectors),
            active_space=space.encoding if space is not None else None,
            reembed_required=True,
            detail="excluded vectors are not dense-compared",
            hint=REEMBED_STATUS,
        )
    return selection.vectors


def loaded_embedding_space() -> EmbeddingSpace | None:
    """Space of the already-initialized memory embedder, else ``None``."""
    from trw_mcp.state import _memory_connection as conn

    embedder = conn.get_initialized_embedder()
    return active_embedding_space(embedder) if embedder is not None else None


def loaded_space_threshold(threshold: float) -> float:
    """*threshold* (reference-encoder scale) in the loaded embedder's cosine scale.

    trw-memory's ``calibrated_threshold`` owns the per-model map; with no loaded
    embedder, or one the map does not know, the value is returned unchanged.
    """
    from trw_mcp.state import _memory_connection as conn

    return calibrated_threshold(threshold, conn.get_initialized_embedder())


def admitted_vectors(
    backend: Any,
    entry_ids: Iterable[str],
    *,
    namespace: str,
    space: EmbeddingSpace | None,
    surface: str,
) -> dict[str, list[float]]:
    """Stored vectors of *entry_ids* that were encoded in *space*.

    Excluded vectors are reported by one structured
    ``dense_vectors_excluded_embedding_space`` warning. With no active space
    (no embedder loaded) nothing can be proven comparable, so nothing is read.
    """
    ids = [entry_id for entry_id in entry_ids if entry_id]
    reader = getattr(backend, "get_vector_records", None)
    if space is None or not ids or not callable(reader):
        logger.debug("dense_comparison_skipped", surface=surface, reason="no_active_space_or_reader")
        return {}  # trw:intentional fail-closed: no provable space means no dense comparison
    return admit_in_space(reader(ids, namespace=namespace), space, namespace=namespace, surface=surface)
