"""Client-profile detection helpers for trw-mcp MCP tools.

Extracted to honour the 350 effective-LOC module gate (P1-05 audit fix).
Re-exported from before_edit_hint and the channels telemetry layer.

PRD-DIST-2400 §6.4 (Phase D2 extraction).

Gap 1 (CUR-04 / STUB-02) — client_profile propagation via FastMCP ctx
-----------------------------------------------------------------------
``resolve_client_profile`` now accepts an optional FastMCP ``Context``
object.  When provided, it probes ``ctx.session.client_params.clientInfo.name``
(the MCP initialize-handshake clientInfo field) before falling back to the
``TRW_CLIENT_PROFILE`` env var.  All ctx access is fail-open: any
``RuntimeError`` or ``AttributeError`` is silently ignored and the env-var
path is tried next.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from fastmcp import Context

__all__ = [
    "resolve_client_profile",
    "resolve_tier_for_client",
]

_ENV_VAR = "TRW_CLIENT_PROFILE"
_UNKNOWN_CLIENT = "unknown"

# Default tier per client (PRD-DIST-2402 §3.2 / PRD-DIST-2403 §2 OC-05).
# Codex gets T2 (token-budget-aware), Copilot gets T0 (compressed segment
# with <50 chars per bullet), Antigravity gets T1.  All others default to T1.
_CLIENT_DEFAULT_TIER: dict[str, str] = {
    "codex": "T2",
    "opencode": "T2",
    "cursor-ide": "T2",
    "cursor-cli": "T2",
    "claude-code": "T1",
    "antigravity": "T1",
    "antigravity-cli": "T1",
    "gemini": "T1",
    "aider": "T1",
    "copilot": "T0",
}


def _probe_ctx_client_name(ctx: Any) -> str | None:
    """Attempt to read clientInfo.name from a FastMCP Context.

    Probes ``ctx.session.client_params.clientInfo.name`` (the MCP
    initialize-handshake field).  Returns None on any exception or if the
    value is blank — callers fall through to the env-var path.

    Never raises.
    """
    try:
        session = ctx.session  # raises RuntimeError when no session yet
        client_params = session.client_params  # may be None pre-initialize
        if client_params is None:
            return None
        client_info = client_params.clientInfo  # mcp.types.Implementation
        name = getattr(client_info, "name", None)
        if isinstance(name, str) and name.strip():
            return name.strip().lower()
    except Exception:
        pass
    return None


def resolve_client_profile(
    ctx_client_profile: str | None = None,
    *,
    ctx: Context | None = None,
) -> str:
    """Return the active client profile string.

    Resolution order:
    1. *ctx_client_profile* explicit string argument.
    2. ``ctx.session.client_params.clientInfo.name`` when *ctx* is provided
       (MCP initialize-handshake; Option A propagation).
    3. ``TRW_CLIENT_PROFILE`` environment variable.
    4. ``"unknown"`` fallback.

    Args:
        ctx_client_profile: Optional explicit profile override.
        ctx: Optional FastMCP ``Context`` to probe for client identity.

    Returns:
        Non-empty client profile string, or ``"unknown"``.
    """
    if ctx_client_profile and ctx_client_profile.strip():
        return ctx_client_profile.strip()
    if ctx is not None:
        name = _probe_ctx_client_name(ctx)
        if name:
            return name
    value = os.environ.get(_ENV_VAR, "").strip()
    return value or _UNKNOWN_CLIENT


def resolve_tier_for_client(client: str, default_tier: str = "T1") -> str:
    """Return the default render tier for *client*.

    Falls back to *default_tier* when the client is not in the built-in
    mapping.

    Args:
        client: Client profile string (e.g. ``"codex"``, ``"copilot"``).
        default_tier: Tier string to return when the client is unknown.

    Returns:
        Tier string from the mapping, or *default_tier*.
    """
    return _CLIENT_DEFAULT_TIER.get(client, default_tier)
