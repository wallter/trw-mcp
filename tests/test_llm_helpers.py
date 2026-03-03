"""Tests for trw_mcp.state.llm_helpers — LLM helper functions.

Uses mock LLMClient instances to test all three helper functions
and the shared _parse_json_lines parser without requiring the
anthropic SDK.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from trw_mcp.clients.llm import LLMClient
from trw_mcp.state.llm_helpers import (
    LLM_BATCH_CAP,
    LLM_EVENT_CAP,
    _parse_json_lines,
    llm_assess_learnings,
    llm_extract_learnings,
    llm_summarize_learnings,
)

# ---------------------------------------------------------------------------
# Helper: mock LLMClient factory
# ---------------------------------------------------------------------------


def _make_mock_llm(response: str | None) -> LLMClient:
    """Create a mock LLMClient that returns a fixed response."""
    mock = MagicMock(spec=LLMClient)
    mock.available = response is not None
    mock.ask_sync.return_value = response
    return mock


def _count_prompt_lines(llm: LLMClient, prefix: str) -> int:
    """Count lines in the first positional prompt arg that start with prefix."""
    prompt: str = llm.ask_sync.call_args[0][0]
    return sum(1 for line in prompt.split("\n") if line.startswith(prefix))


# ---------------------------------------------------------------------------
# _parse_json_lines
# ---------------------------------------------------------------------------


class TestParseJsonLines:
    """Tests for the shared JSON-lines parser."""

    def test_valid_json_lines(self) -> None:
        result = _parse_json_lines('{"a": 1}\n{"b": 2}\n{"c": 3}')
        assert result == [{"a": 1}, {"b": 2}, {"c": 3}]

    def test_mixed_valid_invalid_lines(self) -> None:
        result = _parse_json_lines('{"ok": true}\nNOT JSON\n{"also": "ok"}')
        assert result == [{"ok": True}, {"also": "ok"}]

    def test_empty_input(self) -> None:
        assert _parse_json_lines("") == []
        assert _parse_json_lines("   ") == []

    def test_lines_not_starting_with_brace_skipped(self) -> None:
        text = 'Here is some text\n- bullet point\n["array"]\n{"valid": 1}'
        assert _parse_json_lines(text) == [{"valid": 1}]

    def test_blank_lines_skipped(self) -> None:
        result = _parse_json_lines('{"a": 1}\n\n\n{"b": 2}\n   \n{"c": 3}')
        assert len(result) == 3

    def test_malformed_json_skipped(self) -> None:
        result = _parse_json_lines('{bad json here}\n{"good": "json"}')
        assert result == [{"good": "json"}]


# ---------------------------------------------------------------------------
# llm_assess_learnings
# ---------------------------------------------------------------------------


class TestLlmAssessLearnings:
    """Tests for LLM-based learning assessment."""

    @staticmethod
    def _make_entries(
        count: int = 3,
    ) -> list[tuple[Path, dict[str, object]]]:
        """Create sample learning entries."""
        return [
            (
                Path(f"/fake/learning-{i}.yaml"),
                {
                    "id": f"L-{i:04d}",
                    "created": "2026-01-01",
                    "summary": f"Learning {i}",
                    "detail": f"Detail for learning {i}",
                },
            )
            for i in range(count)
        ]

    def test_returns_obsolete_and_resolved_candidates(self) -> None:
        entries = self._make_entries(3)
        response = (
            '{"id": "L-0000", "status": "OBSOLETE", "reason": "outdated"}\n'
            '{"id": "L-0002", "status": "RESOLVED", "reason": "fixed"}'
        )
        result = llm_assess_learnings(entries, _make_mock_llm(response))

        assert result == [
            {
                "id": "L-0000",
                "summary": "Learning 0",
                "suggested_status": "obsolete",
                "reason": "outdated",
            },
            {
                "id": "L-0002",
                "summary": "Learning 2",
                "suggested_status": "resolved",
                "reason": "fixed",
            },
        ]

    def test_active_entries_filtered_out(self) -> None:
        entries = self._make_entries(2)
        response = '{"id": "L-0000", "status": "ACTIVE", "reason": "still relevant"}'
        assert llm_assess_learnings(entries, _make_mock_llm(response)) == []

    def test_llm_unavailable_returns_empty(self) -> None:
        assert llm_assess_learnings(self._make_entries(2), _make_mock_llm(None)) == []

    def test_garbage_response_returns_empty(self) -> None:
        llm = _make_mock_llm("This is not JSON at all!\nJust random text.")
        assert llm_assess_learnings(self._make_entries(2), llm) == []

    def test_empty_entries_returns_empty(self) -> None:
        llm = _make_mock_llm("should not be called")
        assert llm_assess_learnings([], llm) == []
        llm.ask_sync.assert_not_called()

    def test_batch_cap_respected(self) -> None:
        """Only first batch_cap entries are sent to the LLM."""
        llm = _make_mock_llm('{"id": "L-0000", "status": "OBSOLETE", "reason": "old"}')
        llm_assess_learnings(self._make_entries(30), llm, batch_cap=5)
        assert _count_prompt_lines(llm, "- ID:") == 5

    def test_unknown_id_gets_empty_summary(self) -> None:
        """When LLM returns an ID not in the entries, summary defaults to empty."""
        response = '{"id": "L-9999", "status": "OBSOLETE", "reason": "gone"}'
        result = llm_assess_learnings(self._make_entries(1), _make_mock_llm(response))
        assert result == [{"id": "L-9999", "summary": "", "suggested_status": "obsolete", "reason": "gone"}]

    def test_mixed_statuses(self) -> None:
        """Only RESOLVED and OBSOLETE entries are returned, ACTIVE skipped."""
        entries = self._make_entries(3)
        response = (
            '{"id": "L-0000", "status": "ACTIVE", "reason": "keep"}\n'
            '{"id": "L-0001", "status": "RESOLVED", "reason": "done"}\n'
            '{"id": "L-0002", "status": "OBSOLETE", "reason": "old"}'
        )
        result = llm_assess_learnings(entries, _make_mock_llm(response))

        assert len(result) == 2
        assert {r["id"] for r in result} == {"L-0001", "L-0002"}


# ---------------------------------------------------------------------------
# llm_extract_learnings
# ---------------------------------------------------------------------------


class TestLlmExtractLearnings:
    """Tests for LLM-based learning extraction from events."""

    @staticmethod
    def _make_events(count: int = 3) -> list[dict[str, object]]:
        """Create sample event dicts."""
        return [
            {"event": f"event_{i}", "data": f"data for event {i}"}
            for i in range(count)
        ]

    def test_extracts_learnings_from_json_lines(self) -> None:
        response = (
            '{"summary": "Learn A", "detail": "Detail A", "tags": ["t1"], "impact": 0.8}\n'
            '{"summary": "Learn B", "detail": "Detail B", "tags": ["t2"], "impact": 0.5}'
        )
        result = llm_extract_learnings(self._make_events(3), _make_mock_llm(response))

        assert result == [
            {"summary": "Learn A", "detail": "Detail A", "tags": ["t1"], "impact": "0.8"},
            {"summary": "Learn B", "detail": "Detail B", "tags": ["t2"], "impact": "0.5"},
        ]

    def test_llm_unavailable_returns_none(self) -> None:
        assert llm_extract_learnings(self._make_events(2), _make_mock_llm(None)) is None

    def test_lines_missing_summary_filtered_out(self) -> None:
        response = (
            '{"detail": "no summary here", "tags": ["x"]}\n'
            '{"summary": "Has summary", "detail": "ok"}'
        )
        result = llm_extract_learnings(self._make_events(2), _make_mock_llm(response))

        assert result is not None
        assert len(result) == 1
        assert result[0]["summary"] == "Has summary"

    def test_empty_events_returns_none(self) -> None:
        llm = _make_mock_llm("should not be called")
        assert llm_extract_learnings([], llm) is None
        llm.ask_sync.assert_not_called()

    def test_all_lines_missing_summary_returns_none(self) -> None:
        response = '{"detail": "no summary"}\n{"tags": ["only tags"]}'
        assert llm_extract_learnings(self._make_events(2), _make_mock_llm(response)) is None

    def test_event_cap_respected(self) -> None:
        """Only first event_cap events are sent to the LLM."""
        llm = _make_mock_llm('{"summary": "ok", "detail": "d"}')
        llm_extract_learnings(self._make_events(50), llm, event_cap=10)
        assert _count_prompt_lines(llm, "- ") == 10

    def test_default_tags_when_missing(self) -> None:
        response = '{"summary": "No tags", "detail": "missing tags field"}'
        result = llm_extract_learnings(self._make_events(1), _make_mock_llm(response))
        assert result is not None
        assert result[0]["tags"] == ["auto-discovered", "llm"]

    def test_default_impact_when_missing(self) -> None:
        response = '{"summary": "No impact", "detail": "missing impact"}'
        result = llm_extract_learnings(self._make_events(1), _make_mock_llm(response))
        assert result is not None
        assert result[0]["impact"] == "0.6"

    def test_garbage_response_returns_none(self) -> None:
        assert llm_extract_learnings(self._make_events(2), _make_mock_llm("totally not json at all")) is None


# ---------------------------------------------------------------------------
# llm_summarize_learnings
# ---------------------------------------------------------------------------


class TestLlmSummarizeLearnings:
    """Tests for LLM-based learning summarization."""

    def _call(
        self,
        learnings: list[dict[str, object]],
        patterns: list[dict[str, object]],
        llm: LLMClient,
        *,
        learning_cap: int = 10,
        pattern_cap: int = 10,
    ) -> str | None:
        return llm_summarize_learnings(
            learnings, patterns, llm, learning_cap=learning_cap, pattern_cap=pattern_cap
        )

    def test_returns_markdown_as_is(self) -> None:
        markdown = "### Architecture\n- Key insight here"
        result = self._call(
            [{"summary": "S1", "detail": "D1"}],
            [{"name": "P1", "description": "Pattern 1"}],
            _make_mock_llm(markdown),
        )
        assert result == markdown

    def test_llm_unavailable_returns_none(self) -> None:
        result = self._call(
            [{"summary": "S1", "detail": "D1"}],
            [],
            _make_mock_llm(None),
        )
        assert result is None

    def test_empty_learnings_and_patterns_returns_none(self) -> None:
        llm = _make_mock_llm("should not be called")
        assert self._call([], [], llm) is None
        llm.ask_sync.assert_not_called()

    def test_only_learnings_no_patterns(self) -> None:
        markdown = "### Key Learning\n- S1"
        llm = _make_mock_llm(markdown)
        result = self._call([{"summary": "S1", "detail": "D1"}], [], llm)
        assert result == markdown
        llm.ask_sync.assert_called_once()

    def test_only_patterns_no_learnings(self) -> None:
        markdown = "### Patterns\n- P1"
        llm = _make_mock_llm(markdown)
        result = self._call([], [{"name": "P1", "description": "Pattern desc"}], llm)
        assert result == markdown
        llm.ask_sync.assert_called_once()

    def test_model_override_passed_to_llm(self) -> None:
        """Verify the sonnet model override is passed through."""
        llm = _make_mock_llm("summary text")
        self._call([{"summary": "S1", "detail": "D1"}], [], llm)
        assert llm.ask_sync.call_args[1].get("model") == "sonnet"

    def test_learning_cap_respected(self) -> None:
        learnings = [{"summary": f"S{i}", "detail": f"D{i}"} for i in range(20)]
        llm = _make_mock_llm("summary")
        self._call(learnings, [], llm, learning_cap=5)
        assert _count_prompt_lines(llm, "- Learning:") == 5

    def test_pattern_cap_respected(self) -> None:
        patterns = [{"name": f"P{i}", "description": f"Desc{i}"} for i in range(20)]
        llm = _make_mock_llm("summary")
        self._call([], patterns, llm, pattern_cap=3)
        assert _count_prompt_lines(llm, "- Pattern:") == 3


# ---------------------------------------------------------------------------
# Constants exported correctly
# ---------------------------------------------------------------------------


class TestConstants:
    """Verify module constants are accessible."""

    def test_batch_cap_value(self) -> None:
        assert LLM_BATCH_CAP == 20

    def test_event_cap_value(self) -> None:
        assert LLM_EVENT_CAP == 30
