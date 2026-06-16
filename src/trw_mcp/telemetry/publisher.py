"""Learning publisher — publishes high-impact learnings to platform backend.

PRD-CORE-033: Cross-project knowledge sharing via backend API.
Fail-open: never raises exceptions — all errors are counted and returned.

Change tracking: maintains a content-hash sidecar so only new/modified
entries are published on each run, avoiding redundant API calls.
PRD-DIST-124 (2026-04-30): migrated from urllib to httpx.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import httpx
import structlog
from typing_extensions import TypedDict

from trw_mcp.models.config import get_config
from trw_mcp.models.typed_dicts import PublishResult
from trw_mcp.state._paths import resolve_trw_dir
from trw_mcp.state.memory_adapter import embed_text as embed
from trw_mcp.state.persistence import FileStateReader
from trw_mcp.telemetry.anonymizer import anonymize_installation_id, strip_pii

logger = structlog.get_logger(__name__)

_HASH_FILE = ".publish_hashes.json"


class _LearningPayload(TypedDict):
    """Payload structure POSTed to the backend /v1/learnings endpoint."""

    summary: str
    detail: str
    tags: list[str]
    impact: float
    embedding: list[float] | None
    source_project: str
    source_learning_id: str
    status: str


def _content_hash(data: dict[str, object]) -> str:
    """Deterministic hash of the publishable fields of a learning entry."""
    raw_tags = data.get("tags")
    tags_list: list[object] = raw_tags if isinstance(raw_tags, list) else []
    canonical = json.dumps(
        {
            "summary": str(data.get("summary", "")),
            "detail": str(data.get("detail", "")),
            "tags": sorted(str(t) for t in tags_list),
            "impact": float(str(data.get("impact", 0.5))),
            "status": str(data.get("status", "active")),
        },
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


def _load_hashes(entries_dir: Path) -> dict[str, str]:
    """Load the publish-hash sidecar file.

    Fail-open input seam: the sidecar is advisory change-tracking cache state,
    not a source of truth. Every read, decode, JSON, and shape failure degrades
    to an empty mapping so a corrupt sidecar can never break publishing — at
    worst, already-published entries are re-sent once and the sidecar is
    rewritten cleanly by ``_save_hashes``. Only structural diagnostics (path,
    reason, error class) are recorded; raw sidecar content and hash values are
    never logged.

    The previous implementation cast ``dict(json.loads(read_text(...)))``
    directly, leaking ``UnicodeDecodeError`` (non-UTF-8 bytes), ``TypeError``
    (scalar/array JSON), and ``ValueError`` (non-pair sequences) past the only
    ``(JSONDecodeError, OSError)`` guard — violating the module fail-open
    contract.
    """
    path = entries_dir / _HASH_FILE
    if not path.exists():
        return {}
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        logger.debug(
            "publish_hash_load_failed",
            path=str(path),
            reason="unreadable_or_non_utf8",
            error_class=type(exc).__name__,
        )
        return {}
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        logger.debug(
            "publish_hash_load_failed",
            path=str(path),
            reason="invalid_json",
            error_class=type(exc).__name__,
        )
        return {}
    if not isinstance(parsed, dict):
        logger.debug(
            "publish_hash_load_failed",
            path=str(path),
            reason="not_a_json_object",
            error_class=type(parsed).__name__,
        )
        return {}
    # Keep only well-formed str->str rows; malformed rows are silently dropped
    # (the affected entry re-publishes once, then gets a clean hash written back).
    return {key: value for key, value in parsed.items() if isinstance(key, str) and isinstance(value, str)}


def _save_hashes(entries_dir: Path, hashes: dict[str, str]) -> None:
    """Persist the publish-hash sidecar file."""
    path = entries_dir / _HASH_FILE
    try:
        path.write_text(json.dumps(hashes, indent=2) + "\n", encoding="utf-8")
    except OSError:
        logger.debug("publish_hash_save_failed", path=str(path))


def publish_learnings(min_impact: float = 0.5, *, force: bool = False) -> PublishResult:
    """Publish high-impact learnings to the platform backend.

    Returns dict with: published, skipped, unchanged, errors, skipped_reason.

    Args:
        min_impact: Minimum impact threshold for publishing. Default lowered from
            0.7 to 0.5 (PRD-FIX-052-FR06) to compensate for Bayesian calibration
            pulling scores toward 0.5 — entries that survive calibration at 0.5+
            are genuinely noteworthy.
        force: If True, ignore content hashes and re-publish everything.

    Fail-open: never raises exceptions.
    """
    cfg = get_config()
    urls = cfg.effective_platform_urls
    # PRD-SEC-004-FR05: learning-CONTENT publishing (full summary + detail) is
    # gated by its OWN consent flag, learning_sharing_enabled — NOT by the
    # anonymous-usage telemetry flag (platform_telemetry_enabled). Default off:
    # a user who only enabled usage telemetry never has their learning content
    # uploaded. No off-machine POST occurs unless learning_sharing_enabled=True.
    if not urls or not cfg.learning_sharing_enabled:
        return {
            "published": 0,
            "skipped": 0,
            "unchanged": 0,
            "errors": 0,
            "skipped_reason": "offline_mode",
        }

    trw_dir = resolve_trw_dir()
    entries_dir = trw_dir / "learnings" / "entries"
    if not entries_dir.exists():
        return {
            "published": 0,
            "skipped": 0,
            "unchanged": 0,
            "errors": 0,
            "skipped_reason": "no_entries",
        }

    from concurrent.futures import ThreadPoolExecutor, as_completed

    reader = FileStateReader()
    published = 0
    skipped = 0
    unchanged = 0
    errors = 0

    # PRD-SEC-004-FR08: source_project egresses the installation id with the
    # learning content — hash it (non-reversible) so no raw project-directory
    # name leaves the machine in published learnings.
    source_project = anonymize_installation_id(cfg.installation_id) if cfg.installation_id else "unknown"

    # Load content hashes from previous runs
    prev_hashes = _load_hashes(entries_dir) if not force else {}
    new_hashes: dict[str, str] = dict(prev_hashes)

    use_parallel = len(urls) > 1
    executor = ThreadPoolExecutor(max_workers=len(urls)) if use_parallel else None

    try:
        for yaml_file in sorted(entries_dir.glob("*.yaml")):
            try:
                data = reader.read_yaml(yaml_file)
                if not data:
                    continue

                status = str(data.get("status", "active"))

                impact = float(str(data.get("impact", 0.5)))
                if impact < min_impact:
                    skipped += 1
                    continue

                entry_id = str(data.get("id", yaml_file.stem))
                current_hash = _content_hash(data)

                # Skip if content hasn't changed since last successful publish
                if prev_hashes.get(entry_id) == current_hash:
                    unchanged += 1
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

                payload: _LearningPayload = {
                    "summary": summary,
                    "detail": detail,
                    "tags": [str(t) for t in tags],
                    "impact": impact,
                    "embedding": embedding,
                    "source_project": source_project,
                    "source_learning_id": entry_id,
                    "status": status,
                }

                # Fan-out: publish to all configured backends in parallel
                if executor:
                    futs = {
                        executor.submit(_post_learning, url, payload, cfg.platform_api_key.get_secret_value()): url
                        for url in urls
                    }
                    results = [f.result() for f in as_completed(futs)]
                    any_success = any(results)
                else:
                    any_success = any(
                        _post_learning(url, payload, cfg.platform_api_key.get_secret_value()) for url in urls
                    )

                if any_success:
                    published += 1
                    new_hashes[entry_id] = current_hash
                else:
                    errors += 1

            except Exception as exc:  # justified: fail-open, skip individual entry failures during publish
                logger.debug(
                    "learning_file_processing_failed",
                    file=yaml_file.name,
                    error=str(exc),
                    error_type=type(exc).__name__,
                )
                errors += 1
    finally:
        if executor:
            executor.shutdown(wait=False)
        # Persist hashes even on partial success
        _save_hashes(entries_dir, new_hashes)

    return {
        "published": published,
        "skipped": skipped,
        "unchanged": unchanged,
        "errors": errors,
        "skipped_reason": None,
    }


def _post_learning(platform_url: str, payload: _LearningPayload, api_key: str = "") -> bool:
    """POST a learning to the backend. Returns True on 2xx.

    Retries up to 3 times on 429 (rate limit) with exponential backoff.
    Logs HTTP error details for observability.
    """
    import time as _time

    url = f"{platform_url.rstrip('/')}/v1/learnings"
    max_attempts = 4

    for attempt in range(max_attempts):
        try:
            headers: dict[str, str] = {"Content-Type": "application/json"}
            if api_key:
                headers["Authorization"] = f"Bearer {api_key}"
            with httpx.Client(timeout=10.0) as client:
                response = client.post(url, json=dict(payload), headers=headers)
            if 200 <= response.status_code < 300:
                return True
            if response.status_code == 429 and attempt < max_attempts - 1:
                retry_after_hdr = response.headers.get("Retry-After", str(2**attempt))
                try:
                    retry_after = int(retry_after_hdr)
                except (TypeError, ValueError):
                    retry_after = 2**attempt
                logger.debug(
                    "learning_post_rate_limited",
                    url=platform_url,
                    retry_after=retry_after,
                    attempt=attempt + 1,
                )
                _time.sleep(min(retry_after, 10))
                continue
            body_preview = response.text[:500]
            logger.warning(
                "learning_post_failed",
                url=platform_url,
                status_code=response.status_code,
                reason=response.reason_phrase,
                response_body=body_preview,
                learning_id=payload.get("source_learning_id", ""),
            )
            return False
        except (httpx.HTTPError, OSError) as e:
            logger.warning("learning_post_failed", url=platform_url, error=str(e))
            return False
    return False
