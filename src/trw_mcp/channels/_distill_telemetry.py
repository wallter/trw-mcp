"""Fail-open tool-return telemetry emitter for distill MCP tools.

Called by trw_before_edit_hint and trw_codebase_risk_report
before returning their results.

Thin wrapper over append_channel_event() from _telemetry.py with
tool-call-specific tagging.  Detects client from TRW_CLIENT_PROFILE env var
when not provided (P0-01 audit fix).

NEVER raises — fail-open on all I/O or telemetry errors.

PRD-DIST-2400 ancillary.
"""

from __future__ import annotations

import hashlib
import os

import structlog

from trw_mcp.channels._telemetry import append_channel_event

log = structlog.get_logger(__name__)

__all__ = [
    "emit_hint_delivered",
    "emit_tool_call",
    "hash_file_path",
    "resolve_client_profile",
]

_ENV_VAR = "TRW_CLIENT_PROFILE"
_UNKNOWN_CLIENT = "unknown"
_TOOL_CALL_CHANNEL = "__tool_call__"
#: Channel the FR01 delivery events are attributed to — the CC-03 PreToolUse
#: hint hook, which previously wrote only to a 24h-TTL context directory.
_HINT_CHANNEL = "cc-03-pretooluse-hint"
#: Truncated digest length — enough to distinguish files across a measurement
#: window without persisting a readable path into durable telemetry.
_PATH_HASH_CHARS = 16


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


def hash_file_path(file_path: str) -> str:
    """Return a stable, truncated digest of *file_path*.

    Delivery measurement only needs to tell edits apart, not to know which file
    was edited — durable telemetry therefore carries a digest rather than a
    readable repo path.
    """
    return hashlib.sha256(file_path.encode("utf-8")).hexdigest()[:_PATH_HASH_CHARS]


def emit_hint_delivered(
    *,
    tier: str,
    distill_status: str,
    file_path: str,
    client: str | None = None,
) -> None:
    """Emit the PRD-CORE-231-FR01 ``hint_delivered`` event.

    Fired for every ELIGIBLE edit — one where the feature was entitlement-
    allowed, i.e. ``distill_status`` resolved to anything other than
    ``tier_required`` — regardless of whether a sidecar was actually found.
    Recording the misses is what makes the >=90% delivery gate measurable
    rather than a survivorship statistic.

    Fail-open: never raises.
    """
    try:
        append_channel_event(
            channel_id=_HINT_CHANNEL,
            client=client if client is not None else resolve_client_profile(),
            event_type="hint_delivered",
            tier=tier,
            extra={
                "tier": tier,
                "distill_status": distill_status,
                "eligible": True,
                "file_path_hash": hash_file_path(file_path),
            },
        )
    except Exception as exc:
        log.debug(
            "emit_hint_delivered_failed",
            error=str(exc),
            outcome="telemetry_suppressed",
        )
