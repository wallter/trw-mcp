"""Where this checkout's memory lives -- the one resolver (PRD-CORE-280 FR01).

``selected_store(trw_dir)`` is the only way trw-mcp reaches a memory store. Its
callers see a :class:`MemoryStore` and never learn whether the rows sit in this
checkout's own ``memory.db`` or in another process.

* A MIGRATED checkout (``project_namespace`` pinned by ``memory migrate``) gets
  the daemon store of PRD-CORE-298 FR01 (``_daemon_store``). It fails closed
  when the daemon or the grant is missing: it never falls back to the file.
* An UNPINNED checkout fails closed too: trw-mcp no longer opens a checkout's
  own ``memory.db``. The error names ``trw-mcp memory migrate --to user`` when
  that file holds learnings (``holds_rows``), else ``update-project`` (FR06).

Protocol methods arrive with their first caller: the four sync methods with
PRD-CORE-298 FR01, ``recall`` with the recall seam once PRD-CORE-294 FR01-FR03 land.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, NamedTuple, Protocol, cast

from typing_extensions import TypedDict

if TYPE_CHECKING:
    from trw_memory.lifecycle.correction import LearningPatch
    from trw_memory.lifecycle.dedup import DedupResult
    from trw_memory.lifecycle.verification_pass import MaintainVerifySummary, VerifySettings
    from trw_memory.models.memory import Anchor, Assertion, Confidence, MemoryEntry, MemoryType, ProtectionTier
    from trw_memory.sync import AdmissionOutcome

    from trw_mcp.state._recall_admission import RecallAdmission


class StoreUnavailableError(RuntimeError):
    """The checkout's store cannot be reached; memory tools fail closed."""


class StoreRequest(TypedDict, total=False):
    """The ``memory_store`` arguments beyond content and namespace -- the daemon tool's shape too."""

    tags: list[str]
    importance: float
    detail: str
    metadata: dict[str, str]
    source: Literal["human", "agent", "tool", "consolidated", "team_sync", "company_sync"]
    source_identity: str
    session_id: str | None
    entry_id: str
    evidence: list[str]
    expires: str
    assertions: list[Assertion]
    client_profile: str
    model_id: str
    type: MemoryType
    nudge_line: str
    confidence: Confidence
    task_type: str
    domain: list[str]
    phase_origin: str
    phase_affinity: list[str]
    team_origin: str
    protection_tier: ProtectionTier
    anchors: list[Anchor]
    anchor_validity: float | None


class NamespaceHealth(TypedDict):
    """``memory_status``'s ``health`` block (``trw_memory.storage._namespace_health``).

    ``entries``/``synced`` exclude canaries; ``synced`` rows came from team sync.
    ``has_relations`` counts derived tag relations over a bounded sample of the
    newest roots. ``embedded`` is ``None`` when the store keeps no vectors.
    """

    entries: int
    synced: int
    edges: int
    has_relations: bool
    embedded: int | None
    max_recall_count: int


class VectorCoverage(TypedDict):
    """``memory_status``'s ``coverage`` block (PRD-CORE-302 C3, FR07): where *namespace*'s vectors stand.

    ``outside_active_space`` counts stored vectors dense recall refuses (other
    space or no provenance), what ``memory_reembed`` re-encodes. It and the space
    counts are ``None`` until the daemon has loaded its model.
    """

    active_space: int | None
    other_space: int | None
    unknown_provenance: int
    outside_active_space: int | None
    no_vector: int


class EmbedderStatus(TypedDict, total=False):
    """``memory_status``'s ``embedder`` block (PRD-CORE-302 C3): whether the store can encode.

    ``reason`` names why not (``model_not_cached``, ``embedder_error``) and ``fix``
    the command that repairs it; ``space`` is ``None`` until the model is loaded.
    Reading it never loads the model.
    """

    available: bool
    model: str
    revision: str
    space: dict[str, object] | None
    loaded: bool
    reason: str | None
    fix: str


@dataclass(frozen=True)
class RecallSpec:
    """One recall over a store: a query, or the ids of an ``ids=`` fetch (PRD-CORE-280 FR01).

    Every row a store returns has passed *admission*, the one policy search and
    the by-id fetch share (PRD-CORE-294 FR01).
    """

    admission: RecallAdmission
    query: str = "*"
    ids: tuple[str, ...] = ()
    tags: list[str] | None = None
    min_impact: float = 0.0
    top_k: int = 25
    include_user: bool = True


