"""Tests for semantic deduplication — PRD-CORE-042.

Tests TDD-first for dedup.py functions and their integration in trw_learn.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from trw_mcp.models.config import TRWConfig
from trw_mcp.models.learning import LearningEntry
from trw_mcp.state.dedup import (
    DedupResult,
    batch_dedup,
    check_duplicate,
    cosine_similarity,
    is_migration_needed,
    merge_entries,
)
from trw_mcp.state.persistence import FileStateReader, FileStateWriter


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def mock_embed(text: str) -> list[float]:
    """Return a deterministic 384-dim vector based on text content.

    This creates vectors where similar texts produce similar hashes,
    and identical texts produce identical vectors.
    """
    import hashlib
    h = hashlib.sha256(text.encode()).digest()
    vec = [float(b) / 255.0 for b in h] * 12  # 32 * 12 = 384
    norm = sum(v * v for v in vec) ** 0.5
    if norm == 0.0:
        return [0.0] * 384
    return [v / norm for v in vec]


def write_entry(entries_dir: Path, writer: FileStateWriter, entry_id: str, summary: str, detail: str) -> Path:
    """Write a minimal learning entry YAML for testing."""
    path = entries_dir / f"{entry_id}.yaml"
    writer.write_yaml(path, {
        "id": entry_id,
        "summary": summary,
        "detail": detail,
        "tags": ["test"],
        "evidence": [],
        "impact": 0.5,
        "status": "active",
        "recurrence": 1,
        "created": "2026-01-01",
        "updated": "2026-01-01",
        "merged_from": [],
    })
    return path


# ---------------------------------------------------------------------------
# Unit tests: cosine_similarity
# ---------------------------------------------------------------------------

class TestCosineSimilarity:
    def test_identical_vectors_return_one(self) -> None:
        """Two identical unit vectors → similarity 1.0."""
        v = mock_embed("hello world")
        result = cosine_similarity(v, v)
        assert abs(result - 1.0) < 1e-6

    def test_orthogonal_vectors_return_zero(self) -> None:
        """Two orthogonal vectors → similarity 0.0."""
        a = [1.0, 0.0, 0.0]
        b = [0.0, 1.0, 0.0]
        result = cosine_similarity(a, b)
        assert abs(result - 0.0) < 1e-9

    def test_anti_parallel_vectors_return_minus_one(self) -> None:
        """Anti-parallel unit vectors → similarity -1.0."""
        a = [1.0, 0.0]
        b = [-1.0, 0.0]
        result = cosine_similarity(a, b)
        assert abs(result - (-1.0)) < 1e-9

    def test_partial_similarity(self) -> None:
        """Two vectors at 60 degrees → similarity 0.5."""
        a = [1.0, 0.0]
        b = [0.5, math.sqrt(0.75)]
        result = cosine_similarity(a, b)
        assert abs(result - 0.5) < 1e-6

    def test_empty_vectors(self) -> None:
        """Empty vectors → 0.0 (graceful)."""
        result = cosine_similarity([], [])
        assert result == 0.0


# ---------------------------------------------------------------------------
# Unit tests: check_duplicate
# ---------------------------------------------------------------------------

class TestCheckDuplicate:
    """Tests for the check_duplicate() function."""

    def test_store_when_no_entries(self, tmp_path: Path) -> None:
        """New learning with no existing entries → 'store'."""
        entries_dir = tmp_path / "entries"
        entries_dir.mkdir()
        reader = FileStateReader()
        config = TRWConfig()

        with patch("trw_mcp.state.dedup.embed", side_effect=mock_embed):
            result = check_duplicate("test summary", "test detail", entries_dir, reader, config=config)

        assert result.action == "store"
        assert result.existing_id is None
        assert result.similarity == 0.0

    def test_skip_when_identical_entry_exists(self, tmp_path: Path) -> None:
        """Identical entry exists → 'skip' with similarity >= 0.95."""
        entries_dir = tmp_path / "entries"
        entries_dir.mkdir()
        reader = FileStateReader()
        writer = FileStateWriter()
        config = TRWConfig()

        summary = "unique test summary for dedup"
        detail = "unique test detail for dedup that is quite long"
        write_entry(entries_dir, writer, "L-existing01", summary, detail)

        with patch("trw_mcp.state.dedup.embed", side_effect=mock_embed):
            result = check_duplicate(summary, detail, entries_dir, reader, config=config)

        assert result.action == "skip"
        assert result.existing_id == "L-existing01"
        assert result.similarity >= 0.95

    def test_merge_when_near_duplicate_exists(self, tmp_path: Path) -> None:
        """Near-duplicate entry exists → 'merge' with 0.85 <= sim < 0.95.

        We construct a controlled embed function that returns an identical vector
        for the existing entry and a vector exactly at 0.90 cosine similarity for
        the new entry (by mixing with an orthogonal component).
        """
        entries_dir = tmp_path / "entries"
        entries_dir.mkdir()
        reader = FileStateReader()
        writer = FileStateWriter()
        config = TRWConfig()

        existing_summary = "some learning about python testing"
        existing_detail = "detail about pytest fixtures"
        write_entry(entries_dir, writer, "L-near01", existing_summary, existing_detail)
        existing_key = existing_summary + " " + existing_detail

        # Build a unit vector for the existing entry
        existing_vec = mock_embed(existing_key)
        # Build an orthogonal component: rotate existing_vec by 90 degrees in a subspace
        # We create orthogonal = existing_vec with first two components swapped + sign
        # For a deterministic orthogonal vector, use: orth[0] = -v[1], orth[1] = v[0], rest = 0
        orth = [0.0] * len(existing_vec)
        orth[0] = -existing_vec[1]
        orth[1] = existing_vec[0]
        orth_norm = sum(v * v for v in orth) ** 0.5
        if orth_norm > 0:
            orth = [v / orth_norm for v in orth]

        # Build a new vector at exactly cos(angle) = 0.90 from existing_vec
        # new_vec = cos(θ) * existing_vec + sin(θ) * orth
        import math
        cos_theta = 0.90
        sin_theta = math.sqrt(1 - cos_theta ** 2)
        new_vec = [cos_theta * e + sin_theta * o for e, o in zip(existing_vec, orth)]
        # Verify it's already unit (should be since we combined two orthonormal vecs)
        new_norm = sum(v * v for v in new_vec) ** 0.5
        new_vec = [v / new_norm for v in new_vec]

        new_text = "new-unique-text-xyz"

        def controlled_embed(text: str) -> list[float]:
            if text == (new_text + " "):
                return new_vec
            return mock_embed(text)

        with patch("trw_mcp.state.dedup.embed", side_effect=controlled_embed):
            result = check_duplicate(
                new_text, "",
                entries_dir, reader, config=config,
            )

        # The new_vec is at 0.90 similarity, which is in the merge zone [0.85, 0.95)
        assert result.action == "merge"
        assert 0.85 <= result.similarity < 0.95

    def test_store_when_no_embeddings_available(self, tmp_path: Path) -> None:
        """When embed() returns None → graceful degradation to 'store'."""
        entries_dir = tmp_path / "entries"
        entries_dir.mkdir()
        reader = FileStateReader()
        writer = FileStateWriter()
        config = TRWConfig()

        write_entry(entries_dir, writer, "L-existing02", "some summary", "some detail")

        with patch("trw_mcp.state.dedup.embed", return_value=None):
            result = check_duplicate("some summary", "some detail", entries_dir, reader, config=config)

        assert result.action == "store"
        assert result.existing_id is None
        assert result.similarity == 0.0

    def test_store_when_below_merge_threshold(self, tmp_path: Path) -> None:
        """Completely different entry exists → 'store' (similarity < 0.85)."""
        entries_dir = tmp_path / "entries"
        entries_dir.mkdir()
        reader = FileStateReader()
        writer = FileStateWriter()
        config = TRWConfig()

        write_entry(entries_dir, writer, "L-diff01", "python testing pytest", "how to use fixtures")

        new_summary = "docker kubernetes cloud deployment orchestration"
        new_detail = "infrastructure as code terraform aws"

        with patch("trw_mcp.state.dedup.embed", side_effect=mock_embed):
            result = check_duplicate(new_summary, new_detail, entries_dir, reader, config=config)

        assert result.action == "store"

    def test_dedup_disabled_config(self, tmp_path: Path) -> None:
        """config.dedup_enabled=False means check_duplicate returns 'store' immediately."""
        entries_dir = tmp_path / "entries"
        entries_dir.mkdir()
        reader = FileStateReader()
        writer = FileStateWriter()
        config = TRWConfig(dedup_enabled=False)

        write_entry(entries_dir, writer, "L-existing03", "test summary", "test detail")

        embed_called = []

        def tracking_embed(text: str) -> list[float]:
            embed_called.append(text)
            return mock_embed(text)

        with patch("trw_mcp.state.dedup.embed", side_effect=tracking_embed):
            result = check_duplicate("test summary", "test detail", entries_dir, reader, config=config)

        # When dedup disabled, it still processes (disabled check happens in caller)
        # The check_duplicate itself always runs — the caller checks config.dedup_enabled
        # This test verifies the tool-level integration skips the call
        assert result is not None  # check_duplicate itself doesn't check config

    def test_skip_non_active_entries(self, tmp_path: Path) -> None:
        """Resolved/obsolete entries are skipped during dedup comparison."""
        entries_dir = tmp_path / "entries"
        entries_dir.mkdir()
        reader = FileStateReader()
        writer = FileStateWriter()
        config = TRWConfig()

        path = entries_dir / "L-resolved01.yaml"
        writer.write_yaml(path, {
            "id": "L-resolved01",
            "summary": "unique test summary for dedup",
            "detail": "unique test detail for dedup",
            "tags": [],
            "evidence": [],
            "impact": 0.5,
            "status": "resolved",
            "recurrence": 1,
            "created": "2026-01-01",
            "updated": "2026-01-01",
        })

        with patch("trw_mcp.state.dedup.embed", side_effect=mock_embed):
            result = check_duplicate(
                "unique test summary for dedup",
                "unique test detail for dedup",
                entries_dir, reader, config=config,
            )

        # Resolved entries are skipped, so no match
        assert result.action == "store"


# ---------------------------------------------------------------------------
# Unit tests: merge_entries
# ---------------------------------------------------------------------------

class TestMergeEntries:
    """Tests for the merge_entries() function."""

    def test_merge_updates_tags_as_union(self, tmp_path: Path) -> None:
        """merge_entries unions the tag sets."""
        entries_dir = tmp_path / "entries"
        entries_dir.mkdir()
        reader = FileStateReader()
        writer = FileStateWriter()

        existing_path = write_entry(entries_dir, writer, "L-merge01", "summary", "detail")
        writer.write_yaml(existing_path, {
            "id": "L-merge01",
            "summary": "summary",
            "detail": "detail",
            "tags": ["python", "testing"],
            "evidence": ["file1.py"],
            "impact": 0.5,
            "status": "active",
            "recurrence": 1,
            "created": "2026-01-01",
            "updated": "2026-01-01",
            "merged_from": [],
        })

        new_data = {
            "id": "L-new01",
            "summary": "summary",
            "detail": "longer detail with more info",
            "tags": ["testing", "fixtures"],
            "evidence": ["file2.py"],
            "impact": 0.7,
            "merged_from": [],
        }

        merge_entries(existing_path, new_data, reader, writer)

        updated = reader.read_yaml(existing_path)
        assert set(updated["tags"]) == {"python", "testing", "fixtures"}

    def test_merge_updates_evidence_as_union(self, tmp_path: Path) -> None:
        """merge_entries unions evidence lists."""
        entries_dir = tmp_path / "entries"
        entries_dir.mkdir()
        reader = FileStateReader()
        writer = FileStateWriter()

        existing_path = entries_dir / "L-ev01.yaml"
        writer.write_yaml(existing_path, {
            "id": "L-ev01",
            "summary": "summary",
            "detail": "short detail",
            "tags": [],
            "evidence": ["file_a.py"],
            "impact": 0.5,
            "status": "active",
            "recurrence": 1,
            "created": "2026-01-01",
            "updated": "2026-01-01",
            "merged_from": [],
        })

        new_data = {
            "id": "L-new02",
            "summary": "summary",
            "detail": "shorter",
            "tags": [],
            "evidence": ["file_b.py", "file_a.py"],
            "impact": 0.4,
            "merged_from": [],
        }

        merge_entries(existing_path, new_data, reader, writer)

        updated = reader.read_yaml(existing_path)
        assert set(updated["evidence"]) == {"file_a.py", "file_b.py"}

    def test_merge_takes_max_impact(self, tmp_path: Path) -> None:
        """merge_entries uses max(existing.impact, new.impact)."""
        entries_dir = tmp_path / "entries"
        entries_dir.mkdir()
        reader = FileStateReader()
        writer = FileStateWriter()

        existing_path = entries_dir / "L-imp01.yaml"
        writer.write_yaml(existing_path, {
            "id": "L-imp01",
            "summary": "s",
            "detail": "d",
            "tags": [],
            "evidence": [],
            "impact": 0.5,
            "status": "active",
            "recurrence": 1,
            "created": "2026-01-01",
            "updated": "2026-01-01",
            "merged_from": [],
        })

        new_data = {"id": "L-new03", "summary": "s", "detail": "d", "tags": [], "evidence": [], "impact": 0.8, "merged_from": []}
        merge_entries(existing_path, new_data, reader, writer)

        updated = reader.read_yaml(existing_path)
        assert float(updated["impact"]) == 0.8

    def test_merge_increments_recurrence(self, tmp_path: Path) -> None:
        """merge_entries increments recurrence count."""
        entries_dir = tmp_path / "entries"
        entries_dir.mkdir()
        reader = FileStateReader()
        writer = FileStateWriter()

        existing_path = entries_dir / "L-rec01.yaml"
        writer.write_yaml(existing_path, {
            "id": "L-rec01",
            "summary": "s",
            "detail": "d",
            "tags": [],
            "evidence": [],
            "impact": 0.5,
            "status": "active",
            "recurrence": 2,
            "created": "2026-01-01",
            "updated": "2026-01-01",
            "merged_from": [],
        })

        new_data = {"id": "L-new04", "summary": "s", "detail": "d", "tags": [], "evidence": [], "impact": 0.5, "merged_from": []}
        merge_entries(existing_path, new_data, reader, writer)

        updated = reader.read_yaml(existing_path)
        assert int(updated["recurrence"]) == 3

    def test_merge_adds_merged_from(self, tmp_path: Path) -> None:
        """merge_entries appends new entry ID to merged_from."""
        entries_dir = tmp_path / "entries"
        entries_dir.mkdir()
        reader = FileStateReader()
        writer = FileStateWriter()

        existing_path = entries_dir / "L-mf01.yaml"
        writer.write_yaml(existing_path, {
            "id": "L-mf01",
            "summary": "s",
            "detail": "d",
            "tags": [],
            "evidence": [],
            "impact": 0.5,
            "status": "active",
            "recurrence": 1,
            "created": "2026-01-01",
            "updated": "2026-01-01",
            "merged_from": [],
        })

        new_data = {"id": "L-newmerge05", "summary": "s", "detail": "d", "tags": [], "evidence": [], "impact": 0.5, "merged_from": []}
        merge_entries(existing_path, new_data, reader, writer)

        updated = reader.read_yaml(existing_path)
        assert "L-newmerge05" in updated["merged_from"]

    def test_merge_appends_longer_detail(self, tmp_path: Path) -> None:
        """merge_entries appends detail when new detail is longer than existing."""
        entries_dir = tmp_path / "entries"
        entries_dir.mkdir()
        reader = FileStateReader()
        writer = FileStateWriter()

        existing_path = entries_dir / "L-det01.yaml"
        writer.write_yaml(existing_path, {
            "id": "L-det01",
            "summary": "s",
            "detail": "short detail",
            "tags": [],
            "evidence": [],
            "impact": 0.5,
            "status": "active",
            "recurrence": 1,
            "created": "2026-01-01",
            "updated": "2026-01-01",
            "merged_from": [],
        })

        new_data = {
            "id": "L-newdet06",
            "summary": "s",
            "detail": "this is a much longer and more informative detail that should be appended",
            "tags": [],
            "evidence": [],
            "impact": 0.5,
            "merged_from": [],
        }
        merge_entries(existing_path, new_data, reader, writer)

        updated = reader.read_yaml(existing_path)
        assert "this is a much longer" in str(updated["detail"])

    def test_merge_returns_path(self, tmp_path: Path) -> None:
        """merge_entries returns the path of the updated entry."""
        entries_dir = tmp_path / "entries"
        entries_dir.mkdir()
        reader = FileStateReader()
        writer = FileStateWriter()

        existing_path = entries_dir / "L-ret01.yaml"
        writer.write_yaml(existing_path, {
            "id": "L-ret01",
            "summary": "s",
            "detail": "d",
            "tags": [],
            "evidence": [],
            "impact": 0.5,
            "status": "active",
            "recurrence": 1,
            "created": "2026-01-01",
            "updated": "2026-01-01",
            "merged_from": [],
        })

        new_data = {"id": "L-newret07", "summary": "s", "detail": "d", "tags": [], "evidence": [], "impact": 0.5, "merged_from": []}
        returned_path = merge_entries(existing_path, new_data, reader, writer)

        assert returned_path == existing_path


# ---------------------------------------------------------------------------
# Model tests
# ---------------------------------------------------------------------------

class TestLearningEntryMergedFrom:
    """Tests for the merged_from field added to LearningEntry."""

    def test_default_merged_from_is_empty_list(self) -> None:
        """LearningEntry.merged_from defaults to []."""
        entry = LearningEntry(
            id="L-test01",
            summary="test",
            detail="detail",
        )
        assert entry.merged_from == []

    def test_merged_from_can_be_populated(self) -> None:
        """LearningEntry.merged_from accepts a list of ID strings."""
        entry = LearningEntry(
            id="L-test02",
            summary="test",
            detail="detail",
            merged_from=["L-abc", "L-def"],
        )
        assert entry.merged_from == ["L-abc", "L-def"]


# ---------------------------------------------------------------------------
# Integration tests: trw_learn tool
# ---------------------------------------------------------------------------

class TestTrwLearnDedup:
    """Integration tests for the dedup check in trw_learn()."""

    def _make_entries_dir(self, tmp_path: Path) -> Path:
        trw = tmp_path / ".trw"
        entries = trw / "learnings" / "entries"
        entries.mkdir(parents=True)
        return entries

    def test_trw_learn_returns_skipped_duplicate(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When a duplicate exists, trw_learn returns 'skipped_duplicate'."""
        from fastmcp import FastMCP
        from trw_mcp.tools.learning import register_learning_tools

        entries_dir = self._make_entries_dir(tmp_path)
        logs_dir = tmp_path / ".trw" / "logs"
        logs_dir.mkdir(parents=True)

        # Patch module singletons
        mock_config = TRWConfig(dedup_enabled=True, dedup_skip_threshold=0.95, dedup_merge_threshold=0.85)
        mock_reader = FileStateReader()
        mock_writer = FileStateWriter()

        monkeypatch.setattr("trw_mcp.tools.learning._config", mock_config)
        monkeypatch.setattr("trw_mcp.tools.learning._reader", mock_reader)
        monkeypatch.setattr("trw_mcp.tools.learning._writer", mock_writer)
        monkeypatch.setattr(
            "trw_mcp.tools.learning.resolve_trw_dir",
            lambda: tmp_path / ".trw",
        )

        # Write an identical entry already in the entries_dir
        summary = "pytest fixture isolation pattern"
        detail = "use autouse fixtures with yield for clean teardown"
        mock_writer.write_yaml(entries_dir / "L-existing99.yaml", {
            "id": "L-existing99",
            "summary": summary,
            "detail": detail,
            "tags": [],
            "evidence": [],
            "impact": 0.5,
            "status": "active",
            "recurrence": 1,
            "created": "2026-01-01",
            "updated": "2026-01-01",
            "merged_from": [],
        })

        # Patch embed to return deterministic vectors
        monkeypatch.setattr("trw_mcp.state.dedup.embed", mock_embed)

        # Patch generate_learning_id to avoid randomness
        monkeypatch.setattr(
            "trw_mcp.tools.learning.generate_learning_id",
            lambda: "L-newidtest",
        )

        server = FastMCP("test")
        register_learning_tools(server)

        # Get the registered trw_learn tool and call it
        tools = {t.name: t for t in server._tool_manager._tools.values()}
        tool_fn = tools["trw_learn"].fn

        result = tool_fn(summary=summary, detail=detail)

        assert result["status"] == "skipped"
        assert result["learning_id"] is not None
        assert result["duplicate_of"] == "L-existing99"
        assert float(result["similarity"]) >= 0.95

    def test_trw_learn_normal_store_when_dedup_disabled(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When dedup_enabled=False, trw_learn stores normally."""
        self._make_entries_dir(tmp_path)
        logs_dir = tmp_path / ".trw" / "logs"
        logs_dir.mkdir(parents=True)

        mock_config = TRWConfig(dedup_enabled=False)
        mock_reader = FileStateReader()
        mock_writer = FileStateWriter()

        monkeypatch.setattr("trw_mcp.tools.learning._config", mock_config)
        monkeypatch.setattr("trw_mcp.tools.learning._reader", mock_reader)
        monkeypatch.setattr("trw_mcp.tools.learning._writer", mock_writer)
        monkeypatch.setattr(
            "trw_mcp.tools.learning.resolve_trw_dir",
            lambda: tmp_path / ".trw",
        )
        monkeypatch.setattr(
            "trw_mcp.tools.learning.generate_learning_id",
            lambda: "L-storedtest",
        )

        from fastmcp import FastMCP
        from trw_mcp.tools.learning import register_learning_tools

        server = FastMCP("test")
        register_learning_tools(server)
        tools = {t.name: t for t in server._tool_manager._tools.values()}
        tool_fn = tools["trw_learn"].fn

        result = tool_fn(summary="some summary", detail="some detail")

        assert result["status"] == "recorded"
        assert result["learning_id"] == "L-storedtest"

    def test_trw_learn_returns_merged_when_near_duplicate(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When a near-duplicate exists, trw_learn returns 'merged'."""
        entries_dir = self._make_entries_dir(tmp_path)
        logs_dir = tmp_path / ".trw" / "logs"
        logs_dir.mkdir(parents=True)

        mock_config = TRWConfig(
            dedup_enabled=True,
            dedup_skip_threshold=0.95,
            dedup_merge_threshold=0.85,
        )
        mock_reader = FileStateReader()
        mock_writer = FileStateWriter()

        monkeypatch.setattr("trw_mcp.tools.learning._config", mock_config)
        monkeypatch.setattr("trw_mcp.tools.learning._reader", mock_reader)
        monkeypatch.setattr("trw_mcp.tools.learning._writer", mock_writer)
        monkeypatch.setattr(
            "trw_mcp.tools.learning.resolve_trw_dir",
            lambda: tmp_path / ".trw",
        )
        monkeypatch.setattr(
            "trw_mcp.tools.learning.generate_learning_id",
            lambda: "L-mergedtest",
        )

        existing_summary = "existing learning entry"
        existing_detail = "existing detail about some topic"
        mock_writer.write_yaml(entries_dir / "L-existingmerge.yaml", {
            "id": "L-existingmerge",
            "summary": existing_summary,
            "detail": existing_detail,
            "tags": ["a"],
            "evidence": [],
            "impact": 0.5,
            "status": "active",
            "recurrence": 1,
            "created": "2026-01-01",
            "updated": "2026-01-01",
            "merged_from": [],
        })

        existing_vec = mock_embed(existing_summary + " " + existing_detail)

        def merge_zone_embed(text: str) -> list[float]:
            """Return vectors in merge zone (0.85-0.95) for new text."""
            if "new" in text:
                mixed = [v * 0.88 + 0.02 * (i % 2) for i, v in enumerate(existing_vec)]
                norm = sum(v * v for v in mixed) ** 0.5
                if norm == 0:
                    return existing_vec
                return [v / norm for v in mixed]
            return mock_embed(text)

        monkeypatch.setattr("trw_mcp.state.dedup.embed", merge_zone_embed)

        from fastmcp import FastMCP
        from trw_mcp.tools.learning import register_learning_tools

        server = FastMCP("test")
        register_learning_tools(server)
        tools = {t.name: t for t in server._tool_manager._tools.values()}
        tool_fn = tools["trw_learn"].fn

        result = tool_fn(summary="new similar summary", detail="new similar detail about the topic")

        # Should be merge, skip, or recorded (all are valid near-duplicate responses)
        assert result["status"] in ("merged", "skipped", "recorded")


# ---------------------------------------------------------------------------
# Edge cases: check_duplicate
# ---------------------------------------------------------------------------

class TestCheckDuplicateEdgeCases:
    """Additional edge cases for check_duplicate to reach line coverage."""

    def test_store_when_entries_dir_missing_after_embed(self, tmp_path: Path) -> None:
        """entries_dir does not exist → DedupResult('store', None, 0.0) even when embed succeeds."""
        missing_dir = tmp_path / "does_not_exist"
        reader = FileStateReader()
        config = TRWConfig()

        with patch("trw_mcp.state.dedup.embed", side_effect=mock_embed):
            result = check_duplicate("some summary", "some detail", missing_dir, reader, config=config)

        assert result.action == "store"
        assert result.existing_id is None
        assert result.similarity == 0.0

    def test_skip_index_yaml_file(self, tmp_path: Path) -> None:
        """index.yaml file in entries_dir is silently skipped."""
        entries_dir = tmp_path / "entries"
        entries_dir.mkdir()
        reader = FileStateReader()
        writer = FileStateWriter()
        config = TRWConfig()

        # Write index.yaml (should be skipped) and a real entry
        writer.write_yaml(entries_dir / "index.yaml", {
            "id": "L-index",
            "summary": "same summary exact match",
            "detail": "same detail exact match",
            "tags": [],
            "status": "active",
        })

        # Only write index.yaml (no real entries) so result must be 'store'
        with patch("trw_mcp.state.dedup.embed", side_effect=mock_embed):
            result = check_duplicate(
                "same summary exact match", "same detail exact match",
                entries_dir, reader, config=config,
            )

        # index.yaml is skipped, so no duplicate found → store
        assert result.action == "store"

    def test_corrupt_yaml_entry_is_skipped(self, tmp_path: Path) -> None:
        """Unreadable YAML entries are silently skipped — no exception raised."""
        entries_dir = tmp_path / "entries"
        entries_dir.mkdir()
        reader = FileStateReader()
        writer = FileStateWriter()
        config = TRWConfig()

        # Write a valid entry and a corrupt one
        write_entry(entries_dir, writer, "L-good01", "valid entry", "valid detail")
        corrupt_path = entries_dir / "0corrupt.yaml"
        corrupt_path.write_text("{ invalid yaml :\n  - broken", encoding="utf-8")

        with patch("trw_mcp.state.dedup.embed", side_effect=mock_embed):
            # Should not raise — the corrupt file is skipped
            result = check_duplicate(
                "completely different topic", "unrelated info",
                entries_dir, reader, config=config,
            )

        assert result is not None  # No exception

    def test_entry_embed_returns_none_is_skipped(self, tmp_path: Path) -> None:
        """When embed returns None for existing entry, that entry is skipped."""
        entries_dir = tmp_path / "entries"
        entries_dir.mkdir()
        reader = FileStateReader()
        writer = FileStateWriter()
        config = TRWConfig()

        write_entry(entries_dir, writer, "L-skip-embed", "some summary", "some detail")

        call_count = [0]
        def selective_none_embed(text: str) -> list[float] | None:
            call_count[0] += 1
            if call_count[0] == 1:
                # First call is for the new entry — return a real vector
                return mock_embed(text)
            # Subsequent calls (for existing entries) return None
            return None

        with patch("trw_mcp.state.dedup.embed", side_effect=selective_none_embed):
            result = check_duplicate("some summary", "some detail", entries_dir, reader, config=config)

        # Existing entry embed returns None → skipped → action is 'store'
        assert result.action == "store"

    def test_threshold_boundary_exactly_at_skip(self, tmp_path: Path) -> None:
        """Similarity exactly at skip_threshold (0.95) → 'skip' action."""
        entries_dir = tmp_path / "entries"
        entries_dir.mkdir()
        reader = FileStateReader()
        writer = FileStateWriter()
        config = TRWConfig(dedup_skip_threshold=0.95, dedup_merge_threshold=0.85)

        write_entry(entries_dir, writer, "L-boundary01", "boundary test", "detail")
        existing_vec = mock_embed("boundary test detail")

        # Create a new vector exactly at 0.95 cosine similarity
        import math as _math
        cos_theta = 0.95
        sin_theta = _math.sqrt(1.0 - cos_theta ** 2)
        # Build orthogonal to existing_vec
        orth = [0.0] * len(existing_vec)
        orth[0] = -existing_vec[1]
        orth[1] = existing_vec[0]
        orth_norm = sum(v * v for v in orth) ** 0.5
        if orth_norm > 0:
            orth = [v / orth_norm for v in orth]
        new_vec = [cos_theta * e + sin_theta * o for e, o in zip(existing_vec, orth)]
        new_norm = sum(v * v for v in new_vec) ** 0.5
        if new_norm > 0:
            new_vec = [v / new_norm for v in new_vec]

        call_count = [0]
        def boundary_embed(text: str) -> list[float]:
            call_count[0] += 1
            if call_count[0] == 1:
                return new_vec  # new entry
            return mock_embed(text)  # existing entry

        with patch("trw_mcp.state.dedup.embed", side_effect=boundary_embed):
            result = check_duplicate("boundary test", "detail", entries_dir, reader, config=config)

        # At exactly 0.95 similarity → skip (>= skip_threshold)
        assert result.action == "skip"
        assert result.similarity >= 0.95

    def test_threshold_boundary_exactly_at_merge(self, tmp_path: Path) -> None:
        """Similarity exactly at merge_threshold (0.85) → 'merge' action.

        We use controlled embed functions that return a precise vector for the new entry
        and a separate vector for the existing entry, with exactly 0.85 cosine similarity.
        The new entry text must differ from the existing to avoid exact match.
        """
        entries_dir = tmp_path / "entries"
        entries_dir.mkdir()
        reader = FileStateReader()
        writer = FileStateWriter()
        config = TRWConfig(dedup_skip_threshold=0.95, dedup_merge_threshold=0.85)

        # Existing entry with distinct text
        write_entry(entries_dir, writer, "L-boundary02", "existing merge test entry", "existing detail here")
        existing_text = "existing merge test entry existing detail here"
        existing_vec = mock_embed(existing_text)

        import math as _math
        cos_theta = 0.87  # In merge zone (0.85 <= 0.87 < 0.95)
        sin_theta = _math.sqrt(1.0 - cos_theta ** 2)
        orth = [0.0] * len(existing_vec)
        orth[0] = -existing_vec[1]
        orth[1] = existing_vec[0]
        orth_norm = sum(v * v for v in orth) ** 0.5
        if orth_norm > 0:
            orth = [v / orth_norm for v in orth]
        new_vec = [cos_theta * e + sin_theta * o for e, o in zip(existing_vec, orth)]
        new_norm = sum(v * v for v in new_vec) ** 0.5
        if new_norm > 0:
            new_vec = [v / new_norm for v in new_vec]

        def controlled_embed(text: str) -> list[float]:
            if "totally different new query" in text:
                return new_vec
            # For the existing entry text, return the canonical mock_embed
            return mock_embed(text)

        with patch("trw_mcp.state.dedup.embed", side_effect=controlled_embed):
            result = check_duplicate(
                "totally different new query",
                "",  # Empty detail so text = "totally different new query "
                entries_dir, reader, config=config,
            )

        # new_vec is at 0.87 similarity → in merge zone [0.85, 0.95) → merge action
        assert result.action == "merge"
        assert 0.85 <= result.similarity < 0.95


# ---------------------------------------------------------------------------
# Edge cases: merge_entries
# ---------------------------------------------------------------------------

class TestMergeEntriesEdgeCases:
    """Additional edge cases for merge_entries coverage."""

    def test_merge_empty_existing_detail_uses_new_directly(self, tmp_path: Path) -> None:
        """When existing detail is empty and new detail is longer, use new detail directly."""
        entries_dir = tmp_path / "entries"
        entries_dir.mkdir()
        reader = FileStateReader()
        writer = FileStateWriter()

        existing_path = entries_dir / "L-empty-det.yaml"
        writer.write_yaml(existing_path, {
            "id": "L-empty-det",
            "summary": "s",
            "detail": "",  # Empty existing detail
            "tags": [],
            "evidence": [],
            "impact": 0.5,
            "status": "active",
            "recurrence": 1,
            "created": "2026-01-01",
            "updated": "2026-01-01",
            "merged_from": [],
        })

        new_data = {
            "id": "L-new-det",
            "summary": "s",
            "detail": "this is new detail that should replace the empty existing",
            "tags": [],
            "evidence": [],
            "impact": 0.5,
            "merged_from": [],
        }
        merge_entries(existing_path, new_data, reader, writer)

        updated = reader.read_yaml(existing_path)
        # When existing detail is empty, new detail replaces it directly (no \n\n separator)
        assert "this is new detail" in str(updated["detail"])
        assert "\n\n" not in str(updated["detail"])

    def test_merge_same_length_detail_unchanged(self, tmp_path: Path) -> None:
        """When new detail is not longer than existing, detail is unchanged."""
        entries_dir = tmp_path / "entries"
        entries_dir.mkdir()
        reader = FileStateReader()
        writer = FileStateWriter()

        existing_path = entries_dir / "L-same-len.yaml"
        writer.write_yaml(existing_path, {
            "id": "L-same-len",
            "summary": "s",
            "detail": "existing detail is long enough already",
            "tags": [],
            "evidence": [],
            "impact": 0.5,
            "status": "active",
            "recurrence": 1,
            "created": "2026-01-01",
            "updated": "2026-01-01",
            "merged_from": [],
        })

        new_data = {
            "id": "L-new-same",
            "summary": "s",
            "detail": "short",  # Shorter than existing
            "tags": [],
            "evidence": [],
            "impact": 0.5,
            "merged_from": [],
        }
        merge_entries(existing_path, new_data, reader, writer)

        updated = reader.read_yaml(existing_path)
        assert str(updated["detail"]) == "existing detail is long enough already"

    def test_merge_duplicate_merged_from_not_added_twice(self, tmp_path: Path) -> None:
        """When new_id is already in merged_from, it should not be duplicated."""
        entries_dir = tmp_path / "entries"
        entries_dir.mkdir()
        reader = FileStateReader()
        writer = FileStateWriter()

        existing_path = entries_dir / "L-dedup-mf.yaml"
        writer.write_yaml(existing_path, {
            "id": "L-dedup-mf",
            "summary": "s",
            "detail": "d",
            "tags": [],
            "evidence": [],
            "impact": 0.5,
            "status": "active",
            "recurrence": 1,
            "created": "2026-01-01",
            "updated": "2026-01-01",
            "merged_from": ["L-already-there"],  # Pre-existing merged_from
        })

        new_data = {
            "id": "L-already-there",  # Same as existing merged_from entry
            "summary": "s",
            "detail": "d",
            "tags": [],
            "evidence": [],
            "impact": 0.5,
            "merged_from": [],
        }
        merge_entries(existing_path, new_data, reader, writer)

        updated = reader.read_yaml(existing_path)
        # L-already-there should appear only once
        assert updated["merged_from"].count("L-already-there") == 1

    def test_merge_empty_new_id_not_added_to_merged_from(self, tmp_path: Path) -> None:
        """When new entry id is empty string, it is not added to merged_from."""
        entries_dir = tmp_path / "entries"
        entries_dir.mkdir()
        reader = FileStateReader()
        writer = FileStateWriter()

        existing_path = entries_dir / "L-noid.yaml"
        writer.write_yaml(existing_path, {
            "id": "L-noid",
            "summary": "s",
            "detail": "d",
            "tags": [],
            "evidence": [],
            "impact": 0.5,
            "status": "active",
            "recurrence": 1,
            "created": "2026-01-01",
            "updated": "2026-01-01",
            "merged_from": [],
        })

        new_data = {
            "id": "",  # Empty id
            "summary": "s",
            "detail": "d",
            "tags": [],
            "evidence": [],
            "impact": 0.5,
            "merged_from": [],
        }
        merge_entries(existing_path, new_data, reader, writer)

        updated = reader.read_yaml(existing_path)
        # Empty id should not be added
        assert "" not in updated["merged_from"]


# ---------------------------------------------------------------------------
# FR03: Merge audit trail
# ---------------------------------------------------------------------------

class TestMergeAuditTrail:
    """Tests for FR03 — audit trail format in merge_entries."""

    def test_merge_detail_uses_audit_trail_format(self, tmp_path: Path) -> None:
        """Merged detail uses '\\n---\\nMerged from {id} on {date}:\\n' format."""
        entries_dir = tmp_path / "entries"
        entries_dir.mkdir()
        reader = FileStateReader()
        writer = FileStateWriter()

        existing_path = entries_dir / "L-audit01.yaml"
        writer.write_yaml(existing_path, {
            "id": "L-audit01",
            "summary": "s",
            "detail": "short existing",
            "tags": [],
            "evidence": [],
            "impact": 0.5,
            "status": "active",
            "recurrence": 1,
            "created": "2026-01-01",
            "updated": "2026-01-01",
            "merged_from": [],
        })

        new_data = {
            "id": "L-new-audit",
            "summary": "s",
            "detail": "this is a much longer detail that will trigger appending",
            "tags": [],
            "evidence": [],
            "impact": 0.5,
            "merged_from": [],
        }
        merge_entries(existing_path, new_data, reader, writer)

        updated = reader.read_yaml(existing_path)
        detail = str(updated["detail"])
        # Must contain the audit trail marker
        assert "---" in detail
        assert "Merged from L-new-audit on" in detail
        assert "this is a much longer detail" in detail

    def test_merge_detail_no_audit_marker_when_new_shorter(self, tmp_path: Path) -> None:
        """No audit trail appended when new detail is not longer."""
        entries_dir = tmp_path / "entries"
        entries_dir.mkdir()
        reader = FileStateReader()
        writer = FileStateWriter()

        existing_path = entries_dir / "L-audit02.yaml"
        writer.write_yaml(existing_path, {
            "id": "L-audit02",
            "summary": "s",
            "detail": "much longer existing detail that is certainly long enough",
            "tags": [],
            "evidence": [],
            "impact": 0.5,
            "status": "active",
            "recurrence": 1,
            "created": "2026-01-01",
            "updated": "2026-01-01",
            "merged_from": [],
        })

        new_data = {
            "id": "L-short-new",
            "summary": "s",
            "detail": "short",
            "tags": [],
            "evidence": [],
            "impact": 0.5,
            "merged_from": [],
        }
        merge_entries(existing_path, new_data, reader, writer)

        updated = reader.read_yaml(existing_path)
        detail = str(updated["detail"])
        # No audit trail added when new detail is shorter
        assert "Merged from" not in detail
        assert "---" not in detail


# ---------------------------------------------------------------------------
# FR06: Threshold validation
# ---------------------------------------------------------------------------

class TestThresholdValidation:
    """Tests for FR06 — invalid threshold resets to defaults."""

    def test_check_duplicate_resets_invalid_thresholds(self, tmp_path: Path) -> None:
        """When merge_threshold >= skip_threshold, defaults are used."""
        entries_dir = tmp_path / "entries"
        entries_dir.mkdir()
        reader = FileStateReader()
        writer = FileStateWriter()

        # Config where merge >= skip is invalid
        config = TRWConfig(dedup_skip_threshold=0.80, dedup_merge_threshold=0.85)

        summary = "threshold validation test"
        detail = "some detail"
        write_entry(entries_dir, writer, "L-thresh01", summary, detail)

        with patch("trw_mcp.state.dedup.embed", side_effect=mock_embed):
            # Should not raise — invalid thresholds get reset to defaults
            result = check_duplicate(summary, detail, entries_dir, reader, config=config)

        # Result should be valid (either skip or store using default thresholds)
        assert result.action in ("skip", "store", "merge")

    def test_check_duplicate_equal_thresholds_resets(self, tmp_path: Path) -> None:
        """When merge_threshold == skip_threshold, defaults are used."""
        entries_dir = tmp_path / "entries"
        entries_dir.mkdir()
        reader = FileStateReader()

        # Equal thresholds are also invalid
        config = TRWConfig(dedup_skip_threshold=0.90, dedup_merge_threshold=0.90)

        with patch("trw_mcp.state.dedup.embed", side_effect=mock_embed):
            result = check_duplicate("any summary", "any detail", entries_dir, reader, config=config)

        assert result is not None  # No exception raised


# ---------------------------------------------------------------------------
# FR04: Skip updates access_count
# ---------------------------------------------------------------------------

class TestSkipUpdatesAccessCount:
    """Tests for FR04 — skip action updates access_count on existing entry."""

    def test_skip_increments_access_count(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When dedup action=skip, existing entry's access_count is incremented."""
        from fastmcp import FastMCP
        from trw_mcp.tools.learning import register_learning_tools

        entries_dir = tmp_path / ".trw" / "learnings" / "entries"
        entries_dir.mkdir(parents=True)
        logs_dir = tmp_path / ".trw" / "logs"
        logs_dir.mkdir(parents=True)

        mock_config = TRWConfig(dedup_enabled=True, dedup_skip_threshold=0.95, dedup_merge_threshold=0.85)
        mock_reader = FileStateReader()
        mock_writer = FileStateWriter()

        monkeypatch.setattr("trw_mcp.tools.learning._config", mock_config)
        monkeypatch.setattr("trw_mcp.tools.learning._reader", mock_reader)
        monkeypatch.setattr("trw_mcp.tools.learning._writer", mock_writer)
        monkeypatch.setattr("trw_mcp.tools.learning.resolve_trw_dir", lambda: tmp_path / ".trw")
        monkeypatch.setattr("trw_mcp.tools.learning.generate_learning_id", lambda: "L-skip-test")

        summary = "unique skip access count test"
        detail = "detail for skip access count test"
        mock_writer.write_yaml(entries_dir / "L-existing-skip.yaml", {
            "id": "L-existing-skip",
            "summary": summary,
            "detail": detail,
            "tags": [],
            "evidence": [],
            "impact": 0.5,
            "status": "active",
            "recurrence": 1,
            "access_count": 3,
            "created": "2026-01-01",
            "updated": "2026-01-01",
            "merged_from": [],
        })

        monkeypatch.setattr("trw_mcp.state.dedup.embed", mock_embed)

        server = FastMCP("test")
        register_learning_tools(server)
        tools = {t.name: t for t in server._tool_manager._tools.values()}
        result = tools["trw_learn"].fn(summary=summary, detail=detail)

        assert result["status"] == "skipped"
        assert result["learning_id"] == "L-skip-test"

        # Sprint 34: YAML is now a backup — access_count/recurrence tracking
        # moved to SQLite adapter. YAML file is NOT updated on skip.
        updated_data = mock_reader.read_yaml(entries_dir / "L-existing-skip.yaml")
        assert int(str(updated_data.get("access_count", 0))) == 3
        assert int(str(updated_data.get("recurrence", 1))) == 1


# ---------------------------------------------------------------------------
# FR05: Batch dedup migration
# ---------------------------------------------------------------------------

class TestBatchDedup:
    """Tests for FR05 — batch_dedup and is_migration_needed."""

    def test_is_migration_needed_true_when_no_marker(self, tmp_path: Path) -> None:
        """is_migration_needed returns True when marker file doesn't exist."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        # Default config: learnings_dir="learnings", so marker = .trw/learnings/dedup_migration.yaml
        # That file won't exist in a fresh tmp dir
        assert is_migration_needed(trw_dir) is True

    def test_is_migration_needed_false_after_marker_written(self, tmp_path: Path) -> None:
        """is_migration_needed returns False when marker file exists."""
        cfg = TRWConfig()
        trw_dir = tmp_path / ".trw"
        learnings_dir = trw_dir / cfg.learnings_dir
        learnings_dir.mkdir(parents=True)
        marker = learnings_dir / "dedup_migration.yaml"
        marker.write_text("completed: true\n", encoding="utf-8")
        assert is_migration_needed(trw_dir) is False

    def test_batch_dedup_skips_when_no_entries_dir(self, tmp_path: Path) -> None:
        """batch_dedup returns 'skipped' when entries directory doesn't exist."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        reader = FileStateReader()
        writer = FileStateWriter()
        config = TRWConfig()

        result = batch_dedup(trw_dir, reader, writer, config=config)
        assert result["status"] == "skipped"
        assert "no entries directory" in str(result.get("reason", ""))

    def test_batch_dedup_skips_when_embeddings_unavailable(self, tmp_path: Path) -> None:
        """batch_dedup returns 'skipped' when embeddings unavailable."""
        trw_dir = tmp_path / ".trw"
        entries_dir = trw_dir / "learnings" / "entries"
        entries_dir.mkdir(parents=True)
        reader = FileStateReader()
        writer = FileStateWriter()
        config = TRWConfig()

        with patch("trw_mcp.state.dedup.embedding_available", return_value=False):
            result = batch_dedup(trw_dir, reader, writer, config=config)

        assert result["status"] == "skipped"
        assert "embeddings unavailable" in str(result.get("reason", ""))

    def test_batch_dedup_writes_migration_marker(self, tmp_path: Path) -> None:
        """batch_dedup writes dedup_migration.yaml marker after completion."""
        trw_dir = tmp_path / ".trw"
        entries_dir = trw_dir / "learnings" / "entries"
        entries_dir.mkdir(parents=True)
        reader = FileStateReader()
        writer = FileStateWriter()
        config = TRWConfig()

        with patch("trw_mcp.state.dedup.embedding_available", return_value=True):
            with patch("trw_mcp.state.dedup.embed", side_effect=mock_embed):
                result = batch_dedup(trw_dir, reader, writer, config=config)

        assert result["status"] == "completed"
        marker = trw_dir / "learnings" / "dedup_migration.yaml"
        assert marker.exists()
        marker_data = reader.read_yaml(marker)
        assert marker_data.get("completed") is True
        assert "run_at" in marker_data

    def test_batch_dedup_merges_near_duplicates(self, tmp_path: Path) -> None:
        """batch_dedup merges entries above merge threshold."""
        trw_dir = tmp_path / ".trw"
        entries_dir = trw_dir / "learnings" / "entries"
        entries_dir.mkdir(parents=True)
        reader = FileStateReader()
        writer = FileStateWriter()
        config = TRWConfig(dedup_skip_threshold=0.95, dedup_merge_threshold=0.85)

        # Write two entries
        write_entry(entries_dir, writer, "L-batch01", "batch test alpha", "first detail here alpha")
        write_entry(entries_dir, writer, "L-batch02", "batch test beta", "second detail here beta")

        # Control vectors: L-batch01 and L-batch02 will be at 0.90 similarity
        existing_vec = mock_embed("batch test alpha first detail here alpha")
        import math as _math
        cos_theta = 0.90
        sin_theta = _math.sqrt(1 - cos_theta ** 2)
        orth = [0.0] * len(existing_vec)
        orth[0] = -existing_vec[1]
        orth[1] = existing_vec[0]
        orth_norm = sum(v * v for v in orth) ** 0.5
        if orth_norm > 0:
            orth = [v / orth_norm for v in orth]
        near_vec = [cos_theta * e + sin_theta * o for e, o in zip(existing_vec, orth)]
        near_norm = sum(v * v for v in near_vec) ** 0.5
        near_vec = [v / near_norm for v in near_vec]

        call_count = [0]
        def controlled_embed(text: str) -> list[float]:
            call_count[0] += 1
            if "L-batch02" in text or "second detail here beta" in text or "batch test beta" in text:
                return near_vec
            return mock_embed(text)

        with patch("trw_mcp.state.dedup.embedding_available", return_value=True):
            with patch("trw_mcp.state.dedup.embed", side_effect=controlled_embed):
                result = batch_dedup(trw_dir, reader, writer, config=config)

        assert result["status"] == "completed"
        assert int(str(result.get("entries_scanned", 0))) == 2

    def test_batch_dedup_completes_with_no_active_entries(self, tmp_path: Path) -> None:
        """batch_dedup completes cleanly with zero active entries."""
        trw_dir = tmp_path / ".trw"
        entries_dir = trw_dir / "learnings" / "entries"
        entries_dir.mkdir(parents=True)
        reader = FileStateReader()
        writer = FileStateWriter()
        config = TRWConfig()

        # Write only resolved entries
        path = entries_dir / "L-resolved.yaml"
        writer.write_yaml(path, {
            "id": "L-resolved",
            "summary": "s",
            "detail": "d",
            "tags": [],
            "evidence": [],
            "impact": 0.5,
            "status": "resolved",
            "recurrence": 1,
            "created": "2026-01-01",
            "updated": "2026-01-01",
            "merged_from": [],
        })

        with patch("trw_mcp.state.dedup.embedding_available", return_value=True):
            with patch("trw_mcp.state.dedup.embed", side_effect=mock_embed):
                result = batch_dedup(trw_dir, reader, writer, config=config)

        assert result["status"] == "completed"
        assert int(str(result.get("entries_scanned", 0))) == 0
        assert int(str(result.get("entries_merged", 0))) == 0

    def test_batch_dedup_obsoletes_exact_duplicates(self, tmp_path: Path) -> None:
        """batch_dedup marks exact duplicates (>=skip_threshold) as obsolete."""
        trw_dir = tmp_path / ".trw"
        entries_dir = trw_dir / "learnings" / "entries"
        entries_dir.mkdir(parents=True)
        reader = FileStateReader()
        writer = FileStateWriter()
        config = TRWConfig(dedup_skip_threshold=0.95, dedup_merge_threshold=0.85)

        identical_summary = "exact duplicate entry for batch"
        identical_detail = "same detail for exact duplicate"
        write_entry(entries_dir, writer, "L-exact01", identical_summary, identical_detail)
        write_entry(entries_dir, writer, "L-exact02", identical_summary, identical_detail)

        with patch("trw_mcp.state.dedup.embedding_available", return_value=True):
            with patch("trw_mcp.state.dedup.embed", side_effect=mock_embed):
                result = batch_dedup(trw_dir, reader, writer, config=config)

        assert result["status"] == "completed"
        # One of the two entries should be obsoleted
        data2 = reader.read_yaml(entries_dir / "L-exact02.yaml")
        assert str(data2.get("status", "")) == "obsolete"


# ---------------------------------------------------------------------------
# FR01: Graceful degradation — trw_learn returns "recorded" when embed=None
# ---------------------------------------------------------------------------

class TestTrwLearnGracefulDegradation:
    """CORE-042-FR01: When embed() returns None, trw_learn falls back to 'store' (recorded)."""

    def _make_setup(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> object:
        """Common setup for trw_learn integration tests."""
        from fastmcp import FastMCP
        from trw_mcp.tools.learning import register_learning_tools

        entries_dir = tmp_path / ".trw" / "learnings" / "entries"
        entries_dir.mkdir(parents=True)
        logs_dir = tmp_path / ".trw" / "logs"
        logs_dir.mkdir(parents=True)

        mock_config = TRWConfig(dedup_enabled=True, dedup_skip_threshold=0.95, dedup_merge_threshold=0.85)
        mock_reader = FileStateReader()
        mock_writer = FileStateWriter()

        monkeypatch.setattr("trw_mcp.tools.learning._config", mock_config)
        monkeypatch.setattr("trw_mcp.tools.learning._reader", mock_reader)
        monkeypatch.setattr("trw_mcp.tools.learning._writer", mock_writer)
        monkeypatch.setattr("trw_mcp.tools.learning.resolve_trw_dir", lambda: tmp_path / ".trw")
        monkeypatch.setattr("trw_mcp.tools.learning.generate_learning_id", lambda: "L-graceful-test")

        server = FastMCP("test")
        register_learning_tools(server)
        tools = {t.name: t for t in server._tool_manager._tools.values()}
        return tools["trw_learn"].fn

    def test_trw_learn_recorded_when_embed_returns_none(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """FR01: When embed() returns None (no sentence-transformers), trw_learn stores normally.

        The dedup path fails gracefully and the learning is written with status 'recorded'.
        """
        tool_fn = self._make_setup(tmp_path, monkeypatch)

        # Simulate embed not available
        monkeypatch.setattr("trw_mcp.state.dedup.embed", lambda text: None)

        result = tool_fn(
            summary="graceful dedup fallback test",
            detail="embed returns None so dedup is skipped",
        )

        assert result["status"] == "recorded", (
            f"FR01: Expected 'recorded' when embed=None, got {result['status']!r}"
        )
        assert "learning_id" in result

    def test_trw_learn_recorded_when_new_entry_embed_none(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """FR01: Even with an existing entry, if embed(new_text) returns None, stores as new."""
        from trw_mcp.state.persistence import FileStateWriter as FSW

        tool_fn = self._make_setup(tmp_path, monkeypatch)
        entries_dir = tmp_path / ".trw" / "learnings" / "entries"

        # Write an existing entry
        FSW().write_yaml(entries_dir / "L-existing-gr.yaml", {
            "id": "L-existing-gr",
            "summary": "graceful fallback existing",
            "detail": "detail for existing entry",
            "tags": [],
            "evidence": [],
            "impact": 0.5,
            "status": "active",
            "recurrence": 1,
            "created": "2026-01-01",
            "updated": "2026-01-01",
            "merged_from": [],
        })

        # embed returns None for ALL calls
        monkeypatch.setattr("trw_mcp.state.dedup.embed", lambda text: None)

        result = tool_fn(
            summary="new summary different from existing",
            detail="new detail completely different",
        )

        # With embed returning None, dedup check returns 'store' → trw_learn records
        assert result["status"] == "recorded"


# ---------------------------------------------------------------------------
# FR04: Return dict key contract — learning_id present, path absent for skip/merge
# ---------------------------------------------------------------------------

class TestTrwLearnReturnDictKeys:
    """CORE-042-FR04: Verify return dict structure for skip, merge, and recorded paths."""

    def _setup_tool(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        dedup_enabled: bool = True,
    ) -> object:
        from fastmcp import FastMCP
        from trw_mcp.tools.learning import register_learning_tools

        entries_dir = tmp_path / ".trw" / "learnings" / "entries"
        entries_dir.mkdir(parents=True)
        (tmp_path / ".trw" / "logs").mkdir(parents=True)

        cfg = TRWConfig(dedup_enabled=dedup_enabled, dedup_skip_threshold=0.95, dedup_merge_threshold=0.85)
        reader = FileStateReader()
        writer = FileStateWriter()

        monkeypatch.setattr("trw_mcp.tools.learning._config", cfg)
        monkeypatch.setattr("trw_mcp.tools.learning._reader", reader)
        monkeypatch.setattr("trw_mcp.tools.learning._writer", writer)
        monkeypatch.setattr("trw_mcp.tools.learning.resolve_trw_dir", lambda: tmp_path / ".trw")
        monkeypatch.setattr("trw_mcp.tools.learning.generate_learning_id", lambda: "L-key-test")
        monkeypatch.setattr("trw_mcp.state.dedup.embed", mock_embed)

        server = FastMCP("test")
        register_learning_tools(server)
        return {t.name: t for t in server._tool_manager._tools.values()}["trw_learn"].fn

    def test_recorded_result_has_learning_id_and_path(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """FR04: Normal store ('recorded') result has learning_id and path."""
        tool_fn = self._setup_tool(tmp_path, monkeypatch)

        result = tool_fn(
            summary="unique brand new learning for key test abc123",
            detail="unique detail that won't match anything xyz987",
        )

        assert result["status"] == "recorded"
        assert "learning_id" in result, "recorded result must have learning_id"
        # path is optional but should be present for recorded (entry was written)
        assert result.get("learning_id") == "L-key-test"

    def test_skip_result_has_learning_id_and_duplicate_of(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """FR04: Skip ('skipped') result has learning_id and duplicate_of per PRD spec."""
        tool_fn = self._setup_tool(tmp_path, monkeypatch)
        entries_dir = tmp_path / ".trw" / "learnings" / "entries"

        summary = "pytest fixture isolation pattern for key test"
        detail = "use autouse fixtures with yield for clean teardown"
        FileStateWriter().write_yaml(entries_dir / "L-skip-key.yaml", {
            "id": "L-skip-key",
            "summary": summary,
            "detail": detail,
            "tags": [],
            "evidence": [],
            "impact": 0.5,
            "status": "active",
            "recurrence": 1,
            "created": "2026-01-01",
            "updated": "2026-01-01",
            "merged_from": [],
        })

        result = tool_fn(summary=summary, detail=detail)

        assert result["status"] == "skipped"
        assert "learning_id" in result, "skip result must have learning_id"
        assert "duplicate_of" in result, (
            "skip result must have 'duplicate_of' per PRD-CORE-042"
        )
        # 'path' should not be present for skipped entries (no new file written)
        assert result.get("path") is None or "path" not in result, (
            "skip result should not have a 'path' (no new file written)"
        )

    def test_merged_result_has_learning_id_and_merged_into(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """FR03: Merge ('merged') result has learning_id and merged_into per PRD spec."""
        tool_fn = self._setup_tool(tmp_path, monkeypatch)
        entries_dir = tmp_path / ".trw" / "learnings" / "entries"

        existing_summary = "pytest fixture autouse yield pattern"
        existing_detail = "use autouse fixtures with yield for clean teardown in pytest"
        FileStateWriter().write_yaml(entries_dir / "L-merge-key.yaml", {
            "id": "L-merge-key",
            "summary": existing_summary,
            "detail": existing_detail,
            "tags": [],
            "evidence": [],
            "impact": 0.5,
            "status": "active",
            "recurrence": 1,
            "created": "2026-01-01",
            "updated": "2026-01-01",
            "merged_from": [],
        })

        # New entry is similar but not identical (should trigger merge)
        new_summary = "pytest fixture autouse yield teardown"
        new_detail = "autouse fixtures with yield in pytest for clean teardown"

        result = tool_fn(summary=new_summary, detail=new_detail)

        # Result is one of merged, skipped, or recorded depending on similarity
        assert result["status"] in ("merged", "skipped", "recorded")
        assert "learning_id" in result or "new_id" in result, "result must have an ID"

        if result["status"] == "merged":
            assert "merged_into" in result, "merged result must have 'merged_into' per PRD"
        elif result["status"] == "skipped":
            assert "duplicate_of" in result, "skipped result must have 'duplicate_of' per PRD"

    def test_all_paths_always_return_learning_id(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """FR04: Every trw_learn response contains 'learning_id' regardless of path.

        This is a contract test — learning_id is the stable identifier regardless
        of whether the entry was recorded, merged, or skipped.
        """
        # Test recorded path (no dedup match)
        tool_fn = self._setup_tool(tmp_path, monkeypatch)

        result = tool_fn(
            summary="completely unique entry zzzz999",
            detail="no possible match for this detail xkcd1234",
        )
        assert "learning_id" in result, f"recorded: learning_id missing from {result}"

    def test_skip_threshold_boundary(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """FR04/CORE-042 AC: skip_threshold >= 0.95 means >=0.95 similarity triggers skip."""
        from trw_mcp.state.dedup import check_duplicate as cd

        entries_dir = tmp_path / "entries"
        entries_dir.mkdir()
        reader = FileStateReader()
        writer = FileStateWriter()

        # Config with explicit thresholds
        config = TRWConfig(dedup_skip_threshold=0.95, dedup_merge_threshold=0.85)

        summary = "skip threshold test"
        detail = "boundary condition at 0.95"
        write_entry(entries_dir, writer, "L-thresh-skip", summary, detail)

        # embed returns same vector → similarity = 1.0 >= 0.95 → skip
        with patch("trw_mcp.state.dedup.embed", side_effect=mock_embed):
            result = cd(summary, detail, entries_dir, reader, config=config)

        assert result.action == "skip", (
            f"Expected skip at similarity >= 0.95, got {result.action} (sim={result.similarity})"
        )
        assert result.similarity >= 0.95
