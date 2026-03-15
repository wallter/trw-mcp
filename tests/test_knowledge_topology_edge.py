"""Focused edge-case tests for knowledge_topology algorithms.

Covers uncovered branches and edge cases in:
- _jaccard: empty sets, disjoint sets, subset/superset, identical sets
- _assign_entries_to_clusters: threshold boundary, single-entry behavior
- _merge_small_clusters: cascading merges, all-undersized drop, no merge needed
- _render_cluster_documents: error isolation per cluster
- _write_knowledge_files: existing file with markers, write error resilience
- _base_result: field completeness
- build_cooccurrence_matrix: duplicate tags within single entry
- form_jaccard_clusters: zero threshold, min_size=1, all entries no-tag
- preserve_manual_markers: end marker only, empty manual block
- render_topic_document: empty entries list, missing slug key
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

from trw_memory.models.memory import MemoryEntry, MemoryStatus

from trw_mcp.models.config import TRWConfig
from trw_mcp.state.knowledge_topology import (
    _assign_entries_to_clusters,
    _base_result,
    _jaccard,
    _merge_small_clusters,
    _render_cluster_documents,
    _write_knowledge_files,
    build_cooccurrence_matrix,
    form_jaccard_clusters,
    preserve_manual_markers,
    render_topic_document,
    sanitize_slug,
)
from trw_mcp.state.persistence import FileStateWriter

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _entry(
    entry_id: str = "L-001",
    content: str = "Summary",
    detail: str = "",
    tags: list[str] | None = None,
    importance: float = 0.5,
    evidence: list[str] | None = None,
) -> MemoryEntry:
    now = datetime.now(timezone.utc)
    return MemoryEntry(
        id=entry_id,
        content=content,
        detail=detail,
        tags=tags or [],
        evidence=evidence or [],
        importance=importance,
        status=MemoryStatus.ACTIVE,
        namespace="default",
        created_at=now,
        updated_at=now,
        merged_from=[],
        consolidated_from=[],
        metadata={},
    )


def _make_config(tmp_path: Path, **overrides: object) -> TRWConfig:
    kwargs: dict[str, object] = {
        "trw_dir": str(tmp_path / ".trw"),
        "knowledge_sync_threshold": 5,
        "knowledge_jaccard_threshold": 0.3,
        "knowledge_min_cluster_size": 2,
        "knowledge_output_dir": "knowledge",
    }
    kwargs.update(overrides)
    return TRWConfig(**kwargs)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# _jaccard edge cases
# ---------------------------------------------------------------------------


class TestJaccard:
    """Direct tests for the Jaccard similarity helper."""

    def test_both_empty_returns_zero(self) -> None:
        assert _jaccard(set(), set()) == 0.0

    def test_one_empty_returns_zero(self) -> None:
        assert _jaccard({"a", "b"}, set()) == 0.0
        assert _jaccard(set(), {"a", "b"}) == 0.0

    def test_identical_sets_returns_one(self) -> None:
        s = {"a", "b", "c"}
        assert _jaccard(s, s) == 1.0

    def test_disjoint_sets_returns_zero(self) -> None:
        assert _jaccard({"a", "b"}, {"c", "d"}) == 0.0

    def test_subset_returns_correct_ratio(self) -> None:
        # intersection=2, union=3 => 2/3
        result = _jaccard({"a", "b"}, {"a", "b", "c"})
        assert abs(result - 2.0 / 3.0) < 1e-9

    def test_partial_overlap(self) -> None:
        # {a,b,c} & {b,c,d} => intersection=2, union=4 => 0.5
        result = _jaccard({"a", "b", "c"}, {"b", "c", "d"})
        assert result == 0.5

    def test_single_element_match(self) -> None:
        assert _jaccard({"x"}, {"x"}) == 1.0

    def test_single_element_no_match(self) -> None:
        assert _jaccard({"x"}, {"y"}) == 0.0


# ---------------------------------------------------------------------------
# _assign_entries_to_clusters edge cases
# ---------------------------------------------------------------------------


class TestAssignEntriesToClusters:
    """Direct tests for the greedy cluster assignment step."""

    def test_single_entry_creates_one_cluster(self) -> None:
        entries = [_entry("L-001", tags=["a", "b"])]
        clusters = _assign_entries_to_clusters(entries, similarity_threshold=0.3)
        assert len(clusters) == 1
        assert len(clusters[0]["entry_list"]) == 1

    def test_all_no_tags_returns_empty(self) -> None:
        entries = [_entry(f"L-{i:03d}", tags=[]) for i in range(5)]
        clusters = _assign_entries_to_clusters(entries, similarity_threshold=0.3)
        assert clusters == []

    def test_threshold_zero_disjoint_still_separate(self) -> None:
        """With threshold=0.0 but completely disjoint tags, entries still form
        separate clusters because the best_sim comparison is strict (>), so
        best_idx stays at -1 when all similarities equal the initial best_sim=0.0."""
        entries = [
            _entry("L-001", tags=["a"]),
            _entry("L-002", tags=["b"]),
            _entry("L-003", tags=["c"]),
        ]
        clusters = _assign_entries_to_clusters(entries, similarity_threshold=0.0)
        # Each entry seeds its own cluster (disjoint sets have Jaccard=0.0,
        # and the strict > comparison means no match is found)
        assert len(clusters) == 3

    def test_threshold_zero_with_overlap_merges(self) -> None:
        """With threshold=0.0 and overlapping tags, entries merge into one cluster."""
        entries = [
            _entry("L-001", tags=["a", "b"]),
            _entry("L-002", tags=["a", "c"]),  # Jaccard({a,b},{a,c})=1/3 > 0.0
            _entry("L-003", tags=["a", "d"]),  # overlaps with widened cluster
        ]
        clusters = _assign_entries_to_clusters(entries, similarity_threshold=0.0)
        assert len(clusters) == 1
        assert len(clusters[0]["entry_list"]) == 3

    def test_threshold_one_requires_exact_match(self) -> None:
        entries = [
            _entry("L-001", tags=["a", "b"]),
            _entry("L-002", tags=["a", "b"]),  # exact match -> same cluster
            _entry("L-003", tags=["a", "c"]),  # Jaccard({a,b},{a,b,c})=2/3 < 1.0 -> new cluster
        ]
        clusters = _assign_entries_to_clusters(entries, similarity_threshold=1.0)
        # First two should cluster together; third creates new cluster
        assert len(clusters) == 2

    def test_cluster_tag_set_widens_on_add(self) -> None:
        """When an entry is added to a cluster, the cluster's tag set grows."""
        entries = [
            _entry("L-001", tags=["a", "b"]),
            _entry("L-002", tags=["a", "c"]),  # Jaccard({a,b},{a,c})=1/3, threshold=0.3 -> joins
        ]
        clusters = _assign_entries_to_clusters(entries, similarity_threshold=0.3)
        assert len(clusters) == 1
        assert clusters[0]["tag_set"] == {"a", "b", "c"}

    def test_mixed_tagged_and_untagged(self) -> None:
        entries = [
            _entry("L-001", tags=["a"]),
            _entry("L-002", tags=[]),
            _entry("L-003", tags=["a"]),
        ]
        clusters = _assign_entries_to_clusters(entries, similarity_threshold=0.3)
        total = sum(len(c["entry_list"]) for c in clusters)
        assert total == 2  # only tagged entries


