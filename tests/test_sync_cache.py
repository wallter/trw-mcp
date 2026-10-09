"""Tests for IntelligenceCache — PRD-INFRA-053."""

from __future__ import annotations

import json
import math
import time
from pathlib import Path
from unittest.mock import patch

from tests._layout import requires_local_timing
from tests._timing import assert_budget


def _build_large_state() -> dict[str, object]:
    """Generate a payload large enough to exercise cache performance paths."""
    return {
        "attribution_results": {f"L-{idx:05d}": round((idx % 100) / 100, 4) for idx in range(4000)},
        "synthesis_overlay": {"overlay_content": "x" * 350_000},
    }


def _p99_ms(samples: list[float]) -> float:
    ordered = sorted(samples)
    percentile_index = max(math.ceil(len(ordered) * 0.99) - 1, 0)
    return ordered[percentile_index] * 1000


def test_cache_update_and_read(tmp_path: Path) -> None:
    """update() persists the state to disk and the etag round-trips."""
    from trw_mcp.sync.cache import IntelligenceCache

    cache = IntelligenceCache(trw_dir=tmp_path, ttl_seconds=3600)
    state = {"attribution_results": {"L-1": 1.3, "L-2": 0.8}}
    cache.update(state, etag="etag-v1")

    assert cache.etag == "etag-v1"
    on_disk = json.loads((tmp_path / "intel-cache.json").read_text())
    assert on_disk["attribution_results"] == {"L-1": 1.3, "L-2": 0.8}


def test_cache_expired_returns_none(tmp_path: Path) -> None:
    """Cache with TTL=0 serves no etag (expired immediately)."""
    from trw_mcp.sync.cache import IntelligenceCache

    cache = IntelligenceCache(trw_dir=tmp_path, ttl_seconds=0)
    cache.update({"attribution_results": {"L-1": 1.0}}, etag="old")

    # TTL=0 means cache is always expired
    assert cache.etag is None


def test_cache_expired_logs_age_and_ttl(tmp_path: Path) -> None:
    """Expired reads emit the structured expiration event with age and TTL fields."""
    from trw_mcp.sync.cache import IntelligenceCache

    cache = IntelligenceCache(trw_dir=tmp_path, ttl_seconds=0)
    cache.update({"attribution_results": {"L-1": 1.0}}, etag="old")

    with patch("trw_mcp.sync.cache.logger.debug") as mock_debug:
        assert cache.etag is None

    expired_call = next(call for call in mock_debug.call_args_list if call.args == ("intel_cache_expired",))
    assert expired_call.kwargs["age_seconds"] >= 0
    assert expired_call.kwargs["ttl_seconds"] == 0


def test_cache_missing_returns_none(tmp_path: Path) -> None:
    """Cache with no file serves no etag."""
    from trw_mcp.sync.cache import IntelligenceCache

    cache = IntelligenceCache(trw_dir=tmp_path)
    assert cache.etag is None


def test_cache_atomic_write(tmp_path: Path) -> None:
    """After update, no .tmp files remain (atomic rename cleanup)."""
    from trw_mcp.sync.cache import IntelligenceCache

    cache = IntelligenceCache(trw_dir=tmp_path, ttl_seconds=3600)
    cache.update({"attribution_results": {"L-1": 1.0}}, etag="v1")

    # Verify no .tmp files remain
    tmp_files = list(tmp_path.glob("*.tmp"))
    assert len(tmp_files) == 0

    # Verify the cache file exists
    cache_file = tmp_path / "intel-cache.json"
    assert cache_file.exists()


def test_a_leftover_bandit_params_key_is_ignored(tmp_path: Path) -> None:
    """PRD-CORE-303 FR02: an old cache's bandit_params/attribution_results keys stay on disk, never read."""
    from trw_mcp.sync.cache import IntelligenceCache

    cache = IntelligenceCache(trw_dir=tmp_path, ttl_seconds=3600)
    cache.update(
        {"bandit_params": "not-a-dict", "attribution_results": {"L-001": {"causal_score": 0.8}}},
        etag="v2",
    )

    assert not hasattr(cache, "get_bandit_params")
    with patch("trw_mcp.sync.cache.logger.warning") as mock_warning:
        assert cache.etag == "v2"
    mock_warning.assert_not_called()


def test_cache_etag_property(tmp_path: Path) -> None:
    """etag property returns stored ETag value."""
    from trw_mcp.sync.cache import IntelligenceCache

    cache = IntelligenceCache(trw_dir=tmp_path, ttl_seconds=3600)
    assert cache.etag is None  # Before any write

    cache.update({"attribution_results": {}}, etag="my-etag-123")
    assert cache.etag == "my-etag-123"

    # Update with new etag
    cache.update({"attribution_results": {}}, etag="my-etag-456")
    assert cache.etag == "my-etag-456"