class MemoryStore(Protocol):
    """Every memory operation trw-mcp performs. One contract test covers each implementation."""

    def put(self, summary: str, namespace: str, request: StoreRequest) -> dict[str, object]:
        """Store one entry; answers in the ``memory_store_impl`` status vocabulary."""
        ...

    def get(self, entry_id: str) -> MemoryEntry | None:
        """The entry with *entry_id* in any namespace this checkout owns, or ``None``."""
        ...

    def correct(self, learning_id: str, patch: LearningPatch) -> dict[str, str]:
        """Apply *patch* to *learning_id* in the namespace that owns it (PRD-CORE-294 FR03).

        Answers in the ``memory_update`` vocabulary: ``updated``, ``no_changes``,
        ``invalid`` or ``not_found``.
        """
        ...

    def find_duplicate(self, namespace: str, summary: str, detail: str) -> str | None:
        """Id of an ACTIVE row of *namespace* whose summary and detail match exactly, or ``None``."""
        ...

    def count(self, namespace: str) -> int:
        """How many rows *namespace* holds (PRD-CORE-280 FR05: doctor and export read counts, never a raw backend)."""
        ...

    def health(self, namespace: str) -> NamespaceHealth:
        """The store's own measure of *namespace*: what pipeline health and the inventory surfaces read."""
        ...

    def embedder_status(self, namespace: str) -> EmbedderStatus:
        """Whether the store can encode text, read without loading a model (PRD-CORE-302 FR05)."""
        ...

    def coverage(self, namespace: str) -> VectorCoverage | None:
        """Where *namespace*'s vectors stand against the active space, or ``None`` without a census."""
        ...

    def reembed(self, namespace: str) -> dict[str, object]:
        """Re-encode *namespace*'s vectors outside the active space: counts, or why nothing was (FR07)."""
        ...

    def list_entries(
        self, namespace: str, *, status: str | None = None, tags: list[str] | None = None, limit: int
    ) -> list[MemoryEntry]:
        """Up to *limit* rows of *namespace*, newest first; *status* and every tag in *tags* filter the query."""
        ...

    def page_dirty(self, namespace: str, limit: int) -> list[MemoryEntry]:
        """The oldest *limit* rows of *namespace* not yet pushed, in ``sync_seq`` order."""
        ...

    def mark_synced(self, namespace: str, pushed: list[MemoryEntry]) -> int:
        """Record the *pushed* rows of *namespace* as synced; returns how many were marked.

        A row counts only while its ``sync_seq`` still equals the pushed entry's: one
        edited after it was paged stays dirty for the next push.
        """
        ...

    def find_synced(self, namespace: str, remote_id: str, ids: list[str]) -> MemoryEntry | None:
        """The row of *namespace* a pulled learning maps to: its ``remote_id`` or one of *ids*."""
        ...

    def apply_synced(self, namespace: str, entry: MemoryEntry, *, synced: bool = True) -> tuple[str, str]:
        """Write a merged pulled row through the write gate, left synced unless *synced* is false.

        A row that merges local content the server lacks passes ``synced=False`` so the
        next push carries it. Returns ``(status, reason)``: ``stored``, ``quarantined``, or ``blocked``
        with the refusal reason.
        """
        ...

    def recall(self, spec: RecallSpec) -> list[MemoryEntry]:
        """The admitted rows *spec* selects: the project namespace, then ``user:local``.

        A query returns search hits, the project's first. ``spec.ids`` returns the
        one row that represents each id, in request order; a missing id and a row
        admission refuses are both simply absent.
        """
        ...

    def admit_shared(self, results: list[dict[str, object]]) -> AdmissionOutcome:
        """Run shared results fetched from the platform through the project store's admission gate.

        A refused result is quarantined there and counted, never returned.
        """
        ...

    def vectors(self, ids: list[str]) -> VectorSet | None:
        """The project rows *ids*' vectors in the store's active space, with its collapse threshold.

        ``None`` when the store has no embedder (PRD-CORE-302 C2): recall then collapses exact content only.
        """
        ...

    def verify(self, namespace: str, project_root: Path | None, settings: VerifySettings) -> MaintainVerifySummary:
        """Re-verify *namespace*'s assertion/anchor rows against the files under *project_root*."""
        ...

    def assertion_health(self, namespace: str, stale_days: int) -> dict[str, int] | None:
        """*namespace*'s cached assertion verdicts counted by state; ``None`` when no active row carries one."""
        ...

    def record_surfaced(self, ids: list[str], *, session_start: bool = False) -> None:
        """Count the rows *ids* this checkout showed as accessed, and as surfaced when *session_start*.

        Each id counts once, in the namespace that owns it: the project row wins over a ``user:local`` twin.
        """
        ...

    def graph_backfill(
        self, namespace: str, after: dict[str, str] | None, limit: int, deadline_seconds: float | None
    ) -> dict[str, Any]:
        """Graph one page of *namespace*'s existing rows listed after the *after* cursor (F5-B sweep).

        ``{"processed", "edges_built", "skipped", "failed", "next": cursor | None, "complete": bool}``.
        """
        ...

    def graph_related(
        self, namespace: str, learning_id: str, depth: int, edge_types: list[str] | None, limit: int
    ) -> tuple[list[dict[str, Any]], bool]:
        """Up to *limit* active graph neighbours of *learning_id* in *namespace*, and whether more exist.

        Each is the row's fields plus ``edge_type``, ``weight`` and ``depth``.
        """
        ...

    def maintain(self, namespace: str, consolidation: dict[str, object]) -> dict[str, Any]:
        """Run the store's maintenance for *namespace*: ``{"status", "passes": {"decay": {...}, ...}}``.

        *consolidation* is this project's policy for the consolidation pass (``enabled``,
        ``similarity_threshold``, ``min_cluster``, ``max_per_cycle``); the store's own
        config serves every project, so the policy travels with the request.
        """
        ...

    def similar(
        self, namespace: str, text: str, skip_threshold: float, merge_threshold: float, top_k: int
    ) -> DedupResult | None:
        """The store's skip/merge/store verdict for a new learning's *text* against reference-scale thresholds.

        ``None`` means no semantic verdict can be had -- no embedder, or empty text -- and the
        caller stores after its exact-content check (PRD-CORE-302 C4). Any other refusal raises.
        """
        ...


