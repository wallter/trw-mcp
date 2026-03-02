"""Tests for MCP server transport configuration (PRD-CORE-070)."""

from __future__ import annotations

import argparse
import json
import socket
import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from trw_mcp.bootstrap import _merge_mcp_json, _trw_mcp_server_entry
from trw_mcp.models.config import TRWConfig, _reset_config


@pytest.fixture(autouse=True)
def _reset() -> None:
    """Reset config singleton between tests."""
    _reset_config()


# ── CLI argument parsing ─────────────────────────────────────────────────


@pytest.mark.unit
class TestTransportCLIArgs:
    """Verify transport CLI args are parsed correctly."""

    def test_default_transport_is_none(self) -> None:
        """Without --transport, args.transport is None (falls through to config)."""
        parser = argparse.ArgumentParser()
        parser.add_argument("--debug", action="store_true")
        parser.add_argument(
            "--transport",
            choices=["stdio", "sse", "streamable-http"],
            default=None,
        )
        parser.add_argument("--host", default=None)
        parser.add_argument("--port", type=int, default=None)
        args = parser.parse_args([])
        assert args.transport is None
        assert args.host is None
        assert args.port is None

    def test_transport_sse_parsed(self) -> None:
        """--transport sse is parsed correctly."""
        parser = argparse.ArgumentParser()
        parser.add_argument(
            "--transport",
            choices=["stdio", "sse", "streamable-http"],
            default=None,
        )
        parser.add_argument("--host", default=None)
        parser.add_argument("--port", type=int, default=None)
        args = parser.parse_args(["--transport", "sse", "--port", "9999"])
        assert args.transport == "sse"
        assert args.port == 9999

    def test_transport_streamable_http_parsed(self) -> None:
        """--transport streamable-http is parsed correctly."""
        parser = argparse.ArgumentParser()
        parser.add_argument(
            "--transport",
            choices=["stdio", "sse", "streamable-http"],
            default=None,
        )
        args = parser.parse_args(["--transport", "streamable-http"])
        assert args.transport == "streamable-http"

    def test_invalid_transport_rejected(self) -> None:
        """Invalid transport value raises SystemExit."""
        parser = argparse.ArgumentParser()
        parser.add_argument(
            "--transport",
            choices=["stdio", "sse", "streamable-http"],
            default=None,
        )
        with pytest.raises(SystemExit):
            parser.parse_args(["--transport", "http"])


# ── TRWConfig transport fields ───────────────────────────────────────────


