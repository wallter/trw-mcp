"""Opt-in shared trw-mcp server fields (``trw_mcp.shared_server``)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class SharedMcpConfig(BaseModel):
    """One detached trw-mcp per env behind thin ``trw-mcp-proxy`` stdio shims.

    Off by default: with ``enabled`` false, ``trw-mcp-proxy`` runs the ordinary
    per-client stdio server in-process, so the flag is also the rollback lever.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    enabled: bool = False
    #: Bearer token file; empty means ``.trw/runtime/shared-mcp/token``.
    token_path: str = ""
    #: Requests executing at once before new ones are answered busy (the proxy retries, then reports).
    max_inflight: int = Field(default=256, ge=1, le=10_000)
    #: A server with no request for this long exits; the next proxy call restarts it. 0 = never.
    idle_shutdown_seconds: int = Field(default=3600, ge=0)
    #: Offline wheel source for ``trw-mcp swap --version`` (never a network index).
    wheelhouse: str = "~/.trw/wheelhouse"
    #: One directory per non-stable env: its ``TRW_USER_DIR`` (memory daemon + store) and built venvs.
    envs_dir: str = "~/.trw/envs"
    #: A serving env replaces itself (and drains an older memory daemon) when its installed trw-mcp / trw-memory
    #: changes, with no client restart. False leaves swaps to ``trw-mcp swap``.
    auto_swap: bool = True
    #: Seconds between the auto-swap watcher's polls (each poll is an in-process metadata read).
    auto_swap_poll_seconds: int = Field(default=15, ge=1, le=3600)


class _SharedMcpFields:
    """Mixin carrying the single nested ``shared_mcp`` field."""

    shared_mcp: SharedMcpConfig = Field(default_factory=SharedMcpConfig)
