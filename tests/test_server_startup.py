"""Server startup smoke tests — fast unit tests that catch silent crashes.

These tests verify the MCP server can import, initialize, and configure
without exceptions. They run in < 2 seconds and catch the exact class of
bug that caused the v0.11.3 silent startup failure on fresh installs.
"""

from __future__ import annotations

import io
import os
import subprocess
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

    def test_import_server_package_in_clean_interpreter(self, tmp_path: Path) -> None:
        """Catch fresh-process circular imports that in-process pytest can mask."""
        env = os.environ.copy()
        env["TRW_PROJECT_ROOT"] = str(tmp_path)
        result = subprocess.run(
            [sys.executable, "-c", "import trw_mcp.server; print('ok')"],
            cwd=Path(__file__).resolve().parents[1],
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "ok"

    def test_import_app(self) -> None:
        from trw_mcp._logging import configure_logging
        from trw_mcp.server._app import mcp

        assert configure_logging is not None
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
            requirements,
            review,
        )

        assert all(
            m is not None
            for m in (
                build,
                ceremony,
                checkpoint,
                knowledge,
                learning,
                orchestration,
                requirements,
                review,
            )
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

        # Use _list_tools to bypass the security middleware allowlist filter —
        # this test verifies *tool registration*, not authorization scope.
        tools = await mcp._list_tools()
        assert len(tools) > 0, "No tools registered"

    async def test_mcp_has_expected_tools(self) -> None:
        from trw_mcp.server._app import mcp

        tools = await mcp._list_tools()
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

        tools = await mcp._list_tools()
        tool_names = {t.name for t in tools}
        assert {"trw_review"} <= tool_names

    async def test_mcp_registers_probe_tools(self) -> None:
        """PRD-CORE-144: the empirical probe harness tools are wired into the
        production server surface (consumer-wiring proof for the harness)."""
        from trw_mcp.server._app import mcp

        tools = await mcp._list_tools()
        tool_names = {t.name for t in tools}
        assert {"trw_probe", "trw_probe_budget_status"} <= tool_names

    async def test_mcp_does_not_register_ceremony_feedback_tools(self) -> None:
        """PRD-FIX-076: the ceremony de-escalation kill-switch tools were
        deregistered from the MCP surface (dead — zero skill/agent/hook
        callers). The underlying state logic in
        ``trw_mcp.state.ceremony_feedback`` remains internal-only. Prior to
        FIX-076 (and FIX-051 before it) these were registered tools; this now
        asserts the inverse against the real production ``mcp`` instance.
        """
        from trw_mcp.server._app import mcp

        tools = await mcp._list_tools()
        tool_names = {t.name for t in tools}
        removed = {
            "trw_ceremony_status",
            "trw_ceremony_approve",
            "trw_ceremony_revert",
        }
        leaked = removed & tool_names
        assert not leaked, f"FIX-076 removed ceremony-feedback tools still registered: {leaked}"


# ── .mcp.json command resolution ─────────────────────────────────────


@pytest.mark.unit
class TestMcpJsonCommand:
    """Verify .mcp.json generates a working command."""

    def test_entry_has_required_keys(self) -> None:
        from trw_mcp.bootstrap._utils import _trw_mcp_server_entry

        entry = _trw_mcp_server_entry()
        assert "command" in entry
        assert "args" in entry

    def test_fallback_uses_portable_python(self) -> None:
        # PRD-SEC-006 / audit installer-client-12: the fallback must be a
        # PORTABLE ``python3`` (resolved per-machine via PATH), never the
        # build-machine-absolute ``sys.executable``, so .mcp.json stays portable.
        from trw_mcp.bootstrap._utils import _trw_mcp_server_entry

        with patch("shutil.which", return_value=None):
            entry = _trw_mcp_server_entry()
        cmd = str(entry["command"])
        assert cmd == "python3", f"Fallback should be portable python3, got {cmd}"
        assert not cmd.startswith("/")

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

    def test_main_runs_boot_sequence_and_stdio_transport(self) -> None:
        """``main()`` with no subcommand runs boot maintenance and starts stdio."""

        from trw_mcp.models.config import TRWConfig
        from trw_mcp.server._cli import main

        config = TRWConfig()

        with (
            patch("sys.argv", ["trw-mcp", "--debug"]),
            patch("trw_mcp.server._cli.get_config", return_value=config) as mock_get_config,
            patch("trw_mcp.server._cli.reload_config") as mock_reload_config,
            patch("trw_mcp.server._cli.configure_logging"),
            patch("trw_mcp.server._cli._check_mcp_json_portability"),
            patch("trw_mcp.server._cli._start_boot_sequence") as mock_boot_sequence,
            patch("trw_mcp.server._transport.resolve_and_run_transport") as mock_run_transport,
        ):
            main()

        mock_get_config.assert_called_once_with()
        mock_reload_config.assert_called_once_with(config)
        mock_boot_sequence.assert_called_once()
        mock_run_transport.assert_called_once()

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
        assert (
            "Codex: managed config uses [features].hooks; hooks, agents, skills, and AGENTS.md synced" in captured.out
        )
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
