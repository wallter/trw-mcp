"""Filtering predicate and tag-overlap clustering tests for consolidation."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from trw_mcp.state.consolidation import _is_clusterable, find_clusters
from trw_mcp.state.persistence import FileStateReader, FileStateWriter

from ._consolidation_test_helpers import write_entry

class TestIsClusterable:
    """Direct unit tests for _is_clusterable filtering predicate."""

    def test_active_entry_is_clusterable(self) -> None:
        """A plain active entry with no special fields is clusterable."""
        data: dict[str, object] = {"id": "e1", "status": "active", "summary": "test"}
        assert _is_clusterable(data) is True

    def test_consolidated_source_type_excluded(self) -> None:
        """source_type='consolidated' makes entry non-clusterable."""
        data: dict[str, object] = {"id": "e1", "source_type": "consolidated"}
        assert _is_clusterable(data) is False

    def test_agent_source_type_is_clusterable(self) -> None:
        """source_type='agent' (non-consolidated) is still clusterable."""
        data: dict[str, object] = {"id": "e1", "source_type": "agent"}
        assert _is_clusterable(data) is True

    def test_consolidated_into_set_excluded(self) -> None:
        """Entry with consolidated_into set to a non-None value is excluded."""
        data: dict[str, object] = {"id": "e1", "consolidated_into": "L-abc"}
        assert _is_clusterable(data) is False

    def test_consolidated_into_none_is_clusterable(self) -> None:
        """Entry with consolidated_into=None is clusterable."""
        data: dict[str, object] = {"id": "e1", "consolidated_into": None}
        assert _is_clusterable(data) is True

    def test_missing_source_type_is_clusterable(self) -> None:
        """Entry missing source_type entirely is clusterable."""
        data: dict[str, object] = {"id": "e1"}
        assert _is_clusterable(data) is True

    def test_missing_consolidated_into_is_clusterable(self) -> None:
        """Entry missing consolidated_into entirely is clusterable."""
        data: dict[str, object] = {"id": "e1", "source_type": "agent"}
        assert _is_clusterable(data) is True

    def test_empty_string_source_type_is_clusterable(self) -> None:
        """source_type='' is not 'consolidated', so entry is clusterable."""
        data: dict[str, object] = {"id": "e1", "source_type": ""}
        assert _is_clusterable(data) is True

    def test_both_exclusion_conditions_consolidated_source_type_wins(self) -> None:
        """If both source_type='consolidated' and consolidated_into set, still excluded."""
        data: dict[str, object] = {
            "id": "e1",
            "source_type": "consolidated",
            "consolidated_into": "L-xyz",
        }
        assert _is_clusterable(data) is False

    def test_empty_dict_is_clusterable(self) -> None:
        """Completely empty dict has no exclusion fields, so is clusterable."""
        assert _is_clusterable({}) is True

class TestTagOverlapClusters:
    """PRD-FIX-052-FR03: tag-overlap fallback when embeddings are unavailable."""

    def _write_entry_with_tags(
        self,
        entries_dir: Path,
        writer: FileStateWriter,
        entry_id: str,
        tags: list[str],
        status: str = "active",
    ) -> Path:
        """Write a minimal entry YAML with specific tags."""
        path = entries_dir / f"{entry_id}.yaml"
        writer.write_yaml(
            path,
            {
                "id": entry_id,
                "summary": f"summary for {entry_id}",
                "detail": f"detail for {entry_id}",
                "tags": tags,
                "impact": 0.5,
                "status": status,
                "source_type": "agent",
            },
        )
        return path

    def test_tag_overlap_clusters_basic_5_entries(
        self, tmp_path: Path, reader: FileStateReader, writer: FileStateWriter
    ) -> None:
        """5 entries sharing 2+ tags form 1 cluster when embeddings unavailable."""
        from trw_mcp.state.consolidation import _tag_overlap_clusters

        entries = [
            {"id": f"e{i}", "tags": ["gotcha", "pydantic-v2"], "summary": f"s{i}", "status": "active"} for i in range(5)
        ]
        clusters = _tag_overlap_clusters(entries, min_cluster_size=3, min_shared_tags=2)
        assert len(clusters) == 1
        assert len(clusters[0]) == 5

    def test_tag_overlap_clusters_below_min_size_returns_empty(
        self, tmp_path: Path, reader: FileStateReader, writer: FileStateWriter
    ) -> None:
        """2 entries sharing tags returns [] (below min_cluster_size=3)."""
        from trw_mcp.state.consolidation import _tag_overlap_clusters

        entries = [
            {"id": "e1", "tags": ["gotcha", "pydantic-v2"], "summary": "s1", "status": "active"},
            {"id": "e2", "tags": ["gotcha", "pydantic-v2"], "summary": "s2", "status": "active"},
        ]
        clusters = _tag_overlap_clusters(entries, min_cluster_size=3, min_shared_tags=2)
        assert clusters == []

    def test_tag_overlap_clusters_disjoint_tags_returns_empty(
        self, tmp_path: Path, reader: FileStateReader, writer: FileStateWriter
    ) -> None:
        """Entries with no shared tags return [] even if above min_cluster_size."""
        from trw_mcp.state.consolidation import _tag_overlap_clusters

        entries = [
            {"id": "e1", "tags": ["alpha"], "summary": "s1", "status": "active"},
            {"id": "e2", "tags": ["beta"], "summary": "s2", "status": "active"},
            {"id": "e3", "tags": ["gamma"], "summary": "s3", "status": "active"},
        ]
        clusters = _tag_overlap_clusters(entries, min_cluster_size=2, min_shared_tags=2)
        assert clusters == []

    def test_tag_overlap_single_shared_tag_below_threshold(
        self, tmp_path: Path, reader: FileStateReader, writer: FileStateWriter
    ) -> None:
        """Entries sharing only 1 tag do not cluster when min_shared_tags=2."""
        from trw_mcp.state.consolidation import _tag_overlap_clusters

        entries = [{"id": f"e{i}", "tags": ["gotcha"], "summary": f"s{i}", "status": "active"} for i in range(5)]
        clusters = _tag_overlap_clusters(entries, min_cluster_size=3, min_shared_tags=2)
        assert clusters == []

    def test_find_clusters_uses_tag_fallback_when_embedding_unavailable(
        self, tmp_path: Path, reader: FileStateReader, writer: FileStateWriter
    ) -> None:
        """find_clusters calls tag fallback when embedding_available()=False."""
        entries_dir = tmp_path / "entries"
        entries_dir.mkdir()

        fake_entries = []
        for i in range(5):
            self._write_entry_with_tags(entries_dir, writer, f"e{i:03d}", ["gotcha", "pydantic-v2"])
            fake_entries.append(
                {
                    "id": f"e{i:03d}",
                    "tags": ["gotcha", "pydantic-v2"],
                    "summary": f"s{i}",
                    "status": "active",
                    "source_type": "agent",
                }
            )

        with (
            patch("trw_mcp.state.memory_adapter.embedding_available", return_value=False),
            patch("trw_mcp.state.memory_adapter.list_active_learnings", return_value=fake_entries),
            patch(
                "trw_mcp.state.consolidation._clustering._tag_overlap_clusters",
                wraps=lambda entries, **kw: [],
            ) as mock_tag,
        ):
            find_clusters(entries_dir, reader, min_cluster_size=3)

        mock_tag.assert_called_once()

    def test_find_clusters_tag_fallback_returns_cluster(
        self, tmp_path: Path, reader: FileStateReader, writer: FileStateWriter
    ) -> None:
        """find_clusters returns tag-based clusters when embeddings unavailable."""
        entries_dir = tmp_path / "entries"
        entries_dir.mkdir()

        fake_entries = []
        for i in range(5):
            self._write_entry_with_tags(entries_dir, writer, f"e{i:03d}", ["gotcha", "pydantic-v2", "trw-mcp"])
            fake_entries.append(
                {
                    "id": f"e{i:03d}",
                    "summary": f"summary for e{i:03d}",
                    "detail": f"detail for e{i:03d}",
                    "tags": ["gotcha", "pydantic-v2", "trw-mcp"],
                    "impact": 0.5,
                    "status": "active",
                    "source_type": "agent",
                }
            )

        with (
            patch("trw_mcp.state.memory_adapter.embedding_available", return_value=False),
            patch("trw_mcp.state.memory_adapter.list_active_learnings", return_value=fake_entries),
        ):
            clusters = find_clusters(entries_dir, reader, min_cluster_size=3)

        assert len(clusters) == 1
        assert len(clusters[0]) == 5

    def test_find_clusters_embedding_path_not_regressed(
        self, tmp_path: Path, reader: FileStateReader, writer: FileStateWriter
    ) -> None:
        """When embeddings are available, original embedding path is used (no regression)."""
        entries_dir = tmp_path / "entries"
        entries_dir.mkdir()

        vecs = [[1.0, 0.0], [1.0, 0.0], [1.0, 0.0]]
        for i in range(3):
            self._write_entry_with_tags(entries_dir, writer, f"e{i}", ["gotcha"])

        with (
            patch("trw_mcp.state.memory_adapter.embedding_available", return_value=True),
            patch("trw_mcp.state.memory_adapter.embed_text_batch", return_value=vecs),
            patch("trw_mcp.state.consolidation._clustering._tag_overlap_clusters") as mock_tag_fn,
        ):
            find_clusters(entries_dir, reader, min_cluster_size=3, similarity_threshold=0.5)

        mock_tag_fn.assert_not_called()

    def test_tag_overlap_no_tags_entry_skips_gracefully(
        self, tmp_path: Path, reader: FileStateReader, writer: FileStateWriter
    ) -> None:
        """Entries with no tags are skipped in clustering — no crash."""
        from trw_mcp.state.consolidation import _tag_overlap_clusters

        entries = [
            {"id": "e1", "tags": [], "summary": "s1", "status": "active"},
            {"id": "e2", "tags": ["gotcha", "pydantic-v2"], "summary": "s2", "status": "active"},
            {"id": "e3", "tags": ["gotcha", "pydantic-v2"], "summary": "s3", "status": "active"},
            {"id": "e4", "tags": ["gotcha", "pydantic-v2"], "summary": "s4", "status": "active"},
        ]
        # Should not raise; e1 with empty tags is simply not in any cluster
        clusters = _tag_overlap_clusters(entries, min_cluster_size=3, min_shared_tags=2)
        assert isinstance(clusters, list)
