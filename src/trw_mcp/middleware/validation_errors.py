"""E2E-VALIDATION-ERROR-ECHO: a tool call that fails argument validation never echoes the argument's value.

pydantic's validation errors carry each failed field's raw ``input``, and fastmcp both returned that text to the
client and logged it (WARNING "Invalid arguments for tool ..."), so a secret pasted into the wrong argument landed
in the transcript and the server log. This module closes both, keeping the argument path, pydantic's message and
the error type -- no value at all, so no detector gap can leak one:

- ``ValidationErrorRedactionMiddleware`` (outermost around tool calls) re-raises the error as a value-free
  ``ToolError``;
- ``install_log_filter`` strips ``input``/``ctx`` from fastmcp's own warning before any handler formats it.

Belongs to the ``server/_app.py`` middleware chain.
"""

from __future__ import annotations

import logging
from typing import Any

from fastmcp.exceptions import ToolError
from fastmcp.exceptions import ValidationError as FastMCPValidationError
from fastmcp.server.middleware import Middleware, MiddlewareContext
from pydantic import ValidationError

from trw_mcp._refusal_echo import key_name
from trw_mcp.telemetry.anonymizer import redact_secrets

__all__ = ["ValidationErrorRedactionMiddleware", "install_log_filter", "value_free_message"]

#: fastmcp 3.x logs argument-validation failures here with this exact format (server.py ``call_tool``).
_FASTMCP_LOGGER = "fastmcp.server.server"
_INVALID_ARGS = "Invalid arguments for tool %r: %s"
_VALUE_KEYS = ("input", "ctx")


def value_free_message(exc: ValidationError) -> str:
    """``N validation error(s) for <title>`` plus one ``<path>: <msg> [type=<type>]`` line per error, no input."""
    lines = [f"{exc.error_count()} validation error(s) for {exc.title}"]
    for error in exc.errors(include_input=False, include_url=False, include_context=False):
        # A dict argument's KEY is part of the path and is caller-controlled, so it goes through the one detector.
        path = ".".join(key_name(part) for part in error["loc"]) or "(arguments)"
        # A custom validator's message can quote the input, so the message goes through the detector as well.
        lines.append(f"{path}: {redact_secrets(error['msg'])} [type={error['type']}]")
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
    """One pydantic error dict without ``input``/``ctx``, its ``loc`` keys passed through the detector."""
    kept = {k: v for k, v in entry.items() if k not in _VALUE_KEYS}
    if isinstance(kept.get("msg"), str):
        kept["msg"] = redact_secrets(kept["msg"])
    if isinstance(kept.get("loc"), tuple | list):
        kept["loc"] = tuple(key_name(p) if isinstance(p, str) else p for p in kept["loc"])
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


def install_log_filter() -> None:
    """Attach the filter to fastmcp's server logger once (idempotent)."""
    target = logging.getLogger(_FASTMCP_LOGGER)
    if not any(isinstance(f, _DropInputValues) for f in target.filters):
        target.addFilter(_DropInputValues())