# ---------------------------------------------------------------------------
# _merge_small_clusters edge cases
# ---------------------------------------------------------------------------


class TestMergeSmallClusters:
    """Direct tests for the undersized-cluster merge/drop pass."""

    def _make_cluster(self, tags: set[str], count: int) -> dict[str, Any]:
        return {
            "tag_set": tags,
            "entry_list": [_entry(f"L-{i:03d}", tags=list(tags)) for i in range(count)],
        }

    def test_all_meet_min_size_unchanged(self) -> None:
        clusters = [
            self._make_cluster({"a"}, 5),
            self._make_cluster({"b"}, 5),
        ]
        result = _merge_small_clusters(clusters, min_size=3)
        assert len(result) == 2

    def test_undersized_merged_into_closest(self) -> None:
        clusters = [
            self._make_cluster({"a", "b"}, 5),
            self._make_cluster({"a", "c"}, 1),  # undersized, shares "a" with first
        ]
        result = _merge_small_clusters(clusters, min_size=3)
        assert len(result) == 1
        assert len(result[0]["entry_list"]) == 6

    def test_all_undersized_dropped(self) -> None:
        """If all clusters are under min_size and there's only one, it gets dropped."""
        clusters = [self._make_cluster({"a"}, 1)]
        result = _merge_small_clusters(clusters, min_size=5)
        assert result == []

    def test_two_undersized_merge_then_survive(self) -> None:
        """Two undersized clusters that together meet min_size should survive."""
        clusters = [
            self._make_cluster({"a", "b"}, 2),
            self._make_cluster({"a", "c"}, 2),
        ]
        result = _merge_small_clusters(clusters, min_size=3)
        # One merges into the other -> 4 entries >= 3
        assert len(result) == 1
        assert len(result[0]["entry_list"]) == 4

    def test_cascading_merge(self) -> None:
        """Multiple undersized clusters merge in sequence."""
        clusters = [
            self._make_cluster({"x"}, 10),  # big anchor
            self._make_cluster({"x", "y"}, 1),  # small, merges into anchor
            self._make_cluster({"x", "z"}, 1),  # small, merges into anchor
        ]
        result = _merge_small_clusters(clusters, min_size=3)
        assert len(result) == 1
        assert len(result[0]["entry_list"]) == 12

    def test_min_size_one_keeps_everything(self) -> None:
        clusters = [
            self._make_cluster({"a"}, 1),
            self._make_cluster({"b"}, 1),
        ]
        result = _merge_small_clusters(clusters, min_size=1)
        assert len(result) == 2

    def test_empty_clusters_list(self) -> None:
        result = _merge_small_clusters([], min_size=3)
        assert result == []


