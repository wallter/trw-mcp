"""Stable sync client identity helpers."""

from __future__ import annotations

from trw_mcp.models.config import get_config
from trw_mcp.state._paths import resolve_installation_id


def resolve_sync_client_id() -> str:
    """Return a stable sync client id shared across push and pull."""
    cfg = get_config()
    profile_id = getattr(cfg.client_profile, "client_id", "") or "unknown-client"
    installation_id = resolve_installation_id()
    return f"sync-{profile_id}-{installation_id}"[:128]
