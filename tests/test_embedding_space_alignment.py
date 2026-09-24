"""trw-mcp follows trw-memory's embedding-model change (bge-small-en-v1.5).

Two contracts, each driven through the production call site with fake
embedders (no model download):

1. the default retrieval model IS trw-memory's default, and user-facing text
   names the configured model;
2. every trw-mcp path that compares vectors hands the store the loaded
   embedder's space, and with no known space nothing is compared. The store
   admits only vectors recorded in that space, so a MiniLM-era vector is never
   compared with a bge one. Query encoding belongs to the daemon's recall.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from structlog.testing import capture_logs

from tests._embedding_space_support import NEW_SPACE, SpaceProvider, stored
from trw_mcp.models.config import TRWConfig
from trw_mcp.state._embedding_space import admitted_vectors
from trw_mcp.state._store_selection import SimilarHit, SimilarWindow
from trw_mcp.state.dedup import _check_duplicate_via_backend
from trw_mcp.tools._recall_impl import _dedup_ranked_learnings

_LOADED = "trw_mcp.state._memory_connection.get_initialized_embedder"


def _excluded_warnings(logs: list[dict[str, object]]) -> list[dict[str, object]]:
    return [e for e in logs if e.get("event") == "dense_vectors_excluded_embedding_space"]


# -- 1. default model --------------------------------------------------------


def test_default_retrieval_model_is_the_trw_memory_default() -> None:
    from trw_memory.embeddings import _DEFAULT_MODEL

    assert TRWConfig().retrieval_embedding_model == _DEFAULT_MODEL == "BAAI/bge-small-en-v1.5"


# -- 2 + 3. semantic recall ---------------------------------------------------


# -- 3. learn-time dedup (KNN over stored vectors) ---------------------------


class _SpaceSpyStore:
    """The store seam: which space trw-mcp asks it to compare in.

    Space filtering itself is the daemon's (trw-memory ``memory_similar`` and
    ``memory_vectors``, pinned in ``test_tools_similar.py`` and
    ``test_tools_recall_support.py``); trw-mcp only hands over the loaded space.
    """

    def __init__(self) -> None:
        self.spaces: list[object] = []

    def similar(self, namespace: str, vector: list[float], space: object, top_k: int) -> SimilarWindow:
        self.spaces.append(space)
        return SimilarWindow(1, [] if space is None else [SimilarHit("L-new", 0.99, True)])

    def vectors(self, ids: list[str], space: object) -> dict[str, list[float]]:
        self.spaces.append(space)
        return {entry_id: [1.0, 0.0] for entry_id in ids}


def _spy_store(monkeypatch: pytest.MonkeyPatch) -> _SpaceSpyStore:
    store = _SpaceSpyStore()
    monkeypatch.setattr("trw_mcp.state._store_selection.selected_store", lambda _trw_dir: (store, "project:t"))
    return store


@pytest.mark.parametrize("loaded", [SpaceProvider(NEW_SPACE), None])
def test_learn_dedup_compares_in_the_loaded_space_or_not_at_all(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, loaded: SpaceProvider | None
) -> None:
    store = _spy_store(monkeypatch)
    with patch(_LOADED, return_value=loaded):
        result = _check_duplicate_via_backend([1.0, 0.0], tmp_path, 0.95, 0.85)

    assert result is not None
    if loaded is None:
        assert store.spaces == [None]
        assert (result.action, result.existing_id, result.similarity) == ("store", None, 0.0)
    else:
        assert store.spaces == [NEW_SPACE]
        assert (result.action, result.existing_id) == ("skip", "L-new")


def _ranked(*ids: str) -> list[dict[str, object]]:
    return [{"id": entry_id, "summary": f"summary {entry_id}"} for entry_id in ids]


def test_recall_dedup_reads_vectors_only_in_the_loaded_space(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = _spy_store(monkeypatch)
    with patch(_LOADED, return_value=SpaceProvider(NEW_SPACE)):
        survivors, collapsed = _dedup_ranked_learnings(tmp_path, _ranked("L-1", "L-2"))
    assert store.spaces == [NEW_SPACE]
    assert ([entry["id"] for entry in survivors], collapsed) == (["L-1"], 1)

    store.spaces.clear()
    with patch(_LOADED, return_value=None), capture_logs() as logs:
        survivors, collapsed = _dedup_ranked_learnings(tmp_path, _ranked("L-1", "L-2"))
    assert store.spaces == []  # no loaded space: nothing is provably comparable
    assert (len(survivors), collapsed) == (2, 0)
    assert not _excluded_warnings(logs)  # no embedder loaded is not a stale store


def test_admitted_vectors_reads_the_namespace_it_was_given() -> None:
    backend = MagicMock()
    backend.get_vector_records.return_value = {"L-1": stored((1.0, 0.0), NEW_SPACE)}

    admitted = admitted_vectors(backend, ["L-1", ""], namespace="team:x", space=NEW_SPACE, surface="t")

    assert admitted == {"L-1": [1.0, 0.0]}
    backend.get_vector_records.assert_called_once_with(["L-1"], namespace="team:x")


# -- 4. update-project never opens the checkout's own memory store -----------


def test_update_project_maintenance_never_opens_checkout_store(tmp_path: Path) -> None:
    """PRD-CORE-298 FR01: no local embedding-space probe on the update-project path.

    A checkout's memory lives in the daemon; `_run_auto_maintenance` must not
    call anything that opens `.trw/memory/memory.db` when embeddings are
    enabled and available. It previously called the now-deleted
    `_embedding_space.stale_space_warning`, which did exactly that.
    """
    from trw_mcp.bootstrap._update_project import _run_auto_maintenance

    store = tmp_path / ".trw" / "memory" / "memory.db"
    store.parent.mkdir(parents=True)
    store.write_bytes(b"SQLite format 3\x00 unmigrated checkout store")
    before = store.read_bytes()

    def refuse_open(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("update-project opened a store")

    result: dict[str, list[str]] = {"updated": [], "warnings": [], "created": [], "preserved": []}
    with (
        patch(
            "trw_mcp.state._memory_connection.check_embeddings_status",
            return_value={"enabled": True, "available": True},
        ) as mock_status,
        patch("trw_mcp.state._memory_connection.get_initialized_embedder", return_value=object()),
        patch("sqlite3.connect", side_effect=refuse_open),
        patch("trw_mcp.state._memory_connection.get_backend", side_effect=refuse_open),
    ):
        _run_auto_maintenance(tmp_path, result)  # type: ignore[arg-type]

    mock_status.assert_called_once()
    assert result["warnings"] == []
    assert store.read_bytes() == before
    assert sorted(p.name for p in store.parent.iterdir()) == ["memory.db"]


# -- platform publishing stays in the platform's fixed space -----------------


def test_publisher_embeds_in_the_platform_space_not_the_retrieval_model() -> None:
    from trw_mcp.telemetry import publisher

    with (
        patch.object(publisher, "get_config", return_value=TRWConfig(embeddings_enabled=True)),
        patch.object(publisher, "_platform_embed", return_value=[0.5]) as platform,
        patch("trw_mcp.state._memory_connection.embed_text") as retrieval,
    ):
        assert publisher.embed("summary detail") == [0.5]

    platform.assert_called_once_with("summary detail")
    retrieval.assert_not_called()


def test_publisher_embedding_refusal_publishes_without_a_vector() -> None:
    from trw_memory.exceptions import LocalOnlyViolationError

    from trw_mcp.telemetry import publisher

    with (
        patch.object(publisher, "get_config", return_value=TRWConfig(embeddings_enabled=True)),
        patch.object(publisher, "_platform_embed", side_effect=LocalOnlyViolationError("not cached")),
        capture_logs() as logs,
    ):
        assert publisher.embed("summary detail") is None
    assert [e["event"] for e in logs] == ["publish_embedding_refused"]


def test_publisher_does_not_embed_when_embeddings_are_disabled() -> None:
    from trw_mcp.telemetry import publisher

    with (
        patch.object(publisher, "get_config", return_value=TRWConfig(embeddings_enabled=False)),
        patch.object(publisher, "_platform_embed") as platform,
    ):
        assert publisher.embed("summary detail") is None
    platform.assert_not_called()