# ---------------------------------------------------------------------------
# _base_result
# ---------------------------------------------------------------------------


class TestBaseResult:
    """Verify _base_result returns all expected keys."""

    def test_all_keys_present(self, tmp_path: Path) -> None:
        config = _make_config(tmp_path)
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir(exist_ok=True)
        result = _base_result(42, config, trw_dir, threshold_met=True, dry_run=False)
        assert result["threshold_met"] is True
        assert result["entry_count"] == 42
        assert result["threshold"] == config.knowledge_sync_threshold
        assert result["topics_generated"] == 0
        assert result["entries_clustered"] == 0
        assert result["dry_run"] is False
        assert result["errors"] == []
        assert "output_dir" in result

    def test_dry_run_flag_propagated(self, tmp_path: Path) -> None:
        config = _make_config(tmp_path)
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir(exist_ok=True)
        result = _base_result(0, config, trw_dir, threshold_met=False, dry_run=True)
        assert result["dry_run"] is True
        assert result["threshold_met"] is False


# ---------------------------------------------------------------------------
# build_cooccurrence_matrix additional edge cases
# ---------------------------------------------------------------------------


class TestCooccurrenceEdgeCases:
    """Edge cases not covered in the main test file."""

    def test_duplicate_tags_within_entry_deduplicated(self) -> None:
        """If an entry has duplicate tags, they should be treated as a set."""
        entries = [
            _entry("L-001", tags=["a", "a", "b"]),
            _entry("L-002", tags=["a", "b"]),
        ]
        matrix = build_cooccurrence_matrix(entries)
        # "a" appears in 2 entries, "b" in 2 entries => pair ("a","b") count=2
        assert ("a", "b") in matrix
        assert matrix[("a", "b")] == 2

    def test_many_tags_produces_all_pairs(self) -> None:
        """4 tags in 2 entries should produce C(4,2) = 6 pairs."""
        entries = [
            _entry("L-001", tags=["a", "b", "c", "d"]),
            _entry("L-002", tags=["a", "b", "c", "d"]),
        ]
        matrix = build_cooccurrence_matrix(entries)
        assert len(matrix) == 6  # C(4,2)

    def test_one_entry_never_meets_freq_threshold(self) -> None:
        """A single entry can never have tags with freq >= 2."""
        entries = [_entry("L-001", tags=["a", "b", "c"])]
        matrix = build_cooccurrence_matrix(entries)
        assert matrix == {}


