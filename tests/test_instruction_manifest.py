"""Tests for instruction-tool manifest sync & validation (PRD-CORE-135)."""

from __future__ import annotations

from pathlib import Path

import pytest

from trw_mcp.models.config._defaults import TOOL_PRESETS
from trw_mcp.state.claude_md._tool_manifest import (
    TOOL_DESCRIPTIONS,
    ToolEntry,
    check_instruction_tool_parity,
    render_tool_list,
    resolve_exposed_tools,
    validate_instruction_manifest,
)


# ---------------------------------------------------------------------------
# FR01: TOOL_DESCRIPTIONS mapping
# ---------------------------------------------------------------------------


class TestToolDescriptions:
    """TOOL_DESCRIPTIONS covers all tools and is well-formed."""

    def test_covers_all_preset_tools(self) -> None:
        """Every tool in TOOL_PRESETS['all'] has a description."""
        all_tools = set(TOOL_PRESETS["all"])
        described = set(TOOL_DESCRIPTIONS)
        assert all_tools == described, (
            f"Missing descriptions: {all_tools - described}, "
            f"Extra descriptions: {described - all_tools}"
        )

    def test_descriptions_are_nonempty_strings(self) -> None:
        """Every description is a non-empty string."""
        for tool, desc in TOOL_DESCRIPTIONS.items():
            assert isinstance(desc, str), f"{tool}: description is not a string"
            assert len(desc) > 5, f"{tool}: description too short: {desc!r}"

    def test_no_duplicate_descriptions(self) -> None:
        """No two tools share the exact same description."""
        seen: dict[str, str] = {}
        for tool, desc in TOOL_DESCRIPTIONS.items():
            if desc in seen:
                pytest.fail(f"{tool} and {seen[desc]} share description: {desc!r}")
            seen[desc] = tool


# ---------------------------------------------------------------------------
# FR01: resolve_exposed_tools
# ---------------------------------------------------------------------------


class TestResolveExposedTools:
    """resolve_exposed_tools returns the correct set for each mode."""

    def test_all_mode(self) -> None:
        result = resolve_exposed_tools("all")
        assert result == set(TOOL_PRESETS["all"])

    def test_core_mode(self) -> None:
        result = resolve_exposed_tools("core")
        assert result == set(TOOL_PRESETS["core"])

    def test_minimal_mode(self) -> None:
        result = resolve_exposed_tools("minimal")
        assert result == set(TOOL_PRESETS["minimal"])

    def test_custom_mode(self) -> None:
        custom = ["trw_learn", "trw_deliver"]
        result = resolve_exposed_tools("custom", custom_list=custom)
        assert result == {"trw_learn", "trw_deliver"}

    def test_unknown_mode_falls_back_to_all(self) -> None:
        result = resolve_exposed_tools("nonexistent")
        assert result == set(TOOL_PRESETS["all"])


# ---------------------------------------------------------------------------
# FR01: Conditional rendering
# ---------------------------------------------------------------------------


class TestRenderToolList:
    """render_tool_list filters by exposed_tools."""

    def test_none_renders_all(self) -> None:
        """exposed_tools=None renders all tools (backward compat)."""
        output = render_tool_list(None)
        for tool_name in TOOL_DESCRIPTIONS:
            assert tool_name in output

    def test_subset_omits_unexposed(self) -> None:
        """Only listed tools appear when exposed_tools is a subset."""
        exposed = {"trw_session_start", "trw_learn"}
        output = render_tool_list(exposed)
        assert "trw_session_start" in output
        assert "trw_learn" in output
        assert "trw_deliver" not in output
        assert "trw_build_check" not in output

    def test_empty_set_returns_empty(self) -> None:
        """Empty exposed set produces no output."""
        output = render_tool_list(set())
        assert output == ""


# ---------------------------------------------------------------------------
# FR01: render_agents_trw_section with exposed_tools
# ---------------------------------------------------------------------------


