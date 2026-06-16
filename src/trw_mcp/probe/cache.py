"""Run-scoped LRU probe result cache (PRD-CORE-144 FR-08).

Belongs to the ``probe`` facade. Re-exported from ``probe/__init__.py``.

Cache keys are content-addressed via BLAKE2b over the full
``(command, hypothesis, hypothesis_id, env_fingerprint)`` tuple (RISK-006:
no hash collisions serving forged results). The cache is run-scoped by
default; cross-session caching is OFF unless ``TRW_PROBE_CACHE_CROSS_SESSION``
is set (FR-08 A3) — that opt-in is honored by the harness, not this store.
"""

from __future__ import annotations

import hashlib
from collections import OrderedDict

import structlog

from trw_mcp.models.probe import ProbeResult

logger = structlog.get_logger(__name__)

#: Per-session cache ceiling (FR-08 A2 / NFR-05): 10MB.
_MAX_CACHE_BYTES = 10 * 1024 * 1024


def probe_cache_key(
    *,
    command: str,
    hypothesis: str,
    hypothesis_id: str | None,
    env_fingerprint: str = "",
) -> str:
    """Content-addressed cache key (BLAKE2b over the full probe tuple).

    Including ``hypothesis_id`` in the tuple means a cached entry with a stale
    hypothesis_id mapping is a cache MISS (grooming edge case) — the keys
    differ, so the prior result is not served.
    """
    hasher = hashlib.blake2b(digest_size=16)
    hasher.update(command.encode("utf-8"))
    hasher.update(b"\x00")
    hasher.update(hypothesis.encode("utf-8"))
    hasher.update(b"\x00")
    hasher.update((hypothesis_id or "").encode("utf-8"))
    hasher.update(b"\x00")
    hasher.update(env_fingerprint.encode("utf-8"))
    return hasher.hexdigest()


class ProbeCache:
    """Bounded LRU cache of ``ProbeResult`` keyed by content hash.

    Eviction is LRU on both entry-count overflow and the per-session byte
    ceiling (``_MAX_CACHE_BYTES``). Cache hits are served in O(1).
    """

    def __init__(self, *, max_bytes: int = _MAX_CACHE_BYTES) -> None:
        self._max_bytes = max_bytes
        self._store: OrderedDict[str, ProbeResult] = OrderedDict()
        self._sizes: dict[str, int] = {}
        self._total_bytes = 0

    def get(self, key: str) -> ProbeResult | None:
        """Return the cached result for ``key`` (LRU-touch), or ``None``."""
        result = self._store.get(key)
        if result is None:
            return None
        self._store.move_to_end(key)
        return result.model_copy(update={"cache_hit": True})

    def put(self, key: str, result: ProbeResult) -> None:
        """Store ``result`` under ``key``, evicting LRU entries as needed."""
        if key in self._store:
            self._total_bytes -= self._sizes.pop(key, 0)
            del self._store[key]
        size = len(result.model_dump_json().encode("utf-8"))
        # A single oversized result is not cached rather than thrashing the
        # whole store (keeps cache-put fail-open).
        if size > self._max_bytes:
            logger.warning(
                "probe_cache_skip_oversized",
                component="probe.cache",
                op="put",
                outcome="skipped",
                size=size,
            )
            return
        self._store[key] = result
        self._sizes[key] = size
        self._total_bytes += size
        while self._total_bytes > self._max_bytes and self._store:
            evicted_key, _ = self._store.popitem(last=False)
            self._total_bytes -= self._sizes.pop(evicted_key, 0)

    def __len__(self) -> int:
        return len(self._store)

    @property
    def total_bytes(self) -> int:
        return self._total_bytes


__all__ = ["ProbeCache", "probe_cache_key"]
