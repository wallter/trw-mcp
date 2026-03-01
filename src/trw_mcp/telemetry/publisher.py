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

    from concurrent.futures import ThreadPoolExecutor, as_completed

    reader = FileStateReader()
    published = 0
    skipped = 0
    errors = 0

    # Create executor once; reuse for all entries.
    # Each URL is attempted independently so a slow/failing backend
    # never blocks delivery to the others.
    use_parallel = len(urls) > 1
    executor = ThreadPoolExecutor(max_workers=len(urls)) if use_parallel else None

    try:
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

                # Fan-out: publish to all configured backends in parallel
                if executor:
                    futs = {
                        executor.submit(_post_learning, url, payload, cfg.platform_api_key): url
                        for url in urls
                    }
                    # Collect ALL results (don't short-circuit — every URL must be attempted)
                    results = [f.result() for f in as_completed(futs)]
                    any_success = any(results)
                else:
                    any_success = any(
                        _post_learning(url, payload, cfg.platform_api_key)
                        for url in urls
                    )

                if any_success:
                    published += 1
                else:
                    errors += 1

            except Exception:
                logger.debug("Failed to process learning file %s", yaml_file.name)
                errors += 1
    finally:
        if executor:
            executor.shutdown(wait=False)

    return {"published": published, "skipped": skipped, "errors": errors, "skipped_reason": None}


def _post_learning(platform_url: str, payload: dict[str, Any], api_key: str = "") -> bool:
    """POST a learning to the backend. Returns True on 2xx.

    Retries once on 429 (rate limit) after respecting the Retry-After header.
    Logs HTTP error details for observability.
    """
    import time as _time

    url = f"{platform_url.rstrip('/')}/v1/learnings"
    max_attempts = 2

    for attempt in range(max_attempts):
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
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < max_attempts - 1:
                retry_after = int(e.headers.get("Retry-After", "2"))
                logger.debug(
                    "Rate limited (429) by %s, retrying after %ds",
                    platform_url, retry_after,
                )
                _time.sleep(min(retry_after, 5))
                continue
            logger.warning(
                "Learning POST failed: %s returned HTTP %d: %s",
                platform_url, e.code, e.reason,
            )
            return False
        except (urllib.error.URLError, OSError) as e:
            logger.warning("Learning POST failed: %s — %s", platform_url, e)
            return False
    return False
