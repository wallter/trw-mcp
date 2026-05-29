"""Integration and idempotency tests for consolidate_cycle."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from trw_mcp.models.config import TRWConfig
from trw_mcp.state.consolidation import consolidate_cycle, find_clusters
from trw_mcp.state.persistence import FileStateReader, FileStateWriter

from ._consolidation_test_helpers import make_vec, write_entry


class TestConsolidationIntegration:
    """End-to-end tests combining clustering, summarization, creation, and archival."""

    def test_full_cycle_with_fallback_summarization(
        self, tmp_path: Path, writer: FileStateWriter, reader: FileStateReader
    ) -> None:
        """Full cycle without LLM uses fallback and produces valid results."""
        trw_dir = tmp_path / ".trw"
        entries_dir = trw_dir / "learnings" / "entries"
        entries_dir.mkdir(parents=True)

        entry_ids = [f"entry{i:03d}" for i in range(3)]
        for i in range(3):
            write_entry(
                entries_dir,
                writer,
                entry_ids[i],
                summary=f"test pattern {i}",
                detail=f"detail about pattern {i}",
                impact=0.6 + i * 0.1,
                tags=["testing", f"tag{i}"],
                evidence=[f"evidence{i}"],
                recurrence=i + 1,
            )

        vecs = [make_vec(1.0, 0.0, 0.0)] * 3
        cfg = TRWConfig(
            memory_consolidation_enabled=True,
            memory_consolidation_min_cluster=3,
            memory_consolidation_similarity_threshold=0.75,
        )

        # Make LLM unavailable → fallback path
        mock_llm = MagicMock()
        mock_llm.available = False

        with patch("trw_mcp.state.memory_adapter.list_active_learnings", side_effect=RuntimeError("force yaml")):
            with patch("trw_mcp.state.memory_adapter.embedding_available", return_value=True):
                with patch("trw_mcp.state.memory_adapter.embed_text_batch", return_value=vecs):
                    with patch("trw_mcp.state.consolidation._cycle.LLMClient", return_value=mock_llm):
                        result = consolidate_cycle(trw_dir, config=cfg)

        assert result["status"] == "completed"
        assert int(str(result["consolidated_count"])) >= 1

        # TierManager may have moved original files to cold tier.
        # Check all yaml files recursively in the trw tree for consolidated_into.
        all_yaml: list[Path] = list(trw_dir.rglob("*.yaml"))
        archived_ids = set()

        def _safe_check_yaml(path: Path) -> None:
            try:
                data = reader.read_yaml(path)
                if "consolidated_into" in data and str(data.get("id", "")) in entry_ids:
                    archived_ids.add(str(data["id"]))
            except Exception:
                pass

        for f in all_yaml:
            _safe_check_yaml(f)
        assert len(archived_ids) == 3

        # Verify consolidated entry exists and has correct structure (always stays in entries_dir)
        consolidated_files = [f for f in entries_dir.glob("*.yaml") if f.stem.startswith("L-")]
        assert len(consolidated_files) >= 1
        cons_data = reader.read_yaml(consolidated_files[0])
        assert cons_data["source_type"] == "consolidated"
        assert "consolidated_from" in cons_data

    def test_full_cycle_with_llm_summarization(
        self, tmp_path: Path, writer: FileStateWriter, reader: FileStateReader
    ) -> None:
        """Full cycle with LLM produces consolidated entry with LLM summary."""
        trw_dir = tmp_path / ".trw"
        entries_dir = trw_dir / "learnings" / "entries"
        entries_dir.mkdir(parents=True)

        for i in range(3):
            write_entry(entries_dir, writer, f"entry{i:03d}", summary=f"s{i}", detail=f"d{i}")

        vecs = [make_vec(1.0, 0.0, 0.0)] * 3
        cfg = TRWConfig(
            memory_consolidation_min_cluster=3,
            memory_consolidation_similarity_threshold=0.75,
        )

        llm = MagicMock()
        llm.available = True
        llm.ask_sync.return_value = '{"summary": "llm summary", "detail": "llm detail"}'

        with patch("trw_mcp.state.memory_adapter.list_active_learnings", side_effect=RuntimeError("force yaml")):
            with patch("trw_mcp.state.memory_adapter.embedding_available", return_value=True):
                with patch("trw_mcp.state.memory_adapter.embed_text_batch", return_value=vecs):
                    with patch("trw_mcp.state.consolidation._cycle.LLMClient", return_value=llm):
                        result = consolidate_cycle(trw_dir, config=cfg)

        assert result["status"] == "completed"

        # Find consolidated entry
        consolidated_files = [
            f for f in entries_dir.glob("*.yaml") if f.stem not in [f"entry{i:03d}" for i in range(3)]
        ]
        if consolidated_files:
            cons_data = reader.read_yaml(consolidated_files[0])
            assert cons_data["summary"] == "llm summary"

    def test_dry_run_does_not_modify_existing_entries(
        self, tmp_path: Path, writer: FileStateWriter, reader: FileStateReader
    ) -> None:
        """dry_run=True leaves all existing entries unchanged."""
        trw_dir = tmp_path / ".trw"
        entries_dir = trw_dir / "learnings" / "entries"
        entries_dir.mkdir(parents=True)

        original_data = []
        for i in range(3):
            write_entry(entries_dir, writer, f"entry{i:03d}", summary=f"s{i}", detail=f"d{i}")
            original_data.append(reader.read_yaml(entries_dir / f"entry{i:03d}.yaml"))

        vecs = [make_vec(1.0, 0.0, 0.0)] * 3
        cfg = TRWConfig(memory_consolidation_min_cluster=3)

        with patch("trw_mcp.state.memory_adapter.list_active_learnings", side_effect=RuntimeError("force yaml")):
            with patch("trw_mcp.state.memory_adapter.embedding_available", return_value=True):
                with patch("trw_mcp.state.memory_adapter.embed_text_batch", return_value=vecs):
                    consolidate_cycle(trw_dir, dry_run=True, config=cfg)

        # Verify entries unchanged
        for i in range(3):
            current = reader.read_yaml(entries_dir / f"entry{i:03d}.yaml")
            assert current == original_data[i]


class TestIdempotency:
    """NFR03: Re-running consolidation on already-consolidated entries produces 0 new consolidations."""

    def test_already_consolidated_entries_skipped_on_second_run(
        self, tmp_path: Path, writer: FileStateWriter, reader: FileStateReader
    ) -> None:
        """Second cycle on already-consolidated entries returns consolidated_count=0."""
        trw_dir = tmp_path / ".trw"
        entries_dir = trw_dir / "learnings" / "entries"
        entries_dir.mkdir(parents=True)

        # Write 3 original entries
        for i in range(3):
            write_entry(entries_dir, writer, f"orig{i:03d}", summary=f"orig {i}")

        vecs = [make_vec(1.0, 0.0, 0.0)] * 3
        cfg = TRWConfig(
            memory_consolidation_min_cluster=3,
            memory_consolidation_similarity_threshold=0.75,
        )
        llm = MagicMock()
        llm.available = True
        llm.ask_sync.return_value = '{"summary": "consolidated", "detail": "merged"}'

        # First run — produces 1 consolidated entry
        with patch("trw_mcp.state.memory_adapter.list_active_learnings", side_effect=RuntimeError("force yaml")):
            with patch("trw_mcp.state.memory_adapter.embedding_available", return_value=True):
                with patch("trw_mcp.state.memory_adapter.embed_text_batch", return_value=vecs):
                    with patch("trw_mcp.state.consolidation._cycle.LLMClient", return_value=llm):
                        result1 = consolidate_cycle(trw_dir, config=cfg)

        assert int(str(result1.get("consolidated_count", 0))) >= 1

        # Second run — originals have consolidated_into set; consolidated entry has
        # source_type="consolidated". find_clusters skips both categories.
        with patch("trw_mcp.state.memory_adapter.list_active_learnings", side_effect=RuntimeError("force yaml")):
            with patch("trw_mcp.state.memory_adapter.embedding_available", return_value=True):
                with patch("trw_mcp.state.memory_adapter.embed_text_batch", return_value=[]) as mock_batch:
                    with patch("trw_mcp.state.consolidation._cycle.LLMClient", return_value=llm):
                        result2 = consolidate_cycle(trw_dir, config=cfg)

        # No new consolidations — all eligible entries were already archived
        assert result2.get("consolidated_count", 0) == 0

    def test_entries_with_consolidated_into_are_excluded_from_find_clusters(
        self, tmp_path: Path, reader: FileStateReader, writer: FileStateWriter
    ) -> None:
        """Entries with consolidated_into are not loaded for clustering."""
        entries_dir = tmp_path / "entries"
        entries_dir.mkdir()
        # Write 2 already-archived entries and 1 active
        write_entry(entries_dir, writer, "archived1", consolidated_into="L-existing")
        write_entry(entries_dir, writer, "archived2", consolidated_into="L-existing")
        write_entry(entries_dir, writer, "active1")

        mock_batch = MagicMock(return_value=[make_vec(1.0, 0.0, 0.0)])
        with patch("trw_mcp.state.memory_adapter.list_active_learnings", side_effect=RuntimeError("force yaml")):
            with patch("trw_mcp.state.memory_adapter.embedding_available", return_value=True):
                with patch("trw_mcp.state.memory_adapter.embed_text_batch", mock_batch):
                    find_clusters(entries_dir, reader, min_cluster_size=3)

        # Only 1 active entry should be embedded — below min_cluster_size
        if mock_batch.call_count > 0:
            texts = mock_batch.call_args[0][0]
            assert len(texts) == 1

    def test_consolidated_source_type_entries_excluded_from_find_clusters(
        self, tmp_path: Path, reader: FileStateReader, writer: FileStateWriter
    ) -> None:
        """Entries with source_type='consolidated' are not loaded for clustering."""
        entries_dir = tmp_path / "entries"
        entries_dir.mkdir()
        # Write 2 consolidated entries and 2 active
        write_entry(entries_dir, writer, "cons1", source_type="consolidated")
        write_entry(entries_dir, writer, "cons2", source_type="consolidated")
        write_entry(entries_dir, writer, "active1")
        write_entry(entries_dir, writer, "active2")

        mock_batch = MagicMock(return_value=[make_vec(1.0, 0.0, 0.0)] * 2)
        with patch("trw_mcp.state.memory_adapter.list_active_learnings", side_effect=RuntimeError("force yaml")):
            with patch("trw_mcp.state.memory_adapter.embedding_available", return_value=True):
                with patch("trw_mcp.state.memory_adapter.embed_text_batch", mock_batch):
                    find_clusters(entries_dir, reader, min_cluster_size=3)

        # Only 2 active entries should be embedded
        if mock_batch.call_count > 0:
            texts = mock_batch.call_args[0][0]
            assert len(texts) == 2


class TestConsolidateCycleEdgeCases:
    """Edge cases for consolidate_cycle orchestration."""

    def test_uses_get_config_when_config_is_none(
        self, tmp_path: Path, writer: FileStateWriter, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When config=None, consolidate_cycle calls get_config() for defaults."""
        trw_dir = tmp_path / ".trw"
        entries_dir = trw_dir / "learnings" / "entries"
        entries_dir.mkdir(parents=True)

        cfg = TRWConfig(memory_consolidation_min_cluster=3)
        monkeypatch.setattr("trw_mcp.state.consolidation.get_config", lambda: cfg)

        with patch("trw_mcp.state.memory_adapter.embedding_available", return_value=False):
            result = consolidate_cycle(trw_dir, config=None)

        assert result["status"] == "no_clusters"

    def test_multiple_clusters_all_consolidated(
        self, tmp_path: Path, writer: FileStateWriter, reader: FileStateReader
    ) -> None:
        """When find_clusters returns multiple clusters, all are processed."""
        trw_dir = tmp_path / ".trw"
        entries_dir = trw_dir / "learnings" / "entries"
        entries_dir.mkdir(parents=True)

        # Write 6 entries in 2 groups
        for i in range(6):
            write_entry(entries_dir, writer, f"e{i:03d}", summary=f"s{i}")

        # Create two clusters manually
        cluster1 = [
            {"id": "e000", "summary": "s0", "detail": "d0"},
            {"id": "e001", "summary": "s1", "detail": "d1"},
            {"id": "e002", "summary": "s2", "detail": "d2"},
        ]
        cluster2 = [
            {"id": "e003", "summary": "s3", "detail": "d3"},
            {"id": "e004", "summary": "s4", "detail": "d4"},
            {"id": "e005", "summary": "s5", "detail": "d5"},
        ]

        cfg = TRWConfig(memory_consolidation_min_cluster=3)

        llm = MagicMock()
        llm.available = True
        llm.ask_sync.return_value = '{"summary": "s", "detail": "d"}'

        with patch("trw_mcp.state.consolidation._cycle.find_clusters", return_value=[cluster1, cluster2]):
            with patch("trw_mcp.state.consolidation._cycle.LLMClient", return_value=llm):
                result = consolidate_cycle(trw_dir, config=cfg)

        assert result["status"] == "completed"
        assert result["clusters_found"] == 2
        assert result["consolidated_count"] == 2

    def test_errors_list_populated_on_cluster_failure(self, tmp_path: Path, writer: FileStateWriter) -> None:
        """When a cluster fails to process, its error is appended to errors list."""
        trw_dir = tmp_path / ".trw"
        entries_dir = trw_dir / "learnings" / "entries"
        entries_dir.mkdir(parents=True)

        bad_cluster: list[dict[str, object]] = [
            {"id": "bad1", "summary": "s1"},
            {"id": "bad2", "summary": "s2"},
            {"id": "bad3", "summary": "s3"},
        ]

        cfg = TRWConfig(memory_consolidation_min_cluster=3)

        # _summarize_cluster_llm returns None -> triggers fallback -> fallback raises
        with patch("trw_mcp.state.consolidation._cycle.find_clusters", return_value=[bad_cluster]):
            with patch("trw_mcp.state.consolidation._cycle.LLMClient", side_effect=RuntimeError("no llm")):
                with patch(
                    "trw_mcp.state.consolidation._cycle._summarize_cluster_llm",
                    return_value=None,
                ):
                    with patch(
                        "trw_mcp.state.consolidation._cycle._summarize_cluster_fallback",
                        side_effect=ValueError("boom"),
                    ):
                        result = consolidate_cycle(trw_dir, config=cfg)

        assert result["consolidated_count"] == 0
        assert "errors" in result
        errors = list(result["errors"])
        assert len(errors) == 1
        assert "boom" in errors[0]

    def test_dry_run_with_no_clusters_returns_empty_clusters_list(
        self, tmp_path: Path, writer: FileStateWriter
    ) -> None:
        """Dry run when no clusters found returns empty clusters list."""
        trw_dir = tmp_path / ".trw"
        entries_dir = trw_dir / "learnings" / "entries"
        entries_dir.mkdir(parents=True)

        cfg = TRWConfig(memory_consolidation_min_cluster=3)

        with patch("trw_mcp.state.memory_adapter.embedding_available", return_value=False):
            result = consolidate_cycle(trw_dir, dry_run=True, config=cfg)

        assert result["dry_run"] is True
        assert list(result["clusters"]) == []
        assert result["consolidated_count"] == 0