# ---------------------------------------------------------------------------
# form_jaccard_clusters additional edge cases
# ---------------------------------------------------------------------------


class TestFormJaccardClustersEdge:
    """Additional edge cases for the full clustering pipeline."""

    def test_min_size_one_keeps_singletons(self) -> None:
        entries = [
            _entry("L-001", tags=["a"]),
            _entry("L-002", tags=["b"]),
            _entry("L-003", tags=["c"]),
        ]
        clusters = form_jaccard_clusters(entries, threshold=1.0, min_size=1)
        assert len(clusters) == 3

    def test_all_no_tags_returns_empty(self) -> None:
        entries = [_entry(f"L-{i:03d}", tags=[]) for i in range(10)]
        clusters = form_jaccard_clusters(entries, threshold=0.3, min_size=2)
        assert clusters == []

    def test_avg_importance_is_mean_of_entries(self) -> None:
        entries = [
            _entry("L-001", tags=["x"], importance=0.2),
            _entry("L-002", tags=["x"], importance=0.8),
        ]
        clusters = form_jaccard_clusters(entries, threshold=0.3, min_size=1)
        assert len(clusters) == 1
        # avg = (0.2 + 0.8) / 2 = 0.5
        assert clusters[0]["avg_importance"] == 0.5

    def test_tags_in_output_are_sorted(self) -> None:
        entries = [
            _entry("L-001", tags=["z", "a", "m"]),
            _entry("L-002", tags=["z", "a", "m"]),
        ]
        clusters = form_jaccard_clusters(entries, threshold=0.3, min_size=1)
        assert list(clusters[0]["tags"]) == ["a", "m", "z"]  # type: ignore[arg-type]

    def test_entry_ids_match_input_ids(self) -> None:
        entries = [
            _entry("L-aaa", tags=["x"]),
            _entry("L-bbb", tags=["x"]),
        ]
        clusters = form_jaccard_clusters(entries, threshold=0.3, min_size=1)
        ids = set(clusters[0]["entry_ids"])  # type: ignore[arg-type]
        assert ids == {"L-aaa", "L-bbb"}

    def test_large_min_size_drops_everything(self) -> None:
        entries = [_entry(f"L-{i:03d}", tags=["x"]) for i in range(5)]
        clusters = form_jaccard_clusters(entries, threshold=0.3, min_size=100)
        assert clusters == []

    def test_slug_uses_sanitized_most_common_tag(self) -> None:
        entries = [
            _entry("L-001", tags=["My Tag!", "other"]),
            _entry("L-002", tags=["My Tag!", "other"]),
            _entry("L-003", tags=["My Tag!"]),
        ]
        clusters = form_jaccard_clusters(entries, threshold=0.1, min_size=1)
        # "My Tag!" sanitized -> "my-tag"
        assert clusters[0]["slug"] == "my-tag"


# ---------------------------------------------------------------------------
# _render_cluster_documents
# ---------------------------------------------------------------------------


class TestRenderClusterDocuments:
    """Direct tests for the batch render helper."""

    def test_successful_render(self) -> None:
        entries = [_entry("L-001", tags=["a"], content="Hello")]
        cluster: dict[str, object] = {
            "slug": "test-topic",
            "tags": ["a"],
            "entry_ids": ["L-001"],
            "entries": entries,
            "avg_importance": 0.5,
        }
        docs, errors = _render_cluster_documents([cluster], {})
        assert len(docs) == 1
        assert docs[0]["slug"] == "test-topic"
        assert "Hello" in docs[0]["content"]
        assert errors == []

    def test_render_error_collected_not_raised(self) -> None:
        """A cluster that raises during render should produce an error string,
        not abort the entire batch."""
        good_entries = [_entry("L-001", tags=["a"], content="OK")]
        good_cluster: dict[str, object] = {
            "slug": "good",
            "tags": ["a"],
            "entry_ids": ["L-001"],
            "entries": good_entries,
            "avg_importance": 0.5,
        }
        # Bad cluster: entries is not a list of MemoryEntry, will blow up in render
        bad_cluster: dict[str, object] = {
            "slug": "bad",
            "tags": ["b"],
            "entry_ids": ["L-002"],
            "entries": "not-a-list",  # type: ignore[dict-item]
            "avg_importance": 0.5,
        }
        docs, errors = _render_cluster_documents([bad_cluster, good_cluster], {})
        # Good cluster still renders
        assert len(docs) == 1
        assert docs[0]["slug"] == "good"
        # Bad cluster error captured
        assert len(errors) == 1
        assert "bad" in errors[0].lower()

    def test_empty_clusters_returns_empty(self) -> None:
        docs, errors = _render_cluster_documents([], {})
        assert docs == []
        assert errors == []


