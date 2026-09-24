"""Validation and parity tests for instruction manifest behavior."""

from __future__ import annotations

from pathlib import Path

import pytest

from trw_mcp.state.claude_md._tool_manifest import (
    TOOL_DESCRIPTIONS,
    check_instruction_tool_parity,
    validate_instruction_manifest,
)


class TestValidateInstructionManifest:
    """validate_instruction_manifest detects unexposed tool mentions."""

    def test_clean_manifest(self) -> None:
        """No mismatches when all mentioned tools are exposed."""
        text = "Use `trw_session_start()` and `trw_learn()` in your workflow."
        exposed = {"trw_session_start", "trw_learn"}
        assert validate_instruction_manifest(text, exposed) == []

    def test_unexposed_tools_detected(self) -> None:
        """Unexposed tool mentions are returned sorted."""
        text = "Call trw_session_start() first, then trw_build_check() and trw_deliver() to finish."
        exposed = {"trw_session_start"}
        result = validate_instruction_manifest(text, exposed)
        assert result == ["trw_build_check", "trw_deliver"]

    def test_unknown_trw_prefixed_names_ignored(self) -> None:
        """Names like trw_dir that aren't known tools are not flagged."""
        text = "Store data in .trw_dir and use trw_session_start()"
        exposed = {"trw_session_start"}
        assert validate_instruction_manifest(text, exposed) == []

    def test_empty_text(self) -> None:
        """Empty text produces no mismatches."""
        assert validate_instruction_manifest("", {"trw_learn"}) == []

    def test_all_tools_exposed(self) -> None:
        """No mismatches when everything is in the exposed set."""
        text = "trw_learn trw_deliver trw_session_start"
        exposed = set(TOOL_DESCRIPTIONS)
        assert validate_instruction_manifest(text, exposed) == []


class TestCheckInstructionToolParity:
    """check_instruction_tool_parity is the R-08 soft warning gate."""

    def test_no_agents_md(self, tmp_path: Path) -> None:
        """Returns None if AGENTS.md does not exist."""
        result = check_instruction_tool_parity(tmp_path, {"trw_learn"})
        assert result is None

    def test_clean_agents_md(self, tmp_path: Path) -> None:
        """Returns None when all mentioned tools are exposed."""
        agents = tmp_path / "AGENTS.md"
        agents.write_text("Use trw_session_start() and trw_learn().\n")
        exposed = {"trw_session_start", "trw_learn"}
        result = check_instruction_tool_parity(tmp_path, exposed)
        assert result is None

    def test_mismatch_returns_warning(self, tmp_path: Path) -> None:
        """Returns a warning string when unexposed tools are mentioned."""
        agents = tmp_path / "AGENTS.md"
        agents.write_text("Call trw_session_start() then trw_build_check() and trw_deliver().\n")
        exposed = {"trw_session_start"}
        result = check_instruction_tool_parity(tmp_path, exposed)
        assert result is not None
        assert "2 unexposed tool(s)" in result
        assert "trw_build_check" in result
        assert "trw_deliver" in result

    def test_read_error_returns_none(self, tmp_path: Path) -> None:
        """Returns None on read error (fail-open)."""
        agents = tmp_path / "AGENTS.md"
        agents.mkdir()
        result = check_instruction_tool_parity(tmp_path, {"trw_learn"})
        assert result is None


class TestValidateInstructionManifestEdgeCases:
    """Edge cases for the instruction manifest validator."""

    @pytest.mark.parametrize(
        "text,exposed,expected",
        [
            pytest.param(
                "Use trw_session_start() and trw_learn().",
                {"trw_session_start", "trw_learn"},
                [],
                id="happy_all_exposed",
            ),
            pytest.param(
                "Call trw_build_check() then trw_deliver().",
                {"trw_session_start"},
                ["trw_build_check", "trw_deliver"],
                id="two_mismatches",
            ),
            pytest.param(
                "Store in trw_dir and trw_config paths.",
                {"trw_session_start"},
                [],
                id="non_tool_trw_prefixed_ignored",
            ),
            pytest.param(
                "",
                {"trw_learn"},
                [],
                id="empty_text",
            ),
            pytest.param(
                "No tool mentions at all.",
                set(),
                [],
                id="no_trw_mentions",
            ),
        ],
    )
    def test_parametrized(self, text: str, exposed: set[str], expected: list[str]) -> None:
        assert validate_instruction_manifest(text, exposed) == expected

    def test_accepts_frozenset(self) -> None:
        """validate_instruction_manifest works with frozenset input."""
        text = "Use trw_session_start() and trw_build_check()."
        exposed = frozenset({"trw_session_start"})
        result = validate_instruction_manifest(text, exposed)
        assert "trw_build_check" in result


class TestCheckInstructionToolParityEdgeCases:
    """Edge cases for the parity checker."""

    def test_non_utf8_file_returns_none(self, tmp_path: Path) -> None:
        """Non-UTF-8 AGENTS.md is handled gracefully (fail-open)."""
        agents = tmp_path / "AGENTS.md"
        agents.write_bytes(b"\xff\xfe" + b"\x00" * 100)
        result = check_instruction_tool_parity(tmp_path, frozenset({"trw_learn"}))
        assert result is None
