"""Focused edge-case tests for knowledge_topology rendering and file output."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from tests._knowledge_topology_edge_support import _entry
from trw_mcp.state.knowledge_topology import (
    _render_cluster_documents,
    _write_knowledge_files,
    preserve_manual_markers,
    render_topic_document,
    sanitize_slug,
)
from trw_mcp.state.persistence import FileStateWriter


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
        good_entries = [_entry("L-001", tags=["a"], content="OK")]
        good_cluster: dict[str, object] = {
            "slug": "good",
            "tags": ["a"],
            "entry_ids": ["L-001"],
            "entries": good_entries,
            "avg_importance": 0.5,
        }
        bad_cluster: dict[str, object] = {
            "slug": "bad",
            "tags": ["b"],
            "entry_ids": ["L-002"],
            "entries": "not-a-list",
            "avg_importance": 0.5,
        }
        docs, errors = _render_cluster_documents([bad_cluster, good_cluster], {})
        assert len(docs) == 1
        assert docs[0]["slug"] == "good"
        assert len(errors) == 1
        assert "bad" in errors[0].lower()

    def test_empty_clusters_returns_empty(self) -> None:
        docs, errors = _render_cluster_documents([], {})
        assert docs == []
        assert errors == []


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


class TestPreserveManualMarkersEdge:
    """Additional edge cases for manual marker preservation."""

    def test_only_end_marker_returns_new_content(self) -> None:
        existing = "Some text\n<!-- trw:manual-end -->\nMore text"
        new = "Fresh content"
        result = preserve_manual_markers(existing, new)
        assert result == new

    def test_empty_manual_block(self) -> None:
        existing = "Before\n<!-- trw:manual-start --><!-- trw:manual-end -->\nAfter"
        new = "New"
        result = preserve_manual_markers(existing, new)
        assert "<!-- trw:manual-start --><!-- trw:manual-end -->" in result
        assert "New" in result

    def test_new_content_trailing_newlines_stripped(self) -> None:
        existing = "Old\n<!-- trw:manual-start -->Notes<!-- trw:manual-end -->"
        new = "New content\n\n\n"
        result = preserve_manual_markers(existing, new)
        assert result.startswith("New content\n\n")
        assert "Notes" in result

    def test_both_empty_strings(self) -> None:
        result = preserve_manual_markers("", "")
        assert result == ""


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
        entry_line_idx = None
        for i, line in enumerate(lines):
            if "No tags entry" in line:
                entry_line_idx = i
                break
        assert entry_line_idx is not None
        remaining = [l for l in lines[entry_line_idx + 1 :] if l.strip()]
        if remaining:
            assert not remaining[0].strip().startswith("- Tags:")


class TestSanitizeSlugEdge:
    """Additional edge cases for slug sanitization."""

    def test_all_special_chars_returns_empty(self) -> None:
        assert sanitize_slug("@#$%^&*()!") == ""

    def test_leading_hyphens_preserved(self) -> None:
        result = sanitize_slug("---leading")
        assert result == "---leading"

    def test_unicode_combining_accent_stripped(self) -> None:
        result = sanitize_slug("cafe\u0301")
        assert result == "cafe"
