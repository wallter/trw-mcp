"""Learning publisher — publishes high-impact learnings to platform backend.

PRD-CORE-033: Cross-project knowledge sharing via backend API.
Fail-open: never raises exceptions — all errors are counted and returned.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from typing import Any

from trw_mcp.models.config import get_config
from trw_mcp.state._paths import resolve_trw_dir
from trw_mcp.state.persistence import FileStateReader
from trw_mcp.telemetry.anonymizer import strip_pii
from trw_mcp.telemetry.embeddings import embed

logger = logging.getLogger(__name__)


def publish_learnings(min_impact: float = 0.7) -> dict[str, object]:
    """Publish high-impact learnings to the platform backend.

    Returns dict with: published, skipped, errors, skipped_reason.
    Fail-open: never raises exceptions.
    """
    cfg = get_config()
    urls = cfg.effective_platform_urls
    if not urls or not cfg.platform_telemetry_enabled:
        return {"published": 0, "skipped": 0, "errors": 0, "skipped_reason": "offline_mode"}

    trw_dir = resolve_trw_dir()
    entries_dir = trw_dir / "learnings" / "entries"
    if not entries_dir.exists():
        return {"published": 0, "skipped": 0, "errors": 0, "skipped_reason": "no_entries"}

    reader = FileStateReader()
    published = 0
    skipped = 0
    errors = 0

    for yaml_file in sorted(entries_dir.glob("*.yaml")):
        try:
            data = reader.read_yaml(yaml_file)
            if not data:
                continue

            status = str(data.get("status", "active"))
            if status != "active":
                skipped += 1
                continue

            impact = float(str(data.get("impact", 0.5)))
            if impact < min_impact:
                skipped += 1
                continue

            # Anonymize content
            summary = strip_pii(str(data.get("summary", "")))
            detail = strip_pii(str(data.get("detail", "")))

            # Generate embedding
            embed_text = f"{summary} {detail}".strip()
            embedding = embed(embed_text)

            tags = data.get("tags", [])
            if not isinstance(tags, list):
                tags = []

            payload: dict[str, Any] = {
                "summary": summary,
                "detail": detail,
                "tags": [str(t) for t in tags],
                "impact": impact,
                "embedding": embedding,
                "source_project": cfg.installation_id or "unknown",
                "source_learning_id": str(data.get("id", "")),
            }

            # Fan-out: publish to all configured backends
            any_success = False
            for url in urls:
                if _post_learning(url, payload, cfg.platform_api_key):
                    any_success = True
            if any_success:
                published += 1
            else:
                errors += 1

        except Exception:
            logger.debug("Failed to process learning file %s", yaml_file.name)
            errors += 1

    return {"published": published, "skipped": skipped, "errors": errors, "skipped_reason": None}


def _post_learning(platform_url: str, payload: dict[str, Any], api_key: str = "") -> bool:
    """POST a learning to the backend. Returns True on 2xx."""
    url = f"{platform_url.rstrip('/')}/v1/learnings"
    try:
        data = json.dumps(payload).encode("utf-8")
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        req = urllib.request.Request(
            url,
            data=data,
            headers=headers,
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as response:
            return bool(200 <= response.status < 300)
    except (urllib.error.URLError, urllib.error.HTTPError, OSError):
        return False
