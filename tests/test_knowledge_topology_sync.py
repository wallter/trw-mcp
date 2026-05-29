"""Knowledge topology sync orchestration and atomic write tests."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from trw_memory.models.memory import MemoryEntry

from tests._knowledge_topology_support import _make_config, _make_entry
from trw_mcp.state.knowledge_topology import execute_knowledge_sync


class TestExecuteKnowledgeSync:
    """FR01/FR09/FR10: Orchestration, threshold guard, atomic writes."""

    @pytest.fixture
    def trw_dir(self, tmp_path: Path) -> Path:
        path = tmp_path / ".trw"
        path.mkdir()
        return path

    def _make_entries(self, count: int) -> list[MemoryEntry]:
        entries = []
        for i in range(count):
            tags = ["pydantic", "testing"] if i % 2 == 0 else ["fastapi", "api"]
            entries.append(_make_entry(f"L-{i:04d}", tags=tags, importance=0.6))
        return entries

    def test_below_threshold_returns_early(self, trw_dir: Path, tmp_path: Path) -> None:
        config = _make_config(tmp_path, knowledge_sync_threshold=50)
        with patch("trw_mcp.state.knowledge_topology.count_entries", return_value=10):
            result = execute_knowledge_sync(trw_dir, config)
        assert result["threshold_met"] is False
        assert result["topics_generated"] == 0
        assert result["entries_clustered"] == 0
        assert not (trw_dir / "knowledge").exists()

    def test_below_threshold_returns_correct_fields(self, trw_dir: Path, tmp_path: Path) -> None:
        config = _make_config(tmp_path, knowledge_sync_threshold=50)
        with patch("trw_mcp.state.knowledge_topology.count_entries", return_value=10):
            result = execute_knowledge_sync(trw_dir, config)
        assert "entry_count" in result
        assert result["entry_count"] == 10
        assert "threshold" in result
        assert result["threshold"] == 50
        assert "output_dir" in result
        assert "errors" in result
        assert result["dry_run"] is False

    def test_count_entries_failure_returns_fail_open(self, trw_dir: Path, tmp_path: Path) -> None:
        config = _make_config(tmp_path, knowledge_sync_threshold=50)
        with patch(
            "trw_mcp.state.knowledge_topology.count_entries",
            side_effect=RuntimeError("storage unavailable"),
        ):
            result = execute_knowledge_sync(trw_dir, config)
        assert result["threshold_met"] is False
        assert result["entry_count"] == 0
        assert result["dry_run"] is False
        assert len(result["errors"]) == 1  # type: ignore[arg-type]
        assert "count_entries failed" in str(result["errors"])

    def test_dry_run_above_threshold_no_files(self, trw_dir: Path, tmp_path: Path) -> None:
        config = _make_config(tmp_path, knowledge_sync_threshold=50)
        with patch("trw_mcp.state.knowledge_topology.count_entries", return_value=60):
            result = execute_knowledge_sync(trw_dir, config, dry_run=True)
        assert result["threshold_met"] is True
        assert result.get("dry_run") is True
        assert result["topics_generated"] == 0
        assert not (trw_dir / "knowledge").exists()

    def test_full_sync_writes_topic_documents(self, trw_dir: Path, tmp_path: Path) -> None:
        config = _make_config(tmp_path, knowledge_sync_threshold=5, knowledge_min_cluster_size=2)
        entries = [_make_entry(f"L-{i:03d}", tags=["testing", "python"]) for i in range(8)]
        mock_backend = MagicMock()
        mock_backend.list_entries.return_value = entries

        with (
            patch("trw_mcp.state.knowledge_topology.count_entries", return_value=8),
            patch("trw_mcp.state.knowledge_topology.get_backend", return_value=mock_backend),
        ):
            result = execute_knowledge_sync(trw_dir, config)

        assert result["threshold_met"] is True
        assert result["topics_generated"] >= 1
        knowledge_dir = trw_dir / "knowledge"
        assert knowledge_dir.exists()
        assert len(list(knowledge_dir.glob("*.md"))) >= 1

    def test_clusters_json_written_atomically(self, trw_dir: Path, tmp_path: Path) -> None:
        config = _make_config(tmp_path, knowledge_sync_threshold=5, knowledge_min_cluster_size=2)
        entries = [_make_entry(f"L-{i:03d}", tags=["testing", "python"]) for i in range(6)]
        mock_backend = MagicMock()
        mock_backend.list_entries.return_value = entries

        with (
            patch("trw_mcp.state.knowledge_topology.count_entries", return_value=6),
            patch("trw_mcp.state.knowledge_topology.get_backend", return_value=mock_backend),
        ):
            execute_knowledge_sync(trw_dir, config)

        clusters_path = trw_dir / "knowledge" / "clusters.json"
        assert clusters_path.exists()
        data = json.loads(clusters_path.read_text(encoding="utf-8"))
        assert isinstance(data, dict)
        assert "updated_at" in data

    def test_clusters_json_structure_correct(self, trw_dir: Path, tmp_path: Path) -> None:
        config = _make_config(tmp_path, knowledge_sync_threshold=5, knowledge_min_cluster_size=2)
        entries = [_make_entry(f"L-{i:03d}", tags=["testing", "python"]) for i in range(6)]
        mock_backend = MagicMock()
        mock_backend.list_entries.return_value = entries

        with (
            patch("trw_mcp.state.knowledge_topology.count_entries", return_value=6),
            patch("trw_mcp.state.knowledge_topology.get_backend", return_value=mock_backend),
        ):
            result = execute_knowledge_sync(trw_dir, config)

        data = json.loads((trw_dir / "knowledge" / "clusters.json").read_text(encoding="utf-8"))
        for slug in result.get("clusters", []):
            assert slug in data
            assert isinstance(data[slug], list)

    def test_output_dir_created_if_missing(self, trw_dir: Path, tmp_path: Path) -> None:
        config = _make_config(tmp_path, knowledge_sync_threshold=3, knowledge_min_cluster_size=2)
        entries = [_make_entry(f"L-{i:03d}", tags=["api", "testing"]) for i in range(5)]
        mock_backend = MagicMock()
        mock_backend.list_entries.return_value = entries

        assert not (trw_dir / "knowledge").exists()
        with (
            patch("trw_mcp.state.knowledge_topology.count_entries", return_value=5),
            patch("trw_mcp.state.knowledge_topology.get_backend", return_value=mock_backend),
        ):
            execute_knowledge_sync(trw_dir, config)
        assert (trw_dir / "knowledge").exists()

    def test_idempotent_consecutive_syncs(self, trw_dir: Path, tmp_path: Path) -> None:
        config = _make_config(tmp_path, knowledge_sync_threshold=5, knowledge_min_cluster_size=2)
        entries = [_make_entry(f"L-{i:03d}", tags=["testing", "python"]) for i in range(6)]
        mock_backend = MagicMock()
        mock_backend.list_entries.return_value = entries

        with (
            patch("trw_mcp.state.knowledge_topology.count_entries", return_value=6),
            patch("trw_mcp.state.knowledge_topology.get_backend", return_value=mock_backend),
        ):
            result1 = execute_knowledge_sync(trw_dir, config)
            result2 = execute_knowledge_sync(trw_dir, config)

        assert result1["topics_generated"] == result2["topics_generated"]
        assert result1["entries_clustered"] == result2["entries_clustered"]

    def test_manual_markers_preserved_on_second_sync(self, trw_dir: Path, tmp_path: Path) -> None:
        config = _make_config(tmp_path, knowledge_sync_threshold=5, knowledge_min_cluster_size=2)
        entries = [_make_entry(f"L-{i:03d}", tags=["testing"]) for i in range(5)]
        mock_backend = MagicMock()
        mock_backend.list_entries.return_value = entries

        with (
            patch("trw_mcp.state.knowledge_topology.count_entries", return_value=5),
            patch("trw_mcp.state.knowledge_topology.get_backend", return_value=mock_backend),
        ):
            execute_knowledge_sync(trw_dir, config)

        first_file = list((trw_dir / "knowledge").glob("*.md"))[0]
        existing = first_file.read_text(encoding="utf-8")
        first_file.write_text(
            existing + "\n<!-- trw:manual-start -->MY NOTES<!-- trw:manual-end -->\n",
            encoding="utf-8",
        )

        with (
            patch("trw_mcp.state.knowledge_topology.count_entries", return_value=5),
            patch("trw_mcp.state.knowledge_topology.get_backend", return_value=mock_backend),
        ):
            execute_knowledge_sync(trw_dir, config)

        assert "MY NOTES" in first_file.read_text(encoding="utf-8")

    def test_result_contains_clusters_key(self, trw_dir: Path, tmp_path: Path) -> None:
        config = _make_config(tmp_path, knowledge_sync_threshold=5, knowledge_min_cluster_size=2)
        entries = [_make_entry(f"L-{i:03d}", tags=["testing", "python"]) for i in range(6)]
        mock_backend = MagicMock()
        mock_backend.list_entries.return_value = entries

        with (
            patch("trw_mcp.state.knowledge_topology.count_entries", return_value=6),
            patch("trw_mcp.state.knowledge_topology.get_backend", return_value=mock_backend),
        ):
            result = execute_knowledge_sync(trw_dir, config)

        assert "clusters" in result
        assert isinstance(result["clusters"], list)

    def test_errors_list_returned_on_success(self, trw_dir: Path, tmp_path: Path) -> None:
        config = _make_config(tmp_path, knowledge_sync_threshold=5, knowledge_min_cluster_size=2)
        entries = [_make_entry(f"L-{i:03d}", tags=["testing", "python"]) for i in range(6)]
        mock_backend = MagicMock()
        mock_backend.list_entries.return_value = entries

        with (
            patch("trw_mcp.state.knowledge_topology.count_entries", return_value=6),
            patch("trw_mcp.state.knowledge_topology.get_backend", return_value=mock_backend),
        ):
            result = execute_knowledge_sync(trw_dir, config)

        assert "errors" in result
        assert isinstance(result["errors"], list)

    def test_partial_render_failure_collected_not_raised(self, trw_dir: Path, tmp_path: Path) -> None:
        config = _make_config(tmp_path, knowledge_sync_threshold=5, knowledge_min_cluster_size=2)
        entries = [_make_entry(f"L-{i:03d}", tags=["testing", "python"]) for i in range(6)]
        mock_backend = MagicMock()
        mock_backend.list_entries.return_value = entries

        with (
            patch("trw_mcp.state.knowledge_topology.count_entries", return_value=6),
            patch("trw_mcp.state.knowledge_topology.get_backend", return_value=mock_backend),
            patch(
                "trw_mcp.state.knowledge_topology.render_topic_document",
                side_effect=RuntimeError("Render explosion"),
            ),
        ):
            result = execute_knowledge_sync(trw_dir, config)

        assert len(result["errors"]) >= 1
        assert any("Render explosion" in str(error) for error in result["errors"])


@pytest.mark.unit
class TestAtomicWriteDoubleFault:
    """Atomic clusters.json failures are captured without raising."""

    def _make_entries(self, count: int = 6) -> list[MemoryEntry]:
        return [_make_entry(f"L-{i:03d}", tags=["testing", "python"]) for i in range(count)]

    def test_double_failure_captured_in_errors(self, tmp_path: Path) -> None:
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        config = _make_config(tmp_path, knowledge_sync_threshold=5, knowledge_min_cluster_size=2)
        mock_backend = MagicMock()
        mock_backend.list_entries.return_value = self._make_entries(6)

        replace_error = OSError("replace failed")
        unlink_error = OSError("unlink also failed")
        original_replace = Path.replace
        original_unlink = Path.unlink

        def fail_on_tmp_replace(self_path: Path, target: object) -> None:
            if str(self_path).endswith(".tmp"):
                raise replace_error
            return original_replace(self_path, target)  # type: ignore[arg-type]

        def fail_on_tmp_unlink(self_path: Path, missing_ok: bool = False) -> None:
            if str(self_path).endswith(".tmp"):
                raise unlink_error
            return original_unlink(self_path, missing_ok=missing_ok)

        with (
            patch("trw_mcp.state.knowledge_topology.count_entries", return_value=6),
            patch("trw_mcp.state.knowledge_topology.get_backend", return_value=mock_backend),
            patch.object(Path, "replace", fail_on_tmp_replace),
            patch.object(Path, "unlink", fail_on_tmp_unlink),
        ):
            result = execute_knowledge_sync(trw_dir, config)

        assert any("clusters.json write failed" in error for error in result["errors"])

    def test_replace_fails_cleanup_succeeds(self, tmp_path: Path) -> None:
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        config = _make_config(tmp_path, knowledge_sync_threshold=5, knowledge_min_cluster_size=2)
        mock_backend = MagicMock()
        mock_backend.list_entries.return_value = self._make_entries(6)
        original_replace = Path.replace

        def fail_on_tmp_replace(self_path: Path, target: object) -> None:
            if str(self_path).endswith(".tmp"):
                raise OSError("replace failed")
            return original_replace(self_path, target)  # type: ignore[arg-type]

        with (
            patch("trw_mcp.state.knowledge_topology.count_entries", return_value=6),
            patch("trw_mcp.state.knowledge_topology.get_backend", return_value=mock_backend),
            patch.object(Path, "replace", fail_on_tmp_replace),
        ):
            result = execute_knowledge_sync(trw_dir, config)

        assert any("clusters.json write failed" in error for error in result["errors"])


@pytest.mark.unit
class TestExecuteSyncClustersJsonWriteFailure:
    """execute_knowledge_sync clusters.json write exception captured in errors."""

    def test_clusters_json_write_error_in_result_errors(self, tmp_path: Path) -> None:
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        config = _make_config(tmp_path, knowledge_sync_threshold=3, knowledge_min_cluster_size=2)
        entries = [_make_entry(f"L-{i:03d}", tags=["api", "python"]) for i in range(5)]
        mock_backend = MagicMock()
        mock_backend.list_entries.return_value = entries

        with (
            patch("trw_mcp.state.knowledge_topology.count_entries", return_value=5),
            patch("trw_mcp.state.knowledge_topology.get_backend", return_value=mock_backend),
            patch(
                "trw_mcp.state.knowledge_topology.tempfile.mkstemp",
                side_effect=OSError("no space left on device"),
            ),
        ):
            result = execute_knowledge_sync(trw_dir, config)

        assert any("clusters.json write failed" in error for error in result["errors"])
        assert isinstance(result["topics_generated"], int)

    def test_clusters_json_write_failure_does_not_affect_topics(self, tmp_path: Path) -> None:
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        config = _make_config(tmp_path, knowledge_sync_threshold=3, knowledge_min_cluster_size=2)
        entries = [_make_entry(f"L-{i:03d}", tags=["api", "python"]) for i in range(5)]
        mock_backend = MagicMock()
        mock_backend.list_entries.return_value = entries

        with (
            patch("trw_mcp.state.knowledge_topology.count_entries", return_value=5),
            patch("trw_mcp.state.knowledge_topology.get_backend", return_value=mock_backend),
            patch(
                "trw_mcp.state.knowledge_topology.tempfile.mkstemp",
                side_effect=OSError("disk full"),
            ),
        ):
            result = execute_knowledge_sync(trw_dir, config)

        knowledge_dir = trw_dir / "knowledge"
        md_files = list(knowledge_dir.glob("*.md")) if knowledge_dir.exists() else []
        assert result["topics_generated"] == len(md_files)
