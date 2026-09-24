"""trw_learn's dense dedup verdict over a migrated checkout matches the in-process verdict it replaced.

Before PRD-CORE-280 FR01 slice c4 the KNN ran in trw-mcp against its own SQLite
file; it now runs in the daemon (``memory_similar``) behind ``DaemonMemoryStore``.
Each case seeds real rows and space-recorded vectors into the store file a daemon
of its own then serves, and asserts the verdict the in-process path gave for the same window: skip at or
above the skip threshold (any status), merge only into an active row in the
merge zone, store below it, and ``None`` -- the YAML scan decides -- for an empty
window or one whose neighbours are not all provably in the query's space. With
no loaded space nothing is comparable, which over a nonempty window is a store.
"""

from __future__ import annotations

import math
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
from trw_memory.daemon import DaemonPaths
from trw_memory.embeddings.provenance import EmbeddingSpace, VectorProvenance
from trw_memory.integrations._backend import create_backend_from_config
from trw_memory.models.config import MemoryConfig
from trw_memory.models.memory import MemoryEntry, MemoryStatus
from trw_memory.storage.interface import StorageBackend

from tests._memory_daemon import running_daemon
from tests._memory_fixtures import DaemonCheckout, MemoryDaemon, attach_checkout
from tests._path_isolation import set_current_root
from trw_mcp.models.config import reload_config
from trw_mcp.state import _daemon_store
from trw_mcp.state.dedup import DedupResult, _check_duplicate_via_backend

pytestmark = pytest.mark.integration

_SKIP, _MERGE = 0.95, 0.85


@dataclass(frozen=True)
class Seeded:
    """A checkout, and a writer on the store file its daemon will serve once the test has seeded it."""

    backend: StorageBackend
    space: EmbeddingSpace
    dim: int
    trw_dir: Path
    namespace: str
    user_dir: Path


def _no_autostart(_paths: DaemonPaths) -> None:
    raise AssertionError("a test tried to auto-start a memory daemon")


@pytest.fixture
def seeded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Seeded]:
    """The daemon owns its store, so the test writes the file before any daemon serves it.

    Seeding the session daemon's live file from this process made two processes write
    one WAL store; below SQLite 3.51.3 (the Linux CI image) that is the WAL-reset bug,
    the daemon's next open fails ``quick_check`` and quarantines the store for every
    later test on the worker. ``_verdict`` starts this test's own daemon over the file.
    """
    user_dir = tmp_path / "daemon-user"
    monkeypatch.setenv("TRW_USER_DIR", str(user_dir))
    monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path / "repo"))
    monkeypatch.delenv("TRW_PROJECT_NAMESPACE", raising=False)
    monkeypatch.setattr(_daemon_store, "_clients", {})
    monkeypatch.setattr("trw_memory.daemon.client.start_daemon_detached", _no_autostart)
    paths = DaemonPaths.resolve()
    paths.user_memory_dir.mkdir(parents=True, exist_ok=True)
    trw_dir = tmp_path / "repo" / ".trw"
    namespace, _client = attach_checkout(trw_dir, MemoryDaemon(paths, user_dir))
    set_current_root(trw_dir.parent)
    reload_config()
    backend = create_backend_from_config(MemoryConfig(), namespace, db_path_override=paths.store)
    dim = backend._dim  # type: ignore[attr-defined]
    space = EmbeddingSpace("b" * 64, "trw-declared-encoder-v1:parity", dim)
    try:
        yield Seeded(backend, space, dim, trw_dir, namespace, user_dir)
    finally:
        backend.close()
        reload_config()


def _at(similarity: float, dim: int) -> list[float]:
    """A unit vector at cosine *similarity* to the first axis."""
    vector = [0.0] * dim
    vector[0], vector[1] = similarity, math.sqrt(max(0.0, 1.0 - similarity * similarity))
    return vector


def _seed(
    backend: StorageBackend,
    namespace: str,
    entry_id: str,
    vector: list[float],
    space: EmbeddingSpace,
    *,
    status: MemoryStatus = MemoryStatus.ACTIVE,
    with_row: bool = True,
) -> None:
    # Ids recur across tests: the session daemon holds each test's namespace in one file.
    if with_row:
        backend.store(MemoryEntry(id=entry_id, content=f"row {entry_id}", namespace=namespace, status=status))
    backend.upsert_vector(
        entry_id, vector, namespace=namespace, provenance=VectorProvenance.for_vector(space, entry_id, vector)
    )


