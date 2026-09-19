"""trw-mcp follows trw-memory's embedding-model change (bge-small-en-v1.5).

Three contracts, each driven through the production call site with fake
embedders (no model download):

1. the default retrieval model IS trw-memory's default, and user-facing text
   names the configured model;
2. recall encodes the QUERY in the query role (``embed_query``), so an
   asymmetric encoder gets its instruction prefix; documents stay on ``embed``;
3. every trw-mcp path that compares a fresh vector with STORED vectors admits
   only vectors recorded in the loaded embedder's space. A MiniLM-era vector is
   never compared with a bge one, and with no known space nothing is compared.
"""

from __future__ import annotations

import math
from pathlib import Path
from unittest.mock import MagicMock, patch

from structlog.testing import capture_logs
from trw_memory.models.memory import MemoryEntry
from trw_memory.storage.sqlite_backend import SQLiteBackend

from tests._embedding_space_support import NEW_SPACE, OLD_SPACE, SpaceProvider, stored
from trw_mcp.models.config import TRWConfig
from trw_mcp.state._embedding_space import REPAIR_COMMAND, admitted_vectors, stale_space_warning
from trw_mcp.state._memory_queries import _search_entries
from trw_mcp.state.dedup import _check_duplicate_via_backend
from trw_mcp.tools._recall_impl import _dedup_ranked_learnings

_LOADED = "trw_mcp.state._memory_connection.get_initialized_embedder"
_BACKEND = "trw_mcp.state.memory_adapter.get_backend"


def _excluded_warnings(logs: list[dict[str, object]]) -> list[dict[str, object]]:
    return [e for e in logs if e.get("event") == "dense_vectors_excluded_embedding_space"]


# -- 1. default model --------------------------------------------------------


def test_default_retrieval_model_is_the_trw_memory_default() -> None:
    from trw_memory.embeddings import _DEFAULT_MODEL

    assert TRWConfig().retrieval_embedding_model == _DEFAULT_MODEL == "BAAI/bge-small-en-v1.5"


# -- 2 + 3. semantic recall ---------------------------------------------------


def test_recall_encodes_the_query_in_the_query_role_and_scores_only_its_space(tmp_path: Path) -> None:
    provider = SpaceProvider(NEW_SPACE, (1.0, 0.0))
    rows = [
        MemoryEntry(id="L-old", content="alpha note", importance=0.5),
        MemoryEntry(id="L-new", content="beta note", importance=0.5),
    ]
    # L-old's vector is IDENTICAL to the query vector but lives in the old
    # model's space; L-new is only moderately similar but shares the space.
    records = {
        "L-old": stored((1.0, 0.0), OLD_SPACE, "alpha note "),
        "L-new": stored((0.6, 0.8), NEW_SPACE, "beta note "),
    }
    backend = SQLiteBackend(tmp_path / "memory.db")
    try:
        for row in rows:
            backend.store(row)
        with (
            patch("trw_mcp.state._memory_connection.get_embedder", return_value=provider),
            patch("trw_mcp.models.config.get_config", return_value=TRWConfig()),
            patch.object(backend, "get_vector_records", return_value=records),
            patch("trw_memory.retrieval.pipeline.hybrid_search") as hybrid,
            capture_logs() as logs,
        ):
            hybrid.return_value = rows[::-1]
            _search_entries(backend, "zzz unrelated words", top_k=2)
    finally:
        backend.close()

    assert provider.query_calls == ["zzz unrelated words"]
    assert "zzz unrelated words" not in provider.document_calls
    dense_input = hybrid.call_args.kwargs["stored_embeddings"]
    assert set(dense_input) == {"L-new"}, "an other-space vector reached dense scoring"
    [warning] = _excluded_warnings(logs)
    assert warning["mismatched_space"] == 1
    assert REPAIR_COMMAND in str(warning["hint"])


# -- 3. learn-time dedup (KNN over stored vectors) ---------------------------


def _dedup_backend(hits: list[tuple[str, float]], records: dict[str, object]) -> MagicMock:
    backend = MagicMock()
    backend.search_vectors.return_value = hits
    backend.get_vector_records.return_value = records
    entry = MagicMock()
    entry.status.value = "active"
    backend.get.return_value = entry
    return backend


def test_learn_dedup_never_matches_an_other_space_neighbour(tmp_path: Path) -> None:
    merge_distance = math.sqrt(2 * (1 - 0.90))  # cosine 0.90: merge zone
    backend = _dedup_backend(
        [("L-old", 0.0), ("L-new", merge_distance)],
        {"L-old": stored((1.0, 0.0), OLD_SPACE), "L-new": stored((0.9, 0.44), NEW_SPACE)},
    )
    with patch(_BACKEND, return_value=backend), patch(_LOADED, return_value=SpaceProvider(NEW_SPACE)):
        result = _check_duplicate_via_backend([1.0, 0.0], tmp_path, 0.95, 0.85)

    # Unfiltered, the distance-0 old-space neighbour would have been a "skip".
    assert result is not None
    assert (result.action, result.existing_id) == ("merge", "L-new")


