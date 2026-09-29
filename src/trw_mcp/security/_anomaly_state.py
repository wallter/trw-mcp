"""The anomaly detector's persisted state: the shadow clock and the write-failure policy.

Belongs to the ``anomaly_detector.py`` facade, which re-exports these names. Split out at
the persisted-state seam (2026-09-26, AGY-SANDBOX-WRITE-FAILS) to keep the facade under the
350 effective-LOC gate while adding the best-effort write policy.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import structlog
import yaml

from trw_mcp._checkout_write import UnsafeWriteError, write_checkout_file

logger = structlog.get_logger(__name__)

SHADOW_WINDOW_DAYS = 21


def _ensure_shadow_clock(path: Path, *, now: datetime | None = None) -> dict[str, str]:
    """Idempotent shadow-clock bootstrap at ``path`` (Deliverable #7).

    Writes ``{started_at, phase: "shadow", threshold_review_at}`` on first
    invocation; subsequent invocations return the existing contents.
    """
    now = now or datetime.now(tz=timezone.utc)
    if path.exists():
        try:
            raw = yaml.safe_load(path.read_text()) or {}
        except (OSError, yaml.YAMLError):  # justified: boundary, re-bootstrap on corrupt state rather than crash
            logger.warning(
                "mcp_shadow_clock_corrupt_rebootstrapping",
                path=str(path),
                outcome="rewriting",
            )
            raw = {}
        if isinstance(raw, dict) and "started_at" in raw:
            return {str(k): str(v) for k, v in raw.items()}

    payload = {
        "started_at": now.isoformat(),
        "phase": "shadow",
        "threshold_review_at": (now + timedelta(days=SHADOW_WINDOW_DAYS)).isoformat(),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        write_checkout_file(path.parent, path, yaml.safe_dump(payload, sort_keys=True))
    except UnsafeWriteError:  # trw-fail-silent-allow: a planted symlink must not block every tool call through this middleware; the clock stays in memory, and the refusal is logged below and by safe_fs (PRD-CORE-337 FR08)
        logger.warning("mcp_shadow_clock_write_refused", path=str(path), outcome="in_memory_only")
        return payload
    logger.info(
        "mcp_shadow_clock_started",
        path=str(path),
        started_at=payload["started_at"],
        threshold_review_at=payload["threshold_review_at"],
        outcome="initialized",
    )
    return payload


def _state_unwritable(path: Path | None, exc: OSError) -> None:
    """The detector's own state is best-effort (AGY-SANDBOX-WRITE-FAILS): detection keeps running in memory."""
    logger.warning("mcp_anomaly_state_unwritable", path=str(path), error=str(exc), outcome="in_memory")
