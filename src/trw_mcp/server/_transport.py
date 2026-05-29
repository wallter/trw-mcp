"""Transport resolution for the MCP server.

Handles three transport paths:
  - Path 1: Explicit --transport flag -- run as that transport directly.
  - Path 2: No flag + config stdio -- run stdio normally.
  - Path 3: No flag + config HTTP -- auto-start shared server + stdio proxy.
"""

from __future__ import annotations

import argparse
from typing import Any

import structlog

from trw_mcp.models.config import TRWConfig
from trw_mcp.server._app import mcp
from trw_mcp.server._origin_check import OriginGuardMiddleware, origin_check_enabled
from trw_mcp.server._proxy import ensure_http_server, run_stdio_proxy

SUPPORTED_RUNTIME_RELOAD_FIELDS: frozenset[str] = frozenset(
    {
        "mcp_http_rate_limit_enabled",
        "mcp_http_rate_limit_capacity",
        "mcp_http_rate_limit_refill_per_second",
        "mcp_proxy_handshake_timeout_seconds",
    }
)


def _http_transport_kwargs(config: TRWConfig | None = None) -> dict[str, Any]:
    """Build transport_kwargs for FastMCP HTTP transports.

    Adds the Origin-guard middleware to block cross-origin browser requests
    against the loopback-bound MCP server. See ``_origin_check.py`` for the
    threat model. No-op when ``TRW_MCP_DISABLE_ORIGIN_CHECK=1``.
    """
    middleware = []
    if origin_check_enabled():
        # Late import: starlette is pulled in transitively by fastmcp, but only on
        # HTTP paths — keep stdio launches free of the dependency probe.
        from starlette.middleware import Middleware

        middleware.append(Middleware(OriginGuardMiddleware))
    if config is not None and config.mcp_http_rate_limit_enabled:
        from starlette.middleware import Middleware

        from trw_mcp.server._rate_limit import LocalTokenBucketMiddleware

        middleware.append(
            Middleware(
                LocalTokenBucketMiddleware,
                capacity=config.mcp_http_rate_limit_capacity,
                refill_per_second=config.mcp_http_rate_limit_refill_per_second,
            )
        )
    if not middleware:
        return {}
    return {"middleware": middleware}


def build_transport_fallback_diagnostic(reason: str, config: TRWConfig) -> dict[str, object]:
    """Return structured fallback guidance for wedged stdio/proxy transports."""
    proxy_url = f"http://{config.mcp_host}:{config.mcp_port}/mcp"
    return {
        "diagnostic_event": "trw_transport_fallback_diagnostic",
        "reason": reason,
        "fallback": "standalone_stdio",
        "http_proxy_url": proxy_url,
        "operator_guidance": (
            "If stdio remains wedged, start the shared HTTP server and point the client "
            f"at {proxy_url}; for offline ceremony use `trw-mcp local status|learn|deliver`."
        ),
    }


def summarize_runtime_reload(old: TRWConfig, new: TRWConfig) -> dict[str, object]:
    """Summarize config changes supported by the safe runtime reload boundary."""
    changed = [field for field in sorted(SUPPORTED_RUNTIME_RELOAD_FIELDS) if getattr(old, field) != getattr(new, field)]
    return {
        "reload_supported": True,
        "changed_fields": changed,
        "requires_source_restart": False,
        "boundary": "config_only_no_python_hot_reload",
    }


def resolve_and_run_transport(
    args: argparse.Namespace,
    config: TRWConfig,
    *,
    debug: bool,
    log: structlog.stdlib.BoundLogger,
) -> None:
    """Resolve transport mode and start the MCP server.

    Handles three transport paths:
      - Path 1: Explicit ``--transport`` flag -- run as that transport directly.
      - Path 2: No flag + config ``stdio`` -- run stdio normally.
      - Path 3: No flag + config HTTP -- auto-start shared server + stdio proxy.

    Args:
        args: Parsed CLI arguments (transport, host, port).
        config: TRW configuration.
        debug: Whether debug mode is active.
        log: Structured logger.
    """
    # -- Transport resolution (PRD-CORE-070-FR03) -------------------------
    if args.transport is not None:
        # Path 1: Direct server mode (e.g., spawned by ensure_http_server)
        transport: str = args.transport
        host: str = args.host or config.mcp_host
        port: int = args.port or config.mcp_port

        log.info(
            "trw_server_initialized",
            tools_registered=True,
            debug_mode=debug,
            transport=transport,
            host=host,
            port=port,
            mode="direct",
        )

        if transport == "stdio":
            mcp.run()
        else:
            # Pass host/port via transport_kwargs -- mcp.settings is deprecated
            # in FastMCP 2.14+ and silently ignored.
            mcp.run(
                transport=transport,  # type: ignore[arg-type]
                host=host,
                port=port,
                **_http_transport_kwargs(config),
            )

    elif config.mcp_transport == "stdio":
        # Path 2: Default stdio -- unchanged behavior
        log.info(
            "trw_server_initialized",
            tools_registered=True,
            debug_mode=debug,
            transport="stdio",
            mode="standalone",
        )
        mcp.run()

    else:
        # Path 3: Auto-start shared HTTP server + run as stdio proxy
        _run_http_proxy_transport(config, log, debug=debug)


def _run_http_proxy_transport(
    config: TRWConfig,
    log: structlog.stdlib.BoundLogger,
    *,
    debug: bool,
) -> None:
    """Start a shared HTTP server and bridge via stdio proxy.

    Falls back to standalone stdio if the HTTP server cannot be started
    or the proxy fails to connect.

    Args:
        config: TRW configuration.
        log: Structured logger.
        debug: Whether debug mode is active.
    """
    log.info(
        "trw_proxy_starting",
        target_transport=config.mcp_transport,
        target_host=config.mcp_host,
        target_port=config.mcp_port,
    )

    url = ensure_http_server(config, log, debug=debug)

    if url is not None:
        # Run stdio proxy bridging to the shared server
        import asyncio

        try:
            asyncio.run(
                run_stdio_proxy(
                    url,
                    handshake_timeout_seconds=float(config.mcp_proxy_handshake_timeout_seconds),
                )
            )
        except (KeyboardInterrupt, EOFError):
            pass  # Clean exit when Claude Code disconnects
        except ConnectionError:
            # Proxy exhausted retries -- fall back to standalone
            log.warning("trw_proxy_fallback", **build_transport_fallback_diagnostic("proxy_connect_failed", config))
            mcp.run()
    else:
        # FR06: Fallback to standalone stdio on failure
        log.warning("trw_proxy_fallback", **build_transport_fallback_diagnostic("http_server_start_failed", config))
        mcp.run()