@pytest.mark.unit
class TestConfigTransportFields:
    """Verify TRWConfig has transport fields with correct defaults."""

    def test_default_transport_is_stdio(self) -> None:
        config = TRWConfig()
        assert config.mcp_transport == "stdio"

    def test_default_host(self) -> None:
        config = TRWConfig()
        assert config.mcp_host == "127.0.0.1"

    def test_default_port(self) -> None:
        config = TRWConfig()
        assert config.mcp_port == 8100

    def test_env_var_override_transport(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """TRW_MCP_TRANSPORT env var overrides default."""
        monkeypatch.setenv("TRW_MCP_TRANSPORT", "sse")
        config = TRWConfig()
        assert config.mcp_transport == "sse"

    def test_env_var_override_port(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """TRW_MCP_PORT env var overrides default."""
        monkeypatch.setenv("TRW_MCP_PORT", "9999")
        config = TRWConfig()
        assert config.mcp_port == 9999

    def test_env_var_override_host(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """TRW_MCP_HOST env var overrides default."""
        monkeypatch.setenv("TRW_MCP_HOST", "0.0.0.0")
        config = TRWConfig()
        assert config.mcp_host == "0.0.0.0"


# ── Bootstrap always emits stdio (PRD-CORE-070-FR04) ────────────────────


@pytest.mark.unit
class TestBootstrapServerEntry:
    """Verify _trw_mcp_server_entry always generates stdio format."""

    def test_entry_has_command_and_args(self) -> None:
        entry = _trw_mcp_server_entry()
        assert "command" in entry
        assert "args" in entry

    def test_entry_has_no_type_field(self) -> None:
        """Stdio entries must not have a 'type' field."""
        entry = _trw_mcp_server_entry()
        assert "type" not in entry

    def test_args_include_debug(self) -> None:
        entry = _trw_mcp_server_entry()
        assert "--debug" in entry["args"]  # type: ignore[operator]

    def test_command_is_string(self) -> None:
        entry = _trw_mcp_server_entry()
        assert isinstance(entry["command"], str)


@pytest.mark.unit
class TestMergeMcpJsonTransport:
    """Verify _merge_mcp_json always emits stdio regardless of config."""

    def test_no_config_uses_stdio(self, tmp_path: Path) -> None:
        """Without .trw/config.yaml, generates stdio entry."""
        result: dict[str, list[str]] = {"created": [], "errors": []}
        _merge_mcp_json(tmp_path, result)

        mcp_path = tmp_path / ".mcp.json"
        data = json.loads(mcp_path.read_text(encoding="utf-8"))
        assert "command" in data["mcpServers"]["trw"]
        assert "type" not in data["mcpServers"]["trw"]

    def test_http_config_still_generates_stdio(self, tmp_path: Path) -> None:
        """Even with mcp_transport: streamable-http in config, generates stdio."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        config_path = trw_dir / "config.yaml"
        config_path.write_text(
            "mcp_transport: streamable-http\nmcp_port: 8200\n",
            encoding="utf-8",
        )

        result: dict[str, list[str]] = {"created": [], "errors": []}
        _merge_mcp_json(tmp_path, result)

        mcp_path = tmp_path / ".mcp.json"
        data = json.loads(mcp_path.read_text(encoding="utf-8"))
        entry = data["mcpServers"]["trw"]
        # Must be stdio format, not HTTP
        assert "command" in entry
        assert "type" not in entry

    def test_preserves_other_servers(self, tmp_path: Path) -> None:
        """Merging preserves non-trw server entries."""
        mcp_path = tmp_path / ".mcp.json"
        mcp_path.write_text(
            json.dumps({
                "mcpServers": {
                    "other-server": {"command": "other-cmd", "args": []},
                },
            }),
            encoding="utf-8",
        )

        result: dict[str, list[str]] = {"created": [], "errors": []}
        _merge_mcp_json(tmp_path, result)

        data = json.loads(mcp_path.read_text(encoding="utf-8"))
        assert "other-server" in data["mcpServers"]
        assert "trw" in data["mcpServers"]


# ── Port check ───────────────────────────────────────────────────────────


@pytest.mark.unit
class TestIsPortOpen:
    """Verify _is_port_open detects open/closed ports."""

    def test_closed_port_returns_false(self) -> None:
        from trw_mcp.server import _is_port_open

        # Use a high ephemeral port unlikely to be in use
        assert _is_port_open("127.0.0.1", 59123) is False

    def test_open_port_returns_true(self) -> None:
        from trw_mcp.server import _is_port_open

        # Bind a socket to verify detection
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as srv:
            srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            srv.bind(("127.0.0.1", 0))
            srv.listen(1)
            port = srv.getsockname()[1]
            assert _is_port_open("127.0.0.1", port) is True


# ── Auto-start (_ensure_http_server) ─────────────────────────────────────


@pytest.mark.unit
class TestEnsureHttpServer:
    """Verify auto-start logic for the shared HTTP server."""

    def _make_config(self, **overrides: Any) -> TRWConfig:
        defaults = {
            "mcp_transport": "streamable-http",
            "mcp_host": "127.0.0.1",
            "mcp_port": 59200,
        }
        defaults.update(overrides)
        return TRWConfig(**defaults)  # type: ignore[arg-type]

    def test_returns_url_when_port_already_open(self) -> None:
        from trw_mcp.server import _ensure_http_server

        config = self._make_config()
        log = MagicMock()

        with patch("trw_mcp.server._is_port_open", return_value=True):
            url = _ensure_http_server(config, log)

        assert url == "http://127.0.0.1:59200/mcp"
        log.info.assert_called_once()

    def test_spawns_subprocess_when_port_closed(self, tmp_path: Path) -> None:
        from trw_mcp.server import _ensure_http_server

        config = self._make_config()
        log = MagicMock()

        call_count = 0

        def port_check(host: str, port: int) -> bool:
            nonlocal call_count
            call_count += 1
            # First call (initial check): closed. Second call (poll): open
            return call_count > 1

        mock_proc = MagicMock()
        mock_proc.pid = 12345

        with (
            patch("trw_mcp.server._is_port_open", side_effect=port_check),
            patch("subprocess.Popen", return_value=mock_proc),
            patch("trw_mcp.server.Path.cwd", return_value=tmp_path),
            patch("time.sleep"),
        ):
            (tmp_path / ".trw" / "logs").mkdir(parents=True, exist_ok=True)
            url = _ensure_http_server(config, log)

        assert url == "http://127.0.0.1:59200/mcp"

    def test_returns_none_on_timeout(self, tmp_path: Path) -> None:
        from trw_mcp.server import _ensure_http_server

        config = self._make_config()
        log = MagicMock()

        mock_proc = MagicMock()
        mock_proc.pid = 99999

        with (
            patch("trw_mcp.server._is_port_open", return_value=False),
            patch("subprocess.Popen", return_value=mock_proc),
            patch("trw_mcp.server.Path.cwd", return_value=tmp_path),
            patch("time.sleep"),
        ):
            (tmp_path / ".trw" / "logs").mkdir(parents=True, exist_ok=True)
            url = _ensure_http_server(config, log)

        assert url is None
        log.warning.assert_called()

    def test_sse_transport_url(self) -> None:
        from trw_mcp.server import _ensure_http_server

        config = self._make_config(mcp_transport="sse")
        log = MagicMock()

        with patch("trw_mcp.server._is_port_open", return_value=True):
            url = _ensure_http_server(config, log)

        assert url == "http://127.0.0.1:59200/sse"

    def test_writes_pid_file(self, tmp_path: Path) -> None:
        from trw_mcp.server import _ensure_http_server

        config = self._make_config()
        log = MagicMock()

        call_count = 0

        def port_check(host: str, port: int) -> bool:
            nonlocal call_count
            call_count += 1
            return call_count > 1

        mock_proc = MagicMock()
        mock_proc.pid = 42

        with (
            patch("trw_mcp.server._is_port_open", side_effect=port_check),
            patch("subprocess.Popen", return_value=mock_proc),
            patch("trw_mcp.server.Path.cwd", return_value=tmp_path),
            patch("time.sleep"),
        ):
            (tmp_path / ".trw" / "logs").mkdir(parents=True, exist_ok=True)
            _ensure_http_server(config, log)

        pid_path = tmp_path / ".trw" / "mcp-server.pid"
        assert pid_path.exists()
        assert pid_path.read_text(encoding="utf-8") == "42"

    def test_returns_none_on_spawn_failure(self, tmp_path: Path) -> None:
        from trw_mcp.server import _ensure_http_server

        config = self._make_config()
        log = MagicMock()

        with (
            patch("trw_mcp.server._is_port_open", return_value=False),
            patch(
                "subprocess.Popen",
                side_effect=OSError("spawn failed"),
            ),
            patch("trw_mcp.server.Path.cwd", return_value=tmp_path),
        ):
            (tmp_path / ".trw" / "logs").mkdir(parents=True, exist_ok=True)
            url = _ensure_http_server(config, log)

        assert url is None


# ── Transport resolution (PRD-CORE-070-FR03) ────────────────────────────


@pytest.mark.unit
class TestTransportResolution:
    """Verify the 3-path transport resolution in main()."""

    def test_explicit_transport_uses_direct_mode(self) -> None:
        """Path 1: --transport flag → run as that transport directly."""
        from unittest.mock import call

        with (
            patch("trw_mcp.server.mcp") as mock_mcp,
            patch("trw_mcp.server.TRWConfig") as mock_config_cls,
            patch("trw_mcp.server._configure_logging"),
            patch(
                "trw_mcp.server.argparse.ArgumentParser.parse_args",
                return_value=argparse.Namespace(
                    debug=False,
                    transport="streamable-http",
                    host="0.0.0.0",
                    port=9000,
                    command=None,
                ),
            ),
        ):
            mock_config = MagicMock()
            mock_config.debug = False
            mock_config.mcp_host = "127.0.0.1"
            mock_config.mcp_port = 8100
            mock_config_cls.return_value = mock_config

            mock_mcp.settings = MagicMock()

            from trw_mcp.server import main

            main()

            mock_mcp.settings.__setattr__  # accessed
            mock_mcp.run.assert_called_once_with(transport="streamable-http")

    def test_no_flag_stdio_config_uses_standalone(self) -> None:
        """Path 2: No flag + stdio config → standalone stdio."""
        with (
            patch("trw_mcp.server.mcp") as mock_mcp,
            patch("trw_mcp.server.TRWConfig") as mock_config_cls,
            patch("trw_mcp.server._configure_logging"),
            patch(
                "trw_mcp.server.argparse.ArgumentParser.parse_args",
                return_value=argparse.Namespace(
                    debug=False,
                    transport=None,
                    host=None,
                    port=None,
                    command=None,
                ),
            ),
        ):
            mock_config = MagicMock()
            mock_config.debug = False
            mock_config.mcp_transport = "stdio"
            mock_config_cls.return_value = mock_config

            from trw_mcp.server import main

            main()

            mock_mcp.run.assert_called_once_with()

    def test_no_flag_http_config_auto_starts_and_proxies(self) -> None:
        """Path 3: No flag + HTTP config → auto-start + proxy."""
        with (
            patch("trw_mcp.server.TRWConfig") as mock_config_cls,
            patch("trw_mcp.server._configure_logging"),
            patch("trw_mcp.server._ensure_http_server") as mock_ensure,
            patch("asyncio.run") as mock_asyncio_run,
            patch(
                "trw_mcp.server.argparse.ArgumentParser.parse_args",
                return_value=argparse.Namespace(
                    debug=False,
                    transport=None,
                    host=None,
                    port=None,
                    command=None,
                ),
            ),
        ):
            mock_config = MagicMock()
            mock_config.debug = False
            mock_config.mcp_transport = "streamable-http"
            mock_config.mcp_host = "127.0.0.1"
            mock_config.mcp_port = 8100
            mock_config_cls.return_value = mock_config
            mock_ensure.return_value = "http://127.0.0.1:8100/mcp"

            from trw_mcp.server import main

            main()

            mock_ensure.assert_called_once()
            mock_asyncio_run.assert_called_once()


# ── Fallback (PRD-CORE-070-FR06) ────────────────────────────────────────


@pytest.mark.unit
class TestFallback:
    """Verify fallback to standalone stdio when HTTP server fails."""

    def test_fallback_on_server_start_failure(self) -> None:
        """When _ensure_http_server returns None, fall back to mcp.run()."""
        with (
            patch("trw_mcp.server.mcp") as mock_mcp,
            patch("trw_mcp.server.TRWConfig") as mock_config_cls,
            patch("trw_mcp.server._configure_logging"),
            patch("trw_mcp.server._ensure_http_server", return_value=None),
            patch(
                "trw_mcp.server.argparse.ArgumentParser.parse_args",
                return_value=argparse.Namespace(
                    debug=False,
                    transport=None,
                    host=None,
                    port=None,
                    command=None,
                ),
            ),
        ):
            mock_config = MagicMock()
            mock_config.debug = False
            mock_config.mcp_transport = "streamable-http"
            mock_config.mcp_host = "127.0.0.1"
            mock_config.mcp_port = 8100
            mock_config_cls.return_value = mock_config

            from trw_mcp.server import main

            main()

            # Should fall back to standalone stdio
            mock_mcp.run.assert_called_once_with()

    def test_fallback_on_proxy_keyboard_interrupt(self) -> None:
        """KeyboardInterrupt during proxy is caught cleanly."""
        with (
            patch("trw_mcp.server.mcp"),
            patch("trw_mcp.server.TRWConfig") as mock_config_cls,
            patch("trw_mcp.server._configure_logging"),
            patch(
                "trw_mcp.server._ensure_http_server",
                return_value="http://127.0.0.1:8100/mcp",
            ),
            patch(
                "asyncio.run",
                side_effect=KeyboardInterrupt,
            ),
            patch(
                "trw_mcp.server.argparse.ArgumentParser.parse_args",
                return_value=argparse.Namespace(
                    debug=False,
                    transport=None,
                    host=None,
                    port=None,
                    command=None,
                ),
            ),
        ):
            mock_config = MagicMock()
            mock_config.debug = False
            mock_config.mcp_transport = "streamable-http"
            mock_config.mcp_host = "127.0.0.1"
            mock_config.mcp_port = 8100
            mock_config_cls.return_value = mock_config

            from trw_mcp.server import main

            # Should not raise
            main()
