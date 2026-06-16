"""Remote recall — fetch shared learnings from platform backend.

PRD-CORE-033: Cross-project knowledge sharing via semantic search.
Fail-open: returns empty list on any failure — never blocks local operation.
PRD-DIST-124 (2026-04-30): migrated from urllib to httpx.
"""

from __future__ import annotations

import json
import os

import httpx
import structlog
from trw_memory.security.pii import redact_paths, strip_pii
from typing_extensions import TypedDict

from trw_mcp.models.config import get_config
from trw_mcp.models.typed_dicts import RemoteSharedLearningDict
from trw_mcp.state.memory_adapter import embed_text as embed

logger = structlog.get_logger(__name__)

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

    # PRD-SEC-004 redaction parity: the recall query is raw user-supplied text
    # that egresses off-machine to /v1/learnings/search. Run it through the same
    # PII/path chokepoint that sync/push.py applies to learning content so that
    # an email/secret/absolute-path typed into a recall query never leaves the
    # box verbatim. The consent gate above (platform_telemetry_enabled, default
    # False) already prevents any default egress; this is the content-redaction
    # belt behind that gate. The embedding is computed from the SANITIZED query
    # so the vector cannot reconstruct the redacted tokens either.
    project_root = os.getenv("TRW_PROJECT_ROOT", os.getcwd())
    safe_query = redact_paths(strip_pii(query), project_root) if query.strip() else query

    # Generate embedding for query
    embedding = embed(safe_query) if safe_query.strip() else None

    payload: _RecallSearchPayload = {
        "query": safe_query,
        "embedding": embedding,
        "limit": limit,
    }

    # First-success: try each backend until one responds
    for base_url in urls:
        url = f"{base_url.rstrip('/')}/v1/learnings/search"
        try:
            headers: dict[str, str] = {"Content-Type": "application/json"}
            _api_key = cfg.platform_api_key.get_secret_value()
            if _api_key:
                headers["Authorization"] = f"Bearer {_api_key}"
            with httpx.Client(timeout=float(REMOTE_RECALL_TIMEOUT)) as client:
                response = client.post(url, json=payload, headers=headers)
            if 200 <= response.status_code < 300:
                body = response.json()
                # Backend may return a list directly or {"results": [...]}
                items: list[RemoteSharedLearningDict] = body if isinstance(body, list) else body.get("results", [])
                for r in items:
                    r["summary"] = f"[shared] {r.get('summary', '')}"
                return items
        except (httpx.HTTPError, OSError, json.JSONDecodeError):
            logger.debug("remote_recall_failed", base_url=base_url)

    return []
