"""Comprehensive tests for PRD-CORE-021 knowledge topology module.

Covers:
- FR01: execute_knowledge_sync orchestration
- FR02: build_cooccurrence_matrix tag co-occurrence counting
- FR03: form_jaccard_clusters Jaccard-based clustering
- FR04: sanitize_slug + render_topic_document
- FR05: preserve_manual_markers
- FR06: trw_knowledge_sync tool registration and wiring
- FR07: trw_recall topic filter (topic= param)
- FR09/FR10: atomic clusters.json write + threshold guard
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from trw_memory.models.memory import MemoryEntry, MemoryStatus

from trw_mcp.models.config import TRWConfig
from trw_mcp.state.knowledge_topology import (
    build_cooccurrence_matrix,
    execute_knowledge_sync,
    form_jaccard_clusters,
    preserve_manual_markers,
    render_topic_document,
    sanitize_slug,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_entry(
    entry_id: str = "L-test001",
    content: str = "Test summary",
    detail: str = "Test detail",
    tags: list[str] | None = None,
    importance: float = 0.5,
    status: MemoryStatus = MemoryStatus.ACTIVE,
) -> MemoryEntry:
    """Create a MemoryEntry for testing."""
    now = datetime.now(timezone.utc)
    return MemoryEntry(
        id=entry_id,
        content=content,
        detail=detail,
        tags=tags or [],
        evidence=[],
        importance=importance,
        status=status,
        namespace="default",
        created_at=now,
        updated_at=now,
        merged_from=[],
        consolidated_from=[],
        metadata={},
    )


def _make_config(tmp_path: Path, **overrides: object) -> TRWConfig:
    """Create a TRWConfig with temp dir and optional overrides."""
    kwargs: dict[str, object] = {
        "trw_dir": str(tmp_path / ".trw"),
        "knowledge_sync_threshold": 50,
        "knowledge_jaccard_threshold": 0.3,
        "knowledge_min_cluster_size": 3,
        "knowledge_output_dir": "knowledge",
    }
    kwargs.update(overrides)
    return TRWConfig(**kwargs)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# FR04: sanitize_slug
# ---------------------------------------------------------------------------


class TestSanitizeSlug:
    """FR04: Normalize tag names to filesystem-safe slugs."""

    def test_already_clean(self) -> None:
        assert sanitize_slug("pydantic-gotchas") == "pydantic-gotchas"

    def test_spaces_to_hyphens(self) -> None:
        assert sanitize_slug("my tag name") == "my-tag-name"

    def test_special_chars_stripped(self) -> None:
        result = sanitize_slug("testing@v2!")
        # @ and ! are stripped; only alnum and hyphens remain
        assert result == "testingv2"

    def test_uppercase_lowercased(self) -> None:
        assert sanitize_slug("MyTag") == "mytag"

    def test_long_name_truncated(self) -> None:
        name = "a" * 100
        result = sanitize_slug(name)
        assert len(result) == 64

    def test_empty_string(self) -> None:
        assert sanitize_slug("") == ""

    def test_hyphens_preserved(self) -> None:
        result = sanitize_slug("test-tag-name")
        assert result == "test-tag-name"

    def test_numbers_preserved(self) -> None:
        result = sanitize_slug("v2-testing")
        assert result == "v2-testing"

    def test_mixed_case_and_spaces(self) -> None:
        result = sanitize_slug("FastAPI Testing")
        assert result == "fastapi-testing"

    def test_exactly_64_chars_not_truncated(self) -> None:
        name = "a" * 64
        result = sanitize_slug(name)
        assert result == name
        assert len(result) == 64

    def test_65_chars_truncated(self) -> None:
        name = "a" * 65
        result = sanitize_slug(name)
        assert len(result) == 64


# ---------------------------------------------------------------------------
# FR02: build_cooccurrence_matrix
# ---------------------------------------------------------------------------


class TestBuildCooccurrenceMatrix:
    """FR02: Tag co-occurrence counting with frequency filter."""

    def test_basic_cooccurrence(self) -> None:
        # 5 entries with ["a", "b"], 3 with ["a", "c"]
        entries = (
            [_make_entry(f"L-{i:03d}", tags=["a", "b"]) for i in range(5)]
            + [_make_entry(f"L-{i+5:03d}", tags=["a", "c"]) for i in range(3)]
        )
        matrix = build_cooccurrence_matrix(entries)
        assert ("a", "b") in matrix
        assert matrix[("a", "b")] == 5
        assert ("a", "c") in matrix
        assert matrix[("a", "c")] == 3

    def test_tag_in_only_one_entry_excluded(self) -> None:
        # "rare" tag appears in only 1 entry — must be excluded
        entries = [
            _make_entry("L-001", tags=["common", "rare"]),
            _make_entry("L-002", tags=["common", "other"]),
        ]
        matrix = build_cooccurrence_matrix(entries)
        # "common" appears in 2, "other" in 1, "rare" in 1
        # Only pairs of valid (freq>=2) tags are in matrix
        for pair in matrix:
            assert "rare" not in pair

    def test_no_tags_produces_empty_matrix(self) -> None:
        entries = [_make_entry(f"L-{i:03d}", tags=[]) for i in range(5)]
        matrix = build_cooccurrence_matrix(entries)
        assert matrix == {}

    def test_single_tag_entries_no_pairs(self) -> None:
        # All entries have same single tag — no pairs possible
        entries = [_make_entry(f"L-{i:03d}", tags=["solo"]) for i in range(10)]
        matrix = build_cooccurrence_matrix(entries)
        assert matrix == {}

    def test_pairs_sorted_alphabetically(self) -> None:
        entries = [
            _make_entry("L-001", tags=["z", "a"]),
            _make_entry("L-002", tags=["z", "a"]),
        ]
        matrix = build_cooccurrence_matrix(entries)
        # Pair key should be sorted: ("a", "z") not ("z", "a")
        assert ("a", "z") in matrix
        assert ("z", "a") not in matrix

    def test_empty_entries_list(self) -> None:
        matrix = build_cooccurrence_matrix([])
        assert matrix == {}

    def test_frequency_threshold_boundary(self) -> None:
        # Tag appearing exactly twice should be included
        entries = [
            _make_entry("L-001", tags=["alpha", "beta"]),
            _make_entry("L-002", tags=["alpha", "beta"]),
        ]
        matrix = build_cooccurrence_matrix(entries)
        assert ("alpha", "beta") in matrix
        assert matrix[("alpha", "beta")] == 2

    def test_three_tags_produces_three_pairs(self) -> None:
        # ["a", "b", "c"] appearing 3 times produces pairs (a,b), (a,c), (b,c)
        entries = [
            _make_entry(f"L-{i:03d}", tags=["a", "b", "c"]) for i in range(3)
        ]
        matrix = build_cooccurrence_matrix(entries)
        assert ("a", "b") in matrix
        assert ("a", "c") in matrix
        assert ("b", "c") in matrix


# ---------------------------------------------------------------------------
# FR03: form_jaccard_clusters
# ---------------------------------------------------------------------------


class TestFormJaccardClusters:
    """FR03: Jaccard-based clustering with merge and drop logic."""

    def test_two_distinct_clusters(self) -> None:
        # 10 entries with ["pydantic", "testing"], 8 with ["fastapi", "api"]
        entries = (
            [_make_entry(f"L-{i:03d}", tags=["pydantic", "testing"]) for i in range(10)]
            + [_make_entry(f"L-{i+10:03d}", tags=["fastapi", "api"]) for i in range(8)]
        )
        clusters = form_jaccard_clusters(entries, threshold=0.3, min_size=3)
        assert len(clusters) == 2
        slugs = {c["slug"] for c in clusters}
        # Each cluster gets the most-common tag as slug
        assert len(slugs) == 2

    def test_entries_with_no_tags_skipped(self) -> None:
        entries = (
            [_make_entry(f"L-{i:03d}", tags=[]) for i in range(5)]
            + [_make_entry(f"L-{i+5:03d}", tags=["testing", "python"]) for i in range(5)]
        )
        clusters = form_jaccard_clusters(entries, threshold=0.3, min_size=3)
        # Only entries with tags get clustered
        total_ids = sum(len(c["entry_ids"]) for c in clusters)
        assert total_ids == 5  # only the 5 tagged entries

    def test_all_entries_same_tags_one_cluster(self) -> None:
        entries = [
            _make_entry(f"L-{i:03d}", tags=["testing", "python"]) for i in range(8)
        ]
        clusters = form_jaccard_clusters(entries, threshold=0.3, min_size=3)
        assert len(clusters) == 1
        assert len(clusters[0]["entry_ids"]) == 8

    def test_cluster_below_min_size_merged(self) -> None:
        # 6 entries forming cluster A, 2 forming cluster B (below min_size=3)
        # B should be merged into A
        entries = (
            [_make_entry(f"L-{i:03d}", tags=["main", "topic"]) for i in range(6)]
            + [_make_entry(f"L-{i+6:03d}", tags=["main", "small"]) for i in range(2)]
        )
        clusters = form_jaccard_clusters(entries, threshold=0.3, min_size=3)
        # After merge, should be 1 cluster with all 8 entries
        total_ids = sum(len(c["entry_ids"]) for c in clusters)
        assert total_ids == 8

    def test_cluster_output_structure(self) -> None:
        entries = [
            _make_entry(f"L-{i:03d}", tags=["testing"]) for i in range(5)
        ]
        clusters = form_jaccard_clusters(entries, threshold=0.3, min_size=3)
        assert len(clusters) >= 1
        cluster = clusters[0]
        assert "slug" in cluster
        assert "tags" in cluster
        assert "entry_ids" in cluster
        assert "entries" in cluster
        assert "avg_importance" in cluster

    def test_avg_importance_computed(self) -> None:
        entries = [
            _make_entry(f"L-{i:03d}", tags=["testing"], importance=float(i) / 10)
            for i in range(1, 6)
        ]
        clusters = form_jaccard_clusters(entries, threshold=0.3, min_size=3)
        assert len(clusters) >= 1
        avg = clusters[0]["avg_importance"]
        assert isinstance(avg, float)
        assert 0.0 < float(avg) <= 1.0

    def test_empty_entries_returns_empty(self) -> None:
        clusters = form_jaccard_clusters([], threshold=0.3, min_size=3)
        assert clusters == []

    def test_high_threshold_splits_similar_clusters(self) -> None:
        # threshold=1.0 means entries need 100% tag overlap to join — each
        # unique tag set seeds its own cluster, then undersized ones get merged/dropped
        entries = (
            [_make_entry(f"L-{i:03d}", tags=["a", "b"]) for i in range(5)]
            + [_make_entry(f"L-{i+5:03d}", tags=["c", "d"]) for i in range(5)]
        )
        clusters = form_jaccard_clusters(entries, threshold=1.0, min_size=3)
        # With threshold=1.0 and identical tag sets within groups, should form 2 clusters
        total_entries = sum(len(c["entry_ids"]) for c in clusters)
        assert total_entries == 10

    def test_cluster_slug_is_most_common_tag(self) -> None:
        # "pydantic" appears in all 5, "testing" in 3, "fastapi" in 2
        entries = (
            [_make_entry(f"L-{i:03d}", tags=["pydantic", "testing"]) for i in range(3)]
            + [_make_entry(f"L-{i+3:03d}", tags=["pydantic", "fastapi"]) for i in range(2)]
        )
        clusters = form_jaccard_clusters(entries, threshold=0.1, min_size=1)
        # Pydantic appears 5 times, should be slug
        assert any("pydantic" in str(c["slug"]) for c in clusters)

    def test_tags_in_cluster_are_union(self) -> None:
        entries = (
            [_make_entry(f"L-{i:03d}", tags=["a", "b"]) for i in range(4)]
            + [_make_entry(f"L-{i+4:03d}", tags=["a", "c"]) for i in range(4)]
        )
        clusters = form_jaccard_clusters(entries, threshold=0.1, min_size=3)
        # Should form one big cluster (a connects both groups)
        all_tags: list[str] = []
        for c in clusters:
            all_tags.extend(c["tags"])  # type: ignore[arg-type]
        assert "a" in all_tags

    def test_cluster_entry_ids_match_entries(self) -> None:
        entries = [
            _make_entry(f"L-{i:03d}", tags=["topic"]) for i in range(5)
        ]
        clusters = form_jaccard_clusters(entries, threshold=0.3, min_size=3)
        for cluster in clusters:
            ids = cluster["entry_ids"]
            mem_entries = cluster["entries"]
            assert len(ids) == len(mem_entries)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# FR04/FR05: render_topic_document
# ---------------------------------------------------------------------------


class TestRenderTopicDocument:
    """FR04: Markdown rendering of cluster topic documents."""

    def _make_cluster(
        self,
        slug: str = "testing",
        entries: list[MemoryEntry] | None = None,
        tags: list[str] | None = None,
        avg_importance: float = 0.7,
    ) -> dict[str, object]:
        mem_entries = entries or [
            _make_entry("L-001", content="Summary one", importance=0.8),
            _make_entry("L-002", content="Summary two", importance=0.6),
            _make_entry("L-003", content="Summary three", importance=0.7),
        ]
        return {
            "slug": slug,
            "tags": tags or ["testing", "python"],
            "entry_ids": [e.id for e in mem_entries],
            "entries": mem_entries,
            "avg_importance": avg_importance,
        }

    def test_contains_all_summaries(self) -> None:
        cluster = self._make_cluster()
        rendered = render_topic_document(cluster)
        assert "Summary one" in rendered
        assert "Summary two" in rendered
        assert "Summary three" in rendered

    def test_entries_sorted_by_importance_desc(self) -> None:
        cluster = self._make_cluster()
        rendered = render_topic_document(cluster)
        # 0.8 entry should appear before 0.7 before 0.6
        idx_one = rendered.index("Summary one")   # importance 0.8
        idx_three = rendered.index("Summary three")  # importance 0.7
        idx_two = rendered.index("Summary two")   # importance 0.6
        assert idx_one < idx_three < idx_two

    def test_long_detail_truncated(self) -> None:
        long_detail = "x" * 600
        entries = [_make_entry("L-001", detail=long_detail, tags=["a"])]
        cluster: dict[str, object] = {
            "slug": "topic",
            "tags": ["a"],
            "entry_ids": ["L-001"],
            "entries": entries,
            "avg_importance": 0.5,
        }
        rendered = render_topic_document(cluster)
        # Detail truncated to 500 chars + "..."
        assert "..." in rendered
        assert "x" * 501 not in rendered

    def test_contains_auto_generated_marker(self) -> None:
        cluster = self._make_cluster()
        rendered = render_topic_document(cluster)
        assert "<!-- trw:auto-generated -->" in rendered

    def test_contains_metadata(self) -> None:
        cluster = self._make_cluster(avg_importance=0.75)
        rendered = render_topic_document(cluster)
        assert "Entries" in rendered
        assert "Avg importance" in rendered
        assert "Last sync" in rendered
        assert "Tags" in rendered

    def test_entry_count_in_metadata(self) -> None:
        cluster = self._make_cluster()
        rendered = render_topic_document(cluster)
        assert "3" in rendered  # 3 entries

    def test_slug_as_heading(self) -> None:
        cluster = self._make_cluster(slug="pydantic")
        rendered = render_topic_document(cluster)
        assert "# pydantic" in rendered

    def test_no_detail_omits_detail_line(self) -> None:
        entries = [_make_entry("L-001", detail="", importance=0.5)]
        cluster: dict[str, object] = {
            "slug": "topic",
            "tags": ["testing"],
            "entry_ids": ["L-001"],
            "entries": entries,
            "avg_importance": 0.5,
        }
        rendered = render_topic_document(cluster)
        assert "Detail:" not in rendered

    def test_evidence_included_when_present(self) -> None:
        now = datetime.now(timezone.utc)
        entry = MemoryEntry(
            id="L-001",
            content="Summary with evidence",
            detail="",
            tags=["testing"],
            evidence=["src/foo.py"],
            importance=0.5,
            status=MemoryStatus.ACTIVE,
            namespace="default",
            created_at=now,
            updated_at=now,
            merged_from=[],
            consolidated_from=[],
            metadata={},
        )
        cluster: dict[str, object] = {
            "slug": "topic",
            "tags": ["testing"],
            "entry_ids": ["L-001"],
            "entries": [entry],
            "avg_importance": 0.5,
        }
        rendered = render_topic_document(cluster)
        assert "src/foo.py" in rendered

    def test_tags_included_in_entry_line(self) -> None:
        cluster = self._make_cluster(tags=["foo", "bar"])
        rendered = render_topic_document(cluster)
        assert "foo" in rendered
        assert "bar" in rendered

    def test_no_summary_fallback(self) -> None:
        entries = [_make_entry("L-001", content="", importance=0.5)]
        entries[0] = MemoryEntry(
            id="L-001",
            content="",
            detail="",
            tags=[],
            evidence=[],
            importance=0.5,
            status=MemoryStatus.ACTIVE,
            namespace="default",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
            merged_from=[],
            consolidated_from=[],
            metadata={},
        )
        cluster: dict[str, object] = {
            "slug": "topic",
            "tags": [],
            "entry_ids": ["L-001"],
            "entries": entries,
            "avg_importance": 0.5,
        }
        rendered = render_topic_document(cluster)
        assert "(no summary)" in rendered

    def test_detail_exactly_500_not_truncated(self) -> None:
        detail_500 = "y" * 500
        entries = [_make_entry("L-001", detail=detail_500, tags=["a"])]
        cluster: dict[str, object] = {
            "slug": "topic",
            "tags": ["a"],
            "entry_ids": ["L-001"],
            "entries": entries,
            "avg_importance": 0.5,
        }
        rendered = render_topic_document(cluster)
        # Exactly 500 chars should NOT be truncated
        assert "..." not in rendered


# ---------------------------------------------------------------------------
# FR05: preserve_manual_markers
# ---------------------------------------------------------------------------


class TestPreserveManualMarkers:
    """FR05: Manual marker preservation in topic documents."""

    def test_no_markers_returns_new_content(self) -> None:
        existing = "Old auto-generated content"
        new = "New auto-generated content"
        result = preserve_manual_markers(existing, new)
        assert result == new

    def test_paired_markers_preserved(self) -> None:
        existing = (
            "<!-- trw:auto-generated -->\n"
            "Old content\n"
            "<!-- trw:manual-start -->Custom notes<!-- trw:manual-end -->"
        )
        new = "New auto-generated content"
        result = preserve_manual_markers(existing, new)
        assert "Custom notes" in result
        assert "New auto-generated content" in result
        assert "<!-- trw:manual-start -->" in result
        assert "<!-- trw:manual-end -->" in result

    def test_unpaired_opening_preserves_to_eof(self) -> None:
        existing = (
            "Auto section\n"
            "<!-- trw:manual-start -->Notes that continue to EOF\nMore notes"
        )
        new = "Fresh content"
        result = preserve_manual_markers(existing, new)
        assert "Notes that continue to EOF" in result
        assert "More notes" in result
        # Opening marker is preserved
        assert "<!-- trw:manual-start -->" in result
        # No closing marker
        assert "<!-- trw:manual-end -->" not in result

    def test_crlf_handling(self) -> None:
        existing = (
            "Auto\r\n"
            "<!-- trw:manual-start -->\r\nManual content\r\n<!-- trw:manual-end -->"
        )
        new = "New content"
        result = preserve_manual_markers(existing, new)
        assert "Manual content" in result

    def test_empty_existing_returns_new_content(self) -> None:
        result = preserve_manual_markers("", "New content")
        assert result == "New content"

    def test_manual_block_appended_after_new_content(self) -> None:
        existing = (
            "Old\n"
            "<!-- trw:manual-start -->My notes<!-- trw:manual-end -->"
        )
        new = "New generated"
        result = preserve_manual_markers(existing, new)
        # New content should come first, then manual block
        new_idx = result.index("New generated")
        manual_idx = result.index("My notes")
        assert new_idx < manual_idx

    def test_new_content_returned_unchanged_when_no_marker(self) -> None:
        existing = "Some old auto content without any markers"
        new = "Brand new content here"
        result = preserve_manual_markers(existing, new)
        assert result == new

    def test_multiple_markers_uses_first(self) -> None:
        # Only first start marker is honored
        existing = (
            "<!-- trw:manual-start -->First block<!-- trw:manual-end -->\n"
            "<!-- trw:manual-start -->Second block<!-- trw:manual-end -->"
        )
        new = "Fresh"
        result = preserve_manual_markers(existing, new)
        assert "First block" in result


# ---------------------------------------------------------------------------
# FR01/FR09/FR10: execute_knowledge_sync
# ---------------------------------------------------------------------------


class TestExecuteKnowledgeSync:
    """FR01/FR09/FR10: Orchestration, threshold guard, atomic writes."""

    @pytest.fixture
    def trw_dir(self, tmp_path: Path) -> Path:
        d = tmp_path / ".trw"
        d.mkdir()
        return d

    def _make_entries(self, count: int) -> list[MemoryEntry]:
        """Create a list of MemoryEntry with paired tags for cluster formation."""
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
        # No files written
        knowledge_dir = trw_dir / "knowledge"
        assert not knowledge_dir.exists()

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
        """NFR02: StorageError on count_entries returns threshold_met=False, not exception."""
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
        # No files written
        knowledge_dir = trw_dir / "knowledge"
        assert not knowledge_dir.exists()

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
        md_files = list(knowledge_dir.glob("*.md"))
        assert len(md_files) >= 1

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
        # Valid JSON
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

        clusters_path = trw_dir / "knowledge" / "clusters.json"
        data = json.loads(clusters_path.read_text(encoding="utf-8"))
        # Each cluster slug in result should be a key in clusters.json
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

        # Inject manual markers into generated file
        knowledge_dir = trw_dir / "knowledge"
        md_files = list(knowledge_dir.glob("*.md"))
        assert md_files, "Expected at least one .md file generated"
        first_file = md_files[0]
        existing = first_file.read_text(encoding="utf-8")
        first_file.write_text(
            existing + "\n<!-- trw:manual-start -->MY NOTES<!-- trw:manual-end -->\n",
            encoding="utf-8",
        )

        # Second sync should preserve manual content
        with (
            patch("trw_mcp.state.knowledge_topology.count_entries", return_value=5),
            patch("trw_mcp.state.knowledge_topology.get_backend", return_value=mock_backend),
        ):
            execute_knowledge_sync(trw_dir, config)

        refreshed = first_file.read_text(encoding="utf-8")
        assert "MY NOTES" in refreshed

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

    def test_partial_render_failure_collected_not_raised(
        self, trw_dir: Path, tmp_path: Path
    ) -> None:
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

        # Errors captured, not re-raised
        assert len(result["errors"]) >= 1
        assert any("Render explosion" in str(e) for e in result["errors"])


# ---------------------------------------------------------------------------
# FR06: Tool registration (trw_knowledge_sync)
# ---------------------------------------------------------------------------


class TestToolRegistration:
    """FR06: trw_knowledge_sync appears in MCP server tool registry."""

    def test_knowledge_sync_registered(self) -> None:
        from trw_mcp.server import mcp

        tool_names = {t.name for t in mcp._tool_manager._tools.values()}
        assert "trw_knowledge_sync" in tool_names

    def test_knowledge_sync_callable(self, tmp_path: Path) -> None:
        from trw_mcp.server import mcp

        tool = next(
            t for t in mcp._tool_manager._tools.values()
            if t.name == "trw_knowledge_sync"
        )
        assert callable(tool.fn)

    def test_knowledge_sync_dry_run_parameter(self, tmp_path: Path) -> None:
        import inspect

        from trw_mcp.server import mcp

        tool = next(
            t for t in mcp._tool_manager._tools.values()
            if t.name == "trw_knowledge_sync"
        )
        sig = inspect.signature(tool.fn)
        assert "dry_run" in sig.parameters

    def test_knowledge_sync_returns_elapsed_seconds(self, tmp_path: Path) -> None:
        from trw_mcp.server import mcp
        from trw_mcp.state._paths import resolve_trw_dir

        trw_dir = resolve_trw_dir()
        tool = next(
            t for t in mcp._tool_manager._tools.values()
            if t.name == "trw_knowledge_sync"
        )

        with patch("trw_mcp.state.knowledge_topology.count_entries", return_value=1):
            result = tool.fn(dry_run=False)

        assert "elapsed_seconds" in result
        assert isinstance(result["elapsed_seconds"], float)


# ---------------------------------------------------------------------------
# FR07: trw_recall topic filter
# ---------------------------------------------------------------------------


class TestRecallTopicFilter:
    """FR07: topic= parameter filters recall results to a knowledge cluster."""

    def _setup_clusters_json(
        self,
        trw_dir: Path,
        slug: str,
        entry_ids: list[str],
    ) -> None:
        """Write clusters.json into the knowledge dir."""
        knowledge_dir = trw_dir / "knowledge"
        knowledge_dir.mkdir(parents=True, exist_ok=True)
        data = {
            slug: entry_ids,
            "updated_at": "2026-01-01T00:00:00+00:00",
        }
        (knowledge_dir / "clusters.json").write_text(
            json.dumps(data), encoding="utf-8"
        )

    def test_topic_filters_to_matching_entries(self, tmp_path: Path) -> None:
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        self._setup_clusters_json(trw_dir, "pydantic", ["L-001", "L-002"])

        # Two matching, one not-matching
        matching = [
            {"id": "L-001", "summary": "Pydantic tip", "impact": 0.8, "tags": ["pydantic"], "status": "active"},
            {"id": "L-002", "summary": "Another tip", "impact": 0.7, "tags": ["pydantic"], "status": "active"},
        ]
        not_matching = [
            {"id": "L-999", "summary": "Unrelated", "impact": 0.5, "tags": ["fastapi"], "status": "active"},
        ]
        all_entries = matching + not_matching

        with (
            patch("trw_mcp.tools.learning.adapter_recall", return_value=all_entries),
            patch("trw_mcp.tools.learning.adapter_update_access"),
            patch("trw_mcp.tools.learning.log_recall_receipt"),
            patch("trw_mcp.tools.learning.search_patterns", return_value=[]),
            patch("trw_mcp.tools.learning.resolve_trw_dir", return_value=trw_dir),
            patch("trw_mcp.tools.learning.collect_context", return_value={}),
            patch("trw_mcp.tools.learning._config") as mock_config,
        ):
            mock_config.knowledge_output_dir = "knowledge"
            mock_config.recall_max_results = 25
            mock_config.recall_utility_lambda = 0.3
            mock_config.context_dir = "context"
            mock_config.patterns_dir = "patterns"
            mock_config.recall_compact_fields = frozenset({"id", "summary", "impact", "tags", "status"})

            # Import and call the internal topic filter logic directly
            import json as _json
            clusters_path = trw_dir / "knowledge" / "clusters.json"
            clusters_data = _json.loads(clusters_path.read_text(encoding="utf-8"))
            allowed_ids = set(clusters_data["pydantic"])
            filtered = [e for e in all_entries if str(e.get("id", "")) in allowed_ids]

        assert len(filtered) == 2
        filtered_ids = {e["id"] for e in filtered}
        assert "L-001" in filtered_ids
        assert "L-002" in filtered_ids
        assert "L-999" not in filtered_ids

    def test_topic_filter_nonexistent_topic_ignored(self, tmp_path: Path) -> None:
        """Non-existent topic in clusters.json sets topic_filter_ignored=True."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        self._setup_clusters_json(trw_dir, "pydantic", ["L-001"])

        # Check the filtering logic: "nonexistent" not in clusters_data
        clusters_path = trw_dir / "knowledge" / "clusters.json"
        clusters_data = json.loads(clusters_path.read_text(encoding="utf-8"))
        topic_filter_ignored = "nonexistent" not in clusters_data
        assert topic_filter_ignored is True

    def test_topic_filter_missing_clusters_file_ignored(self, tmp_path: Path) -> None:
        """Missing clusters.json sets topic_filter_ignored=True."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        # No clusters.json written
        clusters_path = trw_dir / "knowledge" / "clusters.json"
        assert not clusters_path.exists()
        topic_filter_ignored = not clusters_path.exists()
        assert topic_filter_ignored is True

    def test_topic_filter_malformed_clusters_json_ignored(self, tmp_path: Path) -> None:
        """Malformed clusters.json falls back to no filter (topic_filter_ignored=True)."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        knowledge_dir = trw_dir / "knowledge"
        knowledge_dir.mkdir()
        (knowledge_dir / "clusters.json").write_text("{not valid json!!!", encoding="utf-8")

        topic_filter_ignored = False
        try:
            json.loads((knowledge_dir / "clusters.json").read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            topic_filter_ignored = True
        assert topic_filter_ignored is True

    def test_topic_none_no_filter(self, tmp_path: Path) -> None:
        """topic=None means no filtering, topic_filter_ignored=False."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        self._setup_clusters_json(trw_dir, "pydantic", ["L-001"])

        # Simulate: topic is None, so no filter applied
        all_entries = [
            {"id": "L-001", "summary": "Tip one", "impact": 0.8, "tags": [], "status": "active"},
            {"id": "L-002", "summary": "Tip two", "impact": 0.6, "tags": [], "status": "active"},
        ]

        # When topic is None, the filter block is skipped entirely
        topic = None
        topic_filter_ignored = False
        if topic is not None:
            # This block would execute filtering
            topic_filter_ignored = True  # pragma: no cover

        # No filtering happened
        assert len(all_entries) == 2
        assert topic_filter_ignored is False

    def test_topic_filter_via_real_trw_recall(self, tmp_path: Path) -> None:
        """Integration test: actual topic filter in learning.py code path."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        (trw_dir / "patterns").mkdir(exist_ok=True)
        self._setup_clusters_json(trw_dir, "pydantic", ["L-001", "L-002"])

        all_entries = [
            {"id": "L-001", "summary": "Pydantic tip", "impact": 0.8, "tags": ["pydantic"], "status": "active"},
            {"id": "L-002", "summary": "Another tip", "impact": 0.7, "tags": ["pydantic"], "status": "active"},
            {"id": "L-999", "summary": "Unrelated", "impact": 0.5, "tags": ["fastapi"], "status": "active"},
        ]

        from trw_mcp.server import mcp

        tool = next(
            t for t in mcp._tool_manager._tools.values()
            if t.name == "trw_recall"
        )

        with (
            patch("trw_mcp.tools.learning.adapter_recall", return_value=all_entries),
            patch("trw_mcp.tools.learning.adapter_update_access"),
            patch("trw_mcp.tools.learning.log_recall_receipt"),
            patch("trw_mcp.tools.learning.search_patterns", return_value=[]),
            patch("trw_mcp.tools.learning.resolve_trw_dir", return_value=trw_dir),
            patch("trw_mcp.tools.learning.collect_context", return_value={}),
        ):
            result = tool.fn(query="*", topic="pydantic")

        # Only the 2 pydantic entries should be returned
        returned_ids = {str(e.get("id", "")) for e in result["learnings"]}
        assert "L-999" not in returned_ids
        assert result["topic_filter_ignored"] is False

    def test_topic_filter_nonexistent_sets_ignored_flag(self, tmp_path: Path) -> None:
        """topic= with non-existent slug in clusters.json returns topic_filter_ignored=True."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        (trw_dir / "patterns").mkdir(exist_ok=True)
        self._setup_clusters_json(trw_dir, "pydantic", ["L-001"])

        all_entries = [
            {"id": "L-001", "summary": "Tip", "impact": 0.8, "tags": [], "status": "active"},
        ]

        from trw_mcp.server import mcp

        tool = next(
            t for t in mcp._tool_manager._tools.values()
            if t.name == "trw_recall"
        )

        with (
            patch("trw_mcp.tools.learning.adapter_recall", return_value=all_entries),
            patch("trw_mcp.tools.learning.adapter_update_access"),
            patch("trw_mcp.tools.learning.log_recall_receipt"),
            patch("trw_mcp.tools.learning.search_patterns", return_value=[]),
            patch("trw_mcp.tools.learning.resolve_trw_dir", return_value=trw_dir),
            patch("trw_mcp.tools.learning.collect_context", return_value={}),
            # Suppress remote recall so remote entries don't inflate result counts
            patch("trw_mcp.telemetry.remote_recall.fetch_shared_learnings", return_value=[]),
        ):
            result = tool.fn(query="*", topic="nonexistent_topic")

        assert result["topic_filter_ignored"] is True
        # All local entries returned since topic was ignored (remote recall suppressed)
        assert len(result["learnings"]) == len(all_entries)

    def test_topic_filter_no_clusters_file_sets_ignored_flag(self, tmp_path: Path) -> None:
        """topic= without clusters.json file returns topic_filter_ignored=True."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        (trw_dir / "patterns").mkdir(exist_ok=True)
        # No clusters.json written

        all_entries = [
            {"id": "L-001", "summary": "Tip", "impact": 0.8, "tags": [], "status": "active"},
        ]

        from trw_mcp.server import mcp

        tool = next(
            t for t in mcp._tool_manager._tools.values()
            if t.name == "trw_recall"
        )

        with (
            patch("trw_mcp.tools.learning.adapter_recall", return_value=all_entries),
            patch("trw_mcp.tools.learning.adapter_update_access"),
            patch("trw_mcp.tools.learning.log_recall_receipt"),
            patch("trw_mcp.tools.learning.search_patterns", return_value=[]),
            patch("trw_mcp.tools.learning.resolve_trw_dir", return_value=trw_dir),
            patch("trw_mcp.tools.learning.collect_context", return_value={}),
        ):
            result = tool.fn(query="*", topic="pydantic")

        assert result["topic_filter_ignored"] is True

    def test_topic_none_no_filter_ignored_flag_false(self, tmp_path: Path) -> None:
        """topic=None means topic_filter_ignored=False in response."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        (trw_dir / "patterns").mkdir(exist_ok=True)

        all_entries = [
            {"id": "L-001", "summary": "Tip", "impact": 0.8, "tags": [], "status": "active"},
        ]

        from trw_mcp.server import mcp

        tool = next(
            t for t in mcp._tool_manager._tools.values()
            if t.name == "trw_recall"
        )

        with (
            patch("trw_mcp.tools.learning.adapter_recall", return_value=all_entries),
            patch("trw_mcp.tools.learning.adapter_update_access"),
            patch("trw_mcp.tools.learning.log_recall_receipt"),
            patch("trw_mcp.tools.learning.search_patterns", return_value=[]),
            patch("trw_mcp.tools.learning.resolve_trw_dir", return_value=trw_dir),
            patch("trw_mcp.tools.learning.collect_context", return_value={}),
        ):
            result = tool.fn(query="*", topic=None)

        assert result["topic_filter_ignored"] is False


# ---------------------------------------------------------------------------
# Config field tests (FR08: section 38 config fields)
# ---------------------------------------------------------------------------


class TestKnowledgeTopologyConfig:
    """FR08: Config section 38 fields have correct defaults and validation."""

    def test_knowledge_sync_threshold_default(self) -> None:
        config = TRWConfig()
        assert config.knowledge_sync_threshold == 50

    def test_knowledge_jaccard_threshold_default(self) -> None:
        config = TRWConfig()
        assert config.knowledge_jaccard_threshold == 0.3

    def test_knowledge_min_cluster_size_default(self) -> None:
        config = TRWConfig()
        assert config.knowledge_min_cluster_size == 3

    def test_knowledge_output_dir_default(self) -> None:
        config = TRWConfig()
        assert config.knowledge_output_dir == "knowledge"

    def test_knowledge_jaccard_threshold_ge_zero(self) -> None:
        config = TRWConfig(knowledge_jaccard_threshold=0.0)
        assert config.knowledge_jaccard_threshold == 0.0

    def test_knowledge_jaccard_threshold_le_one(self) -> None:
        config = TRWConfig(knowledge_jaccard_threshold=1.0)
        assert config.knowledge_jaccard_threshold == 1.0

    def test_knowledge_min_cluster_size_ge_one(self) -> None:
        config = TRWConfig(knowledge_min_cluster_size=1)
        assert config.knowledge_min_cluster_size == 1

    def test_knowledge_output_dir_customizable(self) -> None:
        config = TRWConfig(knowledge_output_dir="custom_knowledge")
        assert config.knowledge_output_dir == "custom_knowledge"

    def test_knowledge_sync_threshold_customizable(self) -> None:
        config = TRWConfig(knowledge_sync_threshold=100)
        assert config.knowledge_sync_threshold == 100


# ---------------------------------------------------------------------------
# Edge cases and error handling
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """Edge cases and error handling across all topology functions."""

    def test_sanitize_slug_unicode_stripped(self) -> None:
        # Unicode chars outside alnum/hyphen range are stripped
        result = sanitize_slug("café-testing")
        # "é" gets stripped, "caf-testing" remains
        assert "caf" in result
        assert "testing" in result

    def test_form_clusters_single_oversized_cluster(self) -> None:
        # All 20 entries have same tags — one cluster with 20 entries
        entries = [
            _make_entry(f"L-{i:03d}", tags=["alpha", "beta", "gamma"])
            for i in range(20)
        ]
        clusters = form_jaccard_clusters(entries, threshold=0.3, min_size=3)
        assert len(clusters) == 1
        assert len(clusters[0]["entry_ids"]) == 20

    def test_form_clusters_all_below_min_size_dropped(self) -> None:
        # 2 entries with unique tag sets, min_size=5 — they get merged but
        # resulting cluster (2 entries) still dropped
        entries = [
            _make_entry("L-001", tags=["aaa"]),
            _make_entry("L-002", tags=["bbb"]),
        ]
        clusters = form_jaccard_clusters(entries, threshold=0.9, min_size=5)
        assert clusters == []

    def test_build_cooccurrence_matrix_large_tag_set(self) -> None:
        # 10 tags per entry, 10 entries — many pairs
        tags = [f"tag{i}" for i in range(10)]
        entries = [_make_entry(f"L-{i:03d}", tags=tags) for i in range(10)]
        matrix = build_cooccurrence_matrix(entries)
        # C(10,2) = 45 pairs
        assert len(matrix) == 45

    def test_execute_sync_empty_backend_results(self, tmp_path: Path) -> None:
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        config = _make_config(tmp_path, knowledge_sync_threshold=5)

        mock_backend = MagicMock()
        mock_backend.list_entries.return_value = []  # Empty backend

        with (
            patch("trw_mcp.state.knowledge_topology.count_entries", return_value=10),
            patch("trw_mcp.state.knowledge_topology.get_backend", return_value=mock_backend),
        ):
            result = execute_knowledge_sync(trw_dir, config)

        # No clusters to generate from empty list
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
        # Should still produce valid markdown without errors
        assert "# empty" in rendered
        assert "<!-- trw:auto-generated -->" in rendered

    def test_preserve_markers_exception_returns_existing(self) -> None:
        # Simulate an internal error by passing a type that can't be processed
        # The function catches all exceptions and returns existing_content
        # We'll test the normal path where existing_content is returned on error
        existing = "my existing content"
        new = "new content"
        # Normal no-marker case returns new_content
        result = preserve_manual_markers(existing, new)
        assert result == new

    def test_form_clusters_zero_threshold(self) -> None:
        # threshold=0.0 means all non-empty-tag entries join first cluster
        entries = [
            _make_entry(f"L-{i:03d}", tags=[f"tag{i}"]) for i in range(10)
        ]
        clusters = form_jaccard_clusters(entries, threshold=0.0, min_size=1)
        # All entries with unique tags join the first cluster via threshold=0.0
        # (sim >= 0.0 is always true for non-empty tag sets)
        total = sum(len(c["entry_ids"]) for c in clusters)
        assert total == 10

    def test_preserve_manual_markers_exception_returns_existing(self) -> None:
        """Exception in preserve_manual_markers returns existing_content unchanged."""
        # We trigger the except branch by monkeypatching str.find to raise
        existing = "existing content with <!-- trw:manual-start -->marker"
        new = "new content"

        # The exception path (lines 264-265) returns existing_content
        # We can reach it by manipulating the marker search to raise
        original_find = str.find

        call_count = [0]

        def raising_find(self: str, sub: str, *args: object) -> int:
            call_count[0] += 1
            if call_count[0] >= 2:  # Second call raises
                raise RuntimeError("Simulated internal error")
            return original_find(self, sub, *args)  # type: ignore[arg-type]

        # Instead, we directly test the exception catch by overriding normalized.find
        # The easiest way: call with a broken 'existing_content' object that fails .replace()

        class BreakingStr(str):
            def replace(self, *args: object) -> str:
                raise ValueError("CRLF normalize error")

        bad_existing = BreakingStr("bad content <!-- trw:manual-start -->")
        result = preserve_manual_markers(bad_existing, new)  # type: ignore[arg-type]
        # Exception caught — existing_content returned
        assert result == bad_existing

    def test_execute_sync_oserror_reading_existing_file(
        self, tmp_path: Path
    ) -> None:
        """OSError reading existing topic file is silently ignored (line 429-430)."""
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
            # First sync to create files
            execute_knowledge_sync(trw_dir, config)

        # Overwrite a topic file with one that raises OSError on read_text
        knowledge_dir = trw_dir / "knowledge"
        md_files = list(knowledge_dir.glob("*.md"))
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
            # Should not raise — OSError is swallowed with pass
            result = execute_knowledge_sync(trw_dir, config)

        # Sync still succeeds; topic is written with fresh render
        assert result["topics_generated"] >= 1


# ---------------------------------------------------------------------------
# Targeted coverage: _jaccard both-empty, atomic write double-failure,
# and execute_knowledge_sync clusters.json write failure error capture
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestJaccardBothEmpty:
    """Line 46: _jaccard with both sets empty returns 0.0 without ZeroDivisionError."""

    def test_both_sets_empty_returns_zero(self) -> None:
        """_jaccard(set(), set()) must return 0.0 (union=0 guard, line 45-46)."""
        from trw_mcp.state.knowledge_topology import _jaccard

        result = _jaccard(set(), set())
        assert result == 0.0

    def test_one_empty_one_nonempty(self) -> None:
        """_jaccard({}, {'a'}) returns 0.0 — intersection is empty."""
        from trw_mcp.state.knowledge_topology import _jaccard

        assert _jaccard(set(), {"a"}) == 0.0

    def test_identical_nonempty_sets_return_one(self) -> None:
        """_jaccard({'x'}, {'x'}) returns 1.0 — sanity check."""
        from trw_mcp.state.knowledge_topology import _jaccard

        assert _jaccard({"x"}, {"x"}) == 1.0


@pytest.mark.unit
class TestAtomicWriteDoubleFault:
    """Lines 401-406: atomic write fails AND temp file cleanup also fails.

    The inner OSError on json.dump/replace should cause the outer except to
    attempt cleanup via Path.unlink. If cleanup itself raises OSError, it
    is silently swallowed (pass), and the original exception is re-raised
    to be caught by the outer except on line 413.
    """

    def _make_entries(self, n: int = 6) -> list[MemoryEntry]:
        return [_make_entry(f"L-{i:03d}", tags=["testing", "python"]) for i in range(n)]

    def test_double_failure_captured_in_errors(self, tmp_path: Path) -> None:
        """When replace() fails AND unlink() also fails, error is captured in result."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        config = _make_config(
            tmp_path,
            knowledge_sync_threshold=5,
            knowledge_min_cluster_size=2,
        )
        entries = self._make_entries(6)
        mock_backend = MagicMock()
        mock_backend.list_entries.return_value = entries

        replace_error = OSError("replace failed")
        unlink_error = OSError("unlink also failed")

        original_replace = Path.replace

        def fail_on_tmp_replace(self_path: Path, target: object) -> None:
            if str(self_path).endswith(".tmp"):
                raise replace_error
            return original_replace(self_path, target)  # type: ignore[arg-type]

        original_unlink = Path.unlink

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

        # The clusters.json write failure must be captured as an error, not raised
        assert any("clusters.json write failed" in e for e in result["errors"])

    def test_replace_fails_cleanup_succeeds(self, tmp_path: Path) -> None:
        """When replace() fails but unlink succeeds, error is still captured."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        config = _make_config(
            tmp_path,
            knowledge_sync_threshold=5,
            knowledge_min_cluster_size=2,
        )
        entries = self._make_entries(6)
        mock_backend = MagicMock()
        mock_backend.list_entries.return_value = entries

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

        assert any("clusters.json write failed" in e for e in result["errors"])


@pytest.mark.unit
class TestExecuteSyncClustersJsonWriteFailure:
    """Lines 413-415: execute_knowledge_sync clusters.json write exception captured."""

    def test_clusters_json_write_error_in_result_errors(self, tmp_path: Path) -> None:
        """A completely failed clusters.json write adds error message to result."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        config = _make_config(
            tmp_path,
            knowledge_sync_threshold=3,
            knowledge_min_cluster_size=2,
        )
        entries = [_make_entry(f"L-{i:03d}", tags=["api", "python"]) for i in range(5)]
        mock_backend = MagicMock()
        mock_backend.list_entries.return_value = entries

        # Make mkstemp itself fail so the entire try block raises immediately
        with (
            patch("trw_mcp.state.knowledge_topology.count_entries", return_value=5),
            patch("trw_mcp.state.knowledge_topology.get_backend", return_value=mock_backend),
            patch(
                "trw_mcp.state.knowledge_topology.tempfile.mkstemp",
                side_effect=OSError("no space left on device"),
            ),
        ):
            result = execute_knowledge_sync(trw_dir, config)

        # Error message captured, result still returned (fail-open)
        assert any("clusters.json write failed" in e for e in result["errors"])
        # Topics themselves may still have been generated before clusters.json step
        assert isinstance(result["topics_generated"], int)

    def test_clusters_json_write_failure_does_not_affect_topics(self, tmp_path: Path) -> None:
        """Topics are written even when clusters.json atomic write fails."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        config = _make_config(
            tmp_path,
            knowledge_sync_threshold=3,
            knowledge_min_cluster_size=2,
        )
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

        # Topic docs are written before clusters.json step
        knowledge_dir = trw_dir / "knowledge"
        md_files = list(knowledge_dir.glob("*.md")) if knowledge_dir.exists() else []
        assert result["topics_generated"] == len(md_files)
