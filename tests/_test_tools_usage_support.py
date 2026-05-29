"""Shared support for split usage tool tests."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from trw_mcp.state.persistence import FileStateWriter


def _write_usage_record(
    writer: FileStateWriter,
    log_path: Path,
    *,
    model: str = "claude-haiku-4-5-20251001",
    input_tokens: int = 150,
    output_tokens: int = 80,
    latency_ms: float = 1234.5,
    caller: str = "ask",
    success: bool = True,
) -> None:
    """Write a single usage record to the JSONL log."""
    writer.append_jsonl(
        log_path,
        {
            "ts": "2026-02-20T12:00:00Z",
            "model": model,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "latency_ms": latency_ms,
            "caller": caller,
            "success": success,
        },
    )


def _get_report_tool_fn() -> Callable[..., Any]:
    """Extract the trw_usage_report fn from the FastMCP server."""
    from tests.conftest import extract_tool_fn, make_test_server

    return extract_tool_fn(make_test_server("usage"), "trw_usage_report")
