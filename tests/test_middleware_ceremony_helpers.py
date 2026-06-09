"""Payload helper tests for trw_mcp.middleware.ceremony."""

from __future__ import annotations

import pytest
from mcp.types import TextContent

from tests._test_middleware_ceremony_support import FakeToolResult
from trw_mcp.middleware.ceremony import (
    _compaction_gate_sessions,
    _extract_session_start_payload,
    _session_start_succeeded,
)


class TestSessionStartPayloadHelpers:
    """Tests for session_start payload extraction and success detection helpers."""

    def test_extracts_structured_content_dict(self) -> None:
        result = type("Result", (), {"structured_content": {"success": True}})()
        assert _extract_session_start_payload(result) == {"success": True}

    def test_extracts_json_payload_from_text_block(self) -> None:
        result = FakeToolResult(content=[TextContent(type="text", text='{"status":"success"}')])
        assert _extract_session_start_payload(result) == {"status": "success"}

    def test_non_list_content_returns_none(self) -> None:
        result = type("Result", (), {"content": "not-a-list"})()
        assert _extract_session_start_payload(result) is None

    def test_session_start_succeeded_handles_status_strings(self) -> None:
        success_result = FakeToolResult(content=[TextContent(type="text", text='{"status":"success"}')])
        failure_result = FakeToolResult(content=[TextContent(type="text", text='{"status":"failed"}')])

        assert _session_start_succeeded(success_result) is True
        assert _session_start_succeeded(failure_result) is False

    def test_compaction_gate_marks_all_known_sessions_pending(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "trw_mcp.middleware.ceremony._is_compaction_gate_required",
            lambda: True,
        )
        from trw_mcp.middleware.ceremony import _register_session, reset_state

        reset_state()
        _register_session("sess-a")
        _register_session("sess-b")

        from trw_mcp.middleware.ceremony import _is_compaction_gate_required_for_session

        assert _is_compaction_gate_required_for_session("sess-a") is True
        assert _compaction_gate_sessions["sess-b"] is True
        assert _is_compaction_gate_required_for_session("sess-b") is True
