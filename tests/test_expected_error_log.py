"""INC-120 (c): a deliberate refusal is one line in the server log, never a Rich traceback.

fastmcp logs a ``ToolError`` without a traceback, but it runs ``logger.exception`` for every other exception a tool
raises, so a bad ``task_name`` (a typed ``StateError`` raised on purpose, with the remedy in its message) printed a
40-line traceback on the server's stderr while the client response was already clean. The log filter installed with
the validation-error one now drops the traceback for a typed TRW error raised directly, and keeps it for an error
that wraps a cause or is not a TRW error at all (those are failures an operator needs the stack for).
"""

from __future__ import annotations

import logging
import sys

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from trw_mcp.exceptions import StateError, TRWError
from trw_mcp.middleware.validation_errors import _QuietDeliberateRefusals


def _record(exc: BaseException | None, msg: str = "Error calling tool 'trw_init'") -> logging.LogRecord:
    exc_info = None
    if exc is not None:
        try:
            raise exc
        except BaseException:
            exc_info = sys.exc_info()
    return logging.LogRecord("fastmcp.server.server", logging.ERROR, "f", 1, msg, (), exc_info)


def test_a_direct_trw_error_loses_its_traceback_and_keeps_its_message() -> None:
    record = _record(StateError("Invalid task_name: must match x, got: ''"))
    assert _QuietDeliberateRefusals().filter(record)
    assert record.exc_info is None
    assert record.getMessage() == "Error calling tool 'trw_init': StateError: Invalid task_name: must match x, got: ''"
    assert record.levelno == logging.ERROR


def test_the_message_goes_through_the_secret_detector() -> None:
    secret = "sk-ant-api03-" + "Zq7" * 30 + "-AbCdEf"  # shaped like a key, never a real one
    record = _record(TRWError(f"bad value {secret}"))
    _QuietDeliberateRefusals().filter(record)
    assert secret not in record.getMessage()


def test_an_error_that_wraps_a_cause_keeps_its_traceback() -> None:
    try:
        try:
            raise OSError("disk full")
        except OSError as cause:
            raise StateError("could not write run.yaml") from cause
    except StateError as exc:
        record = _record(exc)
    assert _QuietDeliberateRefusals().filter(record)
    assert record.exc_info is not None


@pytest.mark.parametrize("exc", [RuntimeError("bug"), ValueError("bug")], ids=["runtime", "value"])
def test_a_non_trw_error_keeps_its_traceback(exc: BaseException) -> None:
    record = _record(exc)
    assert _QuietDeliberateRefusals().filter(record)
    assert record.exc_info is not None


def test_another_log_line_is_left_alone() -> None:
    record = _record(StateError("x"), msg="Something else happened")
    assert _QuietDeliberateRefusals().filter(record)
    assert record.exc_info is not None


@pytest.mark.parametrize(
    ("arguments", "refusal"),
    [
        pytest.param({"task_name": "bad name!"}, "StateError: Invalid task_name", id="bad-task-name"),
        pytest.param(
            {"task_name": "ok-task", "advanced": {"nope": 1}},
            "StateError: Invalid advanced argument",
            id="unknown-advanced-key",
        ),
    ],
)
async def test_a_deliberate_trw_init_refusal_reaches_the_client_and_leaves_no_traceback_in_the_log(
    caplog: pytest.LogCaptureFixture, arguments: dict[str, object], refusal: str
) -> None:
    from trw_mcp.server._app import create_app
    from trw_mcp.server._tools import _tool_registrars

    server = create_app()
    for register in _tool_registrars():
        register(server)
    fastmcp_logger = logging.getLogger("fastmcp.server.server")
    propagate = fastmcp_logger.propagate
    fastmcp_logger.propagate = True  # fastmcp may stop propagation to root; caplog listens on root
    try:
        with caplog.at_level(logging.ERROR, logger="fastmcp.server.server"):
            async with Client(server) as client:
                with pytest.raises(ToolError, match="Invalid"):
                    await client.call_tool("trw_init", arguments)
    finally:
        fastmcp_logger.propagate = propagate
    records = [r for r in caplog.records if "Error calling tool" in r.getMessage()]
    assert records, "fastmcp's error line was captured"
    assert all(r.exc_info is None for r in records)
    assert any(refusal in r.getMessage() for r in records)
    assert all("input_value" not in r.getMessage() for r in records)  # the rejected value is not in the log either
