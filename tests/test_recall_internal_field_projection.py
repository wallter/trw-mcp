"""trw_recall response projection — internal scoring state stays server-side.

The stored learning row carries ranking/telemetry state (outcome_history,
q_observations, counters, …) that is ~3x the content size. The MCP response
must project it away while leaving stored rows, compact mode, and the
config-empty escape hatch intact.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from tests._recall_integration_support import _make_entry
from trw_mcp.models.config import get_config
from trw_mcp.tools._recall_projection import strip_internal_response_fields

INTERNAL_FIELDS = {
    "access_count": 64,
    "anchor_validity": 1.0,
    "avg_rework_delta": 0.1,
    "combined_score": 0.88,
    "helpful_count": 3,
    "last_accessed_at": "2026-07-01",
    "outcome_correlation": {"positive": 2},
    "outcome_history": [{"outcome": "pass"}] * 8,
    "q_observations": 12,
    "q_value": 0.42,
    "recall_count": 9,
    "recurrence": 1,
    "session_count": 4,
    "sessions_surfaced": ["s1", "s2"],
    "unhelpful_count": 0,
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
        assert "q_value" in entry, "stored/stateful dict must not be mutated"

    def test_fail_open_on_non_dict_entries(self) -> None:
        weird: list[dict[str, object]] = [_full_entry(), "not-a-dict"]  # type: ignore[list-item]
        stripped = strip_internal_response_fields(weird, get_config().recall_internal_fields)
        assert stripped[1] == "not-a-dict"


class TestExecuteRecallProjection:
    def _run_recall(self, tmp_path: Path, **kwargs: object) -> dict[str, object]:
        from trw_mcp.tools._recall_impl import execute_recall

        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir(exist_ok=True)
        with (
            patch("trw_mcp.state.memory_adapter.recall_learnings", return_value=[_full_entry()]),
            patch("trw_mcp.state.memory_adapter.update_access_tracking"),
            patch("trw_mcp.state.recall_search.search_patterns", return_value=[]),
            patch("trw_mcp.state.recall_search.collect_context", return_value={}),
            patch("trw_mcp.tools._recall_impl._track_recall"),
            patch("trw_mcp.tools._recall_impl._augment_with_remote", side_effect=lambda _q, m: m),
        ):
            return dict(execute_recall(query="auth", trw_dir=trw_dir, config=get_config(), **kwargs))  # type: ignore[arg-type]

    def test_response_entries_are_projected(self, tmp_path: Path) -> None:
        result = self._run_recall(tmp_path)
        learnings = result["learnings"]
        assert isinstance(learnings, list) and learnings
        entry = learnings[0]
        assert "outcome_history" not in entry
        assert "q_value" not in entry
        assert "access_count" not in entry
        assert entry["detail"] == "root cause and fix"

    def test_tokens_used_reflects_projected_entries(self, tmp_path: Path) -> None:
        from trw_memory.retrieval.token_budget import estimate_serialized_entry_tokens

        result = self._run_recall(tmp_path)
        learnings = result["learnings"]
        assert isinstance(learnings, list)
        expected = sum(estimate_serialized_entry_tokens(e) for e in learnings)
        assert result["tokens_used"] == expected

    def test_topic_filter_fields_omitted_without_topic(self, tmp_path: Path) -> None:
        result = self._run_recall(tmp_path)
        assert "topic_filter_ignored" not in result
        assert "topic_filter_warning" not in result