class TestConditionalSectionRendering:
    """render_agents_trw_section and render_codex_trw_section filter tools."""

    def test_agents_section_none_renders_all(self) -> None:
        """exposed_tools=None includes all tools."""
        from unittest.mock import patch

        with patch(
            "trw_mcp.state.claude_md._static_sections._load_analytics_counts",
            return_value=(10, 50),
        ):
            from trw_mcp.state.claude_md._static_sections import render_agents_trw_section

            output = render_agents_trw_section(exposed_tools=None)
            assert "trw_session_start" in output
            assert "trw_deliver" in output
            assert "trw_build_check" in output

    def test_agents_section_filters_tools(self) -> None:
        """Only exposed tools appear in the rendered section."""
        from unittest.mock import patch

        with patch(
            "trw_mcp.state.claude_md._static_sections._load_analytics_counts",
            return_value=(10, 50),
        ):
            from trw_mcp.state.claude_md._static_sections import render_agents_trw_section

            exposed = {"trw_session_start", "trw_learn"}
            output = render_agents_trw_section(exposed_tools=exposed)
            assert "trw_session_start" in output
            assert "trw_learn" in output
            assert "trw_build_check" not in output
            assert "trw_recall" not in output

    def test_codex_section_none_renders_all(self) -> None:
        """Codex section with None includes all tools."""
        from trw_mcp.state.claude_md._static_sections import render_codex_trw_section

        output = render_codex_trw_section(exposed_tools=None)
        assert "trw_session_start" in output
        assert "trw_deliver" in output

    def test_codex_section_filters_tools(self) -> None:
        """Codex section with subset omits unexposed tools."""
        from trw_mcp.state.claude_md._static_sections import render_codex_trw_section

        exposed = {"trw_session_start", "trw_checkpoint"}
        output = render_codex_trw_section(exposed_tools=exposed)
        assert "trw_session_start" in output
        assert "trw_checkpoint" in output
        assert "trw_build_check" not in output


# ---------------------------------------------------------------------------
# FR02: validate_instruction_manifest
# ---------------------------------------------------------------------------


class TestValidateInstructionManifest:
    """validate_instruction_manifest detects unexposed tool mentions."""

    def test_clean_manifest(self) -> None:
        """No mismatches when all mentioned tools are exposed."""
        text = "Use `trw_session_start()` and `trw_learn()` in your workflow."
        exposed = {"trw_session_start", "trw_learn"}
        assert validate_instruction_manifest(text, exposed) == []

    def test_unexposed_tools_detected(self) -> None:
        """Unexposed tool mentions are returned sorted."""
        text = (
            "Call trw_session_start() first, then trw_build_check() "
            "and trw_deliver() to finish."
        )
        exposed = {"trw_session_start"}
        result = validate_instruction_manifest(text, exposed)
        assert result == ["trw_build_check", "trw_deliver"]

    def test_unknown_trw_prefixed_names_ignored(self) -> None:
        """Names like trw_dir that aren't known tools are not flagged."""
        text = "Store data in .trw_dir and use trw_session_start()"
        exposed = {"trw_session_start"}
        # trw_dir is not a known tool, so it should not appear
        assert validate_instruction_manifest(text, exposed) == []

    def test_empty_text(self) -> None:
        """Empty text produces no mismatches."""
        assert validate_instruction_manifest("", {"trw_learn"}) == []

    def test_all_tools_exposed(self) -> None:
        """No mismatches when everything is in the exposed set."""
        text = "trw_learn trw_deliver trw_session_start"
        exposed = set(TOOL_DESCRIPTIONS)
        assert validate_instruction_manifest(text, exposed) == []


