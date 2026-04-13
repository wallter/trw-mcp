"""Server startup smoke tests — fast unit tests that catch silent crashes.

These tests verify the MCP server can import, initialize, and configure
without exceptions. They run in < 2 seconds and catch the exact class of
bug that caused the v0.11.3 silent startup failure on fresh installs.
"""

from __future__ import annotations

import io
import sys
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def _isolate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))


# ── Module imports (the exact failure chain) ─────────────────────────


@pytest.mark.unit
class TestModuleImports:
    """Verify the full import chain succeeds without exception."""

    def test_import_server_package(self) -> None:
        import trw_mcp.server

        assert trw_mcp.server is not None

    def test_import_app(self) -> None:
        from trw_mcp._logging import configure_logging
        from trw_mcp.server._app import configure_logging_compat, mcp

        assert configure_logging is not None
        assert configure_logging_compat is not None
        assert mcp is not None

    def test_import_cli(self) -> None:
        from trw_mcp.server._cli import _build_arg_parser, main

        assert _build_arg_parser is not None
        assert main is not None

    def test_import_tools(self) -> None:
        from trw_mcp.server._tools import _register_tools

        assert _register_tools is not None

    def test_import_all_tool_modules(self) -> None:
        from trw_mcp.tools import (
            build,
            ceremony,
            checkpoint,
            knowledge,
            learning,
            orchestration,
            report,
            requirements,
            review,
            usage,
        )

        assert all(
            m is not None
            for m in (build, ceremony, checkpoint, knowledge, learning, orchestration, report, requirements, review, usage)
        )

    def test_import_middleware(self) -> None:
        from trw_mcp.middleware.ceremony import CeremonyMiddleware

        assert CeremonyMiddleware is not None

    def test_import_messaging(self) -> None:
        from trw_mcp.prompts.messaging import get_message_or_default

        assert get_message_or_default is not None


# ── FastMCP instance ─────────────────────────────────────────────────


@pytest.mark.unit
class TestMcpInstance:
    """Verify the FastMCP server object is properly initialized."""

    def test_mcp_exists(self) -> None:
        from trw_mcp.server._app import mcp

        assert mcp is not None

    def test_mcp_name(self) -> None:
        from trw_mcp.server._app import mcp

        assert mcp.name == "trw"

    async def test_mcp_has_tools(self) -> None:
        from trw_mcp.server._app import mcp

        tools = await mcp.list_tools()
        assert len(tools) > 0, "No tools registered"

    async def test_mcp_has_expected_tools(self) -> None:
        from trw_mcp.server._app import mcp

        tools = await mcp.list_tools()
        tool_names = {t.name for t in tools}
        expected = {
            "trw_session_start",
            "trw_deliver",
            "trw_recall",
            "trw_learn",
            "trw_init",
            "trw_status",
            "trw_checkpoint",
            "trw_build_check",
        }
        missing = expected - tool_names
        assert not missing, f"Missing tools: {missing}"

    async def test_mcp_registers_review_tool(self) -> None:
        from trw_mcp.server._app import mcp

        tools = await mcp.list_tools()
        tool_names = {t.name for t in tools}
        assert {"trw_review"} <= tool_names


# ── .mcp.json command resolution ─────────────────────────────────────


@pytest.mark.unit
class TestMcpJsonCommand:
    """Verify .mcp.json generates a working command."""

    def test_entry_has_required_keys(self) -> None:
        from trw_mcp.bootstrap._utils import _trw_mcp_server_entry

        entry = _trw_mcp_server_entry()
        assert "command" in entry
        assert "args" in entry

    def test_fallback_uses_absolute_python(self) -> None:
        from trw_mcp.bootstrap._utils import _trw_mcp_server_entry

        with patch("shutil.which", return_value=None):
            entry = _trw_mcp_server_entry()
        cmd = str(entry["command"])
        assert cmd == sys.executable, f"Fallback should use sys.executable ({sys.executable}), got {cmd}"

    def test_fallback_uses_module_invocation(self) -> None:
        from trw_mcp.bootstrap._utils import _trw_mcp_server_entry

        with patch("shutil.which", return_value=None):
            entry = _trw_mcp_server_entry()
        args: Any = entry["args"]
        assert "-m" in args
        assert "trw_mcp.server" in args

    def test_prefers_trw_mcp_cli(self) -> None:
        from trw_mcp.bootstrap._utils import _trw_mcp_server_entry

        with patch("shutil.which", return_value="/usr/bin/trw-mcp"):
            entry = _trw_mcp_server_entry()
        assert entry["command"] == "trw-mcp"


