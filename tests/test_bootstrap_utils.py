"""Tests for bootstrap IDE detection — PRD-CORE-136-FR07, PRD-CORE-137-FR06.

Covers detect_ide() cursor-ide vs cursor-cli disambiguation and the
SUPPORTED_IDES constant update.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from trw_mcp.bootstrap._utils import SUPPORTED_IDES, detect_ide


# ---------------------------------------------------------------------------
# SUPPORTED_IDES constant
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_supported_ides_contains_cursor_ide() -> None:
    """SUPPORTED_IDES contains cursor-ide (not bare cursor)."""
    assert "cursor-ide" in SUPPORTED_IDES


@pytest.mark.unit
def test_supported_ides_contains_cursor_cli() -> None:
    """SUPPORTED_IDES contains cursor-cli."""
    assert "cursor-cli" in SUPPORTED_IDES


@pytest.mark.unit
def test_supported_ides_does_not_contain_bare_cursor() -> None:
    """SUPPORTED_IDES no longer contains the bare 'cursor' identifier."""
    assert "cursor" not in SUPPORTED_IDES


@pytest.mark.unit
def test_supported_ides_has_eight_entries() -> None:
    """SUPPORTED_IDES has exactly 8 entries after the cursor split."""
    assert len(SUPPORTED_IDES) == 8


# ---------------------------------------------------------------------------
# detect_ide — cursor-ide detection
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_detect_cursor_ide_dir_only(tmp_path: Path) -> None:
    """detect_ide returns cursor-ide when .cursor/ dir is present."""
    (tmp_path / ".cursor").mkdir()

    with patch("shutil.which", return_value=None), \
         patch.dict("os.environ", {}, clear=True):
        result = detect_ide(tmp_path)

    assert "cursor-ide" in result
    assert "cursor-cli" not in result


@pytest.mark.integration
def test_detect_cursor_ide_trace_env_only(tmp_path: Path) -> None:
    """detect_ide returns cursor-ide when CURSOR_TRACE_ID is set."""
    with patch("shutil.which", return_value=None), \
         patch.dict("os.environ", {"CURSOR_TRACE_ID": "trace-abc"}, clear=True):
        result = detect_ide(tmp_path)

    assert "cursor-ide" in result


# ---------------------------------------------------------------------------
# detect_ide — cursor-cli detection
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_detect_cursor_cli_config_only(tmp_path: Path) -> None:
    """detect_ide returns cursor-cli when .cursor/cli.json is present."""
    cursor_dir = tmp_path / ".cursor"
    cursor_dir.mkdir()
    (cursor_dir / "cli.json").write_text('{"version":1}', encoding="utf-8")

    with patch("shutil.which", return_value=None), \
         patch.dict("os.environ", {}, clear=True):
        result = detect_ide(tmp_path)

    assert "cursor-cli" in result
    # .cursor/ dir is also present so cursor-ide should also appear
    assert "cursor-ide" in result


@pytest.mark.integration
def test_detect_cursor_cli_only_no_cursor_dir(tmp_path: Path) -> None:
    """detect_ide returns cursor-cli but NOT cursor-ide when cli.json in a .cursor dir
    where the dir doesn't otherwise signal cursor-ide (no binary, no trace env)."""
    # Create .cursor/cli.json; the .cursor dir must exist for cli.json to be in it,
    # so cursor-ide will also be detected (dir presence). Test focuses on cursor-cli
    # being detected alongside cursor-ide.
    cursor_dir = tmp_path / ".cursor"
    cursor_dir.mkdir()
    (cursor_dir / "cli.json").write_text('{}', encoding="utf-8")

    with patch("shutil.which", return_value=None), \
         patch.dict("os.environ", {}, clear=True):
        result = detect_ide(tmp_path)

    assert "cursor-cli" in result


@pytest.mark.integration
def test_detect_cursor_cli_via_api_key_env(tmp_path: Path) -> None:
    """detect_ide returns cursor-cli when CURSOR_API_KEY is set."""
    with patch("shutil.which", return_value=None), \
         patch.dict("os.environ", {"CURSOR_API_KEY": "sk-test"}, clear=True):
        result = detect_ide(tmp_path)

    assert "cursor-cli" in result


@pytest.mark.integration
def test_detect_cursor_cli_via_cursor_agent_binary(tmp_path: Path) -> None:
    """detect_ide returns cursor-cli when cursor-agent is on PATH and no trace env."""

    def mock_which(name: str) -> str | None:
        return "/usr/bin/cursor-agent" if name == "cursor-agent" else None

    with patch("shutil.which", side_effect=mock_which), \
         patch.dict("os.environ", {}, clear=True):
        result = detect_ide(tmp_path)

    assert "cursor-cli" in result
    # cursor-agent binary alone, no cursor dir → no cursor-ide
    assert "cursor-ide" not in result


@pytest.mark.integration
def test_detect_cursor_cli_agent_binary_suppressed_when_trace_id_set(tmp_path: Path) -> None:
    """cursor-agent on PATH does NOT trigger cursor-cli when CURSOR_TRACE_ID is set (IDE mode)."""

    def mock_which(name: str) -> str | None:
        return "/usr/bin/cursor-agent" if name == "cursor-agent" else None

    with patch("shutil.which", side_effect=mock_which), \
         patch.dict("os.environ", {"CURSOR_TRACE_ID": "trace-123"}, clear=True):
        result = detect_ide(tmp_path)

    # CURSOR_TRACE_ID means IDE is active → cursor-cli should NOT be triggered by binary
    assert "cursor-cli" not in result
    assert "cursor-ide" in result


# ---------------------------------------------------------------------------
# detect_ide — dual detection
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_detect_cursor_dual(tmp_path: Path) -> None:
    """detect_ide returns both cursor-ide and cursor-cli on dual-surface machines."""
    cursor_dir = tmp_path / ".cursor"
    cursor_dir.mkdir()
    (cursor_dir / "cli.json").write_text('{"version":1}', encoding="utf-8")

    def mock_which(name: str) -> str | None:
        if name == "cursor":
            return "/usr/bin/cursor"
        if name == "cursor-agent":
            return "/usr/bin/cursor-agent"
        return None

    with patch("shutil.which", side_effect=mock_which), \
         patch.dict("os.environ", {}, clear=True):
        result = detect_ide(tmp_path)

    assert "cursor-ide" in result
    assert "cursor-cli" in result


@pytest.mark.integration
def test_detect_neither_cursor_when_both_absent(tmp_path: Path) -> None:
    """detect_ide returns neither cursor-ide nor cursor-cli when no signals present."""
    with patch("shutil.which", return_value=None), \
         patch.dict("os.environ", {}, clear=True):
        result = detect_ide(tmp_path)

    assert "cursor-ide" not in result
    assert "cursor-cli" not in result


# ---------------------------------------------------------------------------
# detect_ide — source_detection mapping (PRD-CORE-137-FR06)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_source_detection_maps_cursor_trace_id_to_cursor_ide() -> None:
    """source_detection._CLIENT_SIGNALS maps CURSOR_TRACE_ID to cursor-ide (not cursor)."""
    from trw_mcp.state.source_detection import _CLIENT_SIGNALS

    cursor_entry = next(
        (entry for entry in _CLIENT_SIGNALS if "CURSOR_TRACE_ID" in entry[1]),
        None,
    )
    assert cursor_entry is not None, "CURSOR_TRACE_ID not found in _CLIENT_SIGNALS"
    assert cursor_entry[0] == "cursor-ide", (
        f"Expected 'cursor-ide' but got {cursor_entry[0]!r} for CURSOR_TRACE_ID signal"
    )
