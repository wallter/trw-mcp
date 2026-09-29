"""The anchored recall page and its feature probe (PRD-CORE-332 FR05/FR07).

Belongs to ``_daemon_store``: ``DaemonMemoryStore._page`` calls :func:`anchored_page`
for a recall that names an ``anchor_file``. The daemon's ``memory_anchored`` tool
lists the namespace's rows anchored to that repo-relative file.

The lookup is an addition to the pre-edit hint, never a precondition of it. A
``DaemonClient`` without ``anchored`` (trw-memory older than the tool) and a running
daemon that does not serve ``memory_anchored`` (same major, older build) both answer
"no anchored rows" and log ``anchor_lookup_unsupported``, so the hint degrades to its
text queries exactly. Every other failure propagates as it does for ``memory_recall``.
"""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    from trw_memory.models.memory import MemoryEntry

logger = structlog.get_logger(__name__)

#: How fastmcp's server refuses an unregistered tool; ``DaemonClient.call_tool`` surfaces it
#: as ``ToolError``. A test against a real FastMCP server pins the wording, so a change
#: fails that test instead of silently turning every refusal into a hard error.
UNKNOWN_TOOL_PREFIX = "Unknown tool: 'memory_anchored'"


def anchored_page(
    client: object,
    run: Callable[[Coroutine[Any, Any, Any]], Any],
    namespace: str,
    file: str,
    limit: int,
    status: str | None,
) -> list[MemoryEntry]:
    """Up to *limit* rows of *namespace* anchored to *file*, in the daemon's order; ``[]`` when unsupported.

    *run* waits on a daemon coroutine (``_daemon_store._run``). Only this
    namespace's rows are kept, as the ``memory_recall`` page does.
    """
    from trw_mcp.state._daemon_store import _entry

    anchored = getattr(client, "anchored", None)
    if anchored is None:
        logger.debug("anchor_lookup_unsupported", reason="client_has_no_anchored")
        return []

    try:
        page = run(anchored(namespace=namespace, file=file, limit=limit, status=status))
    except Exception as exc:  # trw-fail-silent-allow: only an unserved memory_anchored degrades; others re-raise
        # ``fastmcp.exceptions.ToolError`` deferred to here (not imported at module or
        # function-entry scope): by the time ``run(anchored(...))`` above can raise it,
        # the RPC it made through ``open_session`` (``trw_memory.daemon._session``) has
        # already pulled ``fastmcp`` into ``sys.modules`` -- an eager import above would
        # cold-load the whole package (~0.2s, PRD-DIST-2400-latency) on every anchored
        # lookup that raises nothing at all, which is the common case.
        from fastmcp.exceptions import ToolError

        if not isinstance(exc, ToolError) or not str(exc).startswith(UNKNOWN_TOOL_PREFIX):
            raise
        logger.debug("anchor_lookup_unsupported", reason="daemon_does_not_serve_memory_anchored")
        return []
    if "memories" not in page:
        raise ValueError(f"memory_anchored refused {namespace}: {page.get('error')}")
    return [row for row in map(_entry, page["memories"]) if row.namespace == namespace]