# ── Crash wrapper ────────────────────────────────────────────────────


@pytest.mark.unit
class TestCrashWrapper:
    """Verify the __main__.py crash wrapper captures errors."""

    def test_crash_log_writes_stderr(self) -> None:
        from trw_mcp.server.__main__ import _crash_log

        err = RuntimeError("test boom")
        stderr = io.StringIO()
        with patch("sys.stderr", stderr):
            _crash_log(err)
        output = stderr.getvalue()
        assert "TRW MCP CRASH" in output
        assert "test boom" in output

    def test_crash_log_writes_file(self, tmp_path: Path) -> None:
        from trw_mcp.server.__main__ import _crash_log

        log_dir = tmp_path / ".trw" / "logs"
        log_dir.mkdir(parents=True)

        err = RuntimeError("file boom")
        with patch("pathlib.Path.cwd", return_value=tmp_path):
            _crash_log(err)

        crash_file = log_dir / "crash.log"
        assert crash_file.exists()
        content = crash_file.read_text()
        assert "file boom" in content

    def test_crash_log_survives_no_log_dir(self, tmp_path: Path) -> None:
        from trw_mcp.server.__main__ import _crash_log

        err = RuntimeError("no dir")
        # cwd is tmp_path with no .trw — should not raise
        with patch("pathlib.Path.cwd", return_value=tmp_path):
            _crash_log(err)


# ── Argument parser ──────────────────────────────────────────────────


@pytest.mark.unit
class TestArgParser:
    """Verify CLI argument parser accepts expected flags."""

    def test_parser_returns(self) -> None:
        from trw_mcp.server._cli import _build_arg_parser

        parser = _build_arg_parser()
        assert parser is not None

    def test_debug_flag(self) -> None:
        from trw_mcp.server._cli import _build_arg_parser

        args = _build_arg_parser().parse_args(["--debug"])
        assert args.debug is True

    def test_transport_choices(self) -> None:
        from trw_mcp.server._cli import _build_arg_parser

        for transport in ("stdio", "sse", "streamable-http"):
            args = _build_arg_parser().parse_args(["--transport", transport])
            assert args.transport == transport

    def test_subcommands(self) -> None:
        from trw_mcp.server._cli import _build_arg_parser

        for cmd in ("serve", "init-project", "update-project", "audit", "export"):
            args = _build_arg_parser().parse_args([cmd])
            assert args.command == cmd

    def test_init_project_accepts_codex_ide(self) -> None:
        from trw_mcp.server._cli import _build_arg_parser

        args = _build_arg_parser().parse_args(["init-project", ".", "--ide", "codex"])
        assert args.command == "init-project"
        assert args.ide == "codex"

    def test_update_project_accepts_codex_ide(self) -> None:
        from trw_mcp.server._cli import _build_arg_parser

        args = _build_arg_parser().parse_args(["update-project", ".", "--ide", "codex"])
        assert args.command == "update-project"
        assert args.ide == "codex"


