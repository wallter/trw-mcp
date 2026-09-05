"""Reuse of a persisted CLEAN verification verdict (PRD-CORE-244 FR03).

Belongs to the ``_recall_assertion_verification.py`` pass. Before FR03 the
verdict vocabulary had no positive value and no timestamp, so every recall
re-ran the full filesystem verification for every candidate entry — there was
nothing durable to reuse. ``verification_checked_at`` makes the pass cacheable:
inside ``TRWConfig.verification_cache_ttl_seconds`` a ``"verified"`` verdict is
reused and no file is read for that entry.

The reuse is deliberately ASYMMETRIC. Only a clean verdict is warm. An adverse
verdict (``"stale"``) and an inconclusive one (``None``) are always re-examined,
because those are the entries whose situation an operator is actively fixing —
reusing them would keep a repaired claim convicted for up to the TTL, and
PRD-CORE-231-FR02 AC2 requires a re-passing assertion to clear its verdict on
the very next pass.

Fail-open throughout: a lookup that cannot answer returns ``None``, which means
"verify it properly", never "assume it is fine".
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


def warm_verified_verdict(
    backend: Any | None,
    entry_id: str,
    *,
    namespace: str,
    ttl_seconds: int,
    now: datetime | None = None,
) -> str | None:
    """Return the exam timestamp when *entry_id* holds a reusable clean verdict.

    ``None`` means "no reusable verdict, run the pass" — no backend, reuse
    disabled (``ttl_seconds == 0``), no such row, a non-``"verified"`` verdict,
    never examined (empty ``verification_checked_at``), an unparseable or
    future-dated stamp, or an expired one. Every one of those falls through to a
    real verification pass, so the cache can only ever save work, never invent a
    verdict.
    """
    if backend is None or ttl_seconds <= 0 or not entry_id:
        return None
    try:
        entry = backend.get(entry_id, namespace=namespace)
    except Exception:  # justified: fail-open, a cache miss just means "verify"
        logger.debug("verification_cache_lookup_failed", entry_id=entry_id, exc_info=True)
        return None
    if entry is None or getattr(entry, "verification_status", None) != "verified":
        return None

    checked_at = str(getattr(entry, "verification_checked_at", "") or "")
    if not checked_at:
        # "verified" without a stamp cannot be aged, so it cannot be trusted to
        # still hold. Re-verify rather than reuse it forever.
        return None
    try:
        stamped = datetime.fromisoformat(checked_at)
    except ValueError:
        logger.debug("verification_cache_stamp_unparseable", entry_id=entry_id)
        return None
    if stamped.tzinfo is None:
        stamped = stamped.replace(tzinfo=timezone.utc)

    age_seconds = ((now or datetime.now(timezone.utc)) - stamped).total_seconds()
    # A negative age is a clock-skewed or hand-edited stamp. Re-verifying is the
    # safe direction: the alternative pins a verdict until the future catches up.
    if age_seconds < 0 or age_seconds > ttl_seconds:
        return None
    return checked_at


__all__ = ["warm_verified_verdict"]
