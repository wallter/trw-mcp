"""Tests for IntelligenceCache — PRD-INFRA-053."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest


def test_cache_update_and_read(tmp_path: Path) -> None:
    """Write state then read back bandit_params."""
    from trw_mcp.sync.cache import IntelligenceCache

    cache = IntelligenceCache(trw_dir=tmp_path, ttl_seconds=3600)
    state = {"bandit_params": {"L-1": 1.3, "L-2": 0.8}}
    cache.update(state, etag="etag-v1")

    params = cache.get_bandit_params()
    assert params is not None
    assert params["L-1"] == 1.3
    assert params["L-2"] == 0.8


def test_cache_expired_returns_none(tmp_path: Path) -> None:
    """Cache with TTL=0 returns None (expired immediately)."""
    from trw_mcp.sync.cache import IntelligenceCache

    cache = IntelligenceCache(trw_dir=tmp_path, ttl_seconds=0)
    cache.update({"bandit_params": {"L-1": 1.0}}, etag="old")

    # TTL=0 means cache is always expired
    assert cache.get_bandit_params() is None


def test_cache_missing_returns_none(tmp_path: Path) -> None:
    """Cache with no file returns None for all accessors."""
    from trw_mcp.sync.cache import IntelligenceCache

    cache = IntelligenceCache(trw_dir=tmp_path)
    assert cache.get_bandit_params() is None
    assert cache.get_attribution_results() is None
    assert cache.get_synthesis_overlay() is None
    assert cache.etag is None
    assert not cache.is_fresh


def test_cache_atomic_write(tmp_path: Path) -> None:
    """After update, no .tmp files remain (atomic rename cleanup)."""
    from trw_mcp.sync.cache import IntelligenceCache

    cache = IntelligenceCache(trw_dir=tmp_path, ttl_seconds=3600)
    cache.update({"bandit_params": {"L-1": 1.0}}, etag="v1")

    # Verify no .tmp files remain
    tmp_files = list(tmp_path.glob("*.tmp"))
    assert len(tmp_files) == 0

    # Verify the cache file exists
    cache_file = tmp_path / "intel-cache.json"
    assert cache_file.exists()


def test_cache_get_bandit_params(tmp_path: Path) -> None:
    """Write state with bandit_params, read back correctly."""
    from trw_mcp.sync.cache import IntelligenceCache

    cache = IntelligenceCache(trw_dir=tmp_path, ttl_seconds=3600)
    state = {
        "bandit_params": {"L-001": 1.5, "L-002": 0.7, "L-003": 1.0},
        "attribution_results": {"L-001": {"causal_score": 0.8}},
    }
    cache.update(state, etag="v2")

    params = cache.get_bandit_params()
    assert params is not None
    assert len(params) == 3
    assert params["L-001"] == 1.5


def test_cache_is_fresh_true_within_ttl(tmp_path: Path) -> None:
    """Cache written just now is fresh."""
    from trw_mcp.sync.cache import IntelligenceCache

    cache = IntelligenceCache(trw_dir=tmp_path, ttl_seconds=3600)
    cache.update({"bandit_params": {}}, etag="v1")

    assert cache.is_fresh


def test_cache_is_fresh_false_when_expired(tmp_path: Path) -> None:
    """Cache with TTL=0 is not fresh."""
    from trw_mcp.sync.cache import IntelligenceCache

    cache = IntelligenceCache(trw_dir=tmp_path, ttl_seconds=0)
    cache.update({"bandit_params": {}}, etag="v1")

    assert not cache.is_fresh


def test_cache_etag_property(tmp_path: Path) -> None:
    """etag property returns stored ETag value."""
    from trw_mcp.sync.cache import IntelligenceCache

    cache = IntelligenceCache(trw_dir=tmp_path, ttl_seconds=3600)
    assert cache.etag is None  # Before any write

    cache.update({"bandit_params": {}}, etag="my-etag-123")
    assert cache.etag == "my-etag-123"

    # Update with new etag
    cache.update({"bandit_params": {}}, etag="my-etag-456")
    assert cache.etag == "my-etag-456"


def test_cache_etag_none_when_expired(tmp_path: Path) -> None:
    """Expired caches do not keep serving stale ETags into conditional pulls."""
    from trw_mcp.sync.cache import IntelligenceCache

    cache = IntelligenceCache(trw_dir=tmp_path, ttl_seconds=0)
    cache.update({"bandit_params": {}}, etag="stale-etag")

    assert cache.etag is None


def test_cache_get_attribution_results(tmp_path: Path) -> None:
    """Write and read attribution_results."""
    from trw_mcp.sync.cache import IntelligenceCache

    cache = IntelligenceCache(trw_dir=tmp_path, ttl_seconds=3600)
    cache.update(
        {"attribution_results": {"L-1": {"causal_score": 0.9, "confidence": 0.7}}},
        etag="v1",
    )
    results = cache.get_attribution_results()
    assert results is not None
    assert results["L-1"]["causal_score"] == 0.9


def test_cache_get_synthesis_overlay(tmp_path: Path) -> None:
    """Write and read synthesis_overlay."""
    from trw_mcp.sync.cache import IntelligenceCache

    cache = IntelligenceCache(trw_dir=tmp_path, ttl_seconds=3600)
    cache.update(
        {"synthesis_overlay": {"cluster_count": 5, "top_topics": ["testing", "auth"]}},
        etag="v1",
    )
    overlay = cache.get_synthesis_overlay()
    assert overlay is not None
    assert overlay["cluster_count"] == 5


def test_cache_corrupt_file_returns_none(tmp_path: Path) -> None:
    """Corrupt cache file returns None gracefully."""
    from trw_mcp.sync.cache import IntelligenceCache

    cache = IntelligenceCache(trw_dir=tmp_path, ttl_seconds=3600)
    cache_file = tmp_path / "intel-cache.json"
    cache_file.write_text("NOT VALID JSON {{{{")

    assert cache.get_bandit_params() is None
    assert cache.etag is None


def test_cache_file_permissions(tmp_path: Path) -> None:
    """Cache file is written with restricted permissions (0o600)."""
    from trw_mcp.sync.cache import IntelligenceCache
    import stat

    cache = IntelligenceCache(trw_dir=tmp_path, ttl_seconds=3600)
    cache.update({"bandit_params": {}}, etag="v1")

    cache_file = tmp_path / "intel-cache.json"
    mode = stat.S_IMODE(cache_file.stat().st_mode)
    assert mode == 0o600


def test_cache_etag_none_when_empty_string(tmp_path: Path) -> None:
    """etag returns None when stored as empty string (no etag provided)."""
    from trw_mcp.sync.cache import IntelligenceCache

    cache = IntelligenceCache(trw_dir=tmp_path, ttl_seconds=3600)
    cache.update({"bandit_params": {}})  # No etag provided

    assert cache.etag is None
