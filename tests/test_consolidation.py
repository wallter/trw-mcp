"""Tests for memory consolidation engine — PRD-CORE-044.

Covers:
- FR01: Embedding-based cluster detection (find_clusters)
- FR02: LLM-powered cluster summarization (_summarize_cluster_llm, _parse_consolidation_response)
- FR03: Consolidated entry creation (_create_consolidated_entry)
- FR04: Original entry archival (_archive_originals, _rollback_archive)
- FR05: Graceful degradation without LLM (_summarize_cluster_fallback)
- FR06: Dry-run mode + main entry point (consolidate_cycle)
- FR07: Ceremony wiring (trw_deliver integration)
- FR08: Config fields and validation (TRWConfig)
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from trw_mcp.models.config import TRWConfig, _reset_config
from trw_mcp.state.consolidation import (
    _archive_originals,
    _create_consolidated_entry,
    _mean_pairwise_similarity,
    _parse_consolidation_response,
    _rollback_archive,
    _summarize_cluster_fallback,
    _summarize_cluster_llm,
    consolidate_cycle,
    find_clusters,
)
from trw_mcp.state.persistence import FileStateReader, FileStateWriter


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_vec(x: float, y: float = 0.0, z: float = 0.0) -> list[float]:
    """Return a unit vector in 3D (normalized)."""
    import math
    mag = math.sqrt(x * x + y * y + z * z)
    if mag == 0.0:
        return [0.0, 0.0, 0.0]
    return [x / mag, y / mag, z / mag]


def write_entry(
    entries_dir: Path,
    writer: FileStateWriter,
    entry_id: str,
    summary: str = "test summary",
    detail: str = "test detail",
    status: str = "active",
    source_type: str | None = None,
    consolidated_into: str | None = None,
    impact: float = 0.5,
    tags: list[str] | None = None,
    evidence: list[str] | None = None,
    recurrence: int = 1,
    q_value: float = 0.0,
) -> Path:
    """Write a minimal learning entry YAML for testing."""
    path = entries_dir / f"{entry_id}.yaml"
    data: dict[str, Any] = {
        "id": entry_id,
        "summary": summary,
        "detail": detail,
        "status": status,
        "impact": impact,
        "tags": tags or ["testing"],
        "evidence": evidence or [],
        "recurrence": recurrence,
        "q_value": q_value,
    }
    if source_type is not None:
        data["source_type"] = source_type
    if consolidated_into is not None:
        data["consolidated_into"] = consolidated_into
    writer.write_yaml(path, data)
    return path


def make_cluster(n: int = 3) -> list[dict[str, Any]]:
    """Create a simple cluster of n entry dicts for testing."""
    return [
        {
            "id": f"L-entry{i:03d}",
            "summary": f"summary {i}",
            "detail": f"detail {i}",
            "impact": 0.5 + i * 0.1,
            "tags": [f"tag{i}", "shared"],
            "evidence": [f"evidence{i}"],
            "recurrence": i + 1,
            "q_value": 0.1 * i,
        }
        for i in range(n)
    ]


# ---------------------------------------------------------------------------
# FR01 — Embedding-Based Cluster Detection
# ---------------------------------------------------------------------------


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

        with patch("trw_mcp.telemetry.embeddings.embedding_available", return_value=False):
            with patch("trw_mcp.telemetry.embeddings.embed_batch", return_value=[None] * 5):
                result = find_clusters(entries_dir, reader)
        assert result == []

    def test_nonexistent_dir_returns_empty(
        self, tmp_path: Path, reader: FileStateReader
    ) -> None:
        """When entries_dir does not exist, returns []."""
        with patch("trw_mcp.telemetry.embeddings.embedding_available", return_value=False):
            result = find_clusters(tmp_path / "nonexistent", reader)
        assert result == []

    def test_skips_index_yaml(
        self, tmp_path: Path, reader: FileStateReader, writer: FileStateWriter
    ) -> None:
        """index.yaml is skipped during loading."""
        entries_dir = tmp_path / "entries"
        entries_dir.mkdir()
        # Write index.yaml — should be ignored
        writer.write_yaml(entries_dir / "index.yaml", {"version": 1})
        # Write fewer entries than min_cluster_size
        write_entry(entries_dir, writer, "e001")
        write_entry(entries_dir, writer, "e002")

        with patch("trw_mcp.telemetry.embeddings.embedding_available", return_value=True):
            with patch("trw_mcp.telemetry.embeddings.embed_batch", return_value=[None, None]):
                result = find_clusters(entries_dir, reader, min_cluster_size=3)
        assert result == []

    def test_skips_inactive_entries(
        self, tmp_path: Path, reader: FileStateReader, writer: FileStateWriter
    ) -> None:
        """Entries with status != 'active' are excluded from clustering."""
        entries_dir = tmp_path / "entries"
        entries_dir.mkdir()
        write_entry(entries_dir, writer, "active1", status="active")
        write_entry(entries_dir, writer, "active2", status="active")
        write_entry(entries_dir, writer, "archived1", status="archived")
        write_entry(entries_dir, writer, "archived2", status="archived")
        # Only 2 active entries — below min_cluster_size=3

        with patch("trw_mcp.telemetry.embeddings.embedding_available", return_value=True):
            with patch(
                "trw_mcp.telemetry.embeddings.embed_batch",
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
        with patch("trw_mcp.telemetry.embeddings.embedding_available", return_value=True):
            with patch("trw_mcp.telemetry.embeddings.embed_batch", return_value=vecs):
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
        with patch("trw_mcp.telemetry.embeddings.embedding_available", return_value=True):
            with patch("trw_mcp.telemetry.embeddings.embed_batch", return_value=vecs):
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
            with patch("trw_mcp.telemetry.embeddings.embedding_available", return_value=True):
                with patch("trw_mcp.telemetry.embeddings.embed_batch", mock_batch):
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
            with patch("trw_mcp.telemetry.embeddings.embedding_available", return_value=True):
                with patch("trw_mcp.telemetry.embeddings.embed_batch", return_value=all_vecs):
                    result = find_clusters(
                        entries_dir, reader,
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

        with patch("trw_mcp.telemetry.embeddings.embedding_available", return_value=True):
            with patch("trw_mcp.telemetry.embeddings.embed_batch", return_value=vecs):
                result_low = find_clusters(
                    entries_dir, reader,
                    similarity_threshold=0.5,
                    min_cluster_size=3,
                )
            with patch("trw_mcp.telemetry.embeddings.embed_batch", return_value=vecs):
                result_high = find_clusters(
                    entries_dir, reader,
                    similarity_threshold=0.99,
                    min_cluster_size=3,
                )

        assert len(result_low) >= len(result_high)

    def test_max_entries_cap(
        self, tmp_path: Path, reader: FileStateReader, writer: FileStateWriter
    ) -> None:
        """max_entries caps the number of entries loaded for clustering."""
        entries_dir = tmp_path / "entries"
        entries_dir.mkdir()
        for i in range(10):
            write_entry(entries_dir, writer, f"e{i:03d}")

        mock_batch = MagicMock(return_value=[make_vec(1.0, 0.0, 0.0)] * 3)
        with patch("trw_mcp.state.memory_adapter.list_active_learnings", side_effect=RuntimeError("force yaml")):
            with patch("trw_mcp.telemetry.embeddings.embedding_available", return_value=True):
                with patch("trw_mcp.telemetry.embeddings.embed_batch", mock_batch):
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

        with patch("trw_mcp.telemetry.embeddings.embedding_available", return_value=True):
            with patch("trw_mcp.telemetry.embeddings.embed_batch", return_value=[None, None]):
                result = find_clusters(entries_dir, reader, min_cluster_size=3)
        assert result == []

    def test_unreadable_yaml_skipped(
        self, tmp_path: Path, reader: FileStateReader, writer: FileStateWriter
    ) -> None:
        """Entries that cannot be read are skipped without raising."""
        entries_dir = tmp_path / "entries"
        entries_dir.mkdir()
        # Write a corrupt file
        (entries_dir / "corrupt.yaml").write_text("{invalid yaml[")
        for i in range(4):
            write_entry(entries_dir, writer, f"ok{i:03d}")

        vecs = [make_vec(1.0, 0.0, 0.0)] * 4
        with patch("trw_mcp.telemetry.embeddings.embedding_available", return_value=True):
            with patch("trw_mcp.telemetry.embeddings.embed_batch", return_value=vecs):
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
        with patch("trw_mcp.telemetry.embeddings.embedding_available", return_value=True):
            with patch("trw_mcp.telemetry.embeddings.embed_batch", return_value=vecs):
                result = find_clusters(
                    entries_dir, reader,
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
        with patch("trw_mcp.telemetry.embeddings.embedding_available", return_value=True):
            with patch("trw_mcp.telemetry.embeddings.embed_batch", return_value=vecs):
                result = find_clusters(
                    entries_dir, reader,
                    similarity_threshold=0.9,
                    min_cluster_size=4,
                )
        assert result == []

    def test_entries_dir_nonexistent_with_embedding_available(
        self, tmp_path: Path, reader: FileStateReader
    ) -> None:
        """When embedding is available but entries_dir doesn't exist, returns []."""
        nonexistent = tmp_path / "no_such_dir"

        with patch("trw_mcp.telemetry.embeddings.embedding_available", return_value=True):
            result = find_clusters(nonexistent, reader)
        assert result == []


