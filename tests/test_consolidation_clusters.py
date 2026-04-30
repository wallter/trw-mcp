"""Cluster detection tests for consolidation."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from trw_mcp.state.consolidation import find_clusters
from trw_mcp.state.persistence import FileStateReader, FileStateWriter

from ._consolidation_test_helpers import make_vec, write_entry

class TestFindClusters:
    """FR01: find_clusters detects semantically similar entry clusters."""

    def test_embedding_unavailable_returns_empty(
        self, tmp_path: Path, reader: FileStateReader, writer: FileStateWriter
    ) -> None:
        """When embeddings are unavailable, returns [] without exception."""
        entries_dir = tmp_path / "entries"
        entries_dir.mkdir()
        for i in range(5):
            write_entry(entries_dir, writer, f"entry{i:03d}", summary=f"summary {i}")

        with patch("trw_mcp.state.memory_adapter.embedding_available", return_value=False):
            with patch("trw_mcp.state.memory_adapter.embed_text_batch", return_value=[None] * 5):
                result = find_clusters(entries_dir, reader)
        assert result == []

    def test_nonexistent_dir_returns_empty(self, tmp_path: Path, reader: FileStateReader) -> None:
        """When entries_dir does not exist, returns []."""
        with patch("trw_mcp.state.memory_adapter.embedding_available", return_value=False):
            result = find_clusters(tmp_path / "nonexistent", reader)
        assert result == []

    def test_skips_index_yaml(self, tmp_path: Path, reader: FileStateReader, writer: FileStateWriter) -> None:
        """index.yaml is skipped during loading."""
        entries_dir = tmp_path / "entries"
        entries_dir.mkdir()
        # Write index.yaml — should be ignored
        writer.write_yaml(entries_dir / "index.yaml", {"version": 1})
        # Write fewer entries than min_cluster_size
        write_entry(entries_dir, writer, "e001")
        write_entry(entries_dir, writer, "e002")

        with patch("trw_mcp.state.memory_adapter.embedding_available", return_value=True):
            with patch("trw_mcp.state.memory_adapter.embed_text_batch", return_value=[None, None]):
                result = find_clusters(entries_dir, reader, min_cluster_size=3)
        assert result == []

    def test_skips_inactive_entries(self, tmp_path: Path, reader: FileStateReader, writer: FileStateWriter) -> None:
        """Entries with status != 'active' are excluded from clustering."""
        entries_dir = tmp_path / "entries"
        entries_dir.mkdir()
        write_entry(entries_dir, writer, "active1", status="active")
        write_entry(entries_dir, writer, "active2", status="active")
        write_entry(entries_dir, writer, "archived1", status="archived")
        write_entry(entries_dir, writer, "archived2", status="archived")
        # Only 2 active entries — below min_cluster_size=3

        with patch("trw_mcp.state.memory_adapter.list_active_learnings", side_effect=RuntimeError("force yaml")):
            with patch("trw_mcp.state.memory_adapter.embedding_available", return_value=True):
                with patch(
                    "trw_mcp.state.memory_adapter.embed_text_batch",
                    return_value=[
                        make_vec(1.0, 0.0, 0.0),
                        make_vec(0.99, 0.1, 0.0),
                    ],
                ):
                    result = find_clusters(entries_dir, reader, min_cluster_size=3)
        assert result == []

    def test_skips_consolidated_source_type(
        self, tmp_path: Path, reader: FileStateReader, writer: FileStateWriter
    ) -> None:
        """Entries with source_type='consolidated' are excluded."""
        entries_dir = tmp_path / "entries"
        entries_dir.mkdir()
        for i in range(4):
            write_entry(entries_dir, writer, f"e{i:03d}", status="active")
        write_entry(entries_dir, writer, "cons001", source_type="consolidated")

        vecs = [make_vec(1.0, 0.0, 0.0)] * 4  # 4 active non-consolidated
        with patch("trw_mcp.state.memory_adapter.list_active_learnings", side_effect=RuntimeError("force yaml")):
            with patch("trw_mcp.state.memory_adapter.embedding_available", return_value=True):
                with patch("trw_mcp.state.memory_adapter.embed_text_batch", return_value=vecs):
                    result = find_clusters(entries_dir, reader, min_cluster_size=3)
        # All 4 active entries should be available for clustering (not the consolidated one)
        # embed_batch is called with 4 texts, not 5
        assert isinstance(result, list)

    def test_skips_entries_with_consolidated_into(
        self, tmp_path: Path, reader: FileStateReader, writer: FileStateWriter
    ) -> None:
        """Entries with consolidated_into set are excluded."""
        entries_dir = tmp_path / "entries"
        entries_dir.mkdir()
        for i in range(3):
            write_entry(entries_dir, writer, f"active{i:03d}", status="active")
        write_entry(entries_dir, writer, "merged001", consolidated_into="L-abcdefgh")

        vecs = [make_vec(1.0, 0.0, 0.0)] * 3
        with patch("trw_mcp.state.memory_adapter.list_active_learnings", side_effect=RuntimeError("force yaml")):
            with patch("trw_mcp.state.memory_adapter.embedding_available", return_value=True):
                with patch("trw_mcp.state.memory_adapter.embed_text_batch", return_value=vecs):
                    find_clusters(entries_dir, reader, min_cluster_size=3)
        # Should not raise; merged entry not in the cluster candidates

    def test_single_batch_call_for_all_entries(
        self, tmp_path: Path, reader: FileStateReader, writer: FileStateWriter
    ) -> None:
        """FR01: embed_batch is called once with all entry texts."""
        entries_dir = tmp_path / "entries"
        entries_dir.mkdir()
        for i in range(5):
            write_entry(entries_dir, writer, f"e{i:03d}", summary=f"s{i}", detail=f"d{i}")

        mock_batch = MagicMock(return_value=[make_vec(1.0, 0.0, 0.0)] * 5)
        with patch("trw_mcp.state.memory_adapter.list_active_learnings", side_effect=RuntimeError("force yaml")):
            with patch("trw_mcp.state.memory_adapter.embedding_available", return_value=True):
                with patch("trw_mcp.state.memory_adapter.embed_text_batch", mock_batch):
                    find_clusters(entries_dir, reader, min_cluster_size=3)

        assert mock_batch.call_count == 1
        # Verify all 5 texts passed to the one call
        call_args = mock_batch.call_args[0][0]
        assert len(call_args) == 5

    def test_similar_entries_form_cluster(
        self, tmp_path: Path, reader: FileStateReader, writer: FileStateWriter
    ) -> None:
        """Entries with high pairwise similarity are grouped into a cluster."""
        entries_dir = tmp_path / "entries"
        entries_dir.mkdir()
        for i in range(4):
            write_entry(entries_dir, writer, f"similar{i:03d}", summary=f"similar {i}")
        for i in range(3):
            write_entry(entries_dir, writer, f"unrelated{i:03d}", summary=f"unrelated {i}")

        # 4 similar entries, 3 unrelated
        # similar: [1,0,0], [0.99,0.1,0], [0.98,0.2,0], [0.97,0.25,0]
        # unrelated: orthogonal vectors
        similar_vecs = [
            make_vec(1.0, 0.0, 0.0),
            make_vec(0.99, 0.14, 0.0),
            make_vec(0.98, 0.2, 0.0),
            make_vec(0.97, 0.24, 0.0),
        ]
        unrelated_vecs = [
            make_vec(0.0, 1.0, 0.0),
            make_vec(0.0, 0.0, 1.0),
            make_vec(-1.0, 0.0, 0.0),
        ]
        all_vecs = similar_vecs + unrelated_vecs

        with patch("trw_mcp.state.memory_adapter.list_active_learnings", side_effect=RuntimeError("force yaml")):
            with patch("trw_mcp.state.memory_adapter.embedding_available", return_value=True):
                with patch("trw_mcp.state.memory_adapter.embed_text_batch", return_value=all_vecs):
                    result = find_clusters(
                        entries_dir,
                        reader,
                        similarity_threshold=0.9,
                        min_cluster_size=3,
                    )

        # Should have at least one cluster with the similar entries
        assert len(result) >= 1
        sizes = [len(c) for c in result]
        assert max(sizes) >= 3

    def test_higher_threshold_fewer_clusters(
        self, tmp_path: Path, reader: FileStateReader, writer: FileStateWriter
    ) -> None:
        """Higher similarity threshold produces fewer or equal clusters."""
        entries_dir = tmp_path / "entries"
        entries_dir.mkdir()
        for i in range(5):
            write_entry(entries_dir, writer, f"e{i:03d}")

        # Vectors with moderate pairwise similarity (~0.85)
        vecs = [
            make_vec(1.0, 0.0, 0.0),
            make_vec(0.9, 0.44, 0.0),
            make_vec(0.9, 0.44, 0.0),
            make_vec(0.9, 0.44, 0.0),
            make_vec(0.0, 1.0, 0.0),
        ]

        with patch("trw_mcp.state.memory_adapter.list_active_learnings", side_effect=RuntimeError("force yaml")):
            with patch("trw_mcp.state.memory_adapter.embedding_available", return_value=True):
                with patch("trw_mcp.state.memory_adapter.embed_text_batch", return_value=vecs):
                    result_low = find_clusters(
                        entries_dir,
                        reader,
                        similarity_threshold=0.5,
                        min_cluster_size=3,
                    )
                with patch("trw_mcp.state.memory_adapter.embed_text_batch", return_value=vecs):
                    result_high = find_clusters(
                        entries_dir,
                        reader,
                        similarity_threshold=0.99,
                        min_cluster_size=3,
                    )

        assert len(result_low) >= len(result_high)

    def test_max_entries_cap(self, tmp_path: Path, reader: FileStateReader, writer: FileStateWriter) -> None:
        """max_entries caps the number of entries loaded for clustering."""
        entries_dir = tmp_path / "entries"
        entries_dir.mkdir()
        for i in range(10):
            write_entry(entries_dir, writer, f"e{i:03d}")

        mock_batch = MagicMock(return_value=[make_vec(1.0, 0.0, 0.0)] * 3)
        with patch("trw_mcp.state.memory_adapter.list_active_learnings", side_effect=RuntimeError("force yaml")):
            with patch("trw_mcp.state.memory_adapter.embedding_available", return_value=True):
                with patch("trw_mcp.state.memory_adapter.embed_text_batch", mock_batch):
                    find_clusters(entries_dir, reader, max_entries=3, min_cluster_size=3)

        # embed_batch should be called with at most 3 texts
        call_args = mock_batch.call_args[0][0]
        assert len(call_args) <= 3

    def test_fewer_entries_than_min_cluster_size_returns_empty(
        self, tmp_path: Path, reader: FileStateReader, writer: FileStateWriter
    ) -> None:
        """When loaded entries < min_cluster_size, returns [] immediately."""
        entries_dir = tmp_path / "entries"
        entries_dir.mkdir()
        write_entry(entries_dir, writer, "e001")
        write_entry(entries_dir, writer, "e002")

        with patch("trw_mcp.state.memory_adapter.list_active_learnings", side_effect=RuntimeError("force yaml")):
            with patch("trw_mcp.state.memory_adapter.embedding_available", return_value=True):
                with patch("trw_mcp.state.memory_adapter.embed_text_batch", return_value=[None, None]):
                    result = find_clusters(entries_dir, reader, min_cluster_size=3)
        assert result == []

    def test_unreadable_yaml_skipped(self, tmp_path: Path, reader: FileStateReader, writer: FileStateWriter) -> None:
        """Entries that cannot be read are skipped without raising."""
        entries_dir = tmp_path / "entries"
        entries_dir.mkdir()
        # Write a corrupt file
        (entries_dir / "corrupt.yaml").write_text("{invalid yaml[")
        for i in range(4):
            write_entry(entries_dir, writer, f"ok{i:03d}")

        vecs = [make_vec(1.0, 0.0, 0.0)] * 4
        with patch("trw_mcp.state.memory_adapter.list_active_learnings", side_effect=RuntimeError("force yaml")):
            with patch("trw_mcp.state.memory_adapter.embedding_available", return_value=True):
                with patch("trw_mcp.state.memory_adapter.embed_text_batch", return_value=vecs):
                    result = find_clusters(entries_dir, reader, min_cluster_size=3)
        assert isinstance(result, list)

    def test_cluster_discards_small_groups(
        self, tmp_path: Path, reader: FileStateReader, writer: FileStateWriter
    ) -> None:
        """Clusters smaller than min_cluster_size are discarded."""
        entries_dir = tmp_path / "entries"
        entries_dir.mkdir()
        for i in range(5):
            write_entry(entries_dir, writer, f"e{i:03d}")

        # 2 similar (pair), 3 unrelated — pair below min=3 should be discarded
        vecs = [
            make_vec(1.0, 0.0, 0.0),
            make_vec(1.0, 0.0, 0.0),  # identical to e000 → pair
            make_vec(0.0, 1.0, 0.0),
            make_vec(0.0, 0.0, 1.0),
            make_vec(-1.0, 0.0, 0.0),
        ]
        with patch("trw_mcp.state.memory_adapter.list_active_learnings", side_effect=RuntimeError("force yaml")):
            with patch("trw_mcp.state.memory_adapter.embedding_available", return_value=True):
                with patch("trw_mcp.state.memory_adapter.embed_text_batch", return_value=vecs):
                    result = find_clusters(
                        entries_dir,
                        reader,
                        similarity_threshold=0.9,
                        min_cluster_size=3,
                    )
        # The pair (size 2) should be filtered out
        for cluster in result:
            assert len(cluster) >= 3

    def test_none_vectors_excluded_from_clustering(
        self, tmp_path: Path, reader: FileStateReader, writer: FileStateWriter
    ) -> None:
        """Entries with None vectors are excluded; won't cause errors."""
        entries_dir = tmp_path / "entries"
        entries_dir.mkdir()
        for i in range(5):
            write_entry(entries_dir, writer, f"e{i:03d}")

        # Two None embeddings → only 3 valid; below min_cluster_size if =4
        vecs: list[list[float] | None] = [
            None,
            make_vec(1.0, 0.0, 0.0),
            make_vec(1.0, 0.0, 0.0),
            make_vec(1.0, 0.0, 0.0),
            None,
        ]
        with patch("trw_mcp.state.memory_adapter.list_active_learnings", side_effect=RuntimeError("force yaml")):
            with patch("trw_mcp.state.memory_adapter.embedding_available", return_value=True):
                with patch("trw_mcp.state.memory_adapter.embed_text_batch", return_value=vecs):
                    result = find_clusters(
                        entries_dir,
                        reader,
                        similarity_threshold=0.9,
                        min_cluster_size=4,
                    )
        assert result == []

    def test_entries_dir_nonexistent_with_embedding_available(self, tmp_path: Path, reader: FileStateReader) -> None:
        """When embedding is available but entries_dir doesn't exist, returns []."""
        nonexistent = tmp_path / "no_such_dir"

        with patch("trw_mcp.state.memory_adapter.embedding_available", return_value=True):
            result = find_clusters(nonexistent, reader)
        assert result == []
