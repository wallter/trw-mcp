"""Tests for telemetry FastMCP discovery and run-dir cache behavior."""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastmcp import FastMCP

import trw_mcp.tools.telemetry as telemetry
from tests._tools_telemetry_support import _config_with, _make_ceremony_tools, reset_telemetry_cache  # noqa: F401
from tests.conftest import get_tools_sync
from trw_mcp.tools.telemetry import log_tool_call


class TestToolDiscovery:
    """T-25: Decorated tools remain discoverable by FastMCP."""

    def test_t25_log_tool_call_preserves_function_name(self) -> None:
        """T-25: functools.wraps ensures the decorated function retains its __name__."""

        @log_tool_call
        def my_discoverable_tool() -> str:
            return "result"

        assert my_discoverable_tool.__name__ == "my_discoverable_tool"

    def test_t25_log_tool_call_preserves_docstring(self) -> None:
        """T-25: functools.wraps ensures docstring is preserved for FastMCP schema generation."""

        @log_tool_call
        def documented_tool() -> str:
            """Return a fixed string value."""
            return "value"

        assert documented_tool.__doc__ == "Return a fixed string value."

    def test_t25_decorated_tool_registers_with_fastmcp(self) -> None:
        """T-25: A @log_tool_call-decorated function can be registered as an MCP tool."""
        srv = FastMCP("discovery-test")

        @srv.tool()
        @log_tool_call
        def api_tool(message: str) -> dict[str, str]:
            """Echo the message back."""
            return {"echo": message}

        assert "api_tool" in get_tools_sync(srv)

    def test_t25_registered_tool_is_callable_via_fastmcp(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """T-25: A registered decorated tool can be invoked through FastMCP's tool manager."""
        monkeypatch.setattr(telemetry, "get_config", lambda: _config_with(telemetry_enabled=False, telemetry=False))

        srv = FastMCP("discovery-invoke-test")

        @srv.tool()
        @log_tool_call
        def compute_tool(x: int) -> int:
            """Multiply x by 3."""
            return x * 3

        assert get_tools_sync(srv)["compute_tool"].fn(x=5) == 15

    def test_t25_ceremony_tools_discoverable(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """T-25: trw_session_start and trw_deliver decorated with @log_tool_call are registered."""
        tools = _make_ceremony_tools(monkeypatch, tmp_path)
        assert "trw_session_start" in tools
        assert "trw_deliver" in tools

    def test_t25_tool_call_with_kwargs_works_correctly(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """T-25: Decorated tool accepts keyword arguments and returns correct result."""
        monkeypatch.setattr(telemetry, "get_config", lambda: _config_with(telemetry_enabled=False, telemetry=False))

        @log_tool_call
        def kwargs_tool(a: int = 0, b: int = 0) -> int:
            return a + b

        assert kwargs_tool(a=10, b=20) == 30
        assert kwargs_tool(5, 7) == 12


class TestRunDirCache:
    """Verify the TTL cache in _get_cached_run_dir behaves correctly."""

    def test_cache_is_used_within_ttl(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When cache is fresh, find_active_run is not called again."""
        sentinel_path = Path("/sentinel/run")
        telemetry._cached_run_dir = (time.monotonic(), sentinel_path)
        call_count: list[int] = [0]

        def counting_find() -> Path | None:
            call_count[0] += 1
            return None

        monkeypatch.setattr(telemetry, "find_active_run", counting_find)
        assert telemetry._get_cached_run_dir() is sentinel_path
        assert call_count[0] == 0

    def test_cache_refreshes_after_ttl_expires(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When cache timestamp is older than TTL, find_active_run is called to refresh."""
        telemetry._cached_run_dir = (0.0, Path("/stale/run"))
        fresh_path = Path("/fresh/run")
        monkeypatch.setattr(telemetry, "find_active_run", lambda: fresh_path)
        assert telemetry._get_cached_run_dir() is fresh_path

    def test_cache_stores_new_value_after_refresh(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """After a refresh, the cached value is updated to the new result."""
        telemetry._cached_run_dir = (0.0, None)
        new_path = Path("/new/run/dir")
        monkeypatch.setattr(telemetry, "find_active_run", lambda: new_path)

        telemetry._get_cached_run_dir()

        _ts, cached = telemetry._cached_run_dir
        assert cached is new_path