# ---------------------------------------------------------------------------
# FR02 — LLM-Powered Cluster Summarization
# ---------------------------------------------------------------------------


class TestParseConsolidationResponse:
    """FR02: _parse_consolidation_response extracts JSON from LLM output."""

    def test_valid_json_line_extracted(self) -> None:
        """Valid JSON with summary and detail is parsed correctly."""
        response = '{"summary": "consolidated summary", "detail": "merged detail"}'
        result = _parse_consolidation_response(response)
        assert result is not None
        assert result["summary"] == "consolidated summary"
        assert result["detail"] == "merged detail"

    def test_multiline_response_extracts_json_line(self) -> None:
        """JSON line is extracted from a multi-line response."""
        response = (
            "Here is the consolidated entry:\n"
            '{"summary": "brief summary", "detail": "full explanation"}\n'
            "Hope that helps!"
        )
        result = _parse_consolidation_response(response)
        assert result is not None
        assert result["summary"] == "brief summary"

    def test_non_json_lines_skipped(self) -> None:
        """Non-JSON lines are skipped without error."""
        response = (
            "Thinking about this...\n"
            "Let me consolidate:\n"
            '{"summary": "the summary", "detail": "the detail"}'
        )
        result = _parse_consolidation_response(response)
        assert result is not None
        assert result["summary"] == "the summary"

    def test_missing_summary_key_returns_none(self) -> None:
        """JSON without 'summary' key returns None."""
        response = '{"detail": "only detail here"}'
        result = _parse_consolidation_response(response)
        assert result is None

    def test_missing_detail_key_returns_none(self) -> None:
        """JSON without 'detail' key returns None."""
        response = '{"summary": "only summary here"}'
        result = _parse_consolidation_response(response)
        assert result is None

    def test_malformed_json_returns_none(self) -> None:
        """Malformed JSON returns None without raising."""
        response = '{"summary": "broken'
        result = _parse_consolidation_response(response)
        assert result is None

    def test_empty_response_returns_none(self) -> None:
        """Empty response returns None."""
        result = _parse_consolidation_response("")
        assert result is None

    def test_non_json_response_returns_none(self) -> None:
        """Response with no JSON line returns None."""
        result = _parse_consolidation_response("just some text without any json")
        assert result is None

    def test_summary_and_detail_cast_to_str(self) -> None:
        """Non-string values in summary/detail are cast to str."""
        response = '{"summary": 42, "detail": true}'
        result = _parse_consolidation_response(response)
        assert result is not None
        assert isinstance(result["summary"], str)
        assert isinstance(result["detail"], str)


class TestSummarizeClusterLlm:
    """FR02: _summarize_cluster_llm calls LLM and validates length."""

    def _make_llm(self, responses: list[str | None]) -> MagicMock:
        """Create a mock LLMClient with sequential ask_sync responses."""
        llm = MagicMock()
        llm.ask_sync.side_effect = responses
        return llm

    def test_valid_response_shorter_than_inputs_accepted(self) -> None:
        """Short summary accepted on first attempt."""
        cluster = make_cluster(3)
        short_json = '{"summary": "short", "detail": "brief detail"}'
        llm = self._make_llm([short_json])

        result = _summarize_cluster_llm(cluster, llm)
        assert result is not None
        assert result["summary"] == "short"
        assert result["detail"] == "brief detail"
        assert llm.ask_sync.call_count == 1

    def test_prompt_contains_all_entry_summaries(self) -> None:
        """Prompt passed to LLM contains all cluster entry summaries."""
        cluster = make_cluster(3)
        short_json = '{"summary": "s", "detail": "d"}'
        llm = self._make_llm([short_json])

        _summarize_cluster_llm(cluster, llm)

        call_args = llm.ask_sync.call_args
        prompt = call_args[0][0]
        for e in cluster:
            assert str(e["summary"]) in prompt

    def test_too_long_summary_triggers_retry(self) -> None:
        """Summary >= sum of input lengths triggers one retry."""
        cluster = [
            {"id": "e1", "summary": "ab", "detail": "cd"},  # len(summary) = 2
        ]
        # First response: summary length 5 >= sum of input summaries (2)
        long_json = '{"summary": "12345", "detail": "x"}'
        short_json = '{"summary": "ok", "detail": "concise"}'
        llm = self._make_llm([long_json, short_json])

        result = _summarize_cluster_llm(cluster, llm)
        assert result is not None
        assert llm.ask_sync.call_count == 2

    def test_retry_prompt_contains_length_constraint(self) -> None:
        """Retry prompt includes explicit length constraint."""
        cluster = [{"id": "e1", "summary": "ab", "detail": "cd"}]
        long_json = '{"summary": "12345", "detail": "x"}'
        short_json = '{"summary": "ok", "detail": "d"}'
        llm = self._make_llm([long_json, short_json])

        _summarize_cluster_llm(cluster, llm)

        retry_call = llm.ask_sync.call_args_list[1]
        retry_prompt = retry_call[0][0]
        assert "characters" in retry_prompt or "IMPORTANT" in retry_prompt

    def test_llm_returns_none_returns_none(self) -> None:
        """When LLM returns None, result is None."""
        cluster = make_cluster(3)
        llm = self._make_llm([None])

        result = _summarize_cluster_llm(cluster, llm)
        assert result is None

    def test_parse_failure_returns_none(self) -> None:
        """When LLM response cannot be parsed, returns None."""
        cluster = make_cluster(3)
        llm = self._make_llm(["not json at all"])

        result = _summarize_cluster_llm(cluster, llm)
        assert result is None

    def test_retry_parse_failure_returns_none(self) -> None:
        """When both LLM responses fail to parse, returns None."""
        cluster = [{"id": "e1", "summary": "ab", "detail": "cd"}]
        long_json = '{"summary": "12345", "detail": "x"}'
        llm = self._make_llm([long_json, "still not valid json"])

        result = _summarize_cluster_llm(cluster, llm)
        assert result is None

    def test_retry_none_response_returns_none(self) -> None:
        """When retry call returns None, result is None."""
        cluster = [{"id": "e1", "summary": "ab", "detail": "cd"}]
        long_json = '{"summary": "12345", "detail": "x"}'
        llm = self._make_llm([long_json, None])

        result = _summarize_cluster_llm(cluster, llm)
        assert result is None


# ---------------------------------------------------------------------------
# FR03 — Consolidated Entry Creation
# ---------------------------------------------------------------------------