@pytest.mark.unit
class TestCliSubcommandOutput:
    def test_update_project_plain_output_is_compact(
        self,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        from trw_mcp.server._subcommands import _run_update_project

        result = {
            "updated": [".codex/config.toml", ".codex/hooks.json", "AGENTS.md"],
            "created": [],
            "preserved": [".mcp.json"],
            "errors": [],
            "warnings": ["Example warning"],
            "cleaned": [],
        }

        args = type(
            "Args",
            (),
            {
                "target_dir": ".",
                "pip_install": False,
                "dry_run": False,
                "ide": "codex",
                "log_json": False,
                "debug": False,
                "verbose": 0,
                "quiet": False,
            },
        )()

        with (
            patch("trw_mcp.bootstrap.update_project", return_value=result),
            pytest.raises(SystemExit) as exc,
        ):
            _run_update_project(args)

        assert exc.value.code == 0
        captured = capsys.readouterr()
        assert "TRW update complete" in captured.out
        assert "Codex: managed config, hooks, agents, skills, and AGENTS.md synced" in captured.out
        assert "Use -v for per-file changes or --log-json for structured output." in captured.out
        assert "update_progress" not in captured.out

    def test_main_configures_logging_before_subcommand_dispatch(self) -> None:
        from trw_mcp.server._cli import main

        with (
            patch("sys.argv", ["trw-mcp", "update-project", "."]),
            patch("trw_mcp.server._cli.configure_logging") as mock_configure_logging,
            patch("trw_mcp.server._cli.SUBCOMMAND_HANDLERS", {"update-project": lambda args: None}),
        ):
            main()

        assert mock_configure_logging.called
        assert mock_configure_logging.call_args.kwargs["log_level"] == "WARNING"


# ── Logging configuration ────────────────────────────────────────────


@pytest.mark.unit
class TestLoggingConfig:
    """Verify logging can be configured without errors."""

    def test_configure_logging_normal(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from trw_mcp._logging import configure_logging

        monkeypatch.chdir(tmp_path)
        configure_logging(debug=False)

    def test_configure_logging_debug(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from trw_mcp._logging import configure_logging

        monkeypatch.chdir(tmp_path)
        configure_logging(debug=True, log_dir=tmp_path / ".trw" / "logs")
        log_dir = tmp_path / ".trw" / "logs"
        assert log_dir.is_dir(), "Debug logging should create .trw/logs/"

    def test_configure_logging_verbosity(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from trw_mcp._logging import configure_logging

        monkeypatch.chdir(tmp_path)
        # verbosity=1 → DEBUG level
        configure_logging(verbosity=1)

    def test_configure_logging_quiet(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from trw_mcp._logging import configure_logging

        monkeypatch.chdir(tmp_path)
        # verbosity=-1 → WARNING level (quiet mode)
        configure_logging(verbosity=-1)

    def test_configure_logging_env_override(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from trw_mcp._logging import configure_logging

        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("TRW_LOG_LEVEL", "ERROR")
        configure_logging()

    def test_configure_logging_json_output(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from trw_mcp._logging import configure_logging

        monkeypatch.chdir(tmp_path)
        configure_logging(json_output=True)

    def test_configure_logging_console_output(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from trw_mcp._logging import configure_logging

        monkeypatch.chdir(tmp_path)
        configure_logging(json_output=False)

    def test_configure_logging_compat(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Legacy configure_logging_compat wrapper still works."""
        from trw_mcp.models.config import TRWConfig
        from trw_mcp.server._app import configure_logging_compat

        monkeypatch.chdir(tmp_path)
        configure_logging_compat(debug=False, config=TRWConfig())


# ── Middleware ────────────────────────────────────────────────────────


@pytest.mark.unit
class TestMiddleware:
    """Verify middleware initializes without crashing."""

    def test_build_middleware_returns_list(self) -> None:
        from trw_mcp.server._app import _build_middleware

        result = _build_middleware()
        assert isinstance(result, list)

    def test_ceremony_middleware_instantiates(self) -> None:
        from trw_mcp.middleware.ceremony import CeremonyMiddleware

        mw = CeremonyMiddleware()
        assert mw is not None


# ── Message fallbacks ────────────────────────────────────────────────


@pytest.mark.unit
class TestMessageFallbacks:
    """Verify message loading falls back gracefully."""

    def test_missing_key_returns_default(self) -> None:
        from trw_mcp.prompts.messaging import get_message_or_default

        result = get_message_or_default("nonexistent_key_xyz", "fallback")
        assert result == "fallback"

    def test_broken_yaml_returns_default(self) -> None:
        from trw_mcp.prompts.messaging import get_message_or_default

        with patch("trw_mcp.prompts.messaging._load_messages", side_effect=ImportError("no ruamel")):
            result = get_message_or_default("any_key", "safe fallback")
        assert result == "safe fallback"