# ---------------------------------------------------------------------------
# FR03: Delivery gate R-08 — instruction-tool parity
# ---------------------------------------------------------------------------


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
        agents.write_text(
            "Call trw_session_start() then trw_build_check() and trw_deliver().\n"
        )
        exposed = {"trw_session_start"}
        result = check_instruction_tool_parity(tmp_path, exposed)
        assert result is not None
        assert "2 unexposed tool(s)" in result
        assert "trw_build_check" in result
        assert "trw_deliver" in result

    def test_read_error_returns_none(self, tmp_path: Path) -> None:
        """Returns None on read error (fail-open)."""
        agents = tmp_path / "AGENTS.md"
        agents.mkdir()  # directory, not file -- will cause OSError
        result = check_instruction_tool_parity(tmp_path, {"trw_learn"})
        assert result is None


# ---------------------------------------------------------------------------
# FR03: Delivery gate wiring in check_delivery_gates
# ---------------------------------------------------------------------------


class TestDeliveryGateR08Wiring:
    """The R-08 gate is wired into check_delivery_gates."""

    def test_gate_returns_warning_on_mismatch(self, tmp_path: Path) -> None:
        """_check_instruction_tool_parity_gate returns warning when tools mismatch."""
        from unittest.mock import MagicMock, patch

        # Set up project structure
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        agents = tmp_path / "AGENTS.md"
        agents.write_text("Use trw_build_check() for validation.\n")

        run_path = trw_dir / "runs" / "test-run"
        run_path.mkdir(parents=True)

        mock_config = MagicMock()
        mock_config.effective_tool_exposure_mode = "core"
        mock_config.tool_exposure_list = []

        # Patch at the source module since _check_instruction_tool_parity_gate
        # uses a local import of get_config
        with patch(
            "trw_mcp.models.config.get_config",
            return_value=mock_config,
        ):
            from trw_mcp.tools._delivery_helpers import _check_instruction_tool_parity_gate

            result = _check_instruction_tool_parity_gate(run_path)
            assert result is not None
            assert "trw_build_check" in result

    def test_gate_returns_none_for_all_mode(self, tmp_path: Path) -> None:
        """R-08 gate is a no-op when mode is 'all'."""
        from unittest.mock import MagicMock, patch

        run_path = tmp_path / ".trw" / "runs" / "test"
        run_path.mkdir(parents=True)

        mock_config = MagicMock()
        mock_config.effective_tool_exposure_mode = "all"

        with patch(
            "trw_mcp.models.config.get_config",
            return_value=mock_config,
        ):
            from trw_mcp.tools._delivery_helpers import _check_instruction_tool_parity_gate

            result = _check_instruction_tool_parity_gate(run_path)
            assert result is None


# ---------------------------------------------------------------------------
# P1-1 fix: CLI check-instructions handler tests
# ---------------------------------------------------------------------------


class TestCheckInstructionsCLI:
    """_run_check_instructions CLI handler produces correct exit codes."""

    def test_clean_exit_code_zero(self, tmp_path: Path) -> None:
        """Exit 0 when all instruction files are clean."""
        import argparse

        from unittest.mock import patch

        # Create a clean AGENTS.md
        agents = tmp_path / "AGENTS.md"
        agents.write_text("Use trw_session_start() and trw_learn().\n")

        args = argparse.Namespace(target_dir=str(tmp_path))

        mock_config = type("MockConfig", (), {
            "effective_tool_exposure_mode": "all",
            "tool_exposure_list": [],
        })()

        with patch("trw_mcp.models.config.TRWConfig", return_value=mock_config):
            from trw_mcp.server._subcommands import _run_check_instructions

            with pytest.raises(SystemExit) as exc_info:
                _run_check_instructions(args)
            assert exc_info.value.code == 0

    def test_mismatch_exit_code_one(self, tmp_path: Path) -> None:
        """Exit 1 when instruction files reference unexposed tools."""
        import argparse

        from unittest.mock import patch

        # Create AGENTS.md with unexposed tool
        agents = tmp_path / "AGENTS.md"
        agents.write_text("Use trw_build_check() for validation.\n")

        args = argparse.Namespace(target_dir=str(tmp_path))

        mock_config = type("MockConfig", (), {
            "effective_tool_exposure_mode": "core",
            "tool_exposure_list": [],
        })()

        with patch("trw_mcp.models.config.TRWConfig", return_value=mock_config):
            from trw_mcp.server._subcommands import _run_check_instructions

            with pytest.raises(SystemExit) as exc_info:
                _run_check_instructions(args)
            assert exc_info.value.code == 1

    def test_no_instruction_files_exit_zero(self, tmp_path: Path) -> None:
        """Exit 0 when no instruction files are present."""
        import argparse

        from unittest.mock import patch

        args = argparse.Namespace(target_dir=str(tmp_path))

        mock_config = type("MockConfig", (), {
            "effective_tool_exposure_mode": "all",
            "tool_exposure_list": [],
        })()

        with patch("trw_mcp.models.config.TRWConfig", return_value=mock_config):
            from trw_mcp.server._subcommands import _run_check_instructions

            with pytest.raises(SystemExit) as exc_info:
                _run_check_instructions(args)
            assert exc_info.value.code == 0


