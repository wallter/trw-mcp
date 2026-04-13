"""Unit tests for trw_mcp.state.source_detection — PRD-CORE-099.

Pure env-var tests only (no tmp_path, no filesystem). Classified as unit
tier so they run with ``make test-fast``.

Filesystem-based tests live in test_source_detection.py (integration tier).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from trw_mcp.state.source_detection import detect_client_profile, detect_model_id

# ---------------------------------------------------------------------------
# detect_client_profile — env var signals
# ---------------------------------------------------------------------------


class TestDetectClientProfileEnv:
    """Client profile detection from environment variables."""

    def test_claude_code_version(self) -> None:
        with patch.dict("os.environ", {"CLAUDE_CODE_VERSION": "1.2.3"}, clear=False):
            assert detect_client_profile() == "claude-code"

    def test_claude_code_entrypoint(self) -> None:
        with patch.dict("os.environ", {"CLAUDE_CODE_ENTRYPOINT": "/usr/bin/claude"}, clear=False):
            assert detect_client_profile() == "claude-code"

    def test_codex_cli_version(self) -> None:
        with patch.dict("os.environ", {"CODEX_CLI_VERSION": "0.5.0"}, clear=True):
            assert detect_client_profile() == "codex"

    def test_codex_sandbox_type(self) -> None:
        """Secondary codex signal also triggers detection."""
        with patch.dict("os.environ", {"CODEX_SANDBOX_TYPE": "docker"}, clear=True):
            assert detect_client_profile() == "codex"

    def test_cursor_trace_id(self) -> None:
        with patch.dict("os.environ", {"CURSOR_TRACE_ID": "abc123"}, clear=True):
            assert detect_client_profile() == "cursor-ide"

    def test_cursor_session_id(self) -> None:
        """Secondary cursor-ide signal also triggers detection."""
        with patch.dict("os.environ", {"CURSOR_SESSION_ID": "sess-1"}, clear=True):
            assert detect_client_profile() == "cursor-ide"

    def test_aider_model(self) -> None:
        with patch.dict("os.environ", {"AIDER_MODEL": "claude-sonnet-4-6"}, clear=True):
            assert detect_client_profile() == "aider"

    def test_aider_chat_history(self) -> None:
        """Secondary aider signal also triggers detection."""
        with patch.dict("os.environ", {"AIDER_CHAT_HISTORY_FILE": "/tmp/h.md"}, clear=True):
            assert detect_client_profile() == "aider"

    def test_opencode_model(self) -> None:
        with patch.dict("os.environ", {"OPENCODE_MODEL": "anthropic/claude-sonnet-4-6"}, clear=True):
            assert detect_client_profile() == "opencode"

    def test_opencode_config(self) -> None:
        """Secondary opencode signal also triggers detection."""
        with patch.dict("os.environ", {"OPENCODE_CONFIG": "/path/to/config"}, clear=True):
            assert detect_client_profile() == "opencode"

    def test_priority_claude_code_over_others(self) -> None:
        """Claude Code has highest priority when multiple signals present."""
        with patch.dict(
            "os.environ",
            {"CLAUDE_CODE_VERSION": "1.2.3", "OPENCODE_MODEL": "some-model", "AIDER_MODEL": "x"},
            clear=False,
        ):
            assert detect_client_profile() == "claude-code"

    def test_empty_env_value_ignored(self) -> None:
        """Empty string env var should not trigger detection."""
        with patch.dict("os.environ", {"CLAUDE_CODE_VERSION": ""}, clear=True):
            assert detect_client_profile(cwd=Path("/nonexistent")) == ""

    def test_unknown_env_returns_empty(self) -> None:
        """No matching env vars returns empty string."""
        with patch.dict("os.environ", {"UNRELATED_VAR": "hello"}, clear=True):
            assert detect_client_profile(cwd=Path("/nonexistent")) == ""


# ---------------------------------------------------------------------------
# detect_model_id — env var signals
# ---------------------------------------------------------------------------


class TestDetectModelIdEnv:
    """Model ID detection from environment variables."""

    def test_claude_model(self) -> None:
        with patch.dict("os.environ", {"CLAUDE_MODEL": "claude-opus-4-6"}, clear=True):
            assert detect_model_id() == "claude-opus-4-6"

    def test_anthropic_model(self) -> None:
        with patch.dict("os.environ", {"ANTHROPIC_MODEL": "claude-sonnet-4-6"}, clear=True):
            assert detect_model_id() == "claude-sonnet-4-6"

    def test_opencode_model_strips_provider(self) -> None:
        with patch.dict("os.environ", {"OPENCODE_MODEL": "anthropic/claude-sonnet-4-6"}, clear=True):
            assert detect_model_id() == "claude-sonnet-4-6"

    def test_aider_model(self) -> None:
        with patch.dict("os.environ", {"AIDER_MODEL": "claude-sonnet-4-6"}, clear=True):
            assert detect_model_id() == "claude-sonnet-4-6"

    def test_openai_model(self) -> None:
        with patch.dict("os.environ", {"OPENAI_MODEL": "gpt-4o"}, clear=True):
            assert detect_model_id() == "gpt-4o"

    def test_provider_prefix_stripped(self) -> None:
        with patch.dict("os.environ", {"CLAUDE_MODEL": "anthropic/claude-opus-4-6"}, clear=True):
            assert detect_model_id() == "claude-opus-4-6"

    def test_priority_claude_model_over_opencode(self) -> None:
        with patch.dict(
            "os.environ",
            {"CLAUDE_MODEL": "claude-opus-4-6", "OPENCODE_MODEL": "anthropic/claude-sonnet-4-6"},
            clear=True,
        ):
            assert detect_model_id() == "claude-opus-4-6"

    def test_whitespace_only_env_ignored(self) -> None:
        with patch.dict("os.environ", {"CLAUDE_MODEL": "  "}, clear=True):
            assert detect_model_id(cwd=Path("/nonexistent")) == ""

    def test_unknown_env_returns_empty(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            assert detect_model_id(cwd=Path("/nonexistent")) == ""