def _age_cache(tmp_path: Path, seconds: int) -> None:
    """Backdate the stored ``updated_at`` so no test has to sleep."""
    from datetime import datetime, timedelta, timezone

    cache_file = tmp_path / "intel-cache.json"
    data = json.loads(cache_file.read_text())
    data["_meta"]["updated_at"] = (datetime.now(tz=timezone.utc) - timedelta(seconds=seconds)).isoformat()
    cache_file.write_text(json.dumps(data))


def test_etag_served_inside_the_ttl(tmp_path: Path) -> None:
    """A cache younger than its TTL serves the stored etag."""
    from trw_mcp.sync.cache import IntelligenceCache

    cache = IntelligenceCache(trw_dir=tmp_path, ttl_seconds=3600)
    cache.update({"attribution_results": {}}, etag="v1")
    _age_cache(tmp_path, 3500)

    assert cache.etag == "v1"


def test_etag_is_none_once_the_ttl_has_passed(tmp_path: Path) -> None:
    """A cache older than its TTL serves no etag, forcing a full pull."""
    from trw_mcp.sync.cache import IntelligenceCache

    cache = IntelligenceCache(trw_dir=tmp_path, ttl_seconds=3600)
    cache.update({"attribution_results": {}}, etag="v1")
    _age_cache(tmp_path, 3700)

    assert cache.etag is None


def test_cache_etag_none_when_expired(tmp_path: Path) -> None:
    """Expired caches do not keep serving stale ETags into conditional pulls."""
    from trw_mcp.sync.cache import IntelligenceCache

    cache = IntelligenceCache(trw_dir=tmp_path, ttl_seconds=0)
    cache.update({"attribution_results": {}}, etag="stale-etag")

    assert cache.etag is None


def test_cache_update_logs_payload_size_and_etag(tmp_path: Path) -> None:
    """Successful writes emit the structured observability fields required by the PRD."""
    from trw_mcp.sync.cache import IntelligenceCache

    cache = IntelligenceCache(trw_dir=tmp_path, ttl_seconds=3600)

    with patch("trw_mcp.sync.cache.logger.debug") as mock_debug:
        cache.update({"attribution_results": {"L-1": 1.0}}, etag="etag-v1")

    mock_debug.assert_called_once()
    args, kwargs = mock_debug.call_args
    assert args == ("intel_cache_write_success",)
    assert kwargs["event_type"] == "intel_cache_write_success"
    assert kwargs["etag"] == "etag-v1"
    assert kwargs["payload_size_bytes"] > 0
    assert kwargs["outcome"] == "success"


def test_cache_write_error_logs_error_type(tmp_path: Path) -> None:
    """Write failures include a typed error for troubleshooting."""
    from trw_mcp.sync.cache import IntelligenceCache

    cache = IntelligenceCache(trw_dir=tmp_path, ttl_seconds=3600)

    with (
        patch("trw_mcp.sync.cache.tempfile.mkstemp", side_effect=PermissionError("denied")),
        patch("trw_mcp.sync.cache.logger.warning") as mock_warning,
    ):
        cache.update({"attribution_results": {"L-1": 1.0}}, etag="etag-v1")

    mock_warning.assert_called_once()
    args, kwargs = mock_warning.call_args
    assert args == ("intel_cache_write_error",)
    assert kwargs["event_type"] == "intel_cache_write_error"
    assert kwargs["error_type"] == "PermissionError"
    assert kwargs["outcome"] == "error"
    assert kwargs["exc_info"] is True


def test_cache_read_logs_freshness_metadata(tmp_path: Path) -> None:
    """Fresh reads emit age/freshness metadata for observability."""
    from trw_mcp.sync.cache import IntelligenceCache

    cache = IntelligenceCache(trw_dir=tmp_path, ttl_seconds=3600)
    cache.update({"attribution_results": {"L-1": 1.0}}, etag="etag-v1")

    with patch("trw_mcp.sync.cache.logger.debug") as mock_debug:
        etag = cache.etag

    assert etag == "etag-v1"
    read_call = next(call for call in mock_debug.call_args_list if call.args == ("intel_cache_read",))
    assert read_call.kwargs["event_type"] == "intel_cache_read"
    assert read_call.kwargs["is_fresh"] is True
    assert read_call.kwargs["age_seconds"] >= 0


@requires_local_timing
def test_cache_update_p99_under_50ms_for_large_payload(tmp_path: Path) -> None:
    """Large cache writes stay within the PRD latency budget."""
    from trw_mcp.sync.cache import IntelligenceCache

    cache = IntelligenceCache(trw_dir=tmp_path, ttl_seconds=3600)
    state = _build_large_state()
    cache.update(state, etag="warmup")

    samples: list[float] = []
    for idx in range(20):
        started_at = time.perf_counter()
        cache.update(state, etag=f"etag-{idx}")
        samples.append(time.perf_counter() - started_at)

    assert_budget("cache_update_p99_large_payload", _p99_ms(samples), 50.0, "ms")


