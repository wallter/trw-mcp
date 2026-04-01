"""Tests for trw_mcp.state.source_detection — PRD-CORE-099.

Covers client profile detection, model ID detection, env var signals,
filesystem markers, and provider-prefix stripping.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from trw_mcp.state.source_detection import (
    _parse_opencode_model,
    detect_client_profile,
    detect_model_id,
)


# ---------------------------------------------------------------------------
# detect_client_profile
# ---------------------------------------------------------------------------


class TestDetectClientProfile:
    """Client profile detection from env vars and filesystem."""

    def test_claude_code_env(self) -> None:
        with patch.dict("os.environ", {"CLAUDE_CODE_VERSION": "1.2.3"}, clear=False):
            assert detect_client_profile() == "claude-code"

    def test_claude_code_entrypoint(self) -> None:
        with patch.dict("os.environ", {"CLAUDE_CODE_ENTRYPOINT": "/usr/bin/claude"}, clear=False):
            assert detect_client_profile() == "claude-code"

    def test_codex_env(self) -> None:
        with patch.dict("os.environ", {"CODEX_CLI_VERSION": "0.5.0"}, clear=True):
            assert detect_client_profile() == "codex"

    def test_cursor_env(self) -> None:
        with patch.dict("os.environ", {"CURSOR_TRACE_ID": "abc123"}, clear=True):
            assert detect_client_profile() == "cursor"

    def test_aider_env(self) -> None:
        with patch.dict("os.environ", {"AIDER_MODEL": "claude-sonnet-4-6"}, clear=True):
            assert detect_client_profile() == "aider"

    def test_opencode_env(self) -> None:
        with patch.dict("os.environ", {"OPENCODE_MODEL": "anthropic/claude-sonnet-4-6"}, clear=True):
            assert detect_client_profile() == "opencode"

    def test_opencode_filesystem(self, tmp_path: Path) -> None:
        oc_dir = tmp_path / ".opencode"
        oc_dir.mkdir()
        (oc_dir / "opencode.json").write_text("{}")
        # Clear env to force filesystem fallback
        with patch.dict("os.environ", {}, clear=True):
            assert detect_client_profile(cwd=tmp_path) == "opencode"

    def test_aider_filesystem(self, tmp_path: Path) -> None:
        (tmp_path / ".aider.conf.yml").write_text("model: claude-sonnet-4-6")
        with patch.dict("os.environ", {}, clear=True):
            assert detect_client_profile(cwd=tmp_path) == "aider"

    def test_unknown_returns_empty(self, tmp_path: Path) -> None:
        with patch.dict("os.environ", {}, clear=True):
            assert detect_client_profile(cwd=tmp_path) == ""

    def test_priority_claude_code_over_opencode(self) -> None:
        """When both Claude Code and OpenCode env vars are set, Claude Code wins."""
        with patch.dict(
            "os.environ",
            {"CLAUDE_CODE_VERSION": "1.2.3", "OPENCODE_MODEL": "some-model"},
            clear=False,
        ):
            assert detect_client_profile() == "claude-code"

    def test_empty_env_value_ignored(self) -> None:
        """Empty string env var should not trigger detection."""
        with patch.dict("os.environ", {"CLAUDE_CODE_VERSION": ""}, clear=True):
            result = detect_client_profile(cwd=Path("/nonexistent"))
            assert result == ""


# ---------------------------------------------------------------------------
# detect_model_id
# ---------------------------------------------------------------------------


class TestDetectModelId:
    """Model ID detection from env vars and config files."""

    def test_claude_model_env(self) -> None:
        with patch.dict("os.environ", {"CLAUDE_MODEL": "claude-opus-4-6"}, clear=True):
            assert detect_model_id() == "claude-opus-4-6"

    def test_anthropic_model_env(self) -> None:
        with patch.dict("os.environ", {"ANTHROPIC_MODEL": "claude-sonnet-4-6"}, clear=True):
            assert detect_model_id() == "claude-sonnet-4-6"

    def test_opencode_model_env(self) -> None:
        with patch.dict("os.environ", {"OPENCODE_MODEL": "anthropic/claude-sonnet-4-6"}, clear=True):
            assert detect_model_id() == "claude-sonnet-4-6"

    def test_aider_model_env(self) -> None:
        with patch.dict("os.environ", {"AIDER_MODEL": "claude-sonnet-4-6"}, clear=True):
            assert detect_model_id() == "claude-sonnet-4-6"

    def test_openai_model_env(self) -> None:
        with patch.dict("os.environ", {"OPENAI_MODEL": "gpt-4o"}, clear=True):
            assert detect_model_id() == "gpt-4o"

    def test_provider_prefix_stripped(self) -> None:
        with patch.dict("os.environ", {"CLAUDE_MODEL": "anthropic/claude-opus-4-6"}, clear=True):
            assert detect_model_id() == "claude-opus-4-6"

    def test_opencode_config_fallback(self, tmp_path: Path) -> None:
        oc_dir = tmp_path / ".opencode"
        oc_dir.mkdir()
        config = {"model": "anthropic/claude-sonnet-4-6"}
        (oc_dir / "opencode.json").write_text(json.dumps(config))
        with patch.dict("os.environ", {}, clear=True):
            assert detect_model_id(cwd=tmp_path) == "claude-sonnet-4-6"

    def test_opencode_config_no_slash(self, tmp_path: Path) -> None:
        oc_dir = tmp_path / ".opencode"
        oc_dir.mkdir()
        config = {"model": "claude-sonnet-4-6"}
        (oc_dir / "opencode.json").write_text(json.dumps(config))
        with patch.dict("os.environ", {}, clear=True):
            assert detect_model_id(cwd=tmp_path) == "claude-sonnet-4-6"

    def test_unknown_returns_empty(self, tmp_path: Path) -> None:
        with patch.dict("os.environ", {}, clear=True):
            assert detect_model_id(cwd=tmp_path) == ""

    def test_priority_claude_model_over_opencode(self) -> None:
        with patch.dict(
            "os.environ",
            {"CLAUDE_MODEL": "claude-opus-4-6", "OPENCODE_MODEL": "anthropic/claude-sonnet-4-6"},
            clear=True,
        ):
            assert detect_model_id() == "claude-opus-4-6"

    def test_whitespace_only_env_ignored(self) -> None:
        with patch.dict("os.environ", {"CLAUDE_MODEL": "  "}, clear=True):
            result = detect_model_id(cwd=Path("/nonexistent"))
            assert result == ""


# ---------------------------------------------------------------------------
# _parse_opencode_model
# ---------------------------------------------------------------------------


class TestParseOpencodeModel:
    """OpenCode config file parsing edge cases."""

    def test_root_opencode_json(self, tmp_path: Path) -> None:
        config = {"model": "openai/gpt-4o"}
        (tmp_path / "opencode.json").write_text(json.dumps(config))
        assert _parse_opencode_model(tmp_path) == "gpt-4o"

    def test_missing_model_field(self, tmp_path: Path) -> None:
        oc_dir = tmp_path / ".opencode"
        oc_dir.mkdir()
        (oc_dir / "opencode.json").write_text("{}")
        assert _parse_opencode_model(tmp_path) == ""

    def test_invalid_json(self, tmp_path: Path) -> None:
        oc_dir = tmp_path / ".opencode"
        oc_dir.mkdir()
        (oc_dir / "opencode.json").write_text("not json")
        assert _parse_opencode_model(tmp_path) == ""

    def test_no_config_file(self, tmp_path: Path) -> None:
        assert _parse_opencode_model(tmp_path) == ""

    def test_model_is_not_string(self, tmp_path: Path) -> None:
        oc_dir = tmp_path / ".opencode"
        oc_dir.mkdir()
        config = {"model": 123}
        (oc_dir / "opencode.json").write_text(json.dumps(config))
        assert _parse_opencode_model(tmp_path) == ""
