"""Tests for _distill_telemetry.py — fail-open tool-return telemetry emitter."""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from trw_mcp.channels._distill_telemetry import (
    _ENV_VAR,
    _UNKNOWN_CLIENT,
    emit_tool_call,
    resolve_client_profile,
)


# ---------------------------------------------------------------------------
# resolve_client_profile
# ---------------------------------------------------------------------------


class TestResolveClientProfile:
    def test_returns_env_var_value(self, monkeypatch):
        monkeypatch.setenv(_ENV_VAR, "claude-code")
        assert resolve_client_profile() == "claude-code"

    def test_returns_unknown_when_absent(self, monkeypatch):
        monkeypatch.delenv(_ENV_VAR, raising=False)
        assert resolve_client_profile() == _UNKNOWN_CLIENT

    def test_returns_unknown_when_blank(self, monkeypatch):
        monkeypatch.setenv(_ENV_VAR, "   ")
        assert resolve_client_profile() == _UNKNOWN_CLIENT

    def test_strips_whitespace(self, monkeypatch):
        monkeypatch.setenv(_ENV_VAR, "  codex  ")
        assert resolve_client_profile() == "codex"


# ---------------------------------------------------------------------------
# emit_tool_call — writes valid event
# ---------------------------------------------------------------------------


class TestEmitToolCall:
    def test_writes_valid_event(self, tmp_path, monkeypatch):
        """emit_tool_call writes a pull_tool_call event to the JSONL log."""
        log_path = tmp_path / "channel-events.jsonl"
        monkeypatch.delenv(_ENV_VAR, raising=False)

        with patch(
            "trw_mcp.channels._distill_telemetry.append_channel_event"
        ) as mock_append:
            emit_tool_call(tool_name="trw_before_edit_hint", file_path="backend/app.py")
            mock_append.assert_called_once()
            call_kwargs = mock_append.call_args.kwargs
            assert call_kwargs["event_type"] == "pull_tool_call"
            assert call_kwargs["client"] == _UNKNOWN_CLIENT

    def test_uses_provided_client(self, monkeypatch):
        monkeypatch.delenv(_ENV_VAR, raising=False)
        with patch(
            "trw_mcp.channels._distill_telemetry.append_channel_event"
        ) as mock_append:
            emit_tool_call(tool_name="trw_entity_risk_map", client="cursor-ide")
            call_kwargs = mock_append.call_args.kwargs
            assert call_kwargs["client"] == "cursor-ide"

    def test_resolves_client_from_env_when_not_provided(self, monkeypatch):
        monkeypatch.setenv(_ENV_VAR, "opencode")
        with patch(
            "trw_mcp.channels._distill_telemetry.append_channel_event"
        ) as mock_append:
            emit_tool_call(tool_name="trw_codebase_risk_report")
            call_kwargs = mock_append.call_args.kwargs
            assert call_kwargs["client"] == "opencode"

    def test_includes_tool_name_in_extra(self, monkeypatch):
        monkeypatch.delenv(_ENV_VAR, raising=False)
        with patch(
            "trw_mcp.channels._distill_telemetry.append_channel_event"
        ) as mock_append:
            emit_tool_call(tool_name="trw_before_edit_hint")
            call_kwargs = mock_append.call_args.kwargs
            extra = call_kwargs.get("extra", {})
            assert extra.get("tool_name") == "trw_before_edit_hint"

    def test_passes_record_ids(self, monkeypatch):
        monkeypatch.delenv(_ENV_VAR, raising=False)
        ids = ["hotspot:backend/app.py@a1b2c3d4", "convention:yaml-safe"]
        with patch(
            "trw_mcp.channels._distill_telemetry.append_channel_event"
        ) as mock_append:
            emit_tool_call(tool_name="trw_entity_risk_map", record_ids=ids)
            call_kwargs = mock_append.call_args.kwargs
            assert call_kwargs["record_ids"] == ids


# ---------------------------------------------------------------------------
# emit_tool_call — fail-open behavior
# ---------------------------------------------------------------------------


class TestEmitToolCallFailOpen:
    def test_does_not_raise_on_oserror(self, monkeypatch):
        """emit_tool_call must not raise even when append_channel_event fails."""
        with patch(
            "trw_mcp.channels._distill_telemetry.append_channel_event",
            side_effect=OSError("disk full"),
        ):
            # Must not raise
            emit_tool_call(tool_name="trw_before_edit_hint")

    def test_does_not_raise_on_permission_error(self, monkeypatch):
        with patch(
            "trw_mcp.channels._distill_telemetry.append_channel_event",
            side_effect=PermissionError("read-only filesystem"),
        ):
            emit_tool_call(tool_name="trw_codebase_risk_report")

    def test_does_not_raise_on_runtime_error(self, monkeypatch):
        with patch(
            "trw_mcp.channels._distill_telemetry.append_channel_event",
            side_effect=RuntimeError("unexpected"),
        ):
            emit_tool_call(tool_name="trw_entity_risk_map")

    def test_returns_none(self, monkeypatch):
        with patch(
            "trw_mcp.channels._distill_telemetry.append_channel_event"
        ):
            result = emit_tool_call(tool_name="trw_before_edit_hint")
        assert result is None


# ---------------------------------------------------------------------------
# Integration: emit writes to actual JSONL via real append_channel_event
# ---------------------------------------------------------------------------


class TestEmitToolCallIntegration:
    def test_real_write_produces_valid_jsonl(self, tmp_path, monkeypatch):
        """Round-trip: emit_tool_call → real append_channel_event → valid JSONL."""
        log_path = tmp_path / "channel-events.jsonl"
        monkeypatch.delenv(_ENV_VAR, raising=False)

        # Patch only the log_path default so we write to tmp
        with patch(
            "trw_mcp.channels._telemetry._resolve_log_path",
            return_value=log_path,
        ):
            emit_tool_call(tool_name="trw_before_edit_hint", file_path="foo.py")

        lines = [ln for ln in log_path.read_text().splitlines() if ln.strip()]
        assert len(lines) == 1
        event = json.loads(lines[0])
        assert event["event_type"] == "pull_tool_call"
        assert event["schema_version"] == "channel-event/v1"
        assert "channel_id" in event
        assert "ts" in event