# ---------------------------------------------------------------------------
# Type safety: ToolEntry namedtuple
# ---------------------------------------------------------------------------


class TestToolEntry:
    """ToolEntry NamedTuple is well-formed."""

    def test_tool_entry_fields(self) -> None:
        entry = ToolEntry(name="trw_learn", description="Record discoveries")
        assert entry.name == "trw_learn"
        assert entry.description == "Record discoveries"

    def test_tool_entry_immutable(self) -> None:
        entry = ToolEntry(name="trw_learn", description="desc")
        with pytest.raises(AttributeError):
            entry.name = "changed"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Type safety: resolve_exposed_tools returns frozenset
# ---------------------------------------------------------------------------


class TestResolveExposedToolsFrozenset:
    """resolve_exposed_tools returns frozenset (immutable)."""

    def test_returns_frozenset(self) -> None:
        result = resolve_exposed_tools("all")
        assert isinstance(result, frozenset)

    def test_custom_returns_frozenset(self) -> None:
        result = resolve_exposed_tools("custom", custom_list=["trw_learn"])
        assert isinstance(result, frozenset)

    def test_standard_mode(self) -> None:
        result = resolve_exposed_tools("standard")
        assert result == frozenset(TOOL_PRESETS["standard"])


# ---------------------------------------------------------------------------
# FR02: validate_instruction_manifest edge cases
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# FR01: render_agents_trw_section actually filters tools from output
# ---------------------------------------------------------------------------


class TestAgentsSectionToolFiltering:
    """Verify render_agents_trw_section truly excludes unexposed tools."""

    def test_session_start_only_excludes_build_check_from_tool_list(self) -> None:
        """When only trw_session_start is exposed, tool list omits others."""
        from unittest.mock import patch

        with patch(
            "trw_mcp.state.claude_md._static_sections._load_analytics_counts",
            return_value=(5, 20),
        ):
            from trw_mcp.state.claude_md._static_sections import render_agents_trw_section

            output = render_agents_trw_section(exposed_tools={"trw_session_start"})
            # Tool list uses backtick format: `trw_name()`
            assert "`trw_session_start()`" in output
            # These should NOT appear in the tool list (backtick format)
            assert "`trw_build_check()`" not in output
            assert "`trw_review()`" not in output
            assert "`trw_recall()`" not in output

    def test_codex_section_filters_tools(self) -> None:
        """render_codex_trw_section with subset omits unexposed tools."""
        from trw_mcp.state.claude_md._static_sections import render_codex_trw_section

        exposed = {"trw_session_start", "trw_deliver"}
        output = render_codex_trw_section(exposed_tools=exposed)
        assert "trw_session_start" in output
        assert "trw_deliver" in output
        assert "trw_build_check" not in output
        assert "trw_recall" not in output


# ---------------------------------------------------------------------------
# FR03: Full delivery gate integration
# ---------------------------------------------------------------------------