# ---------------------------------------------------------------------------
# _write_knowledge_files
# ---------------------------------------------------------------------------


class TestWriteKnowledgeFiles:
    """Direct tests for the file-writing helper."""

    def test_writes_markdown_files(self, tmp_path: Path) -> None:
        knowledge_dir = tmp_path / "knowledge"
        knowledge_dir.mkdir()
        writer = FileStateWriter()
        docs = [{"slug": "topic-a", "content": "# topic-a\nContent here"}]
        slugs, count, _, errors = _write_knowledge_files(docs, knowledge_dir, writer)
        assert slugs == ["topic-a"]
        assert count == 1
        assert errors == []
        assert (knowledge_dir / "topic-a.md").read_text(encoding="utf-8") == "# topic-a\nContent here"

    def test_preserves_manual_markers_in_existing_file(self, tmp_path: Path) -> None:
        knowledge_dir = tmp_path / "knowledge"
        knowledge_dir.mkdir()
        # Pre-seed file with manual markers
        existing = (
            "<!-- trw:auto-generated -->\n# old\n\n<!-- trw:manual-start -->MY CUSTOM NOTES<!-- trw:manual-end -->\n"
        )
        (knowledge_dir / "topic-a.md").write_text(existing, encoding="utf-8")

        writer = FileStateWriter()
        docs = [{"slug": "topic-a", "content": "<!-- trw:auto-generated -->\n# new content"}]
        _write_knowledge_files(docs, knowledge_dir, writer)

        result = (knowledge_dir / "topic-a.md").read_text(encoding="utf-8")
        assert "MY CUSTOM NOTES" in result
        assert "# new content" in result

    def test_write_error_collected_not_raised(self, tmp_path: Path) -> None:
        knowledge_dir = tmp_path / "knowledge"
        knowledge_dir.mkdir()
        # Make writer that raises on write
        writer = MagicMock(spec=FileStateWriter)
        writer.write_text.side_effect = OSError("disk full")

        docs = [{"slug": "failing", "content": "content"}]
        slugs, count, _, errors = _write_knowledge_files(docs, knowledge_dir, writer)
        assert slugs == []
        assert count == 0
        assert len(errors) == 1
        assert "failing" in errors[0].lower()

    def test_multiple_documents_written(self, tmp_path: Path) -> None:
        knowledge_dir = tmp_path / "knowledge"
        knowledge_dir.mkdir()
        writer = FileStateWriter()
        docs = [
            {"slug": "alpha", "content": "Alpha content"},
            {"slug": "beta", "content": "Beta content"},
        ]
        slugs, count, _, errors = _write_knowledge_files(docs, knowledge_dir, writer)
        assert set(slugs) == {"alpha", "beta"}
        assert count == 2
        assert errors == []


# ---------------------------------------------------------------------------
# preserve_manual_markers additional edge cases
# ---------------------------------------------------------------------------


class TestPreserveManualMarkersEdge:
    """Additional edge cases for manual marker preservation."""

    def test_only_end_marker_returns_new_content(self) -> None:
        """End marker without start marker should return new content unchanged."""
        existing = "Some text\n<!-- trw:manual-end -->\nMore text"
        new = "Fresh content"
        result = preserve_manual_markers(existing, new)
        assert result == new

    def test_empty_manual_block(self) -> None:
        """Start and end markers with nothing between them."""
        existing = "Before\n<!-- trw:manual-start --><!-- trw:manual-end -->\nAfter"
        new = "New"
        result = preserve_manual_markers(existing, new)
        assert "<!-- trw:manual-start --><!-- trw:manual-end -->" in result
        assert "New" in result

    def test_new_content_trailing_newlines_stripped(self) -> None:
        existing = "Old\n<!-- trw:manual-start -->Notes<!-- trw:manual-end -->"
        new = "New content\n\n\n"
        result = preserve_manual_markers(existing, new)
        # New content trailing newlines are stripped, then manual block appended
        assert result.startswith("New content\n\n")
        assert "Notes" in result

    def test_both_empty_strings(self) -> None:
        result = preserve_manual_markers("", "")
        assert result == ""


