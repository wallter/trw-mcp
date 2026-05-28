"""Client-profile detection helpers for trw-mcp MCP tools.

Extracted to honour the 350 effective-LOC module gate (P1-05 audit fix).
Re-exported from before_edit_hint and the channels telemetry layer.

PRD-DIST-2400 §6.4 (Phase D2 extraction).
"""

from __future__ import annotations

import os

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


def resolve_client_profile(ctx_client_profile: str | None = None) -> str:
    """Return the active client profile string.

    Resolution order:
    1. *ctx_client_profile* argument (from MCP ctx.client_profile or similar).
    2. ``TRW_CLIENT_PROFILE`` environment variable.
    3. ``"unknown"`` fallback.

    Args:
        ctx_client_profile: Optional explicit profile from the call context.

    Returns:
        Non-empty client profile string, or ``"unknown"``.
    """
    if ctx_client_profile and ctx_client_profile.strip():
        return ctx_client_profile.strip()
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
