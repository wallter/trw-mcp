"""One ``memory_recall`` page, with the compat retry for a daemon that predates ``rerank``.

Belongs to ``_daemon_store``: ``DaemonMemoryStore._page`` calls :func:`recall_page` for a
query recall. A caller with a latency budget (the pre-edit hint) sends ``rerank=False``;
a running daemon of the same major but an older build refuses that argument as unknown,
which would fail the whole recall and leave the hint with no lessons. Only that refusal
retries the same page once without the argument (the daemon's default re-rank); every
other failure propagates unchanged. A deadline-bounded caller still abandons its worker
at its deadline, so the retry can never extend the hint past it.
"""

from __future__ import annotations

import re
import sys
from collections.abc import Callable, Coroutine
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

#: How the daemon's argument validation refuses an unknown ``rerank`` (fastmcp over pydantic's
#: ``unexpected_keyword_argument``, located at the ``rerank`` line). A test against a real FastMCP
#: server pins the wording, so a change fails that test instead of silently disabling the retry.
RERANK_REFUSED = re.compile(r"(?m)^rerank\n\s+Unexpected keyword argument")


def recall_page(
    client: Any,
    run: Callable[[Coroutine[Any, Any, Any]], Any],
    query: str,
    namespace: str,
    arguments: dict[str, Any],
) -> Any:
    """The daemon's ``memory_recall`` answer for *query* in *namespace* with *arguments*.

    *run* waits on a daemon coroutine (``_daemon_store._run``).
    """
    try:
        return run(client.recall(query, namespace, **arguments))
    except Exception as exc:  # trw-fail-silent-allow: only a refused rerank argument retries; others re-raise
        if "rerank" not in arguments or not _refuses_rerank(exc):
            raise
    logger.info("daemon_recall_rerank_unsupported", namespace=namespace)
    retried = {key: value for key, value in arguments.items() if key != "rerank"}
    return run(client.recall(query, namespace, **retried))


def _refuses_rerank(exc: BaseException) -> bool:
    """Whether *exc* is the daemon's ``ToolError`` refusing ``rerank`` as an unknown argument.

    Looked up in ``sys.modules`` rather than imported: a daemon answer has already loaded
    fastmcp, and a process that never did cannot hold its error, so a failure that is not
    a daemon answer (a broken client, a transport error) never pays the ~0.2s cold import.
    """
    errors = sys.modules.get("fastmcp.exceptions")
    return errors is not None and isinstance(exc, errors.ToolError) and RERANK_REFUSED.search(str(exc)) is not None
