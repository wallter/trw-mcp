"""Tests for PRD-FIX-065 — DevEx & Documentation improvements.

Covers:
- FR01: CONTRIBUTING.md exists and has key sections
- FR02: README config example
- FR03: README debugging section
- FR04: Tool docstring "See Also" lines
- FR05: CLI typo suggestion
- FR06: Init-project success message
"""

from __future__ import annotations

import argparse
from pathlib import Path
from unittest.mock import patch

import pytest

# ── FR01: CONTRIBUTING.md ────────────────────────────────────────────


class TestContributing:
    """FR01: CONTRIBUTING.md must exist with key content."""

    @pytest.fixture()
    def contributing(self) -> str:
        path = Path(__file__).resolve().parent.parent / "CONTRIBUTING.md"
        assert path.exists(), "CONTRIBUTING.md must exist at trw-mcp root"
        return path.read_text(encoding="utf-8")

    def test_has_prerequisites(self, contributing: str) -> None:
        assert "Python 3.10" in contributing

    def test_has_dev_install(self, contributing: str) -> None:
        assert 'pip install -e ".[dev]"' in contributing

    def test_has_test_commands(self, contributing: str) -> None:
        assert "pytest tests/" in contributing
        assert "mypy --strict" in contributing

    def test_has_architecture_overview(self, contributing: str) -> None:
        assert "tools/" in contributing
        assert "state/" in contributing
        assert "models/" in contributing

    def test_has_module_size_rule(self, contributing: str) -> None:
        assert "500" in contributing
        assert "800" in contributing

    def test_has_commit_format(self, contributing: str) -> None:
        assert "feat(" in contributing
        assert "WHY:" in contributing

    def test_has_error_handling_convention(self, contributing: str) -> None:
        assert "justified:" in contributing

    def test_concise(self, contributing: str) -> None:
        lines = contributing.strip().split("\n")
        assert len(lines) <= 160, f"CONTRIBUTING.md too long: {len(lines)} lines (max 160)"


# ── FR02: README config example ─────────────────────────────────────


class TestReadmeConfig:
    """FR02: README must have a Configuration section with example config."""

    @pytest.fixture()
    def readme(self) -> str:
        path = Path(__file__).resolve().parent.parent / "README.md"
        return path.read_text(encoding="utf-8")

    def test_has_config_yaml_example(self, readme: str) -> None:
        assert "embeddings_enabled:" in readme
        assert "learning_max_entries:" in readme
        assert "ceremony_mode:" in readme

    def test_has_config_section(self, readme: str) -> None:
        assert ".trw/config.yaml" in readme


# ── FR03: README debugging section ──────────────────────────────────


class TestReadmeDebugging:
    """FR03: README must have a Debugging subsection."""

    @pytest.fixture()
    def readme(self) -> str:
        path = Path(__file__).resolve().parent.parent / "README.md"
        return path.read_text(encoding="utf-8")

    def test_has_debugging_heading(self, readme: str) -> None:
        assert "### Debugging" in readme

    def test_has_debug_command(self, readme: str) -> None:
        assert "trw-mcp --debug serve" in readme

    def test_has_env_var_example(self, readme: str) -> None:
        assert "TRW_LOG_LEVEL=DEBUG" in readme

    def test_has_log_path(self, readme: str) -> None:
        assert ".trw/logs/" in readme


# ── FR04: Tool docstring "See Also" lines ───────────────────────────


class TestToolDocstringSeeAlso:
    """FR04: Core tool docstrings must have See Also cross-references."""

    def test_trw_learn_see_also(self) -> None:
        from tests.conftest import get_tools_sync, make_test_server

        server = make_test_server("learning")
        tools = get_tools_sync(server)
        desc = tools["trw_learn"].description or ""
        assert "See Also:" in desc
        assert "trw_recall" in desc
        assert "trw_learn_update" in desc

    def test_trw_recall_see_also(self) -> None:
        from tests.conftest import get_tools_sync, make_test_server

        server = make_test_server("learning")
        tools = get_tools_sync(server)
        desc = tools["trw_recall"].description or ""
        assert "See Also:" in desc
        assert "trw_learn" in desc
        assert "trw_learn" in desc

    def test_trw_session_start_see_also(self) -> None:
        from tests.conftest import get_tools_sync, make_test_server

        server = make_test_server("ceremony")
        tools = get_tools_sync(server)
        desc = tools["trw_session_start"].description or ""
        assert "See Also:" in desc
        assert "trw_init" in desc
        assert "trw_recall" in desc

    def test_trw_deliver_see_also(self) -> None:
        from tests.conftest import get_tools_sync, make_test_server

        server = make_test_server("ceremony")
        tools = get_tools_sync(server)
        desc = tools["trw_deliver"].description or ""
        assert "See Also:" in desc
        assert "trw_checkpoint" in desc
        assert "trw_claude_md_sync" in desc

    def test_trw_prd_create_see_also(self) -> None:
        from tests.conftest import get_tools_sync, make_test_server

        server = make_test_server("requirements")
        tools = get_tools_sync(server)
        desc = tools["trw_prd_create"].description or ""
        assert "See Also:" in desc
        assert "trw_prd_validate" in desc


# ── FR05: CLI typo suggestion ────────────────────────────────────────


class TestCliTypoSuggestion:
    """FR05: CLI must suggest closest command on typo."""

    def test_suggest_closest_match(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Typing 'serv' should suggest 'serve'."""
        from trw_mcp.server._cli import _build_arg_parser, _suggest_command

        parser = _build_arg_parser()
        suggestion = _suggest_command("serv", parser)
        assert suggestion == "serve"

    def test_suggest_init(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Typing 'init' should suggest 'init-project'."""
        from trw_mcp.server._cli import _build_arg_parser, _suggest_command

        parser = _build_arg_parser()
        suggestion = _suggest_command("init", parser)
        assert suggestion == "init-project"

    def test_no_match_for_nonsense(self) -> None:
        """Completely unrelated input returns None."""
        from trw_mcp.server._cli import _build_arg_parser, _suggest_command

        parser = _build_arg_parser()
        suggestion = _suggest_command("zzzzzzz", parser)
        assert suggestion is None


# ── FR06: Init-project success message ──────────────────────────────


class TestInitProjectSuccessMessage:
    """FR06: init-project must print a success line on completion."""

    def test_success_message_printed(self, capsys: pytest.CaptureFixture[str]) -> None:
        """init-project should print success guidance when no errors."""
        from trw_mcp.server._subcommands import _run_init_project

        target = Path("/tmp/test-init-msg")

        mock_result: dict[str, object] = {"errors": [], "created": [], "updated": [], "preserved": []}

        # init_project is imported locally inside _run_init_project, so patch at source
        with patch("trw_mcp.bootstrap.init_project", return_value=mock_result):
            with pytest.raises(SystemExit) as exc_info:
                args = argparse.Namespace(
                    target_dir=str(target),
                    force=False,
                    source_package="",
                    test_path="",
                    runs_root=".trw/runs",
                    ide=None,
                )
                _run_init_project(args)
            assert exc_info.value.code == 0

        captured = capsys.readouterr()
        assert "TRW initialization complete" in captured.out
        assert str(target) in captured.out
