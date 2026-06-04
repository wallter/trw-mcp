"""Edge-case transform tests for state/memory_adapter.py."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from unittest.mock import patch

from trw_memory.models.memory import MemoryEntry, MemoryStatus

from trw_mcp.state.memory_adapter import _learning_to_memory_entry, _memory_to_learning_dict


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
            "client_profile",
            "model_id",
            "created",
            "updated",
            "access_count",
            # PRD-FIX-104: production has emitted these three feedback/recall
            # fields in full mode since commit 4f9b2d256. expected_keys was
            # stale (PRD-IMPROVE-MCP-02 FR2) — kept asserting the FULL set.
            "recall_count",
            "helpful_count",
            "unhelpful_count",
            "last_accessed_at",
            "q_value",
            "q_observations",
            "recurrence",
            "shard_id",
            "outcome_history",
            "type",
            "nudge_line",
            "expires",
            "confidence",
            "task_type",
            "domain",
            "phase_origin",
            "phase_affinity",
            "team_origin",
            "protection_tier",
            "anchor_validity",
            "sessions_surfaced",
            "avg_rework_delta",
            "outcome_correlation",
            "session_count",
        }
        assert expected_keys == set(result.keys())

    def test_full_keys_are_declared_in_learning_entry_dict(self) -> None:
        """Every emitted key is a declared field of the LearningEntryDict contract.

        Guards the SSOT relationship (PRD-FIX-085 FR05 typing follow-up):
        ``_memory_to_learning_dict`` is the source of truth and
        ``LearningEntryDict`` must mirror it. If a new key is added to the
        transform without extending the TypedDict, this fails — preventing the
        contract from silently drifting away from the runtime shape.
        """
        from trw_mcp.models.typed_dicts import LearningEntryCompactDict, LearningEntryDict

        entry = self._make_entry()
        declared = set(LearningEntryDict.__annotations__) | set(LearningEntryCompactDict.__annotations__)
        full_keys = set(_memory_to_learning_dict(entry, compact=False).keys())
        compact_keys = set(_memory_to_learning_dict(entry, compact=True).keys())
        undeclared = (full_keys | compact_keys) - declared
        assert undeclared == set(), f"transform emits keys not declared in LearningEntryDict: {undeclared}"
        # Compact base must equal the always-present LearningEntryCompactDict fields.
        assert compact_keys == set(LearningEntryCompactDict.__annotations__)

    def test_assertions_key_present_when_entry_has_assertions(self) -> None:
        """The ``assertions`` key (declared in LearningEntryDict) is populated."""
        from trw_memory.models.memory import Assertion

        entry = self._make_entry(
            assertions=[
                Assertion.model_validate(
                    {"type": "grep_present", "pattern": "def recall_learnings", "target": "memory_adapter.py"},
                    strict=False,
                )
            ]
        )
        result = _memory_to_learning_dict(entry, compact=False)
        assert "assertions" in result
        assert isinstance(result["assertions"], list)
        assert result["assertions"]

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

    def test_session_count_uses_distinct_field_not_access_count(self) -> None:
        """session_count maps from the dedicated MemoryEntry field."""
        entry = self._make_entry(access_count=7, session_count=3)
        result = _memory_to_learning_dict(entry)
        assert result["access_count"] == 7
        assert result["session_count"] == 3

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

    def test_compact_mode_caps_tags_at_limit(self) -> None:
        """Compact mode truncates tags list to COMPACT_TAGS_CAP."""
        from trw_mcp.models.config._defaults import COMPACT_TAGS_CAP

        many_tags = [f"tag-{i}" for i in range(100)]
        entry = self._make_entry(tags=many_tags)
        result = _memory_to_learning_dict(entry, compact=True)
        tags = list(result["tags"])
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
        tags = list(result["tags"])
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
        tags = list(result["tags"])
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

        assert compact_size < full_size / 10


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

    def test_invalid_anchor_is_skipped_with_debug_log(self) -> None:
        """Invalid anchors are skipped fail-open and emit a debug log."""
        anchors = [
            {"file": "src/good.py", "symbol_name": "good_symbol"},
            {"file": "../bad.py", "symbol_name": "bad_symbol"},
        ]

        with patch("trw_mcp.state._memory_transforms.logger.debug") as mock_debug:
            entry = _learning_to_memory_entry("L-anc001", "s", "d", anchors=anchors)

        assert [anchor.file for anchor in entry.anchors] == ["src/good.py"]
        mock_debug.assert_called_once()
        assert mock_debug.call_args.kwargs["anchor"] == anchors[1]

    def test_caller_metadata_merges_with_shard_id(self) -> None:
        """PRD-DIST-254 §FR02 (cycle 112): caller-supplied metadata + shard_id co-exist."""
        entry = _learning_to_memory_entry(
            "L-meta001",
            "s",
            "d",
            shard_id="shard-X",
            metadata={"utility_grade": "R3", "current_status": "current"},
        )
        # Both internal (shard_id) and caller keys present.
        assert entry.metadata.get("shard_id") == "shard-X"
        assert entry.metadata.get("utility_grade") == "R3"
        assert entry.metadata.get("current_status") == "current"

    def test_caller_metadata_wins_on_collision_with_shard_id(self) -> None:
        """Caller-supplied metadata overrides internal keys on collision (cycle 112)."""
        entry = _learning_to_memory_entry(
            "L-coll001",
            "s",
            "d",
            shard_id="internal-shard",
            metadata={"shard_id": "caller-shard"},
        )
        # Caller's value wins.
        assert entry.metadata.get("shard_id") == "caller-shard"

    def test_metadata_default_none_preserves_back_compat(self) -> None:
        """metadata=None (default) leaves existing shard-only behavior intact."""
        entry = _learning_to_memory_entry(
            "L-bc001",
            "s",
            "d",
            shard_id="only-shard",
            metadata=None,
        )
        assert entry.metadata == {"shard_id": "only-shard"}

    def test_metadata_without_shard_id_round_trips_keys(self) -> None:
        """Without shard_id, caller metadata is the entire metadata dict."""
        entry = _learning_to_memory_entry(
            "L-ms001",
            "s",
            "d",
            shard_id=None,
            metadata={"utility_grade": "R5", "evidence_count": "2"},
        )
        assert entry.metadata == {"utility_grade": "R5", "evidence_count": "2"}
