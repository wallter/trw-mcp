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

import structlog

logger = structlog.get_logger(__name__)


__all__ = []
