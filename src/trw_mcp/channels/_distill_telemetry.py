"""Fail-open tool-return telemetry emitter for distill MCP tools.

Called by trw_before_edit_hint, trw_codebase_risk_report, and
trw_entity_risk_map before returning their results.

Thin wrapper over append_channel_event() from _telemetry.py with
tool-call-specific tagging.  Detects client from TRW_CLIENT_PROFILE env var
when not provided (P0-01 audit fix).

NEVER raises — fail-open on all I/O or telemetry errors.

PRD-DIST-2400 ancillary.
"""

from __future__ import annotations

import os

import structlog

from trw_mcp.channels._telemetry import append_channel_event

log = structlog.get_logger(__name__)

__all__ = [
    "emit_tool_call",
    "resolve_client_profile",
]

_ENV_VAR = "TRW_CLIENT_PROFILE"
_UNKNOWN_CLIENT = "unknown"
_TOOL_CALL_CHANNEL = "__tool_call__"


def resolve_client_profile() -> str:
    """Return the client profile string from the environment.

    Reads ``TRW_CLIENT_PROFILE`` env var.  Returns ``"unknown"`` if absent
    or blank.

    Returns:
        Client profile string (e.g. ``"claude-code"``), or ``"unknown"``.
    """
    value = os.environ.get(_ENV_VAR, "").strip()
    return value or _UNKNOWN_CLIENT


def emit_tool_call(
    *,
    tool_name: str,
    file_path: str | None = None,
    client: str | None = None,
    tier: str = "T2",
    record_ids: list[str] | None = None,
    **extra_fields: object,
) -> None:
    """Emit a ``pull_tool_call`` telemetry event for a distill MCP tool.

    Resolves *client* from ``TRW_CLIENT_PROFILE`` env var when not provided.
    Wraps ``append_channel_event()`` with tool-call-specific tagging.

    All arguments are keyword-only.

    Args:
        tool_name: Name of the MCP tool being called
            (e.g. ``"trw_before_edit_hint"``).
        file_path: Optional repo-relative file path the tool acted on.
        client: Client profile string.  Resolved from env var if not given.
        tier: Tier string used by the tool (default ``"T2"``).
        record_ids: Optional list of canonical record IDs included in the
            tool response.
        **extra_fields: Additional fields forwarded to ``append_channel_event``.

    Returns:
        None.  Never raises.
    """
    try:
        effective_client = client if client is not None else resolve_client_profile()

        extra: dict[str, object] = {"tool_name": tool_name}
        if file_path is not None:
            extra["file_path"] = file_path
        extra.update(extra_fields)

        append_channel_event(
            channel_id=_TOOL_CALL_CHANNEL,
            client=effective_client,
            event_type="pull_tool_call",
            tier=tier,
            record_ids=record_ids,
            file_path=file_path,
            extra=extra,
        )
    except Exception as exc:
        log.debug(
            "emit_tool_call_failed",
            tool_name=tool_name,
            error=str(exc),
            outcome="telemetry_suppressed",
        )
