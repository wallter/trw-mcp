"""Prove to a dispatching parent that this TRW MCP server started and listed its tools (CODEX-P0-B-ZERO-TOOL, server side).

A codex child whose ``mcp_servers.trw.command`` does not exist exits 0 with the same events as a healthy run, so only the
server can vouch for itself. ``dispatch/_handshake.py`` hands the child's server a private receipt path and a per-run nonce
through ``mcp_servers.trw.env.*``; this middleware, outermost in the chain, writes ``{nonce, tools, ...}`` there each time
the client lists tools, recording exactly what the client was shown (after every other layer filtered it).

Inert unless BOTH variables are set, so an ordinary session pays nothing and writes nothing. The write never breaks a
listing: any failure is logged and the tools are returned unchanged (the parent then reports "no proof", which fails the review).
The path is chosen by the dispatcher, but the server still refuses a symlink, a path outside an existing directory, and an
over-long value, and writes through a same-directory temp file and ``os.replace`` so a reader never sees a half receipt.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Iterable, Sequence
from pathlib import Path

import structlog
from fastmcp.server.middleware.middleware import CallNext, Middleware, MiddlewareContext
from fastmcp.tools import Tool
from mcp.types import ListToolsRequest

from trw_mcp.dispatch._handshake_names import NONCE_ENV, PATH_ENV

_MAX_PATH = 4096
_MAX_NONCE = 256

logger = structlog.get_logger(__name__)


def receipt_target() -> tuple[Path, str] | None:
    """``(receipt path, nonce)`` when the dispatcher asked for a receipt, else ``None``."""
    raw_path, nonce = os.environ.get(PATH_ENV, ""), os.environ.get(NONCE_ENV, "")
    if not raw_path or not nonce or len(raw_path) > _MAX_PATH or len(nonce) > _MAX_NONCE:
        return None
    path = Path(raw_path)
    return (path, nonce) if path.is_absolute() else None


def write_receipt(names: Iterable[str]) -> bool:
    """Write the receipt for *names*; ``False`` (logged) when it could not be written. Never raises."""
    target = receipt_target()
    if target is None:
        return False
    path, nonce = target
    from trw_mcp import __version__

    payload = {
        "schema": 1,
        "nonce": nonce,
        "pid": os.getpid(),
        "tools": sorted(set(names)),
        "trw_mcp_version": __version__,
        "ts": time.time(),
    }
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    created = False
    try:
        if path.is_symlink() or not path.parent.is_dir():
            raise OSError(f"refusing receipt path {path}: symlink or missing directory")
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
        created = (
            True  # from here the temp file is ours; before it (EEXIST, a symlink) it is not, and must not be unlinked
        )
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle)
        os.replace(temp, path)
    except OSError:  # trw-fail-silent-allow: a receipt that cannot be written must not break tools/list; the parent then sees no proof and fails the review, which is the safe direction
        logger.warning("dispatch_receipt_write_failed", path=str(path), exc_info=True)
        if created:
            temp.unlink(missing_ok=True)
        return False
    return True


class HandshakeReceiptMiddleware(Middleware):
    """Write the receipt from the final ``tools/list`` result."""

    async def on_list_tools(
        self,
        context: MiddlewareContext[ListToolsRequest],
        call_next: CallNext[ListToolsRequest, Sequence[Tool]],
    ) -> Sequence[Tool]:
        tools = await call_next(context)
        write_receipt(tool.name for tool in tools)
        return tools


def maybe_handshake_middleware() -> HandshakeReceiptMiddleware | None:
    """The middleware when this process was started with a receipt to write, else ``None``."""
    return HandshakeReceiptMiddleware() if receipt_target() is not None else None