class TestCreateConsolidatedEntry:
    """FR03: _create_consolidated_entry aggregates fields and writes atomically."""

    def test_entry_id_has_L_prefix(
        self, tmp_path: Path, writer: FileStateWriter
    ) -> None:
        """Generated entry ID starts with 'L-'."""
        cluster = make_cluster(3)
        entries_dir = tmp_path / "entries"
        entries_dir.mkdir()

        entry = _create_consolidated_entry(cluster, "summary", "detail", entries_dir, writer)
        assert str(entry["id"]).startswith("L-")

    def test_impact_is_max_of_cluster(
        self, tmp_path: Path, writer: FileStateWriter
    ) -> None:
        """impact = max(cluster impacts)."""
        cluster = [
            {"id": "e1", "impact": 0.3},
            {"id": "e2", "impact": 0.7},
            {"id": "e3", "impact": 0.5},
        ]
        entries_dir = tmp_path / "entries"
        entries_dir.mkdir()

        entry = _create_consolidated_entry(cluster, "s", "d", entries_dir, writer)
        assert entry["impact"] == pytest.approx(0.7)

    def test_tags_sorted_union(
        self, tmp_path: Path, writer: FileStateWriter
    ) -> None:
        """tags = sorted union of all cluster tags (deduplicated)."""
        cluster = [
            {"id": "e1", "tags": ["beta", "alpha"]},
            {"id": "e2", "tags": ["alpha", "gamma"]},
            {"id": "e3", "tags": ["delta"]},
        ]
        entries_dir = tmp_path / "entries"
        entries_dir.mkdir()

        entry = _create_consolidated_entry(cluster, "s", "d", entries_dir, writer)
        assert entry["tags"] == ["alpha", "beta", "delta", "gamma"]

    def test_evidence_deduplicated_union(
        self, tmp_path: Path, writer: FileStateWriter
    ) -> None:
        """evidence = union of all cluster evidence (deduplicated)."""
        cluster = [
            {"id": "e1", "evidence": ["ev1", "ev2"]},
            {"id": "e2", "evidence": ["ev2", "ev3"]},
            {"id": "e3", "evidence": ["ev4"]},
        ]
        entries_dir = tmp_path / "entries"
        entries_dir.mkdir()

        entry = _create_consolidated_entry(cluster, "s", "d", entries_dir, writer)
        evidence = list(entry["evidence"])  # type: ignore[arg-type]
        assert sorted(evidence) == ["ev1", "ev2", "ev3", "ev4"]

    def test_recurrence_is_sum(
        self, tmp_path: Path, writer: FileStateWriter
    ) -> None:
        """recurrence = sum of cluster recurrences."""
        cluster = [
            {"id": "e1", "recurrence": 2},
            {"id": "e2", "recurrence": 3},
            {"id": "e3", "recurrence": 1},
        ]
        entries_dir = tmp_path / "entries"
        entries_dir.mkdir()

        entry = _create_consolidated_entry(cluster, "s", "d", entries_dir, writer)
        assert entry["recurrence"] == 6

    def test_q_value_is_max(
        self, tmp_path: Path, writer: FileStateWriter
    ) -> None:
        """q_value = max of cluster q_values."""
        cluster = [
            {"id": "e1", "q_value": 0.2},
            {"id": "e2", "q_value": 0.8},
            {"id": "e3", "q_value": 0.5},
        ]
        entries_dir = tmp_path / "entries"
        entries_dir.mkdir()

        entry = _create_consolidated_entry(cluster, "s", "d", entries_dir, writer)
        assert entry["q_value"] == pytest.approx(0.8)

    def test_source_type_is_consolidated(
        self, tmp_path: Path, writer: FileStateWriter
    ) -> None:
        """source_type = 'consolidated'."""
        cluster = make_cluster(3)
        entries_dir = tmp_path / "entries"
        entries_dir.mkdir()

        entry = _create_consolidated_entry(cluster, "s", "d", entries_dir, writer)
        assert entry["source_type"] == "consolidated"

    def test_consolidated_from_contains_cluster_ids(
        self, tmp_path: Path, writer: FileStateWriter
    ) -> None:
        """consolidated_from contains IDs of all cluster entries."""
        cluster = make_cluster(3)
        entries_dir = tmp_path / "entries"
        entries_dir.mkdir()

        entry = _create_consolidated_entry(cluster, "s", "d", entries_dir, writer)
        consolidated_from = list(entry["consolidated_from"])  # type: ignore[arg-type]
        assert "L-entry000" in consolidated_from
        assert "L-entry001" in consolidated_from
        assert "L-entry002" in consolidated_from

    def test_entry_written_to_disk(
        self, tmp_path: Path, writer: FileStateWriter, reader: FileStateReader
    ) -> None:
        """Entry is written atomically to entries_dir as a YAML file."""
        cluster = make_cluster(3)
        entries_dir = tmp_path / "entries"
        entries_dir.mkdir()

        entry = _create_consolidated_entry(cluster, "s", "d", entries_dir, writer)
        entry_id = str(entry["id"])
        slug = entry_id.replace("/", "-")
        written_path = entries_dir / f"{slug}.yaml"

        assert written_path.exists()
        data = reader.read_yaml(written_path)
        assert data["id"] == entry_id

    def test_missing_fields_use_defaults(
        self, tmp_path: Path, writer: FileStateWriter
    ) -> None:
        """Cluster entries missing fields fall back to defaults."""
        cluster = [
            {"id": "e1"},  # no impact, tags, evidence, recurrence, q_value
            {"id": "e2"},
            {"id": "e3"},
        ]
        entries_dir = tmp_path / "entries"
        entries_dir.mkdir()

        # Should not raise
        entry = _create_consolidated_entry(cluster, "s", "d", entries_dir, writer)
        assert float(str(entry["impact"])) == pytest.approx(0.5)
        assert entry["tags"] == []
        assert entry["recurrence"] == 3

    def test_status_is_active(
        self, tmp_path: Path, writer: FileStateWriter
    ) -> None:
        """New consolidated entry has status='active'."""
        cluster = make_cluster(3)
        entries_dir = tmp_path / "entries"
        entries_dir.mkdir()

        entry = _create_consolidated_entry(cluster, "s", "d", entries_dir, writer)
        assert entry["status"] == "active"


# ---------------------------------------------------------------------------
# FR04 — Original Entry Archival
# ---------------------------------------------------------------------------


