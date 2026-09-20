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

from collections.abc import Callable, Iterable
from typing import TYPE_CHECKING, Any

import structlog
from trw_memory.embeddings._similarity_calibration import calibrated_threshold
from trw_memory.embeddings._space_gate import SpaceSelection, active_embedding_space, select_space_vectors

from trw_mcp.state._constants import DEFAULT_NAMESPACE

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    from trw_memory.embeddings.provenance import EmbeddingSpace, StoredVector

__all__ = [
    "REPAIR_COMMAND",
    "admit_in_space",
    "admitted_hits",
    "admitted_vectors",
    "comparable_hits",
    "loaded_embedding_space",
    "loaded_space_threshold",
    "space_gated_reader",
    "stale_space_selection",
    "stale_space_warning",
]

logger = structlog.get_logger(__name__)

#: trw-mcp's re-embed command. ``trw-memory reembed`` targets trw-memory's own
#: store and model settings, not a project's ``.trw/memory/memory.db`` under
#: ``retrieval_embedding_model``, so trw-mcp surfaces name this one.
REPAIR_COMMAND = "trw-mcp update-project --repair-embeddings 1000"


def admit_in_space(
    records: Mapping[str, StoredVector], space: EmbeddingSpace | None, *, namespace: str, surface: str
) -> dict[str, list[float]]:
    """trw-memory's ``admit_space_vectors``, with trw-mcp's re-embed command.

    Same selection (``select_space_vectors``) and the same structured
    ``dense_vectors_excluded_embedding_space`` warning; only the hint differs,
    because trw-memory's names a command that does not reach a trw-mcp store.
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
            hint=f"run `{REPAIR_COMMAND}` from the project (repeat with --embedding-after) to re-encode them",
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


def admitted_hits(
    backend: Any, hits: list[tuple[str, float]], *, namespace: str, surface: str
) -> list[tuple[str, float]]:
    """KNN *hits* whose stored vector is in the loaded embedder's space."""
    admitted = admitted_vectors(
        backend,
        (entry_id for entry_id, _ in hits),
        namespace=namespace,
        space=loaded_embedding_space(),
        surface=surface,
    )
    return [hit for hit in hits if hit[0] in admitted]


def comparable_hits(
    backend: Any, hits: list[tuple[str, float]], *, namespace: str, surface: str
) -> list[tuple[str, float]] | None:
    """Admitted KNN *hits*, or ``None`` when a dense verdict over them would be incomplete.

    With a space loaded, an excluded hit (other space or unknown provenance) has a
    meaningless distance and may be a textual duplicate or hide loaded-space rows
    past the window; and a single-space window proves nothing about the rows past
    it unless the backend's census shows the WHOLE namespace in the loaded space
    (full identity). The census reads provenance claims, not blob hashes; an
    unsupported census (``None`` or not a mapping) is unproven. ``None`` tells the
    caller to decide exhaustively. With no loaded space nothing is comparable and
    the (empty) admitted list is returned, as before.
    """
    admitted = admitted_hits(backend, hits, namespace=namespace, surface=surface)
    space = loaded_embedding_space()
    if space is None:
        return admitted
    census_of = getattr(backend, "vector_space_census", None)
    census = census_of(namespace=namespace) if callable(census_of) else None
    if len(admitted) < len(hits) or not _census_proves(census, space, rows=len(hits)):
        logger.debug("dense_window_incomplete", surface=surface, window=len(hits), admitted=len(admitted))
        return None
    return admitted


def _census_proves(census: object, space: EmbeddingSpace, *, rows: int) -> bool:
    """A census proves one space only if it is a mapping of positive int counts, all
    keyed by *space*, that accounts for at least the *rows* the window returned. An
    empty census beside a nonempty window, or any invalid count, proves nothing."""
    if not isinstance(census, dict) or not census:
        return False
    counts = list(census.values())
    if not all(type(count) is int and count > 0 for count in counts):
        return False
    return all(key == space for key in census) and sum(counts) >= rows


def space_gated_reader(
    backend: Any, *, surface: str, namespace: str = DEFAULT_NAMESPACE
) -> Callable[[list[str]], dict[str, list[float]]]:
    """An ``ids -> vectors`` reader that returns only loaded-space vectors."""

    def read(entry_ids: list[str]) -> dict[str, list[float]]:
        return admitted_vectors(
            backend, entry_ids, namespace=namespace, space=loaded_embedding_space(), surface=surface
        )

    return read


def stale_space_selection(backend: Any, embedder: object, *, namespace: str) -> SpaceSelection | None:
    """Count *namespace*'s stored vectors outside *embedder*'s space.

    ``None`` when the space or the vector records cannot be read: an
    unmeasured store is not reported as current.
    """
    space = active_embedding_space(embedder)
    reader = getattr(backend, "get_vector_records", None)
    if space is None or not callable(reader):
        return None
    ids = sorted(backend.existing_vector_ids(namespace=namespace))
    return select_space_vectors(reader(ids, namespace=namespace) if ids else {}, space)


def stale_space_warning(trw_dir: Path) -> str:
    """A re-embed warning when stored vectors predate the configured model.

    Empty when every vector is current or when the store cannot be measured
    (no loaded embedder or no vector records); the latter is logged.
    """
    from trw_mcp.state import _memory_connection as conn

    embedder = conn.get_initialized_embedder()
    selection = (
        stale_space_selection(conn.get_backend(trw_dir), embedder, namespace=DEFAULT_NAMESPACE)
        if embedder is not None
        else None
    )
    if selection is None:
        logger.info("embedding_space_unmeasured", reason="no_loaded_embedder_or_vector_records")
        return ""
    if not selection.excluded:
        return ""
    return (
        f"{selection.excluded} stored memory vectors were encoded by another embedding model "
        f"(or record none) and are skipped by semantic recall and dedup; run `{REPAIR_COMMAND}` "
        "(repeat with the printed --embedding-after cursor) to re-embed them"
    )