class TestDeliveryGateFullIntegration:
    """Test R-08 gate through check_delivery_gates."""

    def test_instruction_parity_wired_in_check_delivery_gates(self, tmp_path: Path) -> None:
        """check_delivery_gates includes instruction_parity_warning when mismatch exists."""
        from unittest.mock import MagicMock, patch

        from trw_mcp.state.persistence import FileStateReader
        from trw_mcp.tools._delivery_helpers import check_delivery_gates

        # Set up project structure
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        agents = tmp_path / "AGENTS.md"
        agents.write_text("Use trw_build_check() for validation.\n")

        run_path = trw_dir / "runs" / "test-run"
        (run_path / "meta").mkdir(parents=True)

        mock_config = MagicMock()
        mock_config.effective_tool_exposure_mode = "core"
        mock_config.tool_exposure_list = []

        reader = FileStateReader()

        with patch("trw_mcp.models.config.get_config", return_value=mock_config):
            result = check_delivery_gates(run_path, reader)

        assert "instruction_parity_warning" in result
        assert "trw_build_check" in result["instruction_parity_warning"]

    def test_no_warning_when_all_mode(self, tmp_path: Path) -> None:
        """check_delivery_gates has no instruction_parity_warning in 'all' mode."""
        from unittest.mock import MagicMock, patch

        from trw_mcp.state.persistence import FileStateReader
        from trw_mcp.tools._delivery_helpers import check_delivery_gates

        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        agents = tmp_path / "AGENTS.md"
        agents.write_text("Use trw_build_check() for validation.\n")

        run_path = trw_dir / "runs" / "test-run"
        (run_path / "meta").mkdir(parents=True)

        mock_config = MagicMock()
        mock_config.effective_tool_exposure_mode = "all"

        reader = FileStateReader()

        with patch("trw_mcp.models.config.get_config", return_value=mock_config):
            result = check_delivery_gates(run_path, reader)

        assert "instruction_parity_warning" not in result


# ---------------------------------------------------------------------------
# FR03: check_instruction_tool_parity — UnicodeDecodeError handling
# ---------------------------------------------------------------------------


class TestCheckInstructionToolParityEdgeCases:
    """Edge cases for the parity checker."""

    def test_non_utf8_file_returns_none(self, tmp_path: Path) -> None:
        """Non-UTF-8 AGENTS.md is handled gracefully (fail-open)."""
        agents = tmp_path / "AGENTS.md"
        agents.write_bytes(b"\xff\xfe" + b"\x00" * 100)
        result = check_instruction_tool_parity(tmp_path, frozenset({"trw_learn"}))
        assert result is None


# ---------------------------------------------------------------------------
# CLI: _check_instructions_core testability
# ---------------------------------------------------------------------------


class TestCheckInstructionsCore:
    """Test the separated core logic directly (no sys.exit)."""

    def test_returns_zero_no_files(self, tmp_path: Path) -> None:
        from unittest.mock import patch

        mock_config = type("MockConfig", (), {
            "effective_tool_exposure_mode": "all",
            "tool_exposure_list": [],
        })()

        with patch("trw_mcp.models.config.TRWConfig", return_value=mock_config):
            from trw_mcp.server._subcommands import _check_instructions_core

            exit_code, mismatches = _check_instructions_core(tmp_path)
            assert exit_code == 0
            assert mismatches == {}

    def test_returns_one_on_mismatch(self, tmp_path: Path) -> None:
        from unittest.mock import patch

        agents = tmp_path / "AGENTS.md"
        agents.write_text("Use trw_build_check() here.\n")

        mock_config = type("MockConfig", (), {
            "effective_tool_exposure_mode": "core",
            "tool_exposure_list": [],
        })()

        with patch("trw_mcp.models.config.TRWConfig", return_value=mock_config):
            from trw_mcp.server._subcommands import _check_instructions_core

            exit_code, mismatches = _check_instructions_core(tmp_path)
            assert exit_code == 1
            assert "AGENTS.md" in mismatches
            assert "trw_build_check" in mismatches["AGENTS.md"]