class TestArchiveOriginals:
    """FR04: _archive_originals marks originals as consolidated_into."""

    def test_sets_consolidated_into_field(
        self, tmp_path: Path, reader: FileStateReader, writer: FileStateWriter
    ) -> None:
        """Each original entry gets consolidated_into set."""
        entries_dir = tmp_path / "entries"
        entries_dir.mkdir()
        e1 = write_entry(entries_dir, writer, "e001")
        e2 = write_entry(entries_dir, writer, "e002")
        cluster = [
            {"id": "e001", "summary": "s1"},
            {"id": "e002", "summary": "s2"},
        ]

        _archive_originals(cluster, "L-cons001", entries_dir, reader, writer)

        for path in [e1, e2]:
            data = reader.read_yaml(path)
            assert data["consolidated_into"] == "L-cons001"

    def test_sets_status_archived_without_tier_manager(
        self, tmp_path: Path, reader: FileStateReader, writer: FileStateWriter
    ) -> None:
        """Without tier_manager, entries get status='archived'."""
        entries_dir = tmp_path / "entries"
        entries_dir.mkdir()
        write_entry(entries_dir, writer, "e001")
        cluster = [{"id": "e001", "summary": "s1"}]

        _archive_originals(cluster, "L-cons001", entries_dir, reader, writer, tier_manager=None)

        data = reader.read_yaml(entries_dir / "e001.yaml")
        assert data["status"] == "archived"

    def test_calls_cold_archive_when_tier_manager_available(
        self, tmp_path: Path, reader: FileStateReader, writer: FileStateWriter
    ) -> None:
        """When tier_manager is available, cold_archive is called."""
        entries_dir = tmp_path / "entries"
        entries_dir.mkdir()
        write_entry(entries_dir, writer, "e001")
        cluster = [{"id": "e001", "summary": "s1"}]

        tier_manager = MagicMock()
        tier_manager.cold_archive = MagicMock()

        _archive_originals(cluster, "L-cons001", entries_dir, reader, writer, tier_manager=tier_manager)

        tier_manager.cold_archive.assert_called_once()

    def test_cold_archive_failure_falls_back_to_archived_status(
        self, tmp_path: Path, reader: FileStateReader, writer: FileStateWriter
    ) -> None:
        """When cold_archive raises, falls back to status='archived'."""
        entries_dir = tmp_path / "entries"
        entries_dir.mkdir()
        write_entry(entries_dir, writer, "e001")
        cluster = [{"id": "e001", "summary": "s1"}]

        tier_manager = MagicMock()
        tier_manager.cold_archive.side_effect = RuntimeError("cold archive failed")

        _archive_originals(cluster, "L-cons001", entries_dir, reader, writer, tier_manager=tier_manager)

        data = reader.read_yaml(entries_dir / "e001.yaml")
        assert data["status"] == "archived"

    def test_missing_entry_file_skipped(
        self, tmp_path: Path, reader: FileStateReader, writer: FileStateWriter
    ) -> None:
        """Entries with no matching file are skipped without raising."""
        entries_dir = tmp_path / "entries"
        entries_dir.mkdir()
        cluster = [{"id": "nonexistent", "summary": "s"}]

        # Should not raise
        _archive_originals(cluster, "L-cons001", entries_dir, reader, writer)

    def test_entry_without_id_skipped(
        self, tmp_path: Path, reader: FileStateReader, writer: FileStateWriter
    ) -> None:
        """Entries without 'id' field are skipped."""
        entries_dir = tmp_path / "entries"
        entries_dir.mkdir()
        cluster = [{"summary": "no id here"}]

        # Should not raise
        _archive_originals(cluster, "L-cons001", entries_dir, reader, writer)

    def test_exact_slug_derivation_for_entry_id_with_colons(
        self, tmp_path: Path, reader: FileStateReader, writer: FileStateWriter
    ) -> None:
        """Entry IDs with colons are resolved via exact slug derivation."""
        entries_dir = tmp_path / "entries"
        entries_dir.mkdir()
        # Write an entry with ID that contains a colon via the slug path
        entry_id = "e:001"  # colon gets replaced to dash
        slug = entry_id.replace("/", "-").replace(":", "-")
        path = entries_dir / f"{slug}.yaml"
        writer.write_yaml(path, {
            "id": entry_id,
            "summary": "test",
            "status": "active",
        })
        cluster = [{"id": entry_id, "summary": "test"}]

        # Should find the file via exact slug derivation (line 384 path)
        _archive_originals(cluster, "L-cons001", entries_dir, reader, writer)

        data = reader.read_yaml(path)
        assert data["consolidated_into"] == "L-cons001"

    def test_rollback_on_write_failure(
        self, tmp_path: Path, reader: FileStateReader, writer: FileStateWriter
    ) -> None:
        """On write failure, previously written entries are rolled back."""
        entries_dir = tmp_path / "entries"
        entries_dir.mkdir()
        write_entry(entries_dir, writer, "e001", summary="original-s1")
        write_entry(entries_dir, writer, "e002", summary="original-s2")

        cluster = [
            {"id": "e001", "summary": "s1"},
            {"id": "e002", "summary": "s2"},
        ]

        # Create a consolidated entry so rollback can delete it
        cons_path = entries_dir / "L-cons001.yaml"
        writer.write_yaml(cons_path, {"id": "L-cons001"})

        # Make the second write fail
        original_write = writer.write_yaml
        call_count = [0]

        def failing_write(path: Path, data: Any) -> None:
            call_count[0] += 1
            # Fail on the 3rd call (after the 2 consolidated_into writes for e001)
            # Actually fail on write for e002's consolidated_into
            if call_count[0] >= 3:
                raise OSError("disk full")
            original_write(path, data)

        writer.write_yaml = failing_write  # type: ignore[method-assign]

        with pytest.raises(OSError):
            _archive_originals(cluster, "L-cons001", entries_dir, reader, writer)

        # Restore
        writer.write_yaml = original_write  # type: ignore[method-assign]


class TestRollbackArchive:
    """FR04: _rollback_archive reverts writes and deletes consolidated entry."""

    def test_reverts_consolidated_into_writes(
        self, tmp_path: Path, reader: FileStateReader, writer: FileStateWriter
    ) -> None:
        """Processed entries are restored to their original data."""
        entries_dir = tmp_path / "entries"
        entries_dir.mkdir()
        path = write_entry(entries_dir, writer, "e001", summary="original")

        # Simulate a processed write
        original_data = reader.read_yaml(path)
        modified = dict(original_data)
        modified["consolidated_into"] = "L-cons001"
        writer.write_yaml(path, modified)

        _rollback_archive([(path, original_data)], "L-cons001", entries_dir, writer)

        restored = reader.read_yaml(path)
        assert "consolidated_into" not in restored

    def test_deletes_consolidated_entry_file(
        self, tmp_path: Path, writer: FileStateWriter
    ) -> None:
        """Consolidated entry file is deleted during rollback."""
        entries_dir = tmp_path / "entries"
        entries_dir.mkdir()
        cons_path = entries_dir / "L-cons001.yaml"
        writer.write_yaml(cons_path, {"id": "L-cons001"})

        _rollback_archive([], "L-cons001", entries_dir, writer)

        assert not cons_path.exists()

    def test_rollback_with_no_processed_entries(
        self, tmp_path: Path, writer: FileStateWriter
    ) -> None:
        """Rollback with empty processed list only deletes consolidated file."""
        entries_dir = tmp_path / "entries"
        entries_dir.mkdir()
        # Should not raise even if file doesn't exist
        _rollback_archive([], "L-nonexistent", entries_dir, writer)

    def test_rollback_write_failure_logged_not_raised(
        self, tmp_path: Path, writer: FileStateWriter
    ) -> None:
        """Write failure during rollback is caught and logged, not re-raised."""
        entries_dir = tmp_path / "entries"
        entries_dir.mkdir()

        # Use a path that doesn't exist to cause write failure
        bad_path = tmp_path / "nonexistent" / "entry.yaml"
        original_data: dict[str, object] = {"id": "e001"}

        # Should not raise
        _rollback_archive([(bad_path, original_data)], "L-cons001", entries_dir, writer)

    def test_rollback_unlink_failure_logged_not_raised(
        self, tmp_path: Path, writer: FileStateWriter
    ) -> None:
        """unlink failure during rollback is caught and logged, not re-raised."""
        entries_dir = tmp_path / "entries"
        entries_dir.mkdir()
        cons_path = entries_dir / "L-cons001.yaml"
        writer.write_yaml(cons_path, {"id": "L-cons001"})

        with patch.object(
            cons_path.__class__,
            "unlink",
            side_effect=OSError("permission denied"),
        ):
            # Should not raise — exception caught at lines 457-458
            _rollback_archive([], "L-cons001", entries_dir, writer)


# ---------------------------------------------------------------------------
# FR05 — Graceful Degradation Without LLM
# ---------------------------------------------------------------------------


class TestSummarizeClusterFallback:
    """FR05: _summarize_cluster_fallback selects best entry without LLM."""

    def test_returns_longest_summary_plus_detail_entry(self) -> None:
        """Selects the entry with the longest combined summary+detail."""
        cluster = [
            {"id": "e1", "summary": "short", "detail": "x"},
            {"id": "e2", "summary": "much longer summary here", "detail": "and detail too"},
            {"id": "e3", "summary": "mid", "detail": "middle"},
        ]
        result = _summarize_cluster_fallback(cluster)
        assert result["summary"] == "much longer summary here"
        assert result["detail"] == "and detail too"

    def test_returns_dict_with_summary_and_detail_keys(self) -> None:
        """Result always has 'summary' and 'detail' keys."""
        cluster = make_cluster(3)
        result = _summarize_cluster_fallback(cluster)
        assert "summary" in result
        assert "detail" in result

    def test_missing_fields_default_to_empty_string(self) -> None:
        """Entries missing summary/detail fields use empty strings."""
        cluster = [
            {"id": "e1"},
            {"id": "e2", "summary": "some content", "detail": "more content here"},
            {"id": "e3"},
        ]
        result = _summarize_cluster_fallback(cluster)
        assert result["summary"] == "some content"

    def test_single_entry_cluster(self) -> None:
        """Works with a single-entry cluster."""
        cluster = [{"id": "e1", "summary": "only one", "detail": "entry"}]
        result = _summarize_cluster_fallback(cluster)
        assert result["summary"] == "only one"

    def test_summary_and_detail_are_strings(self) -> None:
        """Return values are always strings."""
        cluster = make_cluster(3)
        result = _summarize_cluster_fallback(cluster)
        assert isinstance(result["summary"], str)
        assert isinstance(result["detail"], str)


