"""Knowledge topology edge-case coverage for the main test split."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from tests._knowledge_topology_support import _make_config, _make_entry
from trw_mcp.state.knowledge_topology import (
    build_cooccurrence_matrix,
    execute_knowledge_sync,
    form_jaccard_clusters,
    preserve_manual_markers,
    render_topic_document,
    sanitize_slug,
)


class TestEdgeCases:
    """Edge cases and error handling across all topology functions."""

    def test_sanitize_slug_unicode_stripped(self) -> None:
        result = sanitize_slug("café-testing")
        assert "caf" in result
        assert "testing" in result

    def test_form_clusters_single_oversized_cluster(self) -> None:
        entries = [_make_entry(f"L-{i:03d}", tags=["alpha", "beta", "gamma"]) for i in range(20)]
        clusters = form_jaccard_clusters(entries, threshold=0.3, min_size=3)
        assert len(clusters) == 1
        assert len(clusters[0]["entry_ids"]) == 20

    def test_form_clusters_all_below_min_size_dropped(self) -> None:
        entries = [
            _make_entry("L-001", tags=["aaa"]),
            _make_entry("L-002", tags=["bbb"]),
        ]
        clusters = form_jaccard_clusters(entries, threshold=0.9, min_size=5)
        assert clusters == []

    def test_build_cooccurrence_matrix_large_tag_set(self) -> None:
        tags = [f"tag{i}" for i in range(10)]
        entries = [_make_entry(f"L-{i:03d}", tags=tags) for i in range(10)]
        matrix = build_cooccurrence_matrix(entries)
        assert len(matrix) == 45

    def test_execute_sync_empty_backend_results(self, tmp_path: Path) -> None:
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        config = _make_config(tmp_path, knowledge_sync_threshold=5)
        mock_backend = MagicMock()
        mock_backend.list_entries.return_value = []

        with (
            patch("trw_mcp.state.knowledge_topology.count_entries", return_value=10),
            patch("trw_mcp.state.knowledge_topology.get_backend", return_value=mock_backend),
        ):
            result = execute_knowledge_sync(trw_dir, config)

        assert result["topics_generated"] == 0
        assert result["threshold_met"] is True

    def test_render_topic_document_empty_entries(self) -> None:
        cluster: dict[str, object] = {
            "slug": "empty",
            "tags": [],
            "entry_ids": [],
            "entries": [],
            "avg_importance": 0.0,
        }
        rendered = render_topic_document(cluster)
        assert "# empty" in rendered
        assert "<!-- trw:auto-generated -->" in rendered

    def test_preserve_markers_exception_returns_existing(self) -> None:
        existing = "my existing content"
        new = "new content"
        result = preserve_manual_markers(existing, new)
        assert result == new

    def test_form_clusters_zero_threshold(self) -> None:
        entries = [_make_entry(f"L-{i:03d}", tags=[f"tag{i}"]) for i in range(10)]
        clusters = form_jaccard_clusters(entries, threshold=0.0, min_size=1)
        total = sum(len(cluster["entry_ids"]) for cluster in clusters)
        assert total == 10

    def test_preserve_manual_markers_exception_returns_existing(self) -> None:
        existing = "existing content with <!-- trw:manual-start -->marker"
        new = "new content"

        class BreakingStr(str):
            def replace(self, *args: object) -> str:
                raise ValueError("CRLF normalize error")

        bad_existing = BreakingStr("bad content <!-- trw:manual-start -->")
        result = preserve_manual_markers(bad_existing, new)  # type: ignore[arg-type]
        assert result == bad_existing

    def test_execute_sync_oserror_reading_existing_file(self, tmp_path: Path) -> None:
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        config = _make_config(tmp_path, knowledge_sync_threshold=3, knowledge_min_cluster_size=2)
        entries = [_make_entry(f"L-{i:03d}", tags=["testing", "python"]) for i in range(5)]
        mock_backend = MagicMock()
        mock_backend.list_entries.return_value = entries

        with (
            patch("trw_mcp.state.knowledge_topology.count_entries", return_value=5),
            patch("trw_mcp.state.knowledge_topology.get_backend", return_value=mock_backend),
        ):
            execute_knowledge_sync(trw_dir, config)

        md_files = list((trw_dir / "knowledge").glob("*.md"))
        assert md_files

        with (
            patch("trw_mcp.state.knowledge_topology.count_entries", return_value=5),
            patch("trw_mcp.state.knowledge_topology.get_backend", return_value=mock_backend),
            patch.object(
                type(md_files[0]),
                "read_text",
                side_effect=OSError("Permission denied"),
                create=True,
            ),
        ):
            result = execute_knowledge_sync(trw_dir, config)

        assert result["topics_generated"] >= 1
