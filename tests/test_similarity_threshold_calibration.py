"""trw-mcp's cosine thresholds are compared in the loaded embedder's scale.

Configured thresholds are on the all-MiniLM-L6-v2 reference scale; trw-memory's
``calibrated_threshold`` maps them to a measured model's scale. Each test drives
a production call site with stub vectors and a stub loaded embedder, and checks
the verdict flips with the embedder's model, not only the helper's return value.
"""

from __future__ import annotations

import math
from pathlib import Path
from unittest.mock import patch

from trw_memory.embeddings.provenance import EmbeddingSpace

from tests._dedup_test_support import write_entry
from tests._memory_store_fake import FakeMemoryStore
from trw_mcp.models.config import TRWConfig
from trw_mcp.state.persistence import FileStateReader, FileStateWriter

BGE = "BAAI/bge-small-en-v1.5"
MINILM = "all-MiniLM-L6-v2"
_LOADED = "trw_mcp.state._memory_connection.get_initialized_embedder"
_SPACE = EmbeddingSpace("c" * 64, "test-encoder:c", 4)


class _Loaded:
    def __init__(self, model_name: str) -> None:
        self.model_name = model_name


def _pair(similarity: float) -> tuple[list[float], list[float]]:
    return [1.0, 0.0], [similarity, math.sqrt(1 - similarity**2)]


def test_loaded_space_threshold_follows_the_loaded_model() -> None:
    from trw_mcp.state._embedding_space import loaded_space_threshold

    with patch(_LOADED, return_value=None):
        assert loaded_space_threshold(0.85) == 0.85
    with patch(_LOADED, return_value=_Loaded(MINILM)):
        assert loaded_space_threshold(0.85) == 0.85
    with patch(_LOADED, return_value=_Loaded(BGE)):
        assert loaded_space_threshold(0.85) == 0.89
        assert loaded_space_threshold(0.95) == 0.98


def _learn_dedup_action(tmp_path: Path, model_name: str, similarity: float) -> str:
    from trw_mcp.state.dedup import dedup_verdict

    entries_dir = tmp_path / "entries"
    entries_dir.mkdir(parents=True)
    write_entry(entries_dir, FileStateWriter(), "L-old", "old", "")
    new, old = _pair(similarity)
    with (
        patch(_LOADED, return_value=_Loaded(model_name)),
        patch("trw_mcp.state.dedup.embed", side_effect=lambda text: new if text.startswith("new") else old),
        patch("trw_mcp.state.dedup._check_duplicate_via_backend", return_value=None),
        patch("trw_mcp.state.dedup._check_exact_content_duplicate", return_value=None),
    ):
        return dedup_verdict("new", "", entries_dir, FileStateReader(), config=TRWConfig()).action


def test_learn_dedup_merges_by_the_loaded_models_scale(tmp_path: Path) -> None:
    assert _learn_dedup_action(tmp_path / "minilm", MINILM, 0.87) == "merge"
    assert _learn_dedup_action(tmp_path / "bge-distinct", BGE, 0.87) == "store"
    assert _learn_dedup_action(tmp_path / "bge-paraphrase", BGE, 0.93) == "merge"
    assert _learn_dedup_action(tmp_path / "bge-copy", BGE, 0.99) == "skip"


def test_recall_dedup_collapses_by_the_loaded_models_scale(tmp_path: Path) -> None:
    from trw_mcp.tools._recall_impl import _dedup_ranked_learnings

    first, second = _pair(0.92)
    ranked: list[dict[str, object]] = [{"id": "a", "summary": "a"}, {"id": "b", "summary": "b"}]

    store = FakeMemoryStore()
    store.stored_vectors = {"a": first, "b": second}

    def _survivors(model_name: str) -> list[object]:
        with (
            patch(_LOADED, return_value=_Loaded(model_name)),
            patch("trw_mcp.state._embedding_space.loaded_embedding_space", return_value=_SPACE),
            patch("trw_mcp.state._store_selection.selected_store", return_value=(store, "default")),
        ):
            kept, _ = _dedup_ranked_learnings(tmp_path, ranked)
        return [entry["id"] for entry in kept]

    assert _survivors(MINILM) == ["a"]  # 0.92 >= 0.90: a MiniLM near-duplicate
    assert _survivors(BGE) == ["a", "b"]  # below bge's 0.945 equivalent


def test_skill_duplicates_calibrate_cosines_but_not_the_jaccard_fallback() -> None:
    from trw_mcp.scoring._skill_contribution import find_duplicate_skills

    first, second = _pair(0.87)

    class _Embedder(_Loaded):
        def embed(self, text: str) -> list[float]:
            return first if text == "alpha" else second

    def _flags(model_name: str) -> int:
        with patch("trw_memory.embeddings.get_local_embedder", return_value=_Embedder(model_name)):
            return len(find_duplicate_skills({"s1": "alpha", "s2": "beta"}, threshold=0.85))

    assert _flags(MINILM) == 1
    assert _flags(BGE) == 0
    with patch("trw_memory.embeddings.get_local_embedder", return_value=None):
        # Jaccard of identical descriptions is 1.0 and stays on the raw threshold.
        assert len(find_duplicate_skills({"s1": "same words", "s2": "same words"}, threshold=0.85)) == 1