# ---------------------------------------------------------------------------
# render_topic_document additional edge cases
# ---------------------------------------------------------------------------


class TestRenderTopicDocumentEdge:
    """Additional edge cases for topic document rendering."""

    def test_empty_entries_list(self) -> None:
        cluster: dict[str, object] = {
            "slug": "empty-topic",
            "tags": ["a"],
            "entry_ids": [],
            "entries": [],
            "avg_importance": 0.0,
        }
        rendered = render_topic_document(cluster)
        assert "# empty-topic" in rendered
        assert "**Entries**: 0" in rendered

    def test_missing_slug_defaults_to_topic(self) -> None:
        cluster: dict[str, object] = {
            "tags": ["a"],
            "entry_ids": [],
            "entries": [],
            "avg_importance": 0.0,
        }
        rendered = render_topic_document(cluster)
        assert "# topic" in rendered

    def test_detail_at_501_chars_is_truncated(self) -> None:
        detail_501 = "d" * 501
        entries = [_entry("L-001", detail=detail_501, tags=["a"])]
        cluster: dict[str, object] = {
            "slug": "topic",
            "tags": ["a"],
            "entry_ids": ["L-001"],
            "entries": entries,
            "avg_importance": 0.5,
        }
        rendered = render_topic_document(cluster)
        assert "..." in rendered
        # Should contain exactly 500 chars of detail + "..."
        assert "d" * 500 + "..." in rendered

    def test_entry_with_all_fields_populated(self) -> None:
        entries = [
            _entry(
                "L-001",
                content="Full entry",
                detail="Some detail",
                tags=["tag1", "tag2"],
                importance=0.9,
                evidence=["file1.py", "file2.py"],
            )
        ]
        cluster: dict[str, object] = {
            "slug": "full",
            "tags": ["tag1", "tag2"],
            "entry_ids": ["L-001"],
            "entries": entries,
            "avg_importance": 0.9,
        }
        rendered = render_topic_document(cluster)
        assert "Full entry" in rendered
        assert "Some detail" in rendered
        assert "file1.py" in rendered
        assert "file2.py" in rendered
        assert "tag1" in rendered
        assert "tag2" in rendered

    def test_no_tags_on_entry_omits_tags_line(self) -> None:
        entries = [_entry("L-001", tags=[], content="No tags entry")]
        cluster: dict[str, object] = {
            "slug": "topic",
            "tags": [],
            "entry_ids": ["L-001"],
            "entries": entries,
            "avg_importance": 0.5,
        }
        rendered = render_topic_document(cluster)
        lines = rendered.split("\n")
        # Find the entry line and check there's no "Tags:" line after it
        entry_line_idx = None
        for i, line in enumerate(lines):
            if "No tags entry" in line:
                entry_line_idx = i
                break
        assert entry_line_idx is not None
        # The next non-empty line should NOT be a Tags line for this entry
        remaining = [l for l in lines[entry_line_idx + 1 :] if l.strip()]
        if remaining:
            assert not remaining[0].strip().startswith("- Tags:")


# ---------------------------------------------------------------------------
# sanitize_slug additional edge cases
# ---------------------------------------------------------------------------


class TestSanitizeSlugEdge:
    """Additional edge cases for slug sanitization."""

    def test_all_special_chars_returns_empty(self) -> None:
        assert sanitize_slug("@#$%^&*()!") == ""

    def test_leading_hyphens_preserved(self) -> None:
        # hyphens are valid chars in the slug
        result = sanitize_slug("---leading")
        assert result == "---leading"

    def test_unicode_combining_accent_stripped(self) -> None:
        # \u0301 is a combining acute accent — it's stripped, but the base 'e' stays
        result = sanitize_slug("cafe\u0301")
        assert result == "cafe"  # combining char stripped, base letters preserved
