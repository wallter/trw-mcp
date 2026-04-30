from __future__ import annotations

from typing import Any

from fastmcp import FastMCP

from tests.conftest import extract_tool_fn, make_test_server


def _make_server() -> FastMCP:
    return make_test_server()


def _extract_tool(server: FastMCP, name: str) -> Any:
    return extract_tool_fn(server, name)
