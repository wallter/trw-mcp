"""trw_recall response projection — internal scoring state stays server-side.

The stored learning row carries ranking/telemetry state (outcome_history,
recall_count, counters, …) that is ~3x the content size. The MCP response
must project it away while leaving stored rows and the config-empty escape
hatch intact.

PRD-CORE-294 FR01: the default (query-based) ``trw_recall`` response returns
stubs (``{id, claim, anchor?}``), so it never carries internal fields in the
first place. Full-row projection now happens only on the ``recall_by_ids``
path (``trw_recall(ids=[...])``), which is what ``TestExecuteRecallProjection``
below exercises.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from tests._memory_store_fake import FakeMemoryStore
from tests._recall_integration_support import _make_entry
from trw_mcp.models.config import get_config
from trw_mcp.tools._recall_projection import strip_internal_response_fields

INTERNAL_FIELDS = {
    "access_count": 64,
    "anchor_validity": 1.0,
    "combined_score": 0.88,
    "last_accessed_at": "2026-07-01",
    "outcome_history": [{"outcome": "pass"}] * 8,
    "recall_count": 9,
    "recurrence": 1,
    "session_count": 4,
}

CONTENT_FIELDS = {
    "detail": "root cause and fix",
    "tags": ["auth", "gotcha"],
    "type": "pattern",
    "status": "active",
    "confidence": "verified",
    "evidence": ["tests/test_auth.py"],
}


def _full_entry(entry_id: str = "L-001") -> dict[str, object]:
    return _make_entry(entry_id, **CONTENT_FIELDS, **INTERNAL_FIELDS)


class TestStripInternalResponseFields:
    def test_removes_internal_and_keeps_content(self) -> None:
        config = get_config()
        stripped = strip_internal_response_fields([_full_entry()], config.recall_internal_fields)

        assert len(stripped) == 1
        entry = stripped[0]
        for key in INTERNAL_FIELDS:
            assert key not in entry, f"internal field {key} leaked into response"
        for key in ("id", "summary", "impact", "created", *CONTENT_FIELDS):
            assert key in entry, f"content field {key} must be preserved"

    def test_empty_config_set_disables_stripping(self) -> None:
        entries = [_full_entry()]
        assert strip_internal_response_fields(entries, frozenset()) is entries

    def test_original_entries_not_mutated(self) -> None:
        entry = _full_entry()
        strip_internal_response_fields([entry], get_config().recall_internal_fields)
        assert "outcome_history" in entry, "stored/stateful dict must not be mutated"

    def test_fail_open_on_non_dict_entries(self) -> None:
        weird: list[dict[str, object]] = [_full_entry(), "not-a-dict"]  # type: ignore[list-item]
        stripped = strip_internal_response_fields(weird, get_config().recall_internal_fields)
        assert stripped[1] == "not-a-dict"


class TestRecallByIdsProjection:
    """``trw_recall(ids=[...])`` returns full rows and still strips internal fields."""

    def _seed(self, fake_memory_store: FakeMemoryStore, tmp_path: Path) -> Path:
        # Known fake quirk: ``memory_adapter.store_learning`` writes under the
        # pinned FAKE_NAMESPACE ("project:test"), but FakeMemoryStore.recall always
        # searches "default" -- write straight through the fake's own ``put`` under
        # "default" instead of routing through store_learning.
        trw_dir = tmp_path / ".trw"
        fake_memory_store.put(
            "test learning",
            "default",
            {"entry_id": "L-001", "detail": "root cause and fix", "tags": ["auth", "gotcha"], "importance": 0.5},
        )
        return trw_dir

    def test_response_entries_are_projected(self, fake_memory_store: FakeMemoryStore, tmp_path: Path) -> None:
        from trw_mcp.tools._recall_impl import recall_by_ids

        trw_dir = self._seed(fake_memory_store, tmp_path)
        result = recall_by_ids(trw_dir, get_config(), ["L-001"])
        learnings = result["learnings"]
        assert isinstance(learnings, list) and learnings
        entry = learnings[0]
        assert "outcome_history" not in entry
        assert "access_count" not in entry
        assert entry["detail"] == "root cause and fix"

    def test_topic_filter_fields_omitted_without_topic(
        self, fake_memory_store: FakeMemoryStore, tmp_path: Path
    ) -> None:
        from trw_mcp.tools._recall_impl import execute_recall

        trw_dir = self._seed(fake_memory_store, tmp_path)
        with (
            patch("trw_mcp.tools._recall_impl._track_recall"),
            patch("trw_mcp.tools._recall_impl._augment_with_remote", side_effect=lambda _q, m: (m, None)),
        ):
            result = dict(execute_recall(query="auth", trw_dir=trw_dir, config=get_config()))
        assert "topic_filter_warning" not in result
