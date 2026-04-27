"""Tests for the per-dispatch MCP security consult hook.

Sprint-96 Carry-Forward (a) — CRIT-2. Verifies that
:func:`trw_mcp.server._security_hook.consult_mcp_security` is a fail-open
no-op when the middleware singleton is uninitialized, calls
``on_tool_call`` when set, swallows middleware exceptions, and that
:func:`trw_mcp.telemetry.tool_call_timing.wrap_tool` invokes the
``security_consult`` callback in its ``finally`` block on both success
and exception paths.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pytest

from trw_mcp.server import _app as _app_mod
from trw_mcp.server._security_hook import consult_mcp_security
from trw_mcp.telemetry.tool_call_timing import wrap_tool


class _SpyMiddleware:
    """Minimal middleware stand-in recording every ``on_tool_call`` call."""

    def __init__(self, *, raises: BaseException | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self._raises = raises

    def on_tool_call(
        self,
        *,
        transport: str,
        server: str,
        tool: str,
        args: dict[str, Any] | None = None,
        session_id: str = "",
        run_id: str | None = None,
    ) -> None:
        self.calls.append(
            {
                "transport": transport,
                "server": server,
                "tool": tool,
                "args": args,
                "session_id": session_id,
                "run_id": run_id,
            }
        )
        if self._raises is not None:
            raise self._raises


@pytest.fixture
def restore_mcp_security() -> Any:
    """Save/restore the module-level ``_mcp_security`` singleton."""
    original = getattr(_app_mod, "_mcp_security", None)
    try:
        yield
    finally:
        _app_mod._mcp_security = original


def test_consult_with_uninitialized_middleware_is_noop(
    restore_mcp_security: None, caplog: pytest.LogCaptureFixture
) -> None:
    """When ``_mcp_security`` is None, consult returns cleanly and logs no warning."""
    _app_mod._mcp_security = None
    with caplog.at_level(logging.WARNING):
        consult_mcp_security("some_tool", {"k": "v"}, "sess-1", "run-1")
    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert warnings == []


def test_consult_calls_on_tool_call_when_set(restore_mcp_security: None) -> None:
    """With a spy middleware installed, consult forwards the dispatch exactly once."""
    spy = _SpyMiddleware()
    spy.default_server_name = "filesystem"
    _app_mod._mcp_security = spy

    consult_mcp_security("trw_query_events", {"session_id": "s"}, "sess-42", "run-9")

    assert len(spy.calls) == 1
    call = spy.calls[0]
    assert call["tool"] == "trw_query_events"
    assert call["args"] == {"session_id": "s"}
    assert call["session_id"] == "sess-42"
    assert call["run_id"] == "run-9"
    assert call["server"] == "filesystem"
    assert call["transport"] == "stdio"


def test_consult_swallows_middleware_exceptions(restore_mcp_security: None, caplog: pytest.LogCaptureFixture) -> None:
    """If middleware.on_tool_call raises, consult swallows it and warns.

    The key contract is fail-open: the call must not re-raise. We capture
    any emitted record at WARNING or above (structlog routing varies by
    configuration), and additionally assert that the spy observed the
    dispatch attempt before raising.
    """
    spy = _SpyMiddleware(raises=RuntimeError("boom"))
    _app_mod._mcp_security = spy

    with caplog.at_level(logging.WARNING):
        # Must not raise — fail-open contract
        consult_mcp_security("some_tool", None, "", None)

    # The call was attempted (spy recorded it before raising).
    assert len(spy.calls) == 1


def test_wrap_tool_calls_security_consult_in_finally_on_success() -> None:
    """wrap_tool with security_consult invokes the spy after successful fn return."""
    calls: list[tuple[str, dict[str, Any] | None, str, str | None]] = []

    def spy(
        tool: str,
        args: dict[str, Any] | None,
        session_id: str,
        run_id: str | None,
    ) -> None:
        calls.append((tool, args, session_id, run_id))

    def noop(x: int, y: str = "default") -> int:
        return x + 1

    wrapped = wrap_tool(
        noop,
        tool_name="noop_tool",
        security_consult=spy,
        run_dir_resolver=lambda: None,
    )
    result = wrapped(41, y="custom")

    assert result == 42
    assert len(calls) == 1
    assert calls[0][0] == "noop_tool"
    assert calls[0][1] == {"x": 41, "y": "custom"}


def test_wrap_tool_calls_security_consult_on_exception() -> None:
    """wrap_tool fires security_consult even when the wrapped fn raises."""
    calls: list[tuple[str, dict[str, Any] | None, str, str | None]] = []

    def spy(
        tool: str,
        args: dict[str, Any] | None,
        session_id: str,
        run_id: str | None,
    ) -> None:
        calls.append((tool, args, session_id, run_id))

    def boom() -> None:
        raise ValueError("kaboom")

    wrapped = wrap_tool(boom, tool_name="boom_tool", security_consult=spy)

    with pytest.raises(ValueError, match="kaboom"):
        wrapped()

    assert len(calls) == 1
    assert calls[0][0] == "boom_tool"


def test_wrap_tool_passes_run_id_from_run_dir() -> None:
    calls: list[tuple[str, dict[str, Any] | None, str, str | None]] = []

    def spy(
        tool: str,
        args: dict[str, Any] | None,
        session_id: str,
        run_id: str | None,
    ) -> None:
        calls.append((tool, args, session_id, run_id))

    def noop() -> str:
        return "ok"

    wrapped = wrap_tool(
        noop,
        tool_name="noop_tool",
        session_id_resolver=lambda: "sess-1",
        run_dir_resolver=lambda: Path("/repo/.trw/runs/task/run-123"),
        security_consult=spy,
    )
    wrapped()

    assert calls == [("noop_tool", {}, "sess-1", "run-123")]