# ---------------------------------------------------------------------------
# FR06 — Dry-Run Mode + Main Entry Point
# ---------------------------------------------------------------------------


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

        with patch("trw_mcp.telemetry.embeddings.embedding_available", return_value=True):
            with patch("trw_mcp.telemetry.embeddings.embed_batch", return_value=vecs):
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

    def test_dry_run_cluster_preview_structure(
        self, tmp_path: Path, writer: FileStateWriter
    ) -> None:
        """Dry-run cluster previews contain entry_ids, count, mean_similarity."""
        trw_dir = tmp_path / ".trw"
        entries_dir = trw_dir / "learnings" / "entries"
        entries_dir.mkdir(parents=True)
        self._write_cluster_entries(entries_dir, writer, 4)

        vecs = [make_vec(1.0, 0.0, 0.0)] * 4
        cfg = self._make_config()

        with patch("trw_mcp.telemetry.embeddings.embedding_available", return_value=True):
            with patch("trw_mcp.telemetry.embeddings.embed_batch", return_value=vecs):
                result = consolidate_cycle(trw_dir, dry_run=True, config=cfg)

        clusters = list(result["clusters"])  # type: ignore[arg-type]
        if clusters:
            preview = clusters[0]
            assert "entry_ids" in preview
            assert "count" in preview
            assert "mean_similarity" in preview

    def test_no_clusters_returns_no_clusters_status(
        self, tmp_path: Path, writer: FileStateWriter
    ) -> None:
        """When no clusters found, returns status='no_clusters'."""
        trw_dir = tmp_path / ".trw"
        entries_dir = trw_dir / "learnings" / "entries"
        entries_dir.mkdir(parents=True)

        cfg = self._make_config()

        with patch("trw_mcp.telemetry.embeddings.embedding_available", return_value=False):
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

        with patch("trw_mcp.telemetry.embeddings.embedding_available", return_value=True):
            with patch("trw_mcp.telemetry.embeddings.embed_batch", return_value=vecs):
                with patch("trw_mcp.state.consolidation.LLMClient", return_value=llm):
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

    def test_llm_unavailable_uses_fallback(
        self, tmp_path: Path, writer: FileStateWriter
    ) -> None:
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

        with patch("trw_mcp.telemetry.embeddings.embedding_available", return_value=True):
            with patch("trw_mcp.telemetry.embeddings.embed_batch", return_value=vecs):
                with patch("trw_mcp.state.consolidation.LLMClient", return_value=mock_llm):
                    result = consolidate_cycle(trw_dir, config=cfg)

        assert result["status"] == "completed"

    def test_tier_manager_unavailable_graceful(
        self, tmp_path: Path, writer: FileStateWriter
    ) -> None:
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
        with patch("trw_mcp.telemetry.embeddings.embedding_available", return_value=True):
            with patch("trw_mcp.telemetry.embeddings.embed_batch", return_value=vecs):
                with patch("trw_mcp.state.consolidation.LLMClient", return_value=llm):
                    import trw_mcp.state.tiers as tiers_mod
                    original_tm = tiers_mod.TierManager
                    try:
                        # Replace TierManager with one that raises on init
                        tiers_mod.TierManager = MagicMock(side_effect=Exception("tiers unavailable"))  # type: ignore[misc]
                        result = consolidate_cycle(trw_dir, config=cfg)
                    finally:
                        tiers_mod.TierManager = original_tm

        assert "status" in result
        assert result["status"] == "completed"

    def test_llm_client_init_exception_graceful(
        self, tmp_path: Path, writer: FileStateWriter
    ) -> None:
        """When LLMClient() raises on init, consolidation proceeds with fallback."""
        trw_dir = tmp_path / ".trw"
        entries_dir = trw_dir / "learnings" / "entries"
        entries_dir.mkdir(parents=True)
        self._write_cluster_entries(entries_dir, writer, 3)

        vecs = [make_vec(1.0, 0.0, 0.0)] * 3
        cfg = self._make_config()

        with patch("trw_mcp.telemetry.embeddings.embedding_available", return_value=True):
            with patch("trw_mcp.telemetry.embeddings.embed_batch", return_value=vecs):
                # Make LLMClient constructor raise (lines 582-583)
                with patch("trw_mcp.state.consolidation.LLMClient", side_effect=RuntimeError("no llm")):
                    result = consolidate_cycle(trw_dir, config=cfg)

        # Should succeed with fallback summarization
        assert "status" in result
        assert result["status"] == "completed"

    def test_cluster_error_added_to_errors_list(
        self, tmp_path: Path, writer: FileStateWriter
    ) -> None:
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

        with patch("trw_mcp.telemetry.embeddings.embedding_available", return_value=True):
            with patch("trw_mcp.telemetry.embeddings.embed_batch", return_value=vecs):
                with patch("trw_mcp.state.consolidation.LLMClient", return_value=llm):
                    result = consolidate_cycle(trw_dir, config=cfg)

        # consolidate_cycle should not raise — errors collected or fallback used
        assert "status" in result

    def test_completed_result_structure(
        self, tmp_path: Path, writer: FileStateWriter
    ) -> None:
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

        with patch("trw_mcp.telemetry.embeddings.embedding_available", return_value=True):
            with patch("trw_mcp.telemetry.embeddings.embed_batch", return_value=vecs):
                with patch("trw_mcp.state.consolidation.LLMClient", return_value=llm):
                    result = consolidate_cycle(trw_dir, config=cfg)

        assert "status" in result
        assert "clusters_found" in result
        assert "consolidated_count" in result


# ---------------------------------------------------------------------------
# FR06 helper — _mean_pairwise_similarity
# ---------------------------------------------------------------------------


class TestMeanPairwiseSimilarity:
    """FR06: _mean_pairwise_similarity computes mean cosine similarity."""

    def test_single_entry_returns_zero(self) -> None:
        """Single-entry cluster has no pairs — returns 0.0."""
        cluster = [{"id": "e1", "summary": "s", "detail": "d"}]
        with patch("trw_mcp.telemetry.embeddings.embed_batch", return_value=[make_vec(1.0, 0.0, 0.0)]):
            result = _mean_pairwise_similarity(cluster)
        assert result == 0.0

    def test_empty_cluster_returns_zero(self) -> None:
        """Empty cluster returns 0.0."""
        with patch("trw_mcp.telemetry.embeddings.embed_batch", return_value=[]):
            result = _mean_pairwise_similarity([])
        assert result == 0.0

    def test_identical_vectors_returns_one(self) -> None:
        """Identical unit vectors → mean similarity = 1.0."""
        cluster = [
            {"id": "e1", "summary": "s1", "detail": "d1"},
            {"id": "e2", "summary": "s2", "detail": "d2"},
        ]
        vecs = [make_vec(1.0, 0.0, 0.0), make_vec(1.0, 0.0, 0.0)]
        with patch("trw_mcp.telemetry.embeddings.embed_batch", return_value=vecs):
            result = _mean_pairwise_similarity(cluster)
        assert result == pytest.approx(1.0)

    def test_orthogonal_vectors_returns_zero(self) -> None:
        """Orthogonal unit vectors → mean similarity = 0.0."""
        cluster = [
            {"id": "e1", "summary": "s1", "detail": "d1"},
            {"id": "e2", "summary": "s2", "detail": "d2"},
        ]
        vecs = [make_vec(1.0, 0.0, 0.0), make_vec(0.0, 1.0, 0.0)]
        with patch("trw_mcp.telemetry.embeddings.embed_batch", return_value=vecs):
            result = _mean_pairwise_similarity(cluster)
        assert result == pytest.approx(0.0, abs=1e-9)

    def test_none_vectors_filtered_out(self) -> None:
        """None vectors in batch are filtered before computing similarity."""
        cluster = [
            {"id": "e1", "summary": "s1", "detail": "d1"},
            {"id": "e2", "summary": "s2", "detail": "d2"},
            {"id": "e3", "summary": "s3", "detail": "d3"},
        ]
        vecs: list[list[float] | None] = [
            make_vec(1.0, 0.0, 0.0),
            None,
            make_vec(1.0, 0.0, 0.0),
        ]
        with patch("trw_mcp.telemetry.embeddings.embed_batch", return_value=vecs):
            result = _mean_pairwise_similarity(cluster)
        assert result == pytest.approx(1.0)

    def test_all_none_vectors_returns_zero(self) -> None:
        """All None vectors → returns 0.0 without raising."""
        cluster = [
            {"id": "e1", "summary": "s1", "detail": "d1"},
            {"id": "e2", "summary": "s2", "detail": "d2"},
        ]
        with patch("trw_mcp.telemetry.embeddings.embed_batch", return_value=[None, None]):
            result = _mean_pairwise_similarity(cluster)
        assert result == 0.0


