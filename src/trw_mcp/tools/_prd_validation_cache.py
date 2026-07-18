"""Content-addressed pure-result cache for ``trw_prd_validate``.

Repository/date truth is never cached. Each pure result is an independent,
atomic JSON shard so one corrupt entry or concurrent writer cannot poison or
replace the entire cache. Cache failures always degrade to a miss (the cache
is disposable acceleration; it can never change validation truth).

PRD-QUAL-114: bounded, config-driven entry/byte ceilings, process-safe
maintenance cadence (one deterministic dual-cap sweep every N writes under a
crash-tolerant advisory lock), and one-time legacy monolithic-YAML retirement.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import tempfile
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from trw_mcp.models.requirements import ValidationResultV2

VALIDATOR_VERSION = "prd-quality-v2-pure:2026-07-10"
CACHE_SCHEMA_VERSION = 2
DEFAULT_MAX_ENTRIES = 512
DEFAULT_MAX_TOTAL_BYTES = 64 * 1024 * 1024
DEFAULT_MAX_ENTRY_BYTES = 4 * 1024 * 1024
DEFAULT_MAINTENANCE_INTERVAL = 32
_KEY_RE = re.compile(r"^[0-9a-f]{64}$")
_COUNTER_NAME = ".maintenance.counter"
_LOCK_NAME = ".maintenance.lock"
_LEGACY_SENTINEL_NAME = "prd-validation.legacy-retired"

# Miss reasons that mean "state was present but unusable" (degraded), vs a
# clean absence. Degraded reasons are surfaced so repeated corruption is an
# observable maintenance signal — never a validation change.
_DEGRADED_REASONS = frozenset({"oversized", "corrupt"})


@dataclass(frozen=True)
class CacheBounds:
    """Resolved, validated ceilings + maintenance cadence for the shard store."""

    max_entries: int = DEFAULT_MAX_ENTRIES
    max_total_bytes: int = DEFAULT_MAX_TOTAL_BYTES
    max_entry_bytes: int = DEFAULT_MAX_ENTRY_BYTES
    maintenance_interval: int = DEFAULT_MAINTENANCE_INTERVAL

    @classmethod
    def from_config(cls, config: object) -> CacheBounds:
        """Read PRD cache ceilings from TRWConfig, falling back to defaults."""
        return cls(
            max_entries=int(getattr(config, "prd_validation_cache_max_entries", DEFAULT_MAX_ENTRIES)),
            max_total_bytes=int(getattr(config, "prd_validation_cache_max_total_bytes", DEFAULT_MAX_TOTAL_BYTES)),
            max_entry_bytes=int(getattr(config, "prd_validation_cache_max_entry_bytes", DEFAULT_MAX_ENTRY_BYTES)),
            maintenance_interval=int(
                getattr(config, "prd_validation_cache_maintenance_interval", DEFAULT_MAINTENANCE_INTERVAL)
            ),
        )


def config_hash(config: object) -> str:
    """Hash the full config so every scoring-affecting change invalidates cache."""
    model_dump = getattr(config, "model_dump", None)
    payload = model_dump(mode="json") if callable(model_dump) else getattr(config, "__dict__", repr(config))
    raw = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def cache_metadata(content: str, config: object) -> dict[str, str]:
    """Return inspectable content/config/version hashes for a cache entry."""
    return {
        "content_hash": "sha256:" + hashlib.sha256(content.encode("utf-8")).hexdigest(),
        "config_hash": config_hash(config),
        "validator_version": VALIDATOR_VERSION,
    }


def cache_key(content: str, config: object) -> str:
    """Return a stable lowercase-hex key for pure text/config scoring."""
    payload = cache_metadata(content, config)
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def cache_path(project_root: Path) -> Path:
    """Return the v2 per-key cache directory; legacy YAML is never trusted."""
    return project_root / ".trw" / "cache" / "prd-validation" / "v2"


def _entry_path(root: Path, key: str) -> Path | None:
    if _KEY_RE.fullmatch(key) is None:
        return None
    entry = root / key[:2] / f"{key}.json"
    try:
        resolved_root = root.resolve(strict=False)
        resolved_entry = entry.resolve(strict=False)
        if not resolved_entry.is_relative_to(resolved_root):
            return None
    except (OSError, RuntimeError):
        return None
    return entry


def load_pure_result(
    path: Path,
    key: str,
    *,
    max_entry_bytes: int = DEFAULT_MAX_ENTRY_BYTES,
) -> ValidationResultV2 | None:
    """Load one validated shard; every absence/corruption condition is a miss."""
    result, _reason = load_pure_result_with_reason(path, key, max_entry_bytes=max_entry_bytes)
    return result


def load_pure_result_with_reason(
    path: Path,
    key: str,
    *,
    max_entry_bytes: int = DEFAULT_MAX_ENTRY_BYTES,
) -> tuple[ValidationResultV2 | None, str]:
    """Load one shard, returning ``(result, miss_reason)``.

    ``miss_reason`` is ``""`` on a hit, ``"absent"``/``"invalid_key"`` for a
    clean miss, and ``"oversized"``/``"corrupt"`` for degraded state. No
    exception ever escapes: a corrupt shard is data, not a fault.
    """
    entry = _entry_path(path, key)
    if entry is None:
        return None, "invalid_key"
    try:
        if not entry.is_file() or entry.is_symlink():
            return None, "absent"
        if entry.stat().st_size > max_entry_bytes:
            return None, "oversized"
        payload = json.loads(entry.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            return None, "corrupt"
        if payload.get("schema_version") != CACHE_SCHEMA_VERSION or payload.get("key") != key:
            return None, "corrupt"
        raw_result = payload.get("pure_result")
        if not isinstance(raw_result, dict):
            return None, "corrupt"
        canonical_result = json.dumps(raw_result, sort_keys=True, separators=(",", ":")).encode("utf-8")
        if payload.get("pure_result_sha256") != hashlib.sha256(canonical_result).hexdigest():
            return None, "corrupt"
        result = ValidationResultV2.model_validate(raw_result, strict=False)
        # Best-effort access recency; correctness never depends on this write.
        try:
            os.utime(entry, None)
        except OSError:
            pass
        return result, ""
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError):
        return None, "corrupt"


def is_degraded_reason(reason: str) -> bool:
    """Return whether a miss reason means present-but-unusable cache state."""
    return reason in _DEGRADED_REASONS


def store_pure_result(
    path: Path,
    key: str,
    result: ValidationResultV2,
    *,
    bounds: CacheBounds | None = None,
) -> None:
    """Atomically persist one shard, then run bounded maintenance on cadence."""
    limits = bounds or CacheBounds()
    entry = _entry_path(path, key)
    if entry is None:
        raise ValueError("invalid PRD validation cache key")
    if path.is_symlink():
        raise ValueError("PRD validation cache root must not be a symlink")
    entry.parent.mkdir(parents=True, exist_ok=True)
    raw_result = result.model_dump(mode="json")
    canonical_result = json.dumps(raw_result, sort_keys=True, separators=(",", ":")).encode("utf-8")
    payload = {
        "schema_version": CACHE_SCHEMA_VERSION,
        "key": key,
        "validator_version": VALIDATOR_VERSION,
        "created_at_unix": time.time(),
        "pure_result": raw_result,
        "pure_result_sha256": hashlib.sha256(canonical_result).hexdigest(),
    }
    encoded = (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    if len(encoded) > limits.max_entry_bytes:
        raise ValueError("PRD validation cache entry exceeds maximum size")
    fd, temp_name = tempfile.mkstemp(prefix=f".{key}.", suffix=".tmp", dir=entry.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, entry)
    finally:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
    _run_maintenance_on_cadence(path, preserve=entry, bounds=limits)


# ---------------------------------------------------------------------------
# Maintenance: process-safe cadence, crash-tolerant lock, dual-cap eviction.
# ---------------------------------------------------------------------------


@contextlib.contextmanager
def _maintenance_lock(root: Path) -> Iterator[bool]:
    """Yield True if a crash-tolerant advisory lock was acquired, else False.

    On POSIX the lock is an ``flock`` on a dedicated lock file (never an entry
    file); it is auto-released when the fd closes or the process dies, so a
    crashed maintainer never leaves a permanent lock (NFR02). When flock is
    unavailable the caller still runs best-effort maintenance (races only cause
    extra misses).
    """
    try:
        import fcntl
    except ImportError:  # pragma: no cover - non-POSIX fallback
        yield True
        return
    lock_path = root / _LOCK_NAME
    fd = None
    try:
        fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    except OSError:
        yield False
        return
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            yield False
            return
        try:
            yield True
        finally:
            with contextlib.suppress(OSError):
                fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        with contextlib.suppress(OSError):
            os.close(fd)


def _bump_write_counter(root: Path) -> int:
    """Increment and return the persistent per-cache write counter."""
    counter_path = root / _COUNTER_NAME
    try:
        count = int(counter_path.read_text(encoding="utf-8").strip() or "0")
    except (OSError, ValueError):
        count = 0
    count += 1
    with contextlib.suppress(OSError):
        counter_path.write_text(str(count), encoding="utf-8")
    return count


def _run_maintenance_on_cadence(root: Path, *, preserve: Path, bounds: CacheBounds) -> None:
    """Every ``maintenance_interval`` writes, run one dual-cap eviction sweep."""
    interval = max(bounds.maintenance_interval, 1)
    try:
        with _maintenance_lock(root) as acquired:
            if not acquired:
                return
            count = _bump_write_counter(root)
            if count % interval == 0:
                _enforce_bounds(root, preserve=preserve, bounds=bounds)
    except (OSError, RuntimeError):
        # Maintenance is advisory; the just-written result stays usable.
        return


def _enforce_bounds(root: Path, *, preserve: Path, bounds: CacheBounds) -> None:
    """Evict oldest shards by ``(accessed_at, cache_key)``; preserve newest.

    Access recency is the shard mtime (touched on every read). Ties break by
    cache key for a fully deterministic order. Invalid/foreign files are never
    eviction candidates and never counted toward the byte ceiling.
    """
    try:
        records: list[tuple[int, str, int, Path]] = []
        for candidate in root.glob("[0-9a-f][0-9a-f]/*.json"):
            if candidate.is_symlink() or not candidate.is_file():
                continue
            if _KEY_RE.fullmatch(candidate.stem) is None:
                continue
            stat = candidate.stat()
            records.append((stat.st_mtime_ns, candidate.stem, stat.st_size, candidate))
        total = sum(record[2] for record in records)
        records.sort(key=lambda record: (record[0], record[1]))
        max_entries = max(bounds.max_entries, 1)
        max_total_bytes = max(bounds.max_total_bytes, 1)
        while records and (len(records) > max_entries or total > max_total_bytes):
            victim_index = next((index for index, record in enumerate(records) if record[3] != preserve), None)
            if victim_index is None:
                break
            _, _, size, candidate = records.pop(victim_index)
            try:
                candidate.unlink()
            except FileNotFoundError:
                pass
            total -= size
    except (OSError, RuntimeError):
        # Cache maintenance is advisory; validation correctness is unaffected.
        return


def retire_legacy_cache(project_root: Path) -> bool:
    """Retire the disposable legacy monolithic YAML cache without parsing it.

    First initialization renames ``.trw/cache/prd-validation.yaml`` to a single
    bounded sentinel; if a sentinel already exists the legacy file is simply
    removed so at most ONE retirement artifact ever exists. Never trusts or
    parses legacy contents. Idempotent: a no-op once retired.
    """
    cache_dir = project_root / ".trw" / "cache"
    legacy = cache_dir / "prd-validation.yaml"
    try:
        if not legacy.is_file() or legacy.is_symlink():
            return False
        sentinel = cache_dir / _LEGACY_SENTINEL_NAME
        if sentinel.exists():
            with contextlib.suppress(FileNotFoundError):
                legacy.unlink()
            return True
        os.replace(legacy, sentinel)
        return True
    except (OSError, RuntimeError):
        # Retirement is best-effort; a stuck legacy file never blocks validation.
        return False


__all__ = [
    "CACHE_SCHEMA_VERSION",
    "VALIDATOR_VERSION",
    "CacheBounds",
    "cache_key",
    "cache_metadata",
    "cache_path",
    "config_hash",
    "is_degraded_reason",
    "load_pure_result",
    "load_pure_result_with_reason",
    "retire_legacy_cache",
    "store_pure_result",
]