class VectorSet(NamedTuple):
    """Stored vectors in one embedding space, with that space's recall collapse threshold (one answer)."""

    vectors: dict[str, list[float]]
    dup_threshold: float


def dedup_answer(answer: Mapping[str, object]) -> DedupResult | None:
    """A ``memory_similar`` answer as a verdict; ``None`` when unavailable or the text is empty; else raise."""
    from trw_memory.lifecycle.dedup import DedupResult

    status = answer.get("status")
    if status == "ok":
        existing = answer["existing_id"]
        action = cast("Literal['skip', 'merge', 'store']", answer["action"])
        return DedupResult(
            action, str(existing) if existing is not None else None, float(cast("float", answer["similarity"]))
        )
    if status == "unavailable" or (status == "invalid" and answer.get("code") == "empty_text"):
        return None
    raise ValueError(f"memory_similar refused: {answer.get('error') or answer}")


#: False while a caller only measures (pipeline health, store counts): an unpinned
#: checkout is then refused on its config alone, and its memory.db is never opened.
_explaining_unpinned: ContextVar[bool] = ContextVar("explaining_unpinned", default=True)


@contextmanager
def measuring_only() -> Iterator[None]:
    """Within this block ``selected_store`` refuses an unpinned checkout without reading its memory.db."""
    token = _explaining_unpinned.set(False)
    try:
        yield
    finally:
        _explaining_unpinned.reset(token)


def selected_store(trw_dir: Path) -> tuple[MemoryStore, str]:
    """This checkout's store and its project namespace.

    The pin is read from *trw_dir*'s own config cascade, never the process
    singleton: a process serving two roots must not route one by the other's pin.
    An unpinned checkout fails closed; outside :func:`measuring_only` its error
    reads the checkout's memory.db (read-only) to tell a migration from an update.
    """
    from trw_mcp.models.config import TRWConfig
    from trw_mcp.models.config._loader import resolve_config_overrides

    pinned = TRWConfig(**resolve_config_overrides(trw_dir / "config.yaml")).project_namespace  # type: ignore[arg-type]
    if not pinned:
        if not _explaining_unpinned.get():
            raise StoreUnavailableError(f"{trw_dir.parent} has no project_namespace; run `trw-mcp doctor`")
        from trw_mcp.state._store_migration import holds_rows

        if holds_rows(trw_dir / "memory" / "memory.db"):
            raise StoreUnavailableError(
                f"{trw_dir.parent} keeps its learnings in its own memory.db, which trw-mcp no longer reads; "
                "run `trw-mcp memory migrate --to user` (preview first, then --apply)"
            )
        # Nothing to move (PRD-CORE-280 FR06): update-project pins the namespace and mints the grant.
        raise StoreUnavailableError(f"{trw_dir.parent} has no project_namespace; run `trw-mcp update-project`")
    from trw_mcp.state._daemon_store import daemon_store_for

    return daemon_store_for(trw_dir, pinned), pinned
