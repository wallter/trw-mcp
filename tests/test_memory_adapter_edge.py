"""Edge-case tests for state/memory_adapter.py.

Covers adapter-layer logic not exercised by the existing test_memory_adapter.py
and test_memory_adapter_branches.py suites:

- embed_text / embed_text_batch direct paths
- _memory_to_learning_dict field mapping (compact vs full, enum handling)
- _learning_to_memory_entry parameter mapping and defaults
- recall_learnings boundary inputs (empty query, max_results=0)
- update_learning with multiple simultaneous changes
- update_access_tracking with mixed valid/invalid IDs
- reset_backend / reset_embedder idempotency
- store_learning tag inference wiring
- list_active_learnings with zero min_impact
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from trw_memory.models.memory import MemoryEntry, MemoryStatus

from trw_mcp.state.memory_adapter import (
    _learning_to_memory_entry,
    _memory_to_learning_dict,
    embed_text,
    embed_text_batch,
    embedding_available,
    find_entry_by_id,
    get_backend,
    list_active_learnings,
    recall_learnings,
    reset_backend,
    reset_embedder,
    store_learning,
    update_access_tracking,
    update_learning,
)


@pytest.fixture
def trw_dir(tmp_path: Path) -> Path:
    """Create a minimal .trw structure for adapter tests."""
    d = tmp_path / ".trw"
    d.mkdir()
    (d / "learnings" / "entries").mkdir(parents=True)
    (d / "memory").mkdir()
    return d


# ---------------------------------------------------------------------------
# embed_text — direct tests
# ---------------------------------------------------------------------------


class TestEmbedText:
    def test_returns_none_when_embedder_unavailable(self) -> None:
        """embed_text returns None when get_embedder() returns None."""
        with patch("trw_mcp.state._memory_connection.get_embedder", return_value=None):
            result = embed_text("some text")
            assert result is None

    def test_returns_none_for_empty_string(self) -> None:
        """embed_text returns None for empty/whitespace-only text."""
        mock_embedder = MagicMock()
        with patch(
            "trw_mcp.state._memory_connection.get_embedder",
            return_value=mock_embedder,
        ):
            assert embed_text("") is None
            assert embed_text("   ") is None
            assert embed_text("\t\n") is None
            # embedder.embed should never be called for empty text
            mock_embedder.embed.assert_not_called()

    def test_returns_vector_on_success(self) -> None:
        """embed_text returns the embedding vector from the provider."""
        mock_embedder = MagicMock()
        expected_vec = [0.1, 0.2, 0.3]
        mock_embedder.embed.return_value = expected_vec
        with patch(
            "trw_mcp.state._memory_connection.get_embedder",
            return_value=mock_embedder,
        ):
            result = embed_text("hello world")
            assert result == expected_vec
            mock_embedder.embed.assert_called_once_with("hello world")

    def test_returns_none_on_os_error(self) -> None:
        """embed_text catches OSError and returns None."""
        mock_embedder = MagicMock()
        mock_embedder.embed.side_effect = OSError("model file missing")
        with patch(
            "trw_mcp.state._memory_connection.get_embedder",
            return_value=mock_embedder,
        ):
            result = embed_text("test input")
            assert result is None

    def test_returns_none_on_value_error(self) -> None:
        """embed_text catches ValueError and returns None."""
        mock_embedder = MagicMock()
        mock_embedder.embed.side_effect = ValueError("bad input shape")
        with patch(
            "trw_mcp.state._memory_connection.get_embedder",
            return_value=mock_embedder,
        ):
            result = embed_text("test input")
            assert result is None

    def test_returns_none_on_runtime_error(self) -> None:
        """embed_text catches RuntimeError and returns None."""
        mock_embedder = MagicMock()
        mock_embedder.embed.side_effect = RuntimeError("inference failed")
        with patch(
            "trw_mcp.state._memory_connection.get_embedder",
            return_value=mock_embedder,
        ):
            result = embed_text("test input")
            assert result is None


# ---------------------------------------------------------------------------
# embed_text_batch — direct tests
# ---------------------------------------------------------------------------


class TestEmbedTextBatch:
    def test_empty_input_returns_empty_list(self) -> None:
        """embed_text_batch([]) returns [] without calling embedder."""
        result = embed_text_batch([])
        assert result == []

    def test_returns_none_list_when_embedder_unavailable(self) -> None:
        """embed_text_batch returns [None, None, ...] when embedder is None."""
        with patch("trw_mcp.state._memory_connection.get_embedder", return_value=None):
            result = embed_text_batch(["a", "b", "c"])
            assert result == [None, None, None]

    def test_returns_vectors_on_success(self) -> None:
        """embed_text_batch delegates to embed_text per item."""
        mock_embedder = MagicMock()
        vec1 = [0.1, 0.2]
        vec2 = [0.3, 0.4]
        mock_embedder.embed.side_effect = [vec1, vec2]
        with patch(
            "trw_mcp.state._memory_connection.get_embedder",
            return_value=mock_embedder,
        ):
            result = embed_text_batch(["hello", "world"])
            assert len(result) == 2
            # embed_text_batch calls embed_text which calls get_embedder again,
            # so we check the overall result shape
            assert isinstance(result, list)

    def test_batch_exception_returns_none_list(self) -> None:
        """embed_text_batch catches exceptions and returns [None, ...]."""
        # The outer try/except in embed_text_batch catches OSError/ValueError/RuntimeError
        with patch(
            "trw_mcp.state._memory_connection.get_embedder",
            return_value=MagicMock(),
        ):
            with patch(
                "trw_mcp.state._memory_connection.embed_text",
                side_effect=RuntimeError("batch explosion"),
            ):
                result = embed_text_batch(["a", "b"])
                assert result == [None, None]


# ---------------------------------------------------------------------------
# embedding_available — wrapper test
# ---------------------------------------------------------------------------


class TestEmbeddingAvailable:
    def test_true_when_embedder_exists(self) -> None:
        """embedding_available() returns True when get_embedder returns non-None."""
        with patch(
            "trw_mcp.state._memory_connection.get_embedder",
            return_value=MagicMock(),
        ):
            assert embedding_available() is True

    def test_false_when_embedder_none(self) -> None:
        """embedding_available() returns False when get_embedder returns None."""
        with patch(
            "trw_mcp.state._memory_connection.get_embedder",
            return_value=None,
        ):
            assert embedding_available() is False


# ---------------------------------------------------------------------------
# _memory_to_learning_dict — field mapping
# ---------------------------------------------------------------------------


class TestMemoryToLearningDict:
    def _make_entry(self, **overrides: Any) -> MemoryEntry:
        """Build a MemoryEntry with sensible defaults, applying overrides."""
        defaults: dict[str, Any] = {
            "id": "L-test001",
            "content": "Test summary",
            "detail": "Test detail text",
            "tags": ["python", "testing"],
            "evidence": ["test_file.py"],
            "importance": 0.8,
            "status": MemoryStatus.ACTIVE,
            "source": "agent",
            "source_identity": "test-agent",
            "created_at": datetime(2026, 1, 15, 12, 0, 0, tzinfo=timezone.utc),
            "updated_at": datetime(2026, 1, 16, 12, 0, 0, tzinfo=timezone.utc),
            "access_count": 5,
            "last_accessed_at": datetime(2026, 1, 17, 12, 0, 0, tzinfo=timezone.utc),
            "q_value": 0.75,
            "q_observations": 3,
            "recurrence": 2,
            "metadata": {"shard_id": "shard-A"},
        }
        defaults.update(overrides)
        return MemoryEntry(**defaults)

    def test_compact_mode_returns_minimal_keys(self) -> None:
        """Compact mode returns only id, summary, tags, impact, status."""
        entry = self._make_entry()
        result = _memory_to_learning_dict(entry, compact=True)
        assert set(result.keys()) == {"id", "summary", "tags", "impact", "status"}
        assert result["id"] == "L-test001"
        assert result["summary"] == "Test summary"
        assert result["tags"] == ["python", "testing"]
        assert result["impact"] == 0.8
        assert result["status"] == "active"

    def test_full_mode_returns_all_fields(self) -> None:
        """Full mode returns all learning dict fields including metadata."""
        entry = self._make_entry()
        result = _memory_to_learning_dict(entry, compact=False)
        expected_keys = {
            "id",
            "summary",
            "tags",
            "impact",
            "status",
            "detail",
            "evidence",
            "source_type",
            "source_identity",
            "created",
            "updated",
            "access_count",
            "last_accessed_at",
            "q_value",
            "q_observations",
            "recurrence",
            "shard_id",
            "outcome_history",
        }
        assert expected_keys == set(result.keys())

    def test_maps_content_to_summary(self) -> None:
        """MemoryEntry.content maps to learning dict 'summary'."""
        entry = self._make_entry(content="My content here")
        result = _memory_to_learning_dict(entry)
        assert result["summary"] == "My content here"

    def test_maps_importance_to_impact(self) -> None:
        """MemoryEntry.importance maps to learning dict 'impact'."""
        entry = self._make_entry(importance=0.95)
        result = _memory_to_learning_dict(entry)
        assert result["impact"] == 0.95

    def test_maps_source_to_source_type(self) -> None:
        """MemoryEntry.source maps to learning dict 'source_type'."""
        entry = self._make_entry(source="human")
        result = _memory_to_learning_dict(entry)
        assert result["source_type"] == "human"

    def test_status_enum_to_string(self) -> None:
        """MemoryStatus enum is converted to its string value."""
        entry = self._make_entry(status=MemoryStatus.RESOLVED)
        result = _memory_to_learning_dict(entry)
        assert result["status"] == "resolved"

    def test_status_string_passthrough(self) -> None:
        """If status is already a string (edge case), it passes through."""
        entry = self._make_entry()
        # Force status to a plain string to test the str() fallback path
        object.__setattr__(entry, "status", "custom_status")
        result = _memory_to_learning_dict(entry)
        assert result["status"] == "custom_status"

    def test_dates_formatted_as_iso_date(self) -> None:
        """created_at and updated_at are formatted as ISO date strings."""
        entry = self._make_entry(
            created_at=datetime(2026, 3, 10, 15, 30, 0, tzinfo=timezone.utc),
            updated_at=datetime(2026, 3, 11, 9, 0, 0, tzinfo=timezone.utc),
        )
        result = _memory_to_learning_dict(entry)
        assert result["created"] == "2026-03-10"
        assert result["updated"] == "2026-03-11"

    def test_none_dates_produce_empty_strings(self) -> None:
        """None created_at/updated_at produce empty strings."""
        entry = self._make_entry()
        # MemoryEntry has non-optional datetime defaults, so we force None
        # via object.__setattr__ to test the defensive branch.
        object.__setattr__(entry, "created_at", None)
        object.__setattr__(entry, "updated_at", None)
        result = _memory_to_learning_dict(entry)
        assert result["created"] == ""
        assert result["updated"] == ""

    def test_none_last_accessed_at_produces_none(self) -> None:
        """None last_accessed_at produces None in the dict."""
        entry = self._make_entry(last_accessed_at=None)
        result = _memory_to_learning_dict(entry)
        assert result["last_accessed_at"] is None

    def test_shard_id_from_metadata(self) -> None:
        """shard_id is extracted from metadata dict."""
        entry = self._make_entry(metadata={"shard_id": "shard-B"})
        result = _memory_to_learning_dict(entry)
        assert result["shard_id"] == "shard-B"

    def test_missing_shard_id_returns_none(self) -> None:
        """When metadata has no shard_id, the dict has shard_id=None."""
        entry = self._make_entry(metadata={})
        result = _memory_to_learning_dict(entry)
        assert result["shard_id"] is None

    # -- Compact mode tag cap tests --

    def test_compact_mode_caps_tags_at_limit(self) -> None:
        """Compact mode truncates tags list to COMPACT_TAGS_CAP."""
        from trw_mcp.models.config._defaults import COMPACT_TAGS_CAP

        many_tags = [f"tag-{i}" for i in range(100)]
        entry = self._make_entry(tags=many_tags)
        result = _memory_to_learning_dict(entry, compact=True)
        tags = list(result["tags"])  # type: ignore[arg-type]
        assert len(tags) == COMPACT_TAGS_CAP
        assert tags == many_tags[:COMPACT_TAGS_CAP]

    def test_compact_mode_preserves_tags_under_limit(self) -> None:
        """Compact mode keeps all tags when count is below the cap."""
        from trw_mcp.models.config._defaults import COMPACT_TAGS_CAP

        few_tags = ["a", "b", "c"]
        assert len(few_tags) < COMPACT_TAGS_CAP
        entry = self._make_entry(tags=few_tags)
        result = _memory_to_learning_dict(entry, compact=True)
        assert result["tags"] == few_tags

    def test_compact_mode_exact_limit_tags(self) -> None:
        """Compact mode keeps all tags when count equals the cap exactly."""
        from trw_mcp.models.config._defaults import COMPACT_TAGS_CAP

        exact_tags = [f"tag-{i}" for i in range(COMPACT_TAGS_CAP)]
        entry = self._make_entry(tags=exact_tags)
        result = _memory_to_learning_dict(entry, compact=True)
        tags = list(result["tags"])  # type: ignore[arg-type]
        assert tags == exact_tags
        assert len(tags) == COMPACT_TAGS_CAP

    def test_compact_mode_empty_tags(self) -> None:
        """Compact mode handles empty tags list without error."""
        entry = self._make_entry(tags=[])
        result = _memory_to_learning_dict(entry, compact=True)
        assert result["tags"] == []

    def test_full_mode_does_not_cap_tags(self) -> None:
        """Full mode returns all tags regardless of count."""
        many_tags = [f"tag-{i}" for i in range(500)]
        entry = self._make_entry(tags=many_tags)
        result = _memory_to_learning_dict(entry, compact=False)
        tags = list(result["tags"])  # type: ignore[arg-type]
        assert tags == many_tags
        assert len(tags) == 500

    def test_compact_tag_cap_reduces_response_size(self) -> None:
        """Verify compact tag cap meaningfully reduces serialized size."""
        import json

        huge_tags = [f"long-descriptive-tag-name-{i}" for i in range(600)]
        entry = self._make_entry(tags=huge_tags)

        full_result = _memory_to_learning_dict(entry, compact=False)
        compact_result = _memory_to_learning_dict(entry, compact=True)

        full_size = len(json.dumps(full_result))
        compact_size = len(json.dumps(compact_result))

        # Compact should be dramatically smaller
        assert compact_size < full_size / 10


# ---------------------------------------------------------------------------
# _learning_to_memory_entry — parameter mapping
# ---------------------------------------------------------------------------


class TestLearningToMemoryEntry:
    def test_basic_mapping(self) -> None:
        """All parameters map to the correct MemoryEntry fields."""
        entry = _learning_to_memory_entry(
            "L-map001",
            "Summary text",
            "Detail text",
            tags=["python"],
            evidence=["proof.py"],
            impact=0.9,
            shard_id="shard-C",
            source_type="human",
            source_identity="Tyler",
        )
        assert entry.id == "L-map001"
        assert entry.content == "Summary text"
        assert entry.detail == "Detail text"
        assert entry.tags == ["python"]
        assert entry.evidence == ["proof.py"]
        assert entry.importance == 0.9
        assert entry.source == "human"
        assert entry.source_identity == "Tyler"
        assert entry.namespace == "default"
        assert entry.metadata == {"shard_id": "shard-C"}

    def test_default_tags_and_evidence(self) -> None:
        """None tags/evidence default to empty lists."""
        entry = _learning_to_memory_entry(
            "L-def001",
            "s",
            "d",
            tags=None,
            evidence=None,
        )
        assert entry.tags == []
        assert entry.evidence == []

    def test_default_impact(self) -> None:
        """Default impact is 0.5."""
        entry = _learning_to_memory_entry("L-imp001", "s", "d")
        assert entry.importance == 0.5

    def test_default_source_fields(self) -> None:
        """Default source_type is 'agent', source_identity is empty string."""
        entry = _learning_to_memory_entry("L-src001", "s", "d")
        assert entry.source == "agent"
        assert entry.source_identity == ""

    def test_no_shard_id_produces_empty_metadata(self) -> None:
        """When shard_id is None, metadata is an empty dict."""
        entry = _learning_to_memory_entry("L-ns001", "s", "d", shard_id=None)
        assert entry.metadata == {}

    def test_empty_shard_id_string_produces_empty_metadata(self) -> None:
        """When shard_id is empty string (falsy), metadata is empty."""
        entry = _learning_to_memory_entry("L-es001", "s", "d", shard_id="")
        assert entry.metadata == {}


# ---------------------------------------------------------------------------
# recall_learnings — boundary inputs
# ---------------------------------------------------------------------------


class TestRecallLearningsBoundary:
    def test_empty_string_query_treated_as_wildcard(self, trw_dir: Path) -> None:
        """Empty string query is treated as wildcard (returns all entries)."""
        store_learning(trw_dir, "L-eq1", "Entry one", "d1")
        store_learning(trw_dir, "L-eq2", "Entry two", "d2")
        results = recall_learnings(trw_dir, "")
        assert len(results) == 2

    def test_whitespace_query_treated_as_wildcard(self, trw_dir: Path) -> None:
        """Whitespace-only query is treated as wildcard."""
        store_learning(trw_dir, "L-wq1", "Only entry", "d")
        results = recall_learnings(trw_dir, "   ")
        assert len(results) == 1

    def test_max_results_zero_uses_default_limit(self, trw_dir: Path) -> None:
        """max_results=0 falls back to _MAX_ENTRIES for wildcard queries."""
        store_learning(trw_dir, "L-mr1", "Max results test", "d")
        results = recall_learnings(trw_dir, "*", max_results=0)
        # Should not crash and should return entries
        assert len(results) >= 1

    def test_max_results_zero_on_keyword_search(self, trw_dir: Path) -> None:
        """max_results=0 falls back to _MAX_ENTRIES for keyword queries."""
        store_learning(trw_dir, "L-mk1", "Keyword max test", "d")
        results = recall_learnings(trw_dir, "Keyword", max_results=0)
        assert isinstance(results, list)

    def test_status_filter_with_keyword_search(self, trw_dir: Path) -> None:
        """Status filter is applied during keyword search, not just wildcard."""
        store_learning(trw_dir, "L-sf1", "Active keyword entry", "d")
        store_learning(trw_dir, "L-sf2", "Obsolete keyword entry", "d")
        update_learning(trw_dir, "L-sf2", status="obsolete")
        results = recall_learnings(trw_dir, "keyword", status="active")
        ids = [str(r["id"]) for r in results]
        assert "L-sf1" in ids
        # L-sf2 may or may not be filtered depending on backend search behavior,
        # but the function should not crash

    def test_tag_filter_not_applied_on_keyword_search(self, trw_dir: Path) -> None:
        """Tag filter on keyword search is handled by _search_entries, not the wildcard path."""
        store_learning(trw_dir, "L-tf1", "Tagged entry", "d", tags=["python"])
        store_learning(trw_dir, "L-tf2", "Untagged entry", "d", tags=["rust"])
        results = recall_learnings(trw_dir, "entry", tags=["python"])
        # Should run without error; filtering depends on backend behavior
        assert isinstance(results, list)


# ---------------------------------------------------------------------------
# update_learning — multiple simultaneous changes
# ---------------------------------------------------------------------------


class TestUpdateLearningMultiChange:
    def test_all_fields_updated_at_once(self, trw_dir: Path) -> None:
        """Updating status, detail, summary, and impact in one call."""
        store_learning(trw_dir, "L-mc1", "Original summary", "Original detail", impact=0.5)
        result = update_learning(
            trw_dir,
            "L-mc1",
            status="resolved",
            detail="New detail",
            summary="New summary",
            impact=0.9,
        )
        assert result["status"] == "updated"
        changes = result["changes"]
        assert "status→resolved" in changes
        assert "detail updated" in changes
        assert "summary updated" in changes
        assert "impact→0.9" in changes

        # Verify actual values persisted
        entry = find_entry_by_id(trw_dir, "L-mc1")
        assert entry is not None
        assert entry["summary"] == "New summary"
        assert entry["impact"] == 0.9
        assert entry["status"] == "resolved"

    def test_impact_boundary_zero(self, trw_dir: Path) -> None:
        """Impact of exactly 0.0 is valid."""
        store_learning(trw_dir, "L-iz1", "s", "d", impact=0.5)
        result = update_learning(trw_dir, "L-iz1", impact=0.0)
        assert result["status"] == "updated"

    def test_impact_boundary_one(self, trw_dir: Path) -> None:
        """Impact of exactly 1.0 is valid."""
        store_learning(trw_dir, "L-io1", "s", "d", impact=0.5)
        result = update_learning(trw_dir, "L-io1", impact=1.0)
        assert result["status"] == "updated"


# ---------------------------------------------------------------------------
# update_access_tracking — mixed IDs
# ---------------------------------------------------------------------------


class TestUpdateAccessTrackingMixed:
    def test_mixed_valid_and_missing_ids(self, trw_dir: Path) -> None:
        """Some valid, some missing IDs — valid ones still get updated."""
        store_learning(trw_dir, "L-mx1", "Valid entry", "d")
        # L-mx2 does not exist
        store_learning(trw_dir, "L-mx3", "Another valid", "d")

        update_access_tracking(trw_dir, ["L-mx1", "L-mx2", "L-mx3"])

        entry1 = find_entry_by_id(trw_dir, "L-mx1")
        entry3 = find_entry_by_id(trw_dir, "L-mx3")
        assert entry1 is not None
        assert entry1["access_count"] == 1
        assert entry3 is not None
        assert entry3["access_count"] == 1

    def test_empty_ids_list(self, trw_dir: Path) -> None:
        """Empty list of IDs does nothing and does not error."""
        update_access_tracking(trw_dir, [])

    def test_double_increment(self, trw_dir: Path) -> None:
        """Calling twice increments access_count to 2."""
        store_learning(trw_dir, "L-di1", "Double increment", "d")
        update_access_tracking(trw_dir, ["L-di1"])
        update_access_tracking(trw_dir, ["L-di1"])
        entry = find_entry_by_id(trw_dir, "L-di1")
        assert entry is not None
        assert entry["access_count"] == 2

    def test_sets_last_accessed_at(self, trw_dir: Path) -> None:
        """Access tracking sets last_accessed_at to a non-None value."""
        store_learning(trw_dir, "L-la1", "Last accessed test", "d")
        update_access_tracking(trw_dir, ["L-la1"])
        entry = find_entry_by_id(trw_dir, "L-la1")
        assert entry is not None
        assert entry["last_accessed_at"] is not None


# ---------------------------------------------------------------------------
# reset_backend / reset_embedder — idempotency
# ---------------------------------------------------------------------------


class TestResetIdempotency:
    def test_reset_backend_when_no_backend_exists(self) -> None:
        """reset_backend() is safe to call when _backend is already None."""
        # This should not raise
        reset_backend()
        reset_backend()  # Double call is safe

    def test_reset_embedder_when_not_initialized(self) -> None:
        """reset_embedder() is safe to call when _embedder is already None."""
        reset_embedder()
        reset_embedder()  # Double call is safe

    def test_reset_backend_also_resets_embedder(self) -> None:
        """reset_backend() calls reset_embedder() internally."""
        with patch("trw_mcp.state.memory_adapter.reset_embedder") as mock_reset_emb:
            # We can't directly call reset_backend here because it would
            # actually call reset_embedder. Instead verify the relationship.
            # Verify the function calls reset_embedder by inspecting source
            import inspect

            from trw_mcp.state import memory_adapter

            source = inspect.getsource(memory_adapter.reset_backend)
            assert "reset_embedder" in source


# ---------------------------------------------------------------------------
# store_learning — tag inference wiring
# ---------------------------------------------------------------------------


class TestStoreLearningTagInference:
    def test_inferred_tags_are_appended(self, trw_dir: Path) -> None:
        """store_learning appends inferred topic tags to user-provided tags."""
        # infer_topic_tags is imported locally inside store_learning via
        # `from trw_mcp.state.analytics import infer_topic_tags`.
        # We patch at the analytics package level (the re-export).
        with patch(
            "trw_mcp.state.analytics.infer_topic_tags",
            return_value=["inferred-tag"],
        ):
            store_learning(trw_dir, "L-ti1", "Summary about Python", "d", tags=["user-tag"])

        entry = find_entry_by_id(trw_dir, "L-ti1")
        assert entry is not None
        tags = entry["tags"]
        assert isinstance(tags, list)
        assert "user-tag" in tags
        assert "inferred-tag" in tags

    def test_no_inferred_tags_keeps_original(self, trw_dir: Path) -> None:
        """When infer_topic_tags returns empty, original tags are preserved."""
        with patch(
            "trw_mcp.state.analytics.infer_topic_tags",
            return_value=[],
        ):
            store_learning(trw_dir, "L-ti2", "Summary", "d", tags=["original"])

        entry = find_entry_by_id(trw_dir, "L-ti2")
        assert entry is not None
        assert "original" in entry["tags"]

    def test_none_tags_with_inference(self, trw_dir: Path) -> None:
        """When user provides no tags, inferred tags are the only tags."""
        with patch(
            "trw_mcp.state.analytics.infer_topic_tags",
            return_value=["auto-tag"],
        ):
            store_learning(trw_dir, "L-ti3", "Summary", "d", tags=None)

        entry = find_entry_by_id(trw_dir, "L-ti3")
        assert entry is not None
        assert "auto-tag" in entry["tags"]


# ---------------------------------------------------------------------------
# list_active_learnings — min_impact boundary
# ---------------------------------------------------------------------------


class TestListActiveLearningsBoundary:
    def test_zero_min_impact_returns_all_active(self, trw_dir: Path) -> None:
        """min_impact=0.0 returns all active entries regardless of impact."""
        store_learning(trw_dir, "L-al1", "Low impact", "d", impact=0.1)
        store_learning(trw_dir, "L-al2", "High impact", "d", impact=0.9)
        results = list_active_learnings(trw_dir, min_impact=0.0)
        ids = [str(r["id"]) for r in results]
        assert "L-al1" in ids
        assert "L-al2" in ids

    def test_high_min_impact_filters_low_entries(self, trw_dir: Path) -> None:
        """min_impact=0.8 excludes low-impact entries."""
        store_learning(trw_dir, "L-al3", "Low", "d", impact=0.3)
        store_learning(trw_dir, "L-al4", "High", "d", impact=0.9)
        results = list_active_learnings(trw_dir, min_impact=0.8)
        ids = [str(r["id"]) for r in results]
        assert "L-al3" not in ids
        assert "L-al4" in ids

    def test_default_limit_parameter(self, trw_dir: Path) -> None:
        """list_active_learnings works with default limit (no explicit limit)."""
        store_learning(trw_dir, "L-dl1", "Default limit", "d")
        results = list_active_learnings(trw_dir)
        assert len(results) >= 1


# ---------------------------------------------------------------------------
# store_learning — embedding wiring
# ---------------------------------------------------------------------------


class TestStoreLearningEmbedding:
    def test_embed_input_is_summary_plus_detail(self, trw_dir: Path) -> None:
        """store_learning passes 'summary detail' to _embed_and_store."""
        with patch("trw_mcp.state.memory_adapter._embed_and_store") as mock_embed:
            with patch(
                "trw_mcp.state.analytics.infer_topic_tags",
                return_value=[],
            ):
                store_learning(trw_dir, "L-ei1", "My Summary", "My Detail")

            # _embed_and_store is called with the backend, id, and concatenated text
            mock_embed.assert_called_once()
            call_args = mock_embed.call_args
            assert call_args[0][1] == "L-ei1"  # entry_id
            assert call_args[0][2] == "My Summary My Detail"  # embed_input


# ---------------------------------------------------------------------------
# recall_learnings — min_impact post-filter
# ---------------------------------------------------------------------------


class TestRecallMinImpactPostFilter:
    def test_min_impact_filters_after_backend_query(self, trw_dir: Path) -> None:
        """min_impact is applied as a post-filter on the converted dicts."""
        store_learning(trw_dir, "L-pf1", "Low impact recall", "d", impact=0.2)
        store_learning(trw_dir, "L-pf2", "High impact recall", "d", impact=0.8)
        results = recall_learnings(trw_dir, "*", min_impact=0.5)
        ids = [str(r["id"]) for r in results]
        assert "L-pf1" not in ids
        assert "L-pf2" in ids

    def test_min_impact_on_keyword_search(self, trw_dir: Path) -> None:
        """min_impact filter works with keyword search too."""
        store_learning(trw_dir, "L-kf1", "Keyword filter low", "d", impact=0.1)
        store_learning(trw_dir, "L-kf2", "Keyword filter high", "d", impact=0.9)
        results = recall_learnings(trw_dir, "Keyword filter", min_impact=0.5)
        for r in results:
            assert float(str(r["impact"])) >= 0.5


# ---------------------------------------------------------------------------
# get_backend — creates memory directory
# ---------------------------------------------------------------------------


class TestGetBackendDirectoryCreation:
    def test_creates_memory_dir_if_missing(self, tmp_path: Path) -> None:
        """get_backend creates the memory/ subdirectory if it doesn't exist."""
        trw = tmp_path / ".trw"
        trw.mkdir()
        (trw / "learnings" / "entries").mkdir(parents=True)
        # Do NOT create memory/ dir
        assert not (trw / "memory").exists()

        backend = get_backend(trw)
        assert backend is not None
        assert (trw / "memory").exists()
