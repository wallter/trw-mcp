"""Edge-case embedding and store wiring tests for state/memory_adapter.py."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from trw_mcp.state.memory_adapter import (
    embed_text,
    embed_text_batch,
    embedding_available,
    find_entry_by_id,
    store_learning,
)
from ._memory_adapter_edge_support import trw_dir  # noqa: F401


class TestEmbedText:
    def test_returns_none_when_embedder_unavailable(self) -> None:
        """embed_text returns None when get_embedder() returns None."""
        with patch("trw_mcp.state._memory_connection.get_embedder", return_value=None):
            result = embed_text("some text")
            assert result is None

    def test_returns_none_for_empty_string(self) -> None:
        """embed_text returns None for empty/whitespace-only text."""
        mock_embedder = MagicMock()
        with patch(
            "trw_mcp.state._memory_connection.get_embedder",
            return_value=mock_embedder,
        ):
            assert embed_text("") is None
            assert embed_text("   ") is None
            assert embed_text("\t\n") is None
            mock_embedder.embed.assert_not_called()

    def test_returns_vector_on_success(self) -> None:
        """embed_text returns the embedding vector from the provider."""
        mock_embedder = MagicMock()
        expected_vec = [0.1, 0.2, 0.3]
        mock_embedder.embed.return_value = expected_vec
        with patch(
            "trw_mcp.state._memory_connection.get_embedder",
            return_value=mock_embedder,
        ):
            result = embed_text("hello world")
            assert result == expected_vec
            mock_embedder.embed.assert_called_once_with("hello world")

    def test_returns_none_on_os_error(self) -> None:
        """embed_text catches OSError and returns None."""
        mock_embedder = MagicMock()
        mock_embedder.embed.side_effect = OSError("model file missing")
        with patch(
            "trw_mcp.state._memory_connection.get_embedder",
            return_value=mock_embedder,
        ):
            result = embed_text("test input")
            assert result is None

    def test_returns_none_on_value_error(self) -> None:
        """embed_text catches ValueError and returns None."""
        mock_embedder = MagicMock()
        mock_embedder.embed.side_effect = ValueError("bad input shape")
        with patch(
            "trw_mcp.state._memory_connection.get_embedder",
            return_value=mock_embedder,
        ):
            result = embed_text("test input")
            assert result is None

    def test_returns_none_on_runtime_error(self) -> None:
        """embed_text catches RuntimeError and returns None."""
        mock_embedder = MagicMock()
        mock_embedder.embed.side_effect = RuntimeError("inference failed")
        with patch(
            "trw_mcp.state._memory_connection.get_embedder",
            return_value=mock_embedder,
        ):
            result = embed_text("test input")
            assert result is None


class TestEmbedTextBatch:
    def test_empty_input_returns_empty_list(self) -> None:
        """embed_text_batch([]) returns [] without calling embedder."""
        result = embed_text_batch([])
        assert result == []

    def test_returns_none_list_when_embedder_unavailable(self) -> None:
        """embed_text_batch returns [None, None, ...] when embedder is None."""
        with patch("trw_mcp.state._memory_connection.get_embedder", return_value=None):
            result = embed_text_batch(["a", "b", "c"])
            assert result == [None, None, None]

    def test_returns_vectors_on_success(self) -> None:
        """embed_text_batch delegates to embed_text per item."""
        mock_embedder = MagicMock()
        vec1 = [0.1, 0.2]
        vec2 = [0.3, 0.4]
        mock_embedder.embed.side_effect = [vec1, vec2]
        with patch(
            "trw_mcp.state._memory_connection.get_embedder",
            return_value=mock_embedder,
        ):
            result = embed_text_batch(["hello", "world"])
            assert len(result) == 2
            assert isinstance(result, list)

    def test_batch_exception_returns_none_list(self) -> None:
        """embed_text_batch catches exceptions and returns [None, ...]."""
        with patch(
            "trw_mcp.state._memory_connection.get_embedder",
            return_value=MagicMock(),
        ):
            with patch(
                "trw_mcp.state._memory_connection.embed_text",
                side_effect=RuntimeError("batch explosion"),
            ):
                result = embed_text_batch(["a", "b"])
                assert result == [None, None]


class TestEmbeddingAvailable:
    def test_true_when_embedder_exists(self) -> None:
        """embedding_available() returns True when get_embedder returns non-None."""
        with patch(
            "trw_mcp.state._memory_connection.get_embedder",
            return_value=MagicMock(),
        ):
            assert embedding_available() is True

    def test_false_when_embedder_none(self) -> None:
        """embedding_available() returns False when get_embedder returns None."""
        with patch(
            "trw_mcp.state._memory_connection.get_embedder",
            return_value=None,
        ):
            assert embedding_available() is False


class TestStoreLearningTagInference:
    def test_inferred_tags_are_appended(self, trw_dir: Path) -> None:
        """store_learning appends inferred topic tags to user-provided tags."""
        with patch(
            "trw_mcp.state.analytics.infer_topic_tags",
            return_value=["inferred-tag"],
        ):
            store_learning(trw_dir, "L-ti1", "Summary about Python", "d", tags=["user-tag"])

        entry = find_entry_by_id(trw_dir, "L-ti1")
        assert entry is not None
        tags = entry["tags"]
        assert isinstance(tags, list)
        assert "user-tag" in tags
        assert "inferred-tag" in tags

    def test_no_inferred_tags_keeps_original(self, trw_dir: Path) -> None:
        """When infer_topic_tags returns empty, original tags are preserved."""
        with patch(
            "trw_mcp.state.analytics.infer_topic_tags",
            return_value=[],
        ):
            store_learning(trw_dir, "L-ti2", "Summary", "d", tags=["original"])

        entry = find_entry_by_id(trw_dir, "L-ti2")
        assert entry is not None
        assert "original" in entry["tags"]

    def test_none_tags_with_inference(self, trw_dir: Path) -> None:
        """When user provides no tags, inferred tags are the only tags."""
        with patch(
            "trw_mcp.state.analytics.infer_topic_tags",
            return_value=["auto-tag"],
        ):
            store_learning(trw_dir, "L-ti3", "Summary", "d", tags=None)

        entry = find_entry_by_id(trw_dir, "L-ti3")
        assert entry is not None
        assert "auto-tag" in entry["tags"]


class TestStoreLearningEmbedding:
    def test_embed_input_is_summary_plus_detail(self, trw_dir: Path) -> None:
        """store_learning passes 'summary detail' to the embed helper.

        PRD-FIX-COMPOUNDING-2 FR02: store_learning now calls
        ``_embed_and_store_returning`` (which RETURNS the vector so the graph
        scheduler can reuse it) instead of the void ``_embed_and_store``.
        """
        with patch(
            "trw_mcp.state.memory_adapter._embed_and_store_returning",
            return_value=None,
        ) as mock_embed:
            with patch(
                "trw_mcp.state.analytics.infer_topic_tags",
                return_value=[],
            ):
                store_learning(trw_dir, "L-ei1", "My Summary", "My Detail")

            mock_embed.assert_called_once()
            call_args = mock_embed.call_args
            assert call_args[0][1] == "L-ei1"
            assert call_args[0][2] == "My Summary My Detail"
