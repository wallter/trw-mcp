"""Tests for trw_mcp.telemetry.embeddings — PRD-CORE-033."""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest

import trw_mcp.telemetry.embeddings as emb_module
from trw_mcp.telemetry.embeddings import (
    _EMBEDDING_DIM,
    embed,
    embed_batch,
    embedding_available,
    embedding_dim,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_model(vectors: list[list[float]] | None = None) -> MagicMock:
    """Return a mock SentenceTransformer-like model.

    For embed() the real model returns a 1-D iterable (one vector) from
    encode(text, ...).  For embed_batch() it returns a 2-D iterable
    (list of vectors).  We store the list-of-lists and set side_effect
    so the first positional arg type determines which shape to return:
    a plain str -> first row (1-D), a list -> all rows (2-D).
    """
    model = MagicMock()
    if vectors is None:
        vectors = [_single_vector()]

    def _encode(text_or_texts: object, **kwargs: object) -> list[float] | list[list[float]]:
        if isinstance(text_or_texts, str):
            # Single-text call from embed() — return a flat iterable of floats
            return vectors[0]
        # Batch call from embed_batch() — return list of vectors
        return vectors

    model.encode.side_effect = _encode
    return model


def _single_vector() -> list[float]:
    return [float(i) * 0.001 for i in range(_EMBEDDING_DIM)]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def reset_model_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reset the module-level singletons before every test."""
    monkeypatch.setattr(emb_module, "_model", None)
    monkeypatch.setattr(emb_module, "_provider", None)


# ===========================================================================
# embedding_dim
# ===========================================================================


class TestEmbeddingDim:
    def test_returns_384(self) -> None:
        assert embedding_dim() == 384

    def test_matches_module_constant(self) -> None:
        assert embedding_dim() == _EMBEDDING_DIM


# ===========================================================================
# _load_model — singleton and error handling
# ===========================================================================


class TestLoadModel:
    def test_returns_none_when_import_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        with patch.dict(sys.modules, {"sentence_transformers": None}):
            result = emb_module._load_model()
        assert result is None

    def test_returns_none_on_generic_exception(self, monkeypatch: pytest.MonkeyPatch) -> None:
        mock_st = MagicMock()
        mock_st.SentenceTransformer.side_effect = RuntimeError("GPU OOM")
        with patch.dict(sys.modules, {"sentence_transformers": mock_st}):
            result = emb_module._load_model()
        assert result is None

    def test_caches_model_singleton(self, monkeypatch: pytest.MonkeyPatch) -> None:
        mock_model = _make_model()
        mock_st = MagicMock()
        mock_st.SentenceTransformer.return_value = mock_model

        with patch.dict(sys.modules, {"sentence_transformers": mock_st}):
            first = emb_module._load_model()
            second = emb_module._load_model()

        # Constructor called only once despite two _load_model() calls
        assert mock_st.SentenceTransformer.call_count == 1
        assert first is second

    def test_returns_model_on_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        mock_model = _make_model()
        mock_st = MagicMock()
        mock_st.SentenceTransformer.return_value = mock_model

        with patch.dict(sys.modules, {"sentence_transformers": mock_st}):
            result = emb_module._load_model()

        assert result is mock_model

    def test_pre_cached_model_returned_without_import(self, monkeypatch: pytest.MonkeyPatch) -> None:
        existing_model = _make_model()
        monkeypatch.setattr(emb_module, "_model", existing_model)

        mock_st = MagicMock()
        with patch.dict(sys.modules, {"sentence_transformers": mock_st}):
            result = emb_module._load_model()

        # SentenceTransformer constructor never called — returned cached value
        mock_st.SentenceTransformer.assert_not_called()
        assert result is existing_model


# ===========================================================================
# embed — success paths
# ===========================================================================


class TestEmbedSuccess:
    def test_returns_list_of_floats(self, monkeypatch: pytest.MonkeyPatch) -> None:
        vector = _single_vector()
        mock_model = _make_model([vector])
        monkeypatch.setattr(emb_module, "_load_model", lambda: mock_model)

        result = embed("hello world")

        assert isinstance(result, list)
        assert all(isinstance(v, float) for v in result)

    def test_returns_correct_length(self, monkeypatch: pytest.MonkeyPatch) -> None:
        vector = _single_vector()
        mock_model = _make_model([vector])
        monkeypatch.setattr(emb_module, "_load_model", lambda: mock_model)

        result = embed("test text")

        assert result is not None
        assert len(result) == _EMBEDDING_DIM

    def test_encode_called_with_normalize(self, monkeypatch: pytest.MonkeyPatch) -> None:
        vector = _single_vector()
        mock_model = _make_model([vector])
        monkeypatch.setattr(emb_module, "_load_model", lambda: mock_model)

        embed("my text")

        mock_model.encode.assert_called_once_with("my text", normalize_embeddings=True)

    def test_values_match_model_output(self, monkeypatch: pytest.MonkeyPatch) -> None:
        vector = [float(i) for i in range(_EMBEDDING_DIM)]
        mock_model = _make_model([vector])
        monkeypatch.setattr(emb_module, "_load_model", lambda: mock_model)

        result = embed("check values")

        assert result == vector


# ===========================================================================
# embed — None paths
# ===========================================================================


class TestEmbedNone:
    def test_empty_string_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(emb_module, "_load_model", lambda: _make_model())
        assert embed("") is None

    def test_whitespace_only_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(emb_module, "_load_model", lambda: _make_model())
        assert embed("   ") is None

    def test_tab_only_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(emb_module, "_load_model", lambda: _make_model())
        assert embed("\t\n") is None

    def test_returns_none_when_model_unavailable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(emb_module, "_load_model", lambda: None)
        assert embed("valid text") is None

    def test_returns_none_on_encode_exception(self, monkeypatch: pytest.MonkeyPatch) -> None:
        mock_model = MagicMock()
        mock_model.encode.side_effect = RuntimeError("encode exploded")
        monkeypatch.setattr(emb_module, "_load_model", lambda: mock_model)

        assert embed("text that will fail") is None

    def test_returns_none_when_import_error(self) -> None:
        with patch.dict(sys.modules, {"sentence_transformers": None}):
            result = embed("some text")
        assert result is None


# ===========================================================================
# embed_batch — success paths
# ===========================================================================


class TestEmbedBatchSuccess:
    def test_returns_list_same_length_as_input(self, monkeypatch: pytest.MonkeyPatch) -> None:
        vectors = [_single_vector() for _ in range(3)]
        mock_model = _make_model(vectors)
        monkeypatch.setattr(emb_module, "_load_model", lambda: mock_model)

        result = embed_batch(["a", "b", "c"])

        assert len(result) == 3

    def test_each_entry_is_list_of_floats(self, monkeypatch: pytest.MonkeyPatch) -> None:
        vectors = [_single_vector()]
        mock_model = _make_model(vectors)
        monkeypatch.setattr(emb_module, "_load_model", lambda: mock_model)

        result = embed_batch(["text"])

        assert result[0] is not None
        assert isinstance(result[0], list)
        assert all(isinstance(v, float) for v in result[0])

    def test_encode_called_with_batch_size_32(self, monkeypatch: pytest.MonkeyPatch) -> None:
        vectors = [_single_vector(), _single_vector()]
        mock_model = _make_model(vectors)
        monkeypatch.setattr(emb_module, "_load_model", lambda: mock_model)

        embed_batch(["first", "second"])

        mock_model.encode.assert_called_once_with(
            ["first", "second"],
            normalize_embeddings=True,
            batch_size=32,
        )

    def test_empty_strings_skipped_in_encode_call(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # "b" is the only non-empty text; only one vector returned
        vector = _single_vector()
        mock_model = _make_model([vector])
        monkeypatch.setattr(emb_module, "_load_model", lambda: mock_model)

        result = embed_batch(["", "b", "  "])

        # encode receives only the non-empty text
        mock_model.encode.assert_called_once_with(
            ["b"],
            normalize_embeddings=True,
            batch_size=32,
        )
        assert result[0] is None  # "" → None
        assert result[1] is not None  # "b" → vector
        assert result[2] is None  # "  " → None

    def test_mixed_empty_non_empty_correct_length(self, monkeypatch: pytest.MonkeyPatch) -> None:
        vectors = [_single_vector(), _single_vector()]
        mock_model = _make_model(vectors)
        monkeypatch.setattr(emb_module, "_load_model", lambda: mock_model)

        result = embed_batch(["x", "", "y"])

        assert len(result) == 3
        assert result[0] is not None
        assert result[1] is None
        assert result[2] is not None

    def test_values_match_model_output(self, monkeypatch: pytest.MonkeyPatch) -> None:
        v0 = [float(i) for i in range(_EMBEDDING_DIM)]
        v1 = [float(i) * 2 for i in range(_EMBEDDING_DIM)]
        mock_model = _make_model([v0, v1])
        monkeypatch.setattr(emb_module, "_load_model", lambda: mock_model)

        result = embed_batch(["first", "second"])

        assert result[0] == v0
        assert result[1] == v1


# ===========================================================================
# embed_batch — None / error paths
# ===========================================================================


class TestEmbedBatchNone:
    def test_empty_input_returns_empty_list(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(emb_module, "_load_model", lambda: _make_model())
        assert embed_batch([]) == []

    def test_returns_all_none_when_model_unavailable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(emb_module, "_load_model", lambda: None)
        result = embed_batch(["a", "b", "c"])
        assert result == [None, None, None]

    def test_none_list_length_matches_input(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(emb_module, "_load_model", lambda: None)
        texts = ["x"] * 7
        result = embed_batch(texts)
        assert len(result) == 7
        assert all(v is None for v in result)

    def test_returns_all_none_on_encode_exception(self, monkeypatch: pytest.MonkeyPatch) -> None:
        mock_model = MagicMock()
        mock_model.encode.side_effect = RuntimeError("CUDA OOM")
        monkeypatch.setattr(emb_module, "_load_model", lambda: mock_model)

        result = embed_batch(["a", "b"])

        assert result == [None, None]

    def test_exception_result_length_matches_input(self, monkeypatch: pytest.MonkeyPatch) -> None:
        mock_model = MagicMock()
        mock_model.encode.side_effect = ValueError("bad input")
        monkeypatch.setattr(emb_module, "_load_model", lambda: mock_model)

        result = embed_batch(["x", "y", "z"])

        assert len(result) == 3
        assert all(v is None for v in result)

    def test_all_empty_strings_skips_encode(self, monkeypatch: pytest.MonkeyPatch) -> None:
        mock_model = MagicMock()
        mock_model.encode.return_value = []
        monkeypatch.setattr(emb_module, "_load_model", lambda: mock_model)

        result = embed_batch(["", "  ", "\t"])

        # encode is called with an empty list (no non-empty texts)
        mock_model.encode.assert_called_once_with([], normalize_embeddings=True, batch_size=32)
        assert result == [None, None, None]


# ===========================================================================
# embedding_available
# ===========================================================================


class TestEmbeddingAvailable:
    def test_returns_true_when_model_loads(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(emb_module, "_load_model", lambda: _make_model())
        assert embedding_available() is True

    def test_returns_false_when_model_unavailable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(emb_module, "_load_model", lambda: None)
        assert embedding_available() is False

    def test_returns_false_when_sentence_transformers_missing(self) -> None:
        with patch.dict(sys.modules, {"sentence_transformers": None}):
            result = embedding_available()
        assert result is False

    def test_returns_true_with_mocked_import(self, monkeypatch: pytest.MonkeyPatch) -> None:
        mock_model = _make_model()
        mock_st = MagicMock()
        mock_st.SentenceTransformer.return_value = mock_model
        with patch.dict(sys.modules, {"sentence_transformers": mock_st}):
            result = embedding_available()
        assert result is True
