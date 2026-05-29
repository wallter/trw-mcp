"""Knowledge topology rendering and marker preservation tests."""

from __future__ import annotations

from datetime import datetime, timezone

from trw_memory.models.memory import MemoryEntry, MemoryStatus

from tests._knowledge_topology_support import _make_entry
from trw_mcp.state.knowledge_topology import preserve_manual_markers, render_topic_document


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
            "entry_ids": [entry.id for entry in mem_entries],
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
        idx_one = rendered.index("Summary one")
        idx_three = rendered.index("Summary three")
        idx_two = rendered.index("Summary two")
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
        assert "3" in rendered

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
        assert "..." not in rendered


class TestPreserveManualMarkers:
    """FR05: Manual marker preservation in topic documents."""

    def test_no_markers_returns_new_content(self) -> None:
        existing = "Old auto-generated content"
        new = "New auto-generated content"
        result = preserve_manual_markers(existing, new)
        assert result == new

    def test_paired_markers_preserved(self) -> None:
        existing = (
            "<!-- trw:auto-generated -->\nOld content\n<!-- trw:manual-start -->Custom notes<!-- trw:manual-end -->"
        )
        new = "New auto-generated content"
        result = preserve_manual_markers(existing, new)
        assert "Custom notes" in result
        assert "New auto-generated content" in result
        assert "<!-- trw:manual-start -->" in result
        assert "<!-- trw:manual-end -->" in result

    def test_unpaired_opening_preserves_to_eof(self) -> None:
        existing = "Auto section\n<!-- trw:manual-start -->Notes that continue to EOF\nMore notes"
        new = "Fresh content"
        result = preserve_manual_markers(existing, new)
        assert "Notes that continue to EOF" in result
        assert "More notes" in result
        assert "<!-- trw:manual-start -->" in result
        assert "<!-- trw:manual-end -->" not in result

    def test_crlf_handling(self) -> None:
        existing = "Auto\r\n<!-- trw:manual-start -->\r\nManual content\r\n<!-- trw:manual-end -->"
        new = "New content"
        result = preserve_manual_markers(existing, new)
        assert "Manual content" in result

    def test_empty_existing_returns_new_content(self) -> None:
        result = preserve_manual_markers("", "New content")
        assert result == "New content"

    def test_manual_block_appended_after_new_content(self) -> None:
        existing = "Old\n<!-- trw:manual-start -->My notes<!-- trw:manual-end -->"
        new = "New generated"
        result = preserve_manual_markers(existing, new)
        new_idx = result.index("New generated")
        manual_idx = result.index("My notes")
        assert new_idx < manual_idx

    def test_new_content_returned_unchanged_when_no_marker(self) -> None:
        existing = "Some old auto content without any markers"
        new = "Brand new content here"
        result = preserve_manual_markers(existing, new)
        assert result == new

    def test_multiple_markers_uses_first(self) -> None:
        existing = (
            "<!-- trw:manual-start -->First block<!-- trw:manual-end -->\n"
            "<!-- trw:manual-start -->Second block<!-- trw:manual-end -->"
        )
        new = "Fresh"
        result = preserve_manual_markers(existing, new)
        assert "First block" in result
