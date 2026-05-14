"""Parsing, LLM summarization, and prompt redaction tests for consolidation."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from trw_mcp.state.consolidation import _parse_consolidation_response, _summarize_cluster_llm

from ._consolidation_test_helpers import make_cluster


class TestParseConsolidationResponse:
    """FR02: _parse_consolidation_response extracts JSON from LLM output."""

    def test_valid_json_line_extracted(self) -> None:
        """Valid JSON with summary and detail is parsed correctly."""
        response = '{"summary": "consolidated summary", "detail": "merged detail"}'
        result = _parse_consolidation_response(response)
        assert result is not None
        assert result["summary"] == "consolidated summary"
        assert result["detail"] == "merged detail"

    def test_multiline_response_extracts_json_line(self) -> None:
        """JSON line is extracted from a multi-line response."""
        response = (
            "Here is the consolidated entry:\n"
            '{"summary": "brief summary", "detail": "full explanation"}\n'
            "Hope that helps!"
        )
        result = _parse_consolidation_response(response)
        assert result is not None
        assert result["summary"] == "brief summary"

    def test_non_json_lines_skipped(self) -> None:
        """Non-JSON lines are skipped without error."""
        response = 'Thinking about this...\nLet me consolidate:\n{"summary": "the summary", "detail": "the detail"}'
        result = _parse_consolidation_response(response)
        assert result is not None
        assert result["summary"] == "the summary"

    def test_missing_summary_key_returns_none(self) -> None:
        """JSON without 'summary' key returns None."""
        response = '{"detail": "only detail here"}'
        result = _parse_consolidation_response(response)
        assert result is None

    def test_missing_detail_key_returns_none(self) -> None:
        """JSON without 'detail' key returns None."""
        response = '{"summary": "only summary here"}'
        result = _parse_consolidation_response(response)
        assert result is None

    def test_malformed_json_returns_none(self) -> None:
        """Malformed JSON returns None without raising."""
        response = '{"summary": "broken'
        result = _parse_consolidation_response(response)
        assert result is None

    def test_empty_response_returns_none(self) -> None:
        """Empty response returns None."""
        result = _parse_consolidation_response("")
        assert result is None

    def test_non_json_response_returns_none(self) -> None:
        """Response with no JSON line returns None."""
        result = _parse_consolidation_response("just some text without any json")
        assert result is None

    def test_summary_and_detail_cast_to_str(self) -> None:
        """Non-string values in summary/detail are cast to str."""
        response = '{"summary": 42, "detail": true}'
        result = _parse_consolidation_response(response)
        assert result is not None
        assert isinstance(result["summary"], str)
        assert isinstance(result["detail"], str)


class TestSummarizeClusterLlm:
    """FR02: _summarize_cluster_llm calls LLM and validates length."""

    def _make_llm(self, responses: list[str | None]) -> MagicMock:
        """Create a mock LLMClient with sequential ask_sync responses."""
        llm = MagicMock()
        llm.ask_sync.side_effect = responses
        return llm

    def test_valid_response_shorter_than_inputs_accepted(self) -> None:
        """Short summary accepted on first attempt."""
        cluster = make_cluster(3)
        short_json = '{"summary": "short", "detail": "brief detail"}'
        llm = self._make_llm([short_json])

        result = _summarize_cluster_llm(cluster, llm)
        assert result is not None
        assert result["summary"] == "short"
        assert result["detail"] == "brief detail"
        assert llm.ask_sync.call_count == 1

    def test_prompt_contains_all_entry_summaries(self) -> None:
        """Prompt passed to LLM contains all cluster entry summaries."""
        cluster = make_cluster(3)
        short_json = '{"summary": "s", "detail": "d"}'
        llm = self._make_llm([short_json])

        _summarize_cluster_llm(cluster, llm)

        call_args = llm.ask_sync.call_args
        prompt = call_args[0][0]
        for e in cluster:
            assert str(e["summary"]) in prompt

    def test_too_long_summary_triggers_retry(self) -> None:
        """Summary >= sum of input lengths triggers one retry."""
        cluster = [
            {"id": "e1", "summary": "ab", "detail": "cd"},  # len(summary) = 2
        ]
        # First response: summary length 5 >= sum of input summaries (2)
        long_json = '{"summary": "12345", "detail": "x"}'
        short_json = '{"summary": "ok", "detail": "concise"}'
        llm = self._make_llm([long_json, short_json])

        result = _summarize_cluster_llm(cluster, llm)
        assert result is not None
        assert llm.ask_sync.call_count == 2

    def test_retry_prompt_contains_length_constraint(self) -> None:
        """Retry prompt includes explicit length constraint."""
        cluster = [{"id": "e1", "summary": "ab", "detail": "cd"}]
        long_json = '{"summary": "12345", "detail": "x"}'
        short_json = '{"summary": "ok", "detail": "d"}'
        llm = self._make_llm([long_json, short_json])

        _summarize_cluster_llm(cluster, llm)

        retry_call = llm.ask_sync.call_args_list[1]
        retry_prompt = retry_call[0][0]
        assert "characters" in retry_prompt or "IMPORTANT" in retry_prompt

    def test_llm_returns_none_returns_none(self) -> None:
        """When LLM returns None, result is None."""
        cluster = make_cluster(3)
        llm = self._make_llm([None])

        result = _summarize_cluster_llm(cluster, llm)
        assert result is None

    def test_parse_failure_returns_none(self) -> None:
        """When LLM response cannot be parsed, returns None."""
        cluster = make_cluster(3)
        llm = self._make_llm(["not json at all"])

        result = _summarize_cluster_llm(cluster, llm)
        assert result is None

    def test_retry_parse_failure_returns_none(self) -> None:
        """When both LLM responses fail to parse, returns None."""
        cluster = [{"id": "e1", "summary": "ab", "detail": "cd"}]
        long_json = '{"summary": "12345", "detail": "x"}'
        llm = self._make_llm([long_json, "still not valid json"])

        result = _summarize_cluster_llm(cluster, llm)
        assert result is None

    def test_retry_none_response_returns_none(self) -> None:
        """When retry call returns None, result is None."""
        cluster = [{"id": "e1", "summary": "ab", "detail": "cd"}]
        long_json = '{"summary": "12345", "detail": "x"}'
        llm = self._make_llm([long_json, None])

        result = _summarize_cluster_llm(cluster, llm)
        assert result is None

class TestPathRedaction:
    """NFR06: _redact_paths removes filesystem paths from LLM prompt content."""

    def test_redact_unix_home_path(self) -> None:
        """Unix home paths (/home/user/...) are redacted."""
        from trw_mcp.state.consolidation import _redact_paths

        text = "see /home/alice/projects/trw/foo.py for details"
        result = _redact_paths(text)
        assert "/home/alice" not in result
        assert "[REDACTED_PATH]" in result

    def test_redact_macos_users_path(self) -> None:
        """macOS /Users/... paths are redacted."""
        from trw_mcp.state.consolidation import _redact_paths

        text = "config at /Users/bob/Desktop/project/.env"
        result = _redact_paths(text)
        assert "/Users/bob" not in result
        assert "[REDACTED_PATH]" in result

    def test_redact_windows_drive_path(self) -> None:
        """Windows drive paths (C:\\...) are redacted."""
        from trw_mcp.state.consolidation import _redact_paths

        text = r"file at C:\Users\Charlie\docs\notes.txt"
        result = _redact_paths(text)
        assert r"C:\Users" not in result
        assert "[REDACTED_PATH]" in result

    def test_no_path_unchanged(self) -> None:
        """Text without filesystem paths is returned unchanged."""
        from trw_mcp.state.consolidation import _redact_paths

        text = "use pydantic v2 with use_enum_values=True"
        assert _redact_paths(text) == text

    def test_multiple_paths_all_redacted(self) -> None:
        """Multiple paths in same text are all redacted."""
        from trw_mcp.state.consolidation import _redact_paths

        text = "from /home/alice/foo.py and /home/bob/bar.py"
        result = _redact_paths(text)
        assert "/home/alice" not in result
        assert "/home/bob" not in result
        assert result.count("[REDACTED_PATH]") == 2

    def test_llm_prompt_contains_no_home_paths(self) -> None:
        """_summarize_cluster_llm calls _redact_paths on entry summary and detail."""
        cluster = [
            {
                "id": "e1",
                "summary": "error in /home/user/project/main.py",
                "detail": "check /home/user/project/config.yaml for settings",
            },
            {"id": "e2", "summary": "s2", "detail": "d2"},
            {"id": "e3", "summary": "s3", "detail": "d3"},
        ]

        captured_prompts: list[str] = []

        def capture_ask_sync(prompt: str, **kwargs: Any) -> str:
            captured_prompts.append(prompt)
            return '{"summary": "short", "detail": "brief"}'

        llm = MagicMock()
        llm.ask_sync.side_effect = capture_ask_sync

        _summarize_cluster_llm(cluster, llm)

        assert len(captured_prompts) >= 1
        for prompt in captured_prompts:
            assert "/home/user" not in prompt
            assert "[REDACTED_PATH]" in prompt

    def test_redact_paths_preserves_non_path_content(self) -> None:
        """Path redaction does not disturb surrounding non-path text."""
        from trw_mcp.state.consolidation import _redact_paths

        text = "before /home/user/file.txt after"
        result = _redact_paths(text)
        assert result.startswith("before ")
        assert result.endswith(" after")

class TestSummarizeClusterLlmEdgeCases:
    """Edge cases for _summarize_cluster_llm length checking and retry logic."""

    def _make_llm(self, responses: list[str | None]) -> MagicMock:
        llm = MagicMock()
        llm.ask_sync.side_effect = responses
        return llm

    def test_summary_exactly_equals_total_input_triggers_retry(self) -> None:
        """Summary with length == total_input_len triggers retry (not strictly less)."""
        cluster = [
            {"id": "e1", "summary": "abc", "detail": "d"},  # total_input_len = 3
        ]
        # First: summary len 3 == total 3 (not < total), so retry
        first_json = '{"summary": "xyz", "detail": "d"}'
        retry_json = '{"summary": "ok", "detail": "d"}'
        llm = self._make_llm([first_json, retry_json])

        result = _summarize_cluster_llm(cluster, llm)
        assert result is not None
        assert llm.ask_sync.call_count == 2

    def test_summary_one_char_shorter_accepted_without_retry(self) -> None:
        """Summary with length < total_input_len is accepted on first try."""
        cluster = [
            {"id": "e1", "summary": "abcd", "detail": "d"},  # total_input_len = 4
        ]
        # summary "abc" has len 3 < 4 -> accepted
        json_resp = '{"summary": "abc", "detail": "detail"}'
        llm = self._make_llm([json_resp])

        result = _summarize_cluster_llm(cluster, llm)
        assert result is not None
        assert result["summary"] == "abc"
        assert llm.ask_sync.call_count == 1

    def test_default_llm_instantiated_when_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When llm=None, LLMClient(model='haiku') is instantiated."""
        mock_client = MagicMock()
        mock_client.ask_sync.return_value = '{"summary": "s", "detail": "d"}'

        monkeypatch.setattr(
            "trw_mcp.state.consolidation._summarize.LLMClient",
            lambda model: mock_client,
        )

        cluster = make_cluster(2)
        result = _summarize_cluster_llm(cluster, llm=None)
        assert result is not None
        mock_client.ask_sync.assert_called_once()

    def test_retry_max_chars_at_least_50(self) -> None:
        """Retry prompt max_chars is at least 50, even for very short inputs."""
        cluster = [
            {"id": "e1", "summary": "a", "detail": "d"},  # total_input_len = 1
        ]
        # summary "12" has len 2 >= 1, triggers retry
        first_json = '{"summary": "12", "detail": "d"}'
        retry_json = '{"summary": "ok", "detail": "d"}'
        llm = self._make_llm([first_json, retry_json])

        _summarize_cluster_llm(cluster, llm)

        retry_prompt = llm.ask_sync.call_args_list[1][0][0]
        # max_chars = max(50, 1 // 2) = 50
        assert "50" in retry_prompt
