"""FR-08 — run-scoped LRU probe cache (PRD-CORE-144)."""

from __future__ import annotations

from datetime import datetime, timezone

from trw_mcp.models.probe import ProbeEvidence, ProbeResult
from trw_mcp.probe.cache import ProbeCache, probe_cache_key


def _result(run_id: str = "run-1") -> ProbeResult:
    return ProbeResult(
        hypothesis="h",
        verdict="supports",
        evidence=ProbeEvidence(stdout="ok", exit_code=0, wall_ms=12),
        confidence=0.9,
        ts=datetime(2026, 4, 16, tzinfo=timezone.utc),
        run_id=run_id,
    )


def test_run_scoped_hit_returns_cached_with_flag() -> None:
    cache = ProbeCache()
    key = probe_cache_key(command="python -c 'pass'", hypothesis="h", hypothesis_id="H1")
    cache.put(key, _result())
    hit = cache.get(key)
    assert hit is not None
    assert hit.cache_hit is True
    assert hit.verdict == "supports"


def test_key_includes_hypothesis_id_so_stale_mapping_is_miss() -> None:
    # Grooming edge case: cached hit with stale hypothesis_id -> cache miss.
    cache = ProbeCache()
    k1 = probe_cache_key(command="c", hypothesis="h", hypothesis_id="H1")
    k2 = probe_cache_key(command="c", hypothesis="h", hypothesis_id="H2")
    assert k1 != k2
    cache.put(k1, _result())
    assert cache.get(k2) is None


def test_miss_returns_none() -> None:
    cache = ProbeCache()
    assert cache.get("nonexistent") is None


def test_byte_ceiling_evicts_lru() -> None:
    # FR-08 A2: cache size bounded; LRU eviction. Use a tiny ceiling.
    cache = ProbeCache(max_bytes=400)
    k1 = probe_cache_key(command="c1", hypothesis="h", hypothesis_id="H1")
    k2 = probe_cache_key(command="c2", hypothesis="h", hypothesis_id="H2")
    cache.put(k1, _result("run-a"))
    cache.put(k2, _result("run-b"))
    # Total exceeds 400 bytes -> oldest (k1) evicted.
    assert cache.get(k1) is None
    assert cache.get(k2) is not None
    assert cache.total_bytes <= 400


def test_oversized_result_not_cached() -> None:
    cache = ProbeCache(max_bytes=10)
    key = probe_cache_key(command="c", hypothesis="h", hypothesis_id="H1")
    cache.put(key, _result())
    assert cache.get(key) is None
    assert len(cache) == 0