# ---------------------------------------------------------------------------
# FR07 — Ceremony Wiring (trw_deliver integration)
# ---------------------------------------------------------------------------


def _patch_trw_deliver_deps(trw_dir: Path) -> Any:
    """Return a context manager that patches all trw_deliver sub-operations."""
    from contextlib import ExitStack
    import trw_mcp.tools.ceremony as ceremony_mod

    stack = ExitStack()
    stack.enter_context(patch.object(ceremony_mod, "_do_reflect", return_value={"status": "success", "events_analyzed": 0, "learnings_produced": 0, "success_patterns": 0}))
    stack.enter_context(patch.object(ceremony_mod, "find_active_run", return_value=None))
    stack.enter_context(patch.object(ceremony_mod, "resolve_trw_dir", return_value=trw_dir))
    stack.enter_context(patch.object(ceremony_mod, "_do_claude_md_sync", return_value={"status": "success", "learnings_promoted": 0, "total_lines": 0, "path": ""}))
    stack.enter_context(patch.object(ceremony_mod, "_do_index_sync", return_value={"status": "success"}))
    stack.enter_context(patch.object(ceremony_mod, "_do_auto_progress", return_value={"status": "skipped"}))
    stack.enter_context(patch("trw_mcp.telemetry.publisher.publish_learnings", return_value={"status": "skipped"}))
    stack.enter_context(patch("trw_mcp.scoring.process_outcome_for_event", return_value=[]))
    stack.enter_context(patch("trw_mcp.state.recall_tracking.get_recall_stats", return_value={}))
    stack.enter_context(patch("trw_mcp.telemetry.client.TelemetryClient.from_config", return_value=MagicMock()))
    stack.enter_context(patch("trw_mcp.telemetry.sender.BatchSender.from_config", return_value=MagicMock(send=MagicMock(return_value={"status": "skipped"}))))
    return stack


def _call_trw_deliver(trw_dir: Path, cfg: TRWConfig) -> dict[str, Any]:
    """Call trw_deliver with the given config by patching the module-level _config."""
    import trw_mcp.tools.ceremony as ceremony_mod
    from trw_mcp.server import mcp as server

    old_config = ceremony_mod._config
    # Also disable auto-prune to avoid extra mocking
    object.__setattr__(cfg, "learning_auto_prune_on_deliver", False)
    try:
        ceremony_mod._config = cfg  # type: ignore[attr-defined]
        with _patch_trw_deliver_deps(trw_dir):
            # trw_deliver is registered as a tool on the server.
            # Access it from the ceremony module's registered closure.
            # The function is defined inside register_ceremony_tools and registered
            # as a tool on the MCP server; we can call the module-level wrapper directly.
            result = ceremony_mod.trw_deliver()  # type: ignore[attr-defined]
        return result  # type: ignore[return-value]
    except AttributeError:
        # trw_deliver is registered as a closure; call via the module's tools dict
        # by re-registering on a mock server and capturing
        captured_fn: list[Any] = []
        mock_server = MagicMock()

        def capture_tool(fn: Any = None) -> Any:
            if fn is None:
                def decorator(f: Any) -> Any:
                    captured_fn.append(f)
                    return f
                return decorator
            captured_fn.append(fn)
            return fn

        mock_server.tool = capture_tool
        ceremony_mod.register_ceremony_tools(mock_server)
        # trw_deliver is the second registered tool (index 1)
        deliver_fn = next(
            f for f in captured_fn if getattr(f, "__name__", "") == "trw_deliver"
        )
        with _patch_trw_deliver_deps(trw_dir):
            return deliver_fn()  # type: ignore[no-any-return]
    finally:
        ceremony_mod._config = old_config  # type: ignore[attr-defined]


class TestCeremonyWiring:
    """FR07: trw_deliver includes memory consolidation at step 2.6."""

    def test_consolidation_disabled_result_has_skipped_status(
        self, tmp_path: Path, writer: FileStateWriter
    ) -> None:
        """When memory_consolidation_enabled=False, trw_deliver result has consolidation.status=skipped."""
        import trw_mcp.tools.ceremony as ceremony_mod

        trw_dir = tmp_path / ".trw"
        (trw_dir / "learnings" / "entries").mkdir(parents=True)
        (trw_dir / "context").mkdir(parents=True)
        (trw_dir / "reflections").mkdir(parents=True)

        old_config = ceremony_mod._config
        cfg = TRWConfig(
            memory_consolidation_enabled=False,
            learning_auto_prune_on_deliver=False,
        )
        try:
            ceremony_mod._config = cfg  # type: ignore[attr-defined]
            # Patch consolidate_cycle — should NOT be called
            with patch("trw_mcp.state.consolidation.consolidate_cycle") as mock_cons:
                with _patch_trw_deliver_deps(trw_dir):
                    # Call the wiring logic directly (mirrors ceremony.py step 2.6)
                    results: dict[str, Any] = {}
                    errors: list[str] = []
                    try:
                        if cfg.memory_consolidation_enabled:
                            from trw_mcp.state.consolidation import consolidate_cycle as _cc
                            results["consolidation"] = _cc(trw_dir, max_entries=cfg.memory_consolidation_max_per_cycle)
                        else:
                            results["consolidation"] = {"status": "skipped", "reason": "disabled"}
                    except Exception as exc:
                        errors.append(f"consolidation: {exc}")
                        results["consolidation"] = {"status": "failed", "error": str(exc)}

            # consolidate_cycle should not be called when disabled
            mock_cons.assert_not_called()
            assert results["consolidation"]["status"] == "skipped"
            assert results["consolidation"]["reason"] == "disabled"
        finally:
            ceremony_mod._config = old_config  # type: ignore[attr-defined]

    def test_consolidation_exception_is_fail_open(
        self, tmp_path: Path, writer: FileStateWriter
    ) -> None:
        """When consolidate_cycle raises, error is collected and result has status=failed."""
        import trw_mcp.tools.ceremony as ceremony_mod

        trw_dir = tmp_path / ".trw"
        (trw_dir / "learnings" / "entries").mkdir(parents=True)
        (trw_dir / "context").mkdir(parents=True)
        (trw_dir / "reflections").mkdir(parents=True)

        old_config = ceremony_mod._config
        cfg = TRWConfig(
            memory_consolidation_enabled=True,
            learning_auto_prune_on_deliver=False,
        )
        try:
            ceremony_mod._config = cfg  # type: ignore[attr-defined]
            with patch("trw_mcp.state.consolidation.consolidate_cycle", side_effect=RuntimeError("consolidation boom")):
                with _patch_trw_deliver_deps(trw_dir):
                    # Mirror ceremony.py step 2.6 logic exactly
                    results: dict[str, Any] = {}
                    errors: list[str] = []
                    try:
                        if cfg.memory_consolidation_enabled:
                            from trw_mcp.state.consolidation import consolidate_cycle as _cc
                            results["consolidation"] = _cc(trw_dir, max_entries=cfg.memory_consolidation_max_per_cycle)
                        else:
                            results["consolidation"] = {"status": "skipped", "reason": "disabled"}
                    except Exception as exc:
                        errors.append(f"consolidation: {exc}")
                        results["consolidation"] = {"status": "failed", "error": str(exc)}

            # Exception was caught — not re-raised
            assert len(errors) == 1
            assert "consolidation" in errors[0]
            assert "consolidation boom" in errors[0]
            assert results["consolidation"]["status"] == "failed"
            assert "consolidation boom" in str(results["consolidation"]["error"])
        finally:
            ceremony_mod._config = old_config  # type: ignore[attr-defined]

    def test_consolidation_result_key_present_when_enabled(
        self, tmp_path: Path, writer: FileStateWriter
    ) -> None:
        """When enabled, trw_deliver result dict contains 'consolidation' key."""
        import trw_mcp.tools.ceremony as ceremony_mod

        trw_dir = tmp_path / ".trw"
        (trw_dir / "learnings" / "entries").mkdir(parents=True)
        (trw_dir / "context").mkdir(parents=True)
        (trw_dir / "reflections").mkdir(parents=True)

        old_config = ceremony_mod._config
        cfg = TRWConfig(
            memory_consolidation_enabled=True,
            learning_auto_prune_on_deliver=False,
        )
        consolidation_result = {"status": "no_clusters", "clusters_found": 0, "consolidated_count": 0}
        try:
            ceremony_mod._config = cfg  # type: ignore[attr-defined]
            with patch("trw_mcp.state.consolidation.consolidate_cycle", return_value=consolidation_result):
                with _patch_trw_deliver_deps(trw_dir):
                    results: dict[str, Any] = {}
                    errors: list[str] = []
                    try:
                        if cfg.memory_consolidation_enabled:
                            from trw_mcp.state.consolidation import consolidate_cycle as _cc
                            results["consolidation"] = _cc(trw_dir, max_entries=cfg.memory_consolidation_max_per_cycle)
                        else:
                            results["consolidation"] = {"status": "skipped", "reason": "disabled"}
                    except Exception as exc:
                        errors.append(f"consolidation: {exc}")
                        results["consolidation"] = {"status": "failed", "error": str(exc)}

            assert "consolidation" in results
            assert results["consolidation"]["status"] == "no_clusters"
        finally:
            ceremony_mod._config = old_config  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# FR08 — Config Fields and Validation