def _verdict(seeded: Seeded, space: EmbeddingSpace | None) -> DedupResult | None:
    """Close the seeding writer, start the daemon over the file, and ask it for the verdict."""
    seeded.backend.close()
    with running_daemon(seeded.user_dir), pytest.MonkeyPatch.context() as patch:
        patch.setattr("trw_mcp.state.dedup.loaded_embedding_space", lambda: space)
        return _check_duplicate_via_backend(_at(1.0, seeded.dim), seeded.trw_dir, _SKIP, _MERGE)


@pytest.mark.parametrize(
    ("similarity", "status", "action", "matched"),
    [
        (1.0, MemoryStatus.ACTIVE, "skip", True),
        (1.0, MemoryStatus.OBSOLETE, "skip", True),
        (0.9, MemoryStatus.ACTIVE, "merge", True),
        (0.9, MemoryStatus.OBSOLETE, "store", False),
        (0.5, MemoryStatus.ACTIVE, "store", False),
    ],
)
def test_one_comparable_neighbour_gives_the_in_process_verdict(
    seeded: Seeded,
    similarity: float,
    status: MemoryStatus,
    action: str,
    matched: bool,
) -> None:
    _seed(seeded.backend, seeded.namespace, "L-near", _at(similarity, seeded.dim), seeded.space, status=status)

    result = _verdict(seeded, seeded.space)

    assert result is not None
    assert (result.action, result.existing_id) == (
        action,
        "L-near" if matched else None,
    )
    assert result.similarity == pytest.approx(similarity, abs=1e-3)


def test_an_empty_window_leaves_the_verdict_to_the_yaml_scan(seeded: Seeded) -> None:
    assert _verdict(seeded, seeded.space) is None


def test_a_neighbour_in_another_space_makes_the_window_incomplete(seeded: Seeded) -> None:
    other = EmbeddingSpace("c" * 64, "trw-declared-encoder-v1:other", seeded.dim)
    _seed(seeded.backend, seeded.namespace, "L-same", _at(0.5, seeded.dim), seeded.space)
    _seed(seeded.backend, seeded.namespace, "L-other", _at(1.0, seeded.dim), other)

    assert _verdict(seeded, seeded.space) is None


def test_a_vector_whose_row_is_gone_is_not_in_the_window(seeded: Seeded) -> None:
    """The KNN joins to the rows, so an orphaned vector leaves the window empty, as it did in-process."""
    _seed(seeded.backend, seeded.namespace, "L-orphan", _at(1.0, seeded.dim), seeded.space, with_row=False)

    assert _verdict(seeded, seeded.space) is None


def test_no_loaded_space_over_a_nonempty_window_is_a_store(seeded: Seeded) -> None:
    """Nothing is provably comparable, and a nonempty window means the YAML scan is not needed either."""
    _seed(seeded.backend, seeded.namespace, "L-near", _at(1.0, seeded.dim), seeded.space)

    assert _verdict(seeded, None) == DedupResult("store", None, 0.0)


def test_the_best_of_several_neighbours_decides(seeded: Seeded) -> None:
    _seed(seeded.backend, seeded.namespace, "L-far", _at(0.5, seeded.dim), seeded.space)
    _seed(seeded.backend, seeded.namespace, "L-merge", _at(0.9, seeded.dim), seeded.space)

    result = _verdict(seeded, seeded.space)

    assert result is not None
    assert (result.action, result.existing_id) == ("merge", "L-merge")


def test_an_unreachable_daemon_leaves_the_verdict_to_the_yaml_scan(
    daemon_checkout: DaemonCheckout, monkeypatch: pytest.MonkeyPatch
) -> None:
    from trw_mcp.state._store_selection import StoreUnavailableError

    def _unreachable(_trw_dir: object) -> None:
        raise StoreUnavailableError("the memory daemon is not answering; run `trw-mcp doctor`")

    monkeypatch.setattr("trw_mcp.state._store_selection.selected_store", _unreachable)

    assert _check_duplicate_via_backend([1.0], daemon_checkout.trw_dir, _SKIP, _MERGE) is None