def test_learn_dedup_compares_nothing_without_a_loaded_space(tmp_path: Path) -> None:
    backend = _dedup_backend([("L-new", 0.0)], {"L-new": stored((1.0, 0.0), NEW_SPACE)})
    with patch(_BACKEND, return_value=backend), patch(_LOADED, return_value=None):
        result = _check_duplicate_via_backend([1.0, 0.0], tmp_path, 0.95, 0.85)

    assert result is not None
    assert (result.action, result.existing_id, result.similarity) == ("store", None, 0.0)
    backend.get_vector_records.assert_not_called()


# -- 3. recall near-duplicate collapse (stored vs stored) ---------------------


def _ranked(*ids: str) -> list[dict[str, object]]:
    return [{"id": entry_id, "summary": f"summary {entry_id}"} for entry_id in ids]


def test_recall_dedup_collapses_only_within_the_loaded_space(tmp_path: Path) -> None:
    backend = MagicMock()
    backend.get_vector_records.return_value = {
        "L-1": stored((1.0, 0.0), NEW_SPACE),
        "L-2": stored((1.0, 0.0), OLD_SPACE),  # identical bytes, other model
        "L-3": stored((1.0, 0.0), NEW_SPACE),  # true near-duplicate of L-1
        "L-4": stored((1.0, 0.0), None),  # written with no recorded space
    }
    with patch(_BACKEND, return_value=backend), patch(_LOADED, return_value=SpaceProvider(NEW_SPACE)):
        survivors, collapsed = _dedup_ranked_learnings(tmp_path, _ranked("L-1", "L-2", "L-3", "L-4"))

    assert [entry["id"] for entry in survivors] == ["L-1", "L-2", "L-4"]
    assert collapsed == 1


def test_recall_dedup_skips_the_cosine_pass_without_a_loaded_space(tmp_path: Path) -> None:
    backend = MagicMock()
    backend.get_vector_records.return_value = {"L-1": stored((1.0, 0.0), NEW_SPACE)}
    with patch(_BACKEND, return_value=backend), patch(_LOADED, return_value=None), capture_logs() as logs:
        survivors, collapsed = _dedup_ranked_learnings(tmp_path, _ranked("L-1", "L-2"))

    assert (len(survivors), collapsed) == (2, 0)
    backend.get_vector_records.assert_not_called()
    # No embedder loaded is not a stale store: no re-embed warning.
    assert not _excluded_warnings(logs)


def test_admitted_vectors_reads_the_namespace_it_was_given() -> None:
    backend = MagicMock()
    backend.get_vector_records.return_value = {"L-1": stored((1.0, 0.0), NEW_SPACE)}

    admitted = admitted_vectors(backend, ["L-1", ""], namespace="team:x", space=NEW_SPACE, surface="t")

    assert admitted == {"L-1": [1.0, 0.0]}
    backend.get_vector_records.assert_called_once_with(["L-1"], namespace="team:x")


# -- 4. re-embed need surfaced by update-project ------------------------------


def test_stale_space_warning_counts_other_space_and_unrecorded_vectors(tmp_path: Path) -> None:
    backend = MagicMock()
    backend.existing_vector_ids.return_value = {"L-1", "L-2", "L-3"}
    backend.get_vector_records.return_value = {
        "L-1": stored((1.0, 0.0), NEW_SPACE),
        "L-2": stored((1.0, 0.0), OLD_SPACE),
        "L-3": stored((1.0, 0.0), None),
    }
    with (
        patch("trw_mcp.state._memory_connection.get_backend", return_value=backend),
        patch(_LOADED, return_value=SpaceProvider(NEW_SPACE)),
    ):
        warning = stale_space_warning(tmp_path)

    assert warning.startswith("2 stored memory vectors")
    assert REPAIR_COMMAND in warning
    backend.existing_vector_ids.assert_called_once_with(namespace="default")


def test_stale_space_warning_is_silent_when_current_or_unmeasurable(tmp_path: Path) -> None:
    backend = MagicMock()
    backend.existing_vector_ids.return_value = {"L-1"}
    backend.get_vector_records.return_value = {"L-1": stored((1.0, 0.0), NEW_SPACE)}
    with patch("trw_mcp.state._memory_connection.get_backend", return_value=backend):
        with patch(_LOADED, return_value=SpaceProvider(NEW_SPACE)):
            assert stale_space_warning(tmp_path) == ""
        with patch(_LOADED, return_value=None):
            assert stale_space_warning(tmp_path) == ""


def test_update_project_maintenance_reports_stale_vectors(tmp_path: Path) -> None:
    from trw_mcp.bootstrap._update_project import _run_auto_maintenance

    (tmp_path / ".trw").mkdir()
    result: dict[str, list[str]] = {"updated": [], "warnings": [], "created": [], "preserved": []}
    with (
        patch(
            "trw_mcp.state._memory_connection.check_embeddings_status",
            return_value={"enabled": True, "available": True},
        ),
        patch("trw_mcp.state._memory_connection.backfill_embeddings", return_value={"embedded": 0}),
        patch("trw_mcp.state._embedding_space.stale_space_warning", return_value="3 stored memory vectors ..."),
    ):
        _run_auto_maintenance(tmp_path, result)  # type: ignore[arg-type]

    assert "3 stored memory vectors ..." in result["warnings"]


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