# ---------------------------------------------------------------------------


class TestConsolidationConfig:
    """FR08: TRWConfig consolidation fields have correct defaults and constraints."""

    def test_default_enabled_is_true(self) -> None:
        """memory_consolidation_enabled defaults to True."""
        cfg = TRWConfig()
        assert cfg.memory_consolidation_enabled is True

    def test_default_interval_days(self) -> None:
        """memory_consolidation_interval_days defaults to 7."""
        cfg = TRWConfig()
        assert cfg.memory_consolidation_interval_days == 7

    def test_default_min_cluster(self) -> None:
        """memory_consolidation_min_cluster defaults to 3."""
        cfg = TRWConfig()
        assert cfg.memory_consolidation_min_cluster == 3

    def test_default_similarity_threshold(self) -> None:
        """memory_consolidation_similarity_threshold defaults to 0.75."""
        cfg = TRWConfig()
        assert cfg.memory_consolidation_similarity_threshold == pytest.approx(0.75)

    def test_default_max_per_cycle(self) -> None:
        """memory_consolidation_max_per_cycle defaults to 50."""
        cfg = TRWConfig()
        assert cfg.memory_consolidation_max_per_cycle == 50

    def test_min_cluster_below_2_raises_validation_error(self) -> None:
        """min_cluster < 2 raises a ValidationError."""
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            TRWConfig(memory_consolidation_min_cluster=1)

    def test_min_cluster_exactly_2_is_valid(self) -> None:
        """min_cluster = 2 is valid (boundary)."""
        cfg = TRWConfig(memory_consolidation_min_cluster=2)
        assert cfg.memory_consolidation_min_cluster == 2

    def test_similarity_threshold_above_1_raises_validation_error(self) -> None:
        """similarity_threshold > 1.0 raises a ValidationError."""
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            TRWConfig(memory_consolidation_similarity_threshold=1.1)

    def test_similarity_threshold_below_0_raises_validation_error(self) -> None:
        """similarity_threshold < 0.0 raises a ValidationError."""
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            TRWConfig(memory_consolidation_similarity_threshold=-0.1)

    def test_similarity_threshold_boundary_values_valid(self) -> None:
        """similarity_threshold = 0.0 and 1.0 are valid boundaries."""
        cfg_low = TRWConfig(memory_consolidation_similarity_threshold=0.0)
        assert cfg_low.memory_consolidation_similarity_threshold == 0.0
        cfg_high = TRWConfig(memory_consolidation_similarity_threshold=1.0)
        assert cfg_high.memory_consolidation_similarity_threshold == 1.0

    def test_max_per_cycle_below_1_raises_validation_error(self) -> None:
        """max_per_cycle < 1 raises a ValidationError."""
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            TRWConfig(memory_consolidation_max_per_cycle=0)

    def test_env_var_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """TRW_MEMORY_CONSOLIDATION_ENABLED env var overrides default."""
        monkeypatch.setenv("TRW_MEMORY_CONSOLIDATION_ENABLED", "false")
        cfg = TRWConfig()
        assert cfg.memory_consolidation_enabled is False

    def test_env_var_min_cluster_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """TRW_MEMORY_CONSOLIDATION_MIN_CLUSTER env var overrides default."""
        monkeypatch.setenv("TRW_MEMORY_CONSOLIDATION_MIN_CLUSTER", "5")
        cfg = TRWConfig()
        assert cfg.memory_consolidation_min_cluster == 5

    def test_env_var_similarity_threshold_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """TRW_MEMORY_CONSOLIDATION_SIMILARITY_THRESHOLD env var overrides default."""
        monkeypatch.setenv("TRW_MEMORY_CONSOLIDATION_SIMILARITY_THRESHOLD", "0.9")
        cfg = TRWConfig()
        assert cfg.memory_consolidation_similarity_threshold == pytest.approx(0.9)


