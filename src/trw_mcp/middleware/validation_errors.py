"""E2E-VALIDATION-ERROR-ECHO: a tool call that fails argument validation never echoes the argument's value.

pydantic's validation errors carry each failed field's raw ``input``, and fastmcp both returned that text to the
client and logged it (WARNING "Invalid arguments for tool ..."), so a secret pasted into the wrong argument landed
in the transcript and the server log. This module closes both, keeping the argument path, pydantic's message and the
error type -- no value at all, so no detector gap can leak one. A dict key in the path is shown as ``<key>``, and a
message a validator wrote (or one that quotes the input tag) becomes a generic "invalid value":

- ``ValidationErrorRedactionMiddleware`` (outermost around tool calls) re-raises the error as a value-free
  ``ToolError``;
- ``install_log_filter`` strips ``input``/``ctx`` from fastmcp's own warning before any handler formats it.

It also keeps a deliberate refusal out of the server log as a traceback (INC-120 (c)): fastmcp logs a ``ToolError``
as one line but runs ``logger.exception`` for every other exception a tool raises, so a typed ``StateError`` with its
remedy in the message printed a 40-line Rich traceback on stderr while the client response was already clean.

Belongs to the ``server/_app.py`` middleware chain.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from typing import Any

from fastmcp.exceptions import ToolError
from fastmcp.exceptions import ValidationError as FastMCPValidationError
from fastmcp.server.middleware import Middleware, MiddlewareContext
from pydantic import ValidationError

from trw_mcp._refusal_echo import key_name
from trw_mcp.exceptions import TRWError
from trw_mcp.telemetry.anonymizer import redact_secrets

__all__ = ["ValidationErrorRedactionMiddleware", "install_log_filter", "value_free_message"]

#: fastmcp 3.x logs argument-validation failures here with this exact format (server.py ``call_tool``).
_FASTMCP_LOGGER = "fastmcp.server.server"
_INVALID_ARGS = "Invalid arguments for tool %r: %s"
_TOOL_ERROR = "Error calling tool "
_VALUE_KEYS = ("input", "ctx")
#: Error types whose message is written by a validator (or quotes the caller's input): dropped, not redacted, because
#: no detector recognises a personal value (a name, an email) the way it recognises a secret.
_MESSAGE_QUOTES_INPUT = frozenset({"value_error", "assertion_error", "union_tag_invalid"})
_GENERIC_MESSAGE = "invalid value"
#: What a caller-chosen dict key becomes in an error path: an identifier-shaped personal key passes the secret detector.
_DICT_KEY = "<key>"


def _safe_loc(loc: Iterable[object]) -> tuple[object, ...]:
    """A pydantic ``loc`` holding nothing the caller chose: the argument name by the one key rule, any deeper string
    (a dict key) as ``<key>``, a list index as it is."""
    return tuple(
        (key_name(part) if index == 0 else _DICT_KEY) if isinstance(part, str) else part
        for index, part in enumerate(loc)
    )


def _safe_message(error_type: object, message: object) -> str:
    """*message* for the client and the log: generic when the error type may quote the input, else detector-clean."""
    if error_type in _MESSAGE_QUOTES_INPUT:
        return _GENERIC_MESSAGE
    return redact_secrets(str(message))


def value_free_message(exc: ValidationError) -> str:
    """``N validation error(s) for <title>`` plus one ``<path>: <msg> [type=<type>]`` line per error, no input."""
    lines = [f"{exc.error_count()} validation error(s) for {exc.title}"]
    for error in exc.errors(include_input=False, include_url=False, include_context=False):
        # A dict argument's KEY is part of the path and is caller-controlled, so it is never shown.
        path = ".".join(str(part) for part in _safe_loc(error["loc"])) or "(arguments)"
        lines.append(f"{path}: {_safe_message(error['type'], error['msg'])} [type={error['type']}]")
    return "\n".join(lines)


class ValidationErrorRedactionMiddleware(Middleware):
    """Re-raise an argument validation error as a ``ToolError`` whose text holds no input value."""

    async def on_call_tool(self, context: MiddlewareContext[Any], call_next: Any) -> Any:
        try:
            return await call_next(context)
        except FastMCPValidationError as exc:  # fastmcp wraps pydantic's error, which stays as __cause__
            cause = exc.__cause__
            if isinstance(cause, ValidationError):
                raise ToolError(value_free_message(cause)) from None
            raise ToolError("invalid arguments (detail withheld: it may contain an argument value)") from None
        except ValidationError as exc:
            raise ToolError(value_free_message(exc)) from None  # from None: the chained original holds the value


def _value_free_entry(entry: dict[str, Any]) -> dict[str, Any]:
    """One pydantic error dict without ``input``/``ctx``, its ``loc`` free of caller-chosen keys, its message safe."""
    kept = {k: v for k, v in entry.items() if k not in _VALUE_KEYS}
    if isinstance(kept.get("msg"), str):
        kept["msg"] = _safe_message(kept.get("type"), kept["msg"])
    if isinstance(kept.get("loc"), tuple | list):
        kept["loc"] = _safe_loc(kept["loc"])
    return kept


class _DropInputValues(logging.Filter):
    """Rewrite fastmcp's "Invalid arguments" warning so its per-error dicts carry no ``input``/``ctx`` value."""

    def filter(self, record: logging.LogRecord) -> bool:
        if record.msg == _INVALID_ARGS and isinstance(record.args, tuple) and len(record.args) == 2:
            name, detail = record.args
            if isinstance(detail, list):
                detail = [_value_free_entry(d) if isinstance(d, dict) else d for d in detail]
            else:
                detail = "(detail withheld: it may contain an argument value)"
            record.args = (name, detail)
        return True


class _QuietDeliberateRefusals(logging.Filter):
    """One line, no traceback, for a typed TRW error a tool raised directly (the way fastmcp logs a ToolError).

    An error that wraps a cause, or is not a TRW error at all, is a failure and keeps its stack.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        exc = record.exc_info[1] if record.exc_info else None
        if isinstance(exc, TRWError) and exc.__cause__ is None and str(record.msg).startswith(_TOOL_ERROR):
            record.msg = f"{record.getMessage()}: {type(exc).__name__}: {redact_secrets(str(exc))}"
            record.args = None
            record.exc_info = record.exc_text = None
        return True


def install_log_filter() -> None:
    """Attach the filters to fastmcp's server logger once each (idempotent)."""
    target = logging.getLogger(_FASTMCP_LOGGER)
    for kind in (_DropInputValues, _QuietDeliberateRefusals):
        if not any(isinstance(f, kind) for f in target.filters):
            target.addFilter(kind())