def test_cache_read_p99_under_10ms_for_large_payload(tmp_path: Path) -> None:
    """Large cache reads return valid data.

    The p99 latency budget is a host-resource measurement, moved to
    ``test_cache_read_p99_under_10ms_for_large_payload_budget``
    (``requires_local_timing``, skipped on CI). This test keeps the
    deterministic non-None assertion gating.
    """
    from trw_mcp.sync.cache import IntelligenceCache

    cache = IntelligenceCache(trw_dir=tmp_path, ttl_seconds=3600)
    state = _build_large_state()
    cache.update(state, etag="etag-v1")

    assert cache.etag == "etag-v1"


@requires_local_timing
def test_cache_read_p99_under_10ms_for_large_payload_budget(tmp_path: Path) -> None:
    """Large cache reads stay within the PRD latency budget."""
    from trw_mcp.sync.cache import IntelligenceCache

    cache = IntelligenceCache(trw_dir=tmp_path, ttl_seconds=3600)
    state = _build_large_state()
    cache.update(state, etag="etag-v1")

    samples: list[float] = []
    for _ in range(100):
        started_at = time.perf_counter()
        cache.etag
        samples.append(time.perf_counter() - started_at)
    assert_budget("cache_read_p99_large_payload", _p99_ms(samples), 10.0, "ms")


def test_cache_corrupt_file_returns_none(tmp_path: Path) -> None:
    """Corrupt cache file returns None gracefully."""
    from trw_mcp.sync.cache import IntelligenceCache

    cache = IntelligenceCache(trw_dir=tmp_path, ttl_seconds=3600)
    cache_file = tmp_path / "intel-cache.json"
    cache_file.write_text("NOT VALID JSON {{{{")

    assert cache.etag is None


def test_cache_corrupt_file_logs_file_size_and_error_type(tmp_path: Path) -> None:
    """Corrupt cache reads expose file-size and exception type in the warning event."""
    from trw_mcp.sync.cache import IntelligenceCache

    cache = IntelligenceCache(trw_dir=tmp_path, ttl_seconds=3600)
    cache_file = tmp_path / "intel-cache.json"
    cache_file.write_text("NOT VALID JSON {{{{")

    with patch("trw_mcp.sync.cache.logger.warning") as mock_warning:
        assert cache.etag is None

    args, kwargs = mock_warning.call_args
    assert args == ("intel_cache_corrupt",)
    assert kwargs["event_type"] == "intel_cache_corrupt"
    assert kwargs["error_type"] == "JSONDecodeError"
    assert kwargs["file_size_bytes"] == cache_file.stat().st_size
    assert kwargs["exc_info"] is True


def test_cache_file_permissions(tmp_path: Path) -> None:
    """Cache file is written with restricted permissions (0o600)."""
    import stat

    from trw_mcp.sync.cache import IntelligenceCache

    cache = IntelligenceCache(trw_dir=tmp_path, ttl_seconds=3600)
    cache.update({"attribution_results": {}}, etag="v1")

    cache_file = tmp_path / "intel-cache.json"
    mode = stat.S_IMODE(cache_file.stat().st_mode)
    assert mode == 0o600


def test_cache_etag_none_when_empty_string(tmp_path: Path) -> None:
    """etag returns None when stored as empty string (no etag provided)."""
    from trw_mcp.sync.cache import IntelligenceCache

    cache = IntelligenceCache(trw_dir=tmp_path, ttl_seconds=3600)
    cache.update({"attribution_results": {}})  # No etag provided

    assert cache.etag is None


def test_cache_validation_error_logged_for_invalid_meta(tmp_path: Path) -> None:
    """Invalid metadata structures are rejected with a precise field-level validation event."""
    from trw_mcp.sync.cache import IntelligenceCache

    cache = IntelligenceCache(trw_dir=tmp_path, ttl_seconds=3600)
    cache_file = tmp_path / "intel-cache.json"
    cache_file.write_text(
        json.dumps(
            {
                "attribution_results": {"L-1": 1.0},
                "_meta": {"etag": "e", "ttl_seconds": 3600, "updated_at": "not-an-iso8601-timestamp"},
            }
        )
    )

    with patch("trw_mcp.sync.cache.logger.warning") as mock_warning:
        assert cache.etag is None

    args, kwargs = mock_warning.call_args
    assert args == ("intel_cache_validation_error",)
    assert kwargs["event_type"] == "intel_cache_validation_error"
    assert kwargs["field_name"] == "_meta.updated_at"
    assert kwargs["reason"] == "invalid_iso8601"