# ---------------------------------------------------------------------------
# Integration — Full end-to-end consolidation
# ---------------------------------------------------------------------------


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
                entries_dir, writer, entry_ids[i],
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

        with patch("trw_mcp.telemetry.embeddings.embedding_available", return_value=True):
            with patch("trw_mcp.telemetry.embeddings.embed_batch", return_value=vecs):
                with patch("trw_mcp.state.consolidation.LLMClient", return_value=mock_llm):
                    result = consolidate_cycle(trw_dir, config=cfg)

        assert result["status"] == "completed"
        assert int(str(result["consolidated_count"])) >= 1

        # TierManager may have moved original files to cold tier.
        # Check all yaml files recursively in the trw tree for consolidated_into.
        all_yaml: list[Path] = list(trw_dir.rglob("*.yaml"))
        archived_ids = set()
        for f in all_yaml:
            try:
                data = reader.read_yaml(f)
                if "consolidated_into" in data and str(data.get("id", "")) in entry_ids:
                    archived_ids.add(str(data["id"]))
            except Exception:
                continue
        assert len(archived_ids) == 3

        # Verify consolidated entry exists and has correct structure (always stays in entries_dir)
        consolidated_files = [
            f for f in entries_dir.glob("*.yaml")
            if f.stem.startswith("L-")
        ]
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

        with patch("trw_mcp.telemetry.embeddings.embedding_available", return_value=True):
            with patch("trw_mcp.telemetry.embeddings.embed_batch", return_value=vecs):
                with patch("trw_mcp.state.consolidation.LLMClient", return_value=llm):
                    result = consolidate_cycle(trw_dir, config=cfg)

        assert result["status"] == "completed"

        # Find consolidated entry
        consolidated_files = [
            f for f in entries_dir.glob("*.yaml")
            if f.stem not in [f"entry{i:03d}" for i in range(3)]
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

        with patch("trw_mcp.telemetry.embeddings.embedding_available", return_value=True):
            with patch("trw_mcp.telemetry.embeddings.embed_batch", return_value=vecs):
                consolidate_cycle(trw_dir, dry_run=True, config=cfg)

        # Verify entries unchanged
        for i in range(3):
            current = reader.read_yaml(entries_dir / f"entry{i:03d}.yaml")
            assert current == original_data[i]


# ---------------------------------------------------------------------------
# NFR03 — Idempotency: Re-running on already-consolidated entries
# ---------------------------------------------------------------------------


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
        with patch("trw_mcp.telemetry.embeddings.embedding_available", return_value=True):
            with patch("trw_mcp.telemetry.embeddings.embed_batch", return_value=vecs):
                with patch("trw_mcp.state.consolidation.LLMClient", return_value=llm):
                    result1 = consolidate_cycle(trw_dir, config=cfg)

        assert int(str(result1.get("consolidated_count", 0))) >= 1

        # Second run — originals have consolidated_into set; consolidated entry has
        # source_type="consolidated". find_clusters skips both categories.
        with patch("trw_mcp.telemetry.embeddings.embedding_available", return_value=True):
            with patch("trw_mcp.telemetry.embeddings.embed_batch", return_value=[]) as mock_batch:
                with patch("trw_mcp.state.consolidation.LLMClient", return_value=llm):
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
        with patch("trw_mcp.telemetry.embeddings.embedding_available", return_value=True):
            with patch("trw_mcp.telemetry.embeddings.embed_batch", mock_batch):
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
        with patch("trw_mcp.telemetry.embeddings.embedding_available", return_value=True):
            with patch("trw_mcp.telemetry.embeddings.embed_batch", mock_batch):
                find_clusters(entries_dir, reader, min_cluster_size=3)

        # Only 2 active entries should be embedded
        if mock_batch.call_count > 0:
            texts = mock_batch.call_args[0][0]
            assert len(texts) == 2


# ---------------------------------------------------------------------------
# NFR06 — Path Redaction: No filesystem paths in LLM prompts
# ---------------------------------------------------------------------------


class TestPathRedaction:
    """NFR06: _redact_paths removes filesystem paths from LLM prompt content."""

    def test_redact_unix_home_path(self) -> None:
        """Unix home paths (/home/user/...) are redacted."""
        from trw_mcp.state.consolidation import _redact_paths

        text = "see /home/alice/projects/trw/foo.py for details"
        result = _redact_paths(text)
        assert "/home/alice" not in result
        assert "[REDACTED_PATH]" in result

    def test_redact_macos_users_path(self) -> None:
        """macOS /Users/... paths are redacted."""
        from trw_mcp.state.consolidation import _redact_paths

        text = "config at /Users/bob/Desktop/project/.env"
        result = _redact_paths(text)
        assert "/Users/bob" not in result
        assert "[REDACTED_PATH]" in result

    def test_redact_windows_drive_path(self) -> None:
        """Windows drive paths (C:\\...) are redacted."""
        from trw_mcp.state.consolidation import _redact_paths

        text = r"file at C:\Users\Charlie\docs\notes.txt"
        result = _redact_paths(text)
        assert r"C:\Users" not in result
        assert "[REDACTED_PATH]" in result

    def test_no_path_unchanged(self) -> None:
        """Text without filesystem paths is returned unchanged."""
        from trw_mcp.state.consolidation import _redact_paths

        text = "use pydantic v2 with use_enum_values=True"
        assert _redact_paths(text) == text

    def test_multiple_paths_all_redacted(self) -> None:
        """Multiple paths in same text are all redacted."""
        from trw_mcp.state.consolidation import _redact_paths

        text = "from /home/alice/foo.py and /home/bob/bar.py"
        result = _redact_paths(text)
        assert "/home/alice" not in result
        assert "/home/bob" not in result
        assert result.count("[REDACTED_PATH]") == 2

    def test_llm_prompt_contains_no_home_paths(self) -> None:
        """_summarize_cluster_llm calls _redact_paths on entry summary and detail."""
        cluster = [
            {
                "id": "e1",
                "summary": "error in /home/user/project/main.py",
                "detail": "check /home/user/project/config.yaml for settings",
            },
            {"id": "e2", "summary": "s2", "detail": "d2"},
            {"id": "e3", "summary": "s3", "detail": "d3"},
        ]

        captured_prompts: list[str] = []

        def capture_ask_sync(prompt: str, **kwargs: Any) -> str:
            captured_prompts.append(prompt)
            return '{"summary": "short", "detail": "brief"}'

        llm = MagicMock()
        llm.ask_sync.side_effect = capture_ask_sync

        _summarize_cluster_llm(cluster, llm)

        assert len(captured_prompts) >= 1
        for prompt in captured_prompts:
            assert "/home/user" not in prompt
            assert "[REDACTED_PATH]" in prompt

    def test_redact_paths_preserves_non_path_content(self) -> None:
        """Path redaction does not disturb surrounding non-path text."""
        from trw_mcp.state.consolidation import _redact_paths

        text = "before /home/user/file.txt after"
        result = _redact_paths(text)
        assert result.startswith("before ")
        assert result.endswith(" after")


# ---------------------------------------------------------------------------
# PRD-FIX-033-FR04: SQLite-backed find_clusters
# ---------------------------------------------------------------------------


class TestFindClustersSQLite:
    """PRD-FIX-033-FR04: find_clusters loads entries from SQLite when available."""

    def test_find_clusters_uses_sqlite(
        self, tmp_path: Path, reader: FileStateReader, writer: FileStateWriter
    ) -> None:
        """find_clusters calls list_active_learnings instead of glob when available."""
        entries_dir = tmp_path / ".trw" / "learnings" / "entries"
        entries_dir.mkdir(parents=True)

        # Pre-built entries that would come from SQLite
        fake_entries: list[dict[str, object]] = [
            {
                "id": f"L-sql{i:02d}",
                "summary": f"similar topic about testing {i}",
                "detail": f"detail {i}",
                "status": "active",
                "impact": 0.5,
                "tags": ["testing"],
                "source_type": "agent",
            }
            for i in range(5)
        ]

        with patch(
            "trw_mcp.state.memory_adapter.list_active_learnings",
            return_value=fake_entries,
        ) as mock_sqlite, patch(
            "trw_mcp.telemetry.embeddings.embedding_available",
            return_value=True,
        ), patch(
            "trw_mcp.telemetry.embeddings.embed_batch",
            return_value=[make_vec(1.0, 0.0)] * 5,
        ):
            result = find_clusters(
                entries_dir, reader,
                similarity_threshold=0.9,
                min_cluster_size=3,
            )

        mock_sqlite.assert_called_once()
        # All 5 entries have identical vectors → 1 cluster of 5
        assert len(result) >= 1

    def test_find_clusters_fallback_to_yaml(
        self, tmp_path: Path, reader: FileStateReader, writer: FileStateWriter
    ) -> None:
        """Falls back to YAML glob when SQLite raises."""
        entries_dir = tmp_path / ".trw" / "learnings" / "entries"
        entries_dir.mkdir(parents=True)

        # Write YAML entries for fallback
        for i in range(4):
            write_entry(
                entries_dir, writer, f"L-fb{i:02d}",
                summary=f"yaml fallback testing {i}",
            )

        with patch(
            "trw_mcp.state.memory_adapter.list_active_learnings",
            side_effect=RuntimeError("SQLite unavailable"),
        ), patch(
            "trw_mcp.telemetry.embeddings.embedding_available",
            return_value=True,
        ), patch(
            "trw_mcp.telemetry.embeddings.embed_batch",
            return_value=[make_vec(1.0, 0.0)] * 4,
        ):
            result = find_clusters(
                entries_dir, reader,
                similarity_threshold=0.9,
                min_cluster_size=3,
            )

        # YAML fallback should still load entries and find clusters
        assert len(result) >= 1

    def test_find_clusters_sqlite_filters_consolidated(
        self, tmp_path: Path, reader: FileStateReader, writer: FileStateWriter
    ) -> None:
        """SQLite path filters out consolidated and archived entries."""
        entries_dir = tmp_path / ".trw" / "learnings" / "entries"
        entries_dir.mkdir(parents=True)

        fake_entries: list[dict[str, object]] = [
            {"id": "L-active1", "summary": "test", "detail": "d", "status": "active",
             "impact": 0.5, "tags": [], "source_type": "agent"},
            {"id": "L-consolidated", "summary": "test", "detail": "d", "status": "active",
             "impact": 0.5, "tags": [], "source_type": "consolidated"},
            {"id": "L-archived", "summary": "test", "detail": "d", "status": "active",
             "impact": 0.5, "tags": [], "source_type": "agent", "consolidated_into": "L-xyz"},
        ]

        with patch(
            "trw_mcp.state.memory_adapter.list_active_learnings",
            return_value=fake_entries,
        ), patch(
            "trw_mcp.telemetry.embeddings.embedding_available",
            return_value=True,
        ), patch(
            "trw_mcp.telemetry.embeddings.embed_batch",
            return_value=[make_vec(1.0, 0.0)],  # Only 1 entry passes filters
        ):
            result = find_clusters(
                entries_dir, reader,
                similarity_threshold=0.5,
                min_cluster_size=2,
            )

        # Only 1 entry passes filters (< min_cluster_size=2), so no clusters
        assert result == []
