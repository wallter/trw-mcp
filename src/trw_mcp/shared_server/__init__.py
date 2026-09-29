"""Opt-in shared trw-mcp: one detached server per env behind thin stdio proxies.

Enable with ``shared_mcp: {enabled: true}`` in ``.trw/config.yaml`` and point a
client's MCP entry at ``trw-mcp-proxy`` (``--env`` or ``TRW_MCP_ENV`` picks the
env; default ``stable``). ``trw-mcp swap`` repoints and hot-swaps one env;
``trw-mcp status --shared`` shows each env's server. Modules: ``_records``
(discovery, token, env map), ``_server`` (``serve --shared``), ``_proxy`` (the
stdio pass-through), ``_ops`` (swap, env create, status, doctor), ``_cli``.
"""

from __future__ import annotations


def main_proxy() -> None:
    """``trw-mcp-proxy`` console-script entry; imports the private CLI lazily so the proxy stays lean."""
    from trw_mcp.shared_server._cli import main_proxy as _main_proxy

    _main_proxy()
