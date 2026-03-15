"""Remote recall — fetch shared learnings from platform backend.

PRD-CORE-033: Cross-project knowledge sharing via semantic search.
Fail-open: returns empty list on any failure — never blocks local operation.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import TypedDict

import structlog

from trw_mcp.models.config import get_config
from trw_mcp.models.typed_dicts import RemoteSharedLearningDict
from trw_mcp.state.memory_adapter import embed_text as embed

logger = structlog.get_logger()

REMOTE_RECALL_TIMEOUT = 3  # seconds


class _RecallSearchPayload(TypedDict):
    """Payload structure for the /v1/learnings/search endpoint."""

    query: str
    embedding: list[float] | None
    limit: int


def fetch_shared_learnings(query: str = "", limit: int = 5) -> list[RemoteSharedLearningDict]:
    """Fetch shared learnings from the platform backend.

    Returns list of learning dicts with [shared] label prefix.
    Returns empty list on any failure (fail-open).
    """
    cfg = get_config()
    urls = cfg.effective_platform_urls
    if not urls or not cfg.platform_telemetry_enabled:
        return []

    # Generate embedding for query
    embedding = embed(query) if query.strip() else None

    payload: _RecallSearchPayload = {
        "query": query,
        "embedding": embedding,
        "limit": limit,
    }

    # First-success: try each backend until one responds
    for base_url in urls:
        url = f"{base_url.rstrip('/')}/v1/learnings/search"
        try:
            data = json.dumps(payload).encode("utf-8")
            headers: dict[str, str] = {"Content-Type": "application/json"}
            _api_key = cfg.platform_api_key.get_secret_value()
            if _api_key:
                headers["Authorization"] = f"Bearer {_api_key}"
            req = urllib.request.Request(  # noqa: S310 — URL built from cfg.effective_platform_urls (operator-configured TRW platform endpoint, not user input)
                url,
                data=data,
                headers=headers,
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=REMOTE_RECALL_TIMEOUT) as response:  # noqa: S310 — see Request comment above
                if 200 <= response.status < 300:
                    body = json.loads(response.read().decode("utf-8"))
                    # Backend may return a list directly or {"results": [...]}
                    items: list[RemoteSharedLearningDict] = body if isinstance(body, list) else body.get("results", [])
                    for r in items:
                        r["summary"] = f"[shared] {r.get('summary', '')}"
                    return items
        except (urllib.error.URLError, urllib.error.HTTPError, OSError, json.JSONDecodeError):
            logger.debug("remote_recall_failed", base_url=base_url)

    return []
