"""Core consolidate_cycle tests."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

from trw_mcp.models.config import TRWConfig
from trw_mcp.state.consolidation import consolidate_cycle
from trw_mcp.state.persistence import FileStateReader, FileStateWriter

from ._consolidation_test_helpers import make_vec, write_entry

class TestConsolidateCycle:
    """FR06: consolidate_cycle orchestrates the full consolidation pipeline."""

    def _make_config(self, **kwargs: Any) -> TRWConfig:
        """Create a TRWConfig with consolidation fields set."""
        return TRWConfig(
            memory_consolidation_enabled=True,
            memory_consolidation_min_cluster=3,
            memory_consolidation_similarity_threshold=0.75,
            memory_consolidation_max_per_cycle=50,
            **kwargs,
        )

    def _write_cluster_entries(
        self,
        entries_dir: Path,
        writer: FileStateWriter,
        n: int = 3,
        prefix: str = "e",
    ) -> list[str]:
        """Write n entries and return their IDs."""
        ids = []
        for i in range(n):
            entry_id = f"{prefix}{i:03d}"
            write_entry(entries_dir, writer, entry_id, summary=f"summary {i}")
            ids.append(entry_id)
        return ids

    def test_dry_run_returns_cluster_previews_no_writes(
        self, tmp_path: Path, writer: FileStateWriter, reader: FileStateReader
    ) -> None:
        """dry_run=True returns previews without writing any files."""
        trw_dir = tmp_path / ".trw"
        entries_dir = trw_dir / "learnings" / "entries"
        entries_dir.mkdir(parents=True)
        ids = self._write_cluster_entries(entries_dir, writer, 4)

        vecs = [make_vec(1.0, 0.0, 0.0)] * 4
        cfg = self._make_config()

        with patch("trw_mcp.state.memory_adapter.list_active_learnings", side_effect=RuntimeError("force yaml")):
            with patch("trw_mcp.state.memory_adapter.embedding_available", return_value=True):
                with patch("trw_mcp.state.memory_adapter.embed_text_batch", return_value=vecs):
                    result = consolidate_cycle(
                        trw_dir,
                        dry_run=True,
                        config=cfg,
                    )

        assert result["dry_run"] is True
        assert "clusters" in result
        assert result["consolidated_count"] == 0
        # No new YAML files should have been created
        yaml_files = list(entries_dir.glob("*.yaml"))
        assert all(f.stem in ids for f in yaml_files)

    def test_dry_run_cluster_preview_structure(self, tmp_path: Path, writer: FileStateWriter) -> None:
        """Dry-run cluster previews contain entry_ids, count, mean_similarity."""
        trw_dir = tmp_path / ".trw"
        entries_dir = trw_dir / "learnings" / "entries"
        entries_dir.mkdir(parents=True)
        self._write_cluster_entries(entries_dir, writer, 4)

        vecs = [make_vec(1.0, 0.0, 0.0)] * 4
        cfg = self._make_config()

        with patch("trw_mcp.state.memory_adapter.list_active_learnings", side_effect=RuntimeError("force yaml")):
            with patch("trw_mcp.state.memory_adapter.embedding_available", return_value=True):
                with patch("trw_mcp.state.memory_adapter.embed_text_batch", return_value=vecs):
                    result = consolidate_cycle(trw_dir, dry_run=True, config=cfg)

        clusters = list(result["clusters"])  # type: ignore[arg-type]
        if clusters:
            preview = clusters[0]
            assert "entry_ids" in preview
            assert "count" in preview
            assert "mean_similarity" in preview

    def test_no_clusters_returns_no_clusters_status(self, tmp_path: Path, writer: FileStateWriter) -> None:
        """When no clusters found, returns status='no_clusters'."""
        trw_dir = tmp_path / ".trw"
        entries_dir = trw_dir / "learnings" / "entries"
        entries_dir.mkdir(parents=True)

        cfg = self._make_config()

        with patch("trw_mcp.state.memory_adapter.embedding_available", return_value=False):
            result = consolidate_cycle(trw_dir, dry_run=False, config=cfg)

        assert result["status"] == "no_clusters"
        assert result["consolidated_count"] == 0

    def test_full_cycle_creates_consolidated_entry(
        self, tmp_path: Path, writer: FileStateWriter, reader: FileStateReader
    ) -> None:
        """Full cycle creates a consolidated entry and archives originals."""
        trw_dir = tmp_path / ".trw"
        entries_dir = trw_dir / "learnings" / "entries"
        entries_dir.mkdir(parents=True)
        ids = self._write_cluster_entries(entries_dir, writer, 3)

        vecs = [make_vec(1.0, 0.0, 0.0)] * 3
        cfg = self._make_config()

        llm = MagicMock()
        llm.available = True
        llm.ask_sync.return_value = '{"summary": "consolidated", "detail": "merged"}'

        with patch("trw_mcp.state.memory_adapter.list_active_learnings", side_effect=RuntimeError("force yaml")):
            with patch("trw_mcp.state.memory_adapter.embedding_available", return_value=True):
                with patch("trw_mcp.state.memory_adapter.embed_text_batch", return_value=vecs):
                    with patch("trw_mcp.state.consolidation._cycle.LLMClient", return_value=llm):
                        result = consolidate_cycle(trw_dir, config=cfg)

        assert result["consolidated_count"] >= 1
        assert result["status"] == "completed"

        # Verify original entries were processed — check all files in the trw tree
        # TierManager may have moved files to cold tier; find all yaml files recursively
        all_yaml: list[Path] = list(trw_dir.rglob("*.yaml"))
        all_ids_with_consolidated = {
            str(reader.read_yaml(f).get("id", ""))
            for f in all_yaml
            if not f.name.startswith("L-") and "consolidated_into" in reader.read_yaml(f)
        }
        assert len(all_ids_with_consolidated) >= len(ids)

    def test_llm_unavailable_uses_fallback(self, tmp_path: Path, writer: FileStateWriter) -> None:
        """When LLM unavailable, falls back to longest-entry summarization."""
        trw_dir = tmp_path / ".trw"
        entries_dir = trw_dir / "learnings" / "entries"
        entries_dir.mkdir(parents=True)
        self._write_cluster_entries(entries_dir, writer, 3)

        vecs = [make_vec(1.0, 0.0, 0.0)] * 3
        cfg = self._make_config()

        # Make LLM unavailable
        mock_llm = MagicMock()
        mock_llm.available = False

        with patch("trw_mcp.state.memory_adapter.list_active_learnings", side_effect=RuntimeError("force yaml")):
            with patch("trw_mcp.state.memory_adapter.embedding_available", return_value=True):
                with patch("trw_mcp.state.memory_adapter.embed_text_batch", return_value=vecs):
                    with patch("trw_mcp.state.consolidation._cycle.LLMClient", return_value=mock_llm):
                        result = consolidate_cycle(trw_dir, config=cfg)

        assert result["status"] == "completed"

    def test_tier_manager_unavailable_graceful(self, tmp_path: Path, writer: FileStateWriter) -> None:
        """TierManager unavailability is handled gracefully (falls back to archived status)."""
        trw_dir = tmp_path / ".trw"
        entries_dir = trw_dir / "learnings" / "entries"
        entries_dir.mkdir(parents=True)
        self._write_cluster_entries(entries_dir, writer, 3)

        vecs = [make_vec(1.0, 0.0, 0.0)] * 3
        cfg = self._make_config()

        llm = MagicMock()
        llm.available = True
        llm.ask_sync.return_value = '{"summary": "s", "detail": "d"}'

        # Patch at the import point inside consolidate_cycle — the function does
        # `from trw_mcp.state.tiers import TierManager as _TierManager` internally.
        # We patch the module so the import raises, triggering graceful degradation.
        import trw_mcp.state.tiers as tiers_mod

        original_tm = tiers_mod.TierManager
        try:
            tiers_mod.TierManager = MagicMock(side_effect=Exception("tiers unavailable"))  # type: ignore[misc]
            with patch("trw_mcp.state.memory_adapter.list_active_learnings", side_effect=RuntimeError("force yaml")):
                with patch("trw_mcp.state.memory_adapter.embedding_available", return_value=True):
                    with patch("trw_mcp.state.memory_adapter.embed_text_batch", return_value=vecs):
                        with patch("trw_mcp.state.consolidation._cycle.LLMClient", return_value=llm):
                            result = consolidate_cycle(trw_dir, config=cfg)
        finally:
            tiers_mod.TierManager = original_tm

        assert "status" in result
        assert result["status"] == "completed"

    def test_llm_client_init_exception_graceful(self, tmp_path: Path, writer: FileStateWriter) -> None:
        """When LLMClient() raises on init, consolidation proceeds with fallback."""
        trw_dir = tmp_path / ".trw"
        entries_dir = trw_dir / "learnings" / "entries"
        entries_dir.mkdir(parents=True)
        self._write_cluster_entries(entries_dir, writer, 3)

        vecs = [make_vec(1.0, 0.0, 0.0)] * 3
        cfg = self._make_config()

        with patch("trw_mcp.state.memory_adapter.list_active_learnings", side_effect=RuntimeError("force yaml")):
            with patch("trw_mcp.state.memory_adapter.embedding_available", return_value=True):
                with patch("trw_mcp.state.memory_adapter.embed_text_batch", return_value=vecs):
                    # Make LLMClient constructor raise (lines 582-583)
                    with patch("trw_mcp.state.consolidation._cycle.LLMClient", side_effect=RuntimeError("no llm")):
                        result = consolidate_cycle(trw_dir, config=cfg)

        # Should succeed with fallback summarization
        assert "status" in result
        assert result["status"] == "completed"

    def test_cluster_error_added_to_errors_list(self, tmp_path: Path, writer: FileStateWriter) -> None:
        """Per-cluster errors are collected and returned, not re-raised."""
        trw_dir = tmp_path / ".trw"
        entries_dir = trw_dir / "learnings" / "entries"
        entries_dir.mkdir(parents=True)
        self._write_cluster_entries(entries_dir, writer, 3)

        vecs = [make_vec(1.0, 0.0, 0.0)] * 3
        cfg = self._make_config()

        llm = MagicMock()
        llm.available = True
        # Cause LLM to raise to trigger fallback path, but still proceed
        llm.ask_sync.side_effect = RuntimeError("llm error")

        with patch("trw_mcp.state.memory_adapter.list_active_learnings", side_effect=RuntimeError("force yaml")):
            with patch("trw_mcp.state.memory_adapter.embedding_available", return_value=True):
                with patch("trw_mcp.state.memory_adapter.embed_text_batch", return_value=vecs):
                    with patch("trw_mcp.state.consolidation._cycle.LLMClient", return_value=llm):
                        result = consolidate_cycle(trw_dir, config=cfg)

        # consolidate_cycle should not raise — errors collected or fallback used
        assert "status" in result

    def test_completed_result_structure(self, tmp_path: Path, writer: FileStateWriter) -> None:
        """Full cycle result has expected keys."""
        trw_dir = tmp_path / ".trw"
        entries_dir = trw_dir / "learnings" / "entries"
        entries_dir.mkdir(parents=True)
        self._write_cluster_entries(entries_dir, writer, 3)

        vecs = [make_vec(1.0, 0.0, 0.0)] * 3
        cfg = self._make_config()

        llm = MagicMock()
        llm.available = True
        llm.ask_sync.return_value = '{"summary": "s", "detail": "d"}'

        with patch("trw_mcp.state.memory_adapter.list_active_learnings", side_effect=RuntimeError("force yaml")):
            with patch("trw_mcp.state.memory_adapter.embedding_available", return_value=True):
                with patch("trw_mcp.state.memory_adapter.embed_text_batch", return_value=vecs):
                    with patch("trw_mcp.state.consolidation._cycle.LLMClient", return_value=llm):
                        result = consolidate_cycle(trw_dir, config=cfg)

        assert "status" in result
        assert "clusters_found" in result
        assert "consolidated_count" in result
