"""Remote recall — fetch shared learnings from platform backend.

PRD-CORE-033: Cross-project knowledge sharing via semantic search.
Fail-open: returns empty list on any failure — never blocks local operation.
"""

from __future__ import annotations

import json
import structlog
import urllib.error
import urllib.request
from typing import Any

from trw_mcp.models.config import get_config
from trw_mcp.telemetry.embeddings import embed

logger = structlog.get_logger()

REMOTE_RECALL_TIMEOUT = 3  # seconds


def fetch_shared_learnings(query: str = "", limit: int = 5) -> list[dict[str, Any]]:
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

    payload: dict[str, Any] = {
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
            if cfg.platform_api_key:
                headers["Authorization"] = f"Bearer {cfg.platform_api_key}"
            req = urllib.request.Request(
                url,
                data=data,
                headers=headers,
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=REMOTE_RECALL_TIMEOUT) as response:
                if 200 <= response.status < 300:
                    body = json.loads(response.read().decode("utf-8"))
                    # Backend may return a list directly or {"results": [...]}
                    items: list[dict[str, Any]] = (
                        body if isinstance(body, list) else body.get("results", [])
                    )
                    for r in items:
                        r["summary"] = f"[shared] {r.get('summary', '')}"
                    return items
        except (urllib.error.URLError, urllib.error.HTTPError, OSError, json.JSONDecodeError):
            logger.debug("remote_recall_failed", base_url=base_url)

    return []
