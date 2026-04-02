"""Integration tests for trw_mcp.state.source_detection — PRD-CORE-099.

Filesystem-based tests (tmp_path required). Pure env-var tests live in
test_source_detection_unit.py (unit tier).
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from trw_mcp.state.source_detection import (
    _parse_opencode_model,
    detect_client_profile,
    detect_model_id,
)

# ---------------------------------------------------------------------------
# detect_client_profile — filesystem markers
# ---------------------------------------------------------------------------


class TestDetectClientProfileFilesystem:
    """Client detection via filesystem markers (no env vars)."""

    def test_opencode_directory(self, tmp_path: Path) -> None:
        oc_dir = tmp_path / ".opencode"
        oc_dir.mkdir()
        (oc_dir / "opencode.json").write_text("{}")
        with patch.dict("os.environ", {}, clear=True):
            assert detect_client_profile(cwd=tmp_path) == "opencode"

    def test_aider_conf_file(self, tmp_path: Path) -> None:
        (tmp_path / ".aider.conf.yml").write_text("model: claude-sonnet-4-6")
        with patch.dict("os.environ", {}, clear=True):
            assert detect_client_profile(cwd=tmp_path) == "aider"

    def test_no_signals_returns_empty(self, tmp_path: Path) -> None:
        with patch.dict("os.environ", {}, clear=True):
            assert detect_client_profile(cwd=tmp_path) == ""


# ---------------------------------------------------------------------------
# detect_model_id — config file fallback
# ---------------------------------------------------------------------------


class TestDetectModelIdFilesystem:
    """Model ID detection from config files (no env vars)."""

    def test_opencode_json_nested(self, tmp_path: Path) -> None:
        oc_dir = tmp_path / ".opencode"
        oc_dir.mkdir()
        config = {"model": "anthropic/claude-sonnet-4-6"}
        (oc_dir / "opencode.json").write_text(json.dumps(config))
        with patch.dict("os.environ", {}, clear=True):
            assert detect_model_id(cwd=tmp_path) == "claude-sonnet-4-6"

    def test_opencode_json_no_slash(self, tmp_path: Path) -> None:
        oc_dir = tmp_path / ".opencode"
        oc_dir.mkdir()
        config = {"model": "claude-sonnet-4-6"}
        (oc_dir / "opencode.json").write_text(json.dumps(config))
        with patch.dict("os.environ", {}, clear=True):
            assert detect_model_id(cwd=tmp_path) == "claude-sonnet-4-6"

    def test_no_config_returns_empty(self, tmp_path: Path) -> None:
        with patch.dict("os.environ", {}, clear=True):
            assert detect_model_id(cwd=tmp_path) == ""


# ---------------------------------------------------------------------------
# _parse_opencode_model — edge cases
# ---------------------------------------------------------------------------


class TestParseOpencodeModel:
    """OpenCode config file parsing edge cases."""

    def test_root_opencode_json(self, tmp_path: Path) -> None:
        config = {"model": "openai/gpt-4o"}
        (tmp_path / "opencode.json").write_text(json.dumps(config))
        assert _parse_opencode_model(tmp_path) == "gpt-4o"

    def test_nested_beats_root(self, tmp_path: Path) -> None:
        """When both .opencode/opencode.json and opencode.json exist, nested wins."""
        oc_dir = tmp_path / ".opencode"
        oc_dir.mkdir()
        (oc_dir / "opencode.json").write_text(json.dumps({"model": "anthropic/claude-opus-4-6"}))
        (tmp_path / "opencode.json").write_text(json.dumps({"model": "openai/gpt-4o"}))
        assert _parse_opencode_model(tmp_path) == "claude-opus-4-6"

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
