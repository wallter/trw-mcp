"""Persistent pin store at ``.trw/runtime/pins.json`` (PRD-CORE-141 FR04).

Parent facade: :mod:`trw_mcp.state._paths`.

This module owns the on-disk representation of per-connection pin state
plus its concurrency discipline.  Two writer classes cooperate:

1. **In-process** — multiple FastMCP tool-call threads in the serve
   process, serialized by :data:`_pin_store_threading_lock`.
2. **Cross-process** — the serve process plus a concurrently-running
   ``trw-mcp gc`` CLI (Wave 4), serialized by advisory file locking on
   ``.trw/runtime/pins.json.lock`` via :mod:`trw_mcp._locking`.

The save sequence is authoritative (matching PRD-CORE-141 FR04):

    acquire threading lock
      → open/create lock file → _lock_ex(lock_fd)
        → write tmp (pretty JSON, fsync) → os.replace(tmp, pins.json)
        → chmod 0600 on pins.json (NFR03)
        → invalidate the 1-second read cache **immediately**
      → _lock_un(lock_fd) → close lock file
    release threading lock

Reads use a 1-second-TTL in-memory cache.  The cache MUST be invalidated
by every successful save **before** the threading lock is released —
otherwise the window between the disk write and the next read opens a
write-after-read hazard that reintroduces the isolation bug.

Schema (JSON root is a ``dict[str, dict]``):

    {
        "<pin_key>": {
            "run_path": "<absolute path>",
            "created_ts": "<ISO8601>",
            "last_heartbeat_ts": "<ISO8601>",
            "client_hint": "<str | null>",
            "pid": <int>
        },
        ...
    }
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast

import structlog

from trw_mcp._locking import _lock_ex, _lock_un

logger = structlog.get_logger(__name__)


def _runtime_logger() -> Any:
    """Return a fresh logger so structlog test capture sees late-bound events."""
    return structlog.get_logger(__name__)


__all__ = [
    "PIN_STORE_CACHE_TTL_SECONDS",
    "invalidate_pin_store_cache",
    "load_pin_store",
    "pin_store_lock_path",
    "pin_store_path",
    "save_pin_store",
]


# --- Constants ---------------------------------------------------------------

#: TTL for the in-memory read cache.  Short enough that cross-process GC
#: changes land quickly; long enough to collapse burst reads on the hot
#: path.  Every write MUST invalidate the cache immediately (see
#: :func:`invalidate_pin_store_cache`).
PIN_STORE_CACHE_TTL_SECONDS: float = 1.0

_RUNTIME_SUBDIR = "runtime"
_PINS_FILENAME = "pins.json"
_PINS_LOCK_FILENAME = "pins.json.lock"
_PINS_TMP_SUFFIX = ".tmp"


# --- Module state ------------------------------------------------------------

#: Serializes in-process writers so the file-lock window is held by one
#: thread at a time.  Must be acquired OUTSIDE the file lock to avoid
#: deadlock with any reader thread that (in future) might block on either.
_pin_store_threading_lock = threading.Lock()

#: 1-second TTL read cache.  ``None`` means "cold — re-read from disk".
_pin_store_cache: dict[str, dict[str, Any]] | None = None

#: Monotonic timestamp the cache was populated at.
_pin_store_cache_ts: float = 0.0

#: Source file mtime associated with the cached snapshot.
_pin_store_cache_mtime_ns: int | None = None


# --- Path helpers ------------------------------------------------------------


def pin_store_path() -> Path:
    """Return the absolute path of ``.trw/runtime/pins.json``.

    Resolved lazily so test fixtures can redirect ``resolve_trw_dir`` to
    a per-test ``tmp_path`` via monkeypatch.
    """
    # Imported lazily to avoid a circular import with _paths.
    from trw_mcp.state._paths import resolve_trw_dir

    return resolve_trw_dir() / _RUNTIME_SUBDIR / _PINS_FILENAME


def pin_store_lock_path() -> Path:
    """Return the absolute path of the pin-store advisory lock sentinel."""
    from trw_mcp.state._paths import resolve_trw_dir

    return resolve_trw_dir() / _RUNTIME_SUBDIR / _PINS_LOCK_FILENAME


# --- Cache control -----------------------------------------------------------


def invalidate_pin_store_cache() -> None:
    """Force the next :func:`load_pin_store` call to re-read from disk.

    Called immediately after every successful save.  Exposed publicly so
    tests and cross-module callers (e.g. boot-sweep code in Wave 4) can
    flush the cache explicitly when they know the disk state has changed.
    """
    global _pin_store_cache, _pin_store_cache_mtime_ns, _pin_store_cache_ts
    _pin_store_cache = None
    _pin_store_cache_ts = 0.0
    _pin_store_cache_mtime_ns = None


# --- PID liveness -----------------------------------------------------------


def _is_pid_alive(pid: int) -> bool:
    """Return ``True`` when *pid* names a live process.

    On POSIX uses ``os.kill(pid, 0)`` — catches ``ProcessLookupError``
    for dead pids and ``PermissionError`` (live process we lack
    permission to signal — still counts as alive).

    On Windows uses ``psutil.pid_exists`` when ``psutil`` is installed;
    when it is not, logs a one-time DEBUG and returns True so the entry
    is preserved (better to retain a possibly-orphan pin than to drop a
    live one on the platform where we cannot probe).
    """
    if pid <= 0:
        return False
    if sys.platform == "win32":
        try:
            import psutil  # type: ignore[import-not-found]
        except ImportError:
            _runtime_logger().debug("pid_check_skipped_no_psutil", pid=pid)
            return True
        return bool(psutil.pid_exists(pid))
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:  # justified: signal denied implies the process exists
        return True
    except OSError:
        # Other errno — treat as alive to avoid evicting live pins on exotic errors.
        return True
    return True


# --- Load path ---------------------------------------------------------------


def _apply_eviction_passes(raw: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Drop entries with stale ``run_path`` or orphan ``pid``.

    Emits ``pin_stale_run_path_evicted`` and ``pin_orphan_evicted`` WARN
    logs per dropped entry so analytics can track eviction velocity.
    """
    survivors: dict[str, dict[str, Any]] = {}
    for pin_key, entry in raw.items():
        if not isinstance(entry, dict):
            _runtime_logger().warning(
                "pin_store_entry_malformed",
                pin_key=pin_key,
                entry_type=type(entry).__name__,
            )
            continue

        run_path_raw = entry.get("run_path")
        if not isinstance(run_path_raw, str) or not run_path_raw:
            _runtime_logger().warning("pin_store_entry_missing_run_path", pin_key=pin_key)
            continue

        # Stale run_path eviction.
        if not Path(run_path_raw).exists():
            _runtime_logger().warning(
                "pin_stale_run_path_evicted",
                pin_key=pin_key,
                run_path=run_path_raw,
            )
            continue

        # Orphan pid eviction.
        pid_raw = entry.get("pid")
        if isinstance(pid_raw, int) and not _is_pid_alive(pid_raw):
            _runtime_logger().warning(
                "pin_orphan_evicted",
                pin_key=pin_key,
                pid=pid_raw,
                run_path=run_path_raw,
            )
            continue

        survivors[pin_key] = entry
    return survivors


def load_pin_store() -> dict[str, dict[str, Any]]:
    """Return the current pin store, honoring the 1-second read cache.

    Fail-open on malformed JSON: logs ``pin_store_malformed_fallback``
    at WARN level and returns ``{}``.  Each load applies the stale-path
    and orphan-pid eviction passes before caching.
    """
    global _pin_store_cache, _pin_store_cache_mtime_ns, _pin_store_cache_ts

    pins_path = pin_store_path()
    if _pin_store_cache is not None and (time.monotonic() - _pin_store_cache_ts < PIN_STORE_CACHE_TTL_SECONDS):
        try:
            current_mtime_ns = pins_path.stat().st_mtime_ns if pins_path.exists() else None
        except OSError:
            current_mtime_ns = None
        if current_mtime_ns == _pin_store_cache_mtime_ns:
            return dict(_pin_store_cache)

    if not pins_path.exists():
        _pin_store_cache = {}
        _pin_store_cache_ts = time.monotonic()
        _pin_store_cache_mtime_ns = None
        return {}

    try:
        with pins_path.open("r", encoding="utf-8") as fh:
            raw = json.load(fh)
    except (json.JSONDecodeError, OSError) as exc:
        _runtime_logger().warning(
            "pin_store_malformed_fallback",
            path=str(pins_path),
            error=type(exc).__name__,
            detail=str(exc),
        )
        _pin_store_cache = {}
        _pin_store_cache_ts = time.monotonic()
        _pin_store_cache_mtime_ns = pins_path.stat().st_mtime_ns if pins_path.exists() else None
        return {}

    if not isinstance(raw, dict):
        _runtime_logger().warning(
            "pin_store_malformed_fallback",
            path=str(pins_path),
            error="root_not_dict",
            root_type=type(raw).__name__,
        )
        _pin_store_cache = {}
        _pin_store_cache_ts = time.monotonic()
        _pin_store_cache_mtime_ns = pins_path.stat().st_mtime_ns if pins_path.exists() else None
        return {}

    # The json module can only produce str keys for root dicts, but cast
    # to satisfy mypy --strict (raw is typed as object via json.load).
    typed = cast("dict[str, dict[str, Any]]", raw)
    survivors = _apply_eviction_passes(typed)
    _pin_store_cache = survivors
    _pin_store_cache_ts = time.monotonic()
    _pin_store_cache_mtime_ns = pins_path.stat().st_mtime_ns
    return dict(survivors)


# --- Save path ---------------------------------------------------------------


def _atomic_write_json(pins_path: Path, payload: dict[str, dict[str, Any]]) -> None:
    """Write *payload* to *pins_path* atomically, with mode 0o600 (NFR03).

    Sequence: tmp → fsync → os.replace.  Removes the tmp file if the
    rename fails so no orphan ``pins.json.tmp`` survives a crash.
    """
    tmp_path = pins_path.with_suffix(pins_path.suffix + _PINS_TMP_SUFFIX)
    try:
        with tmp_path.open("w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, sort_keys=True)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_path, pins_path)
        # NFR03 — pid and run_path must not be world-readable.  chmod 0o600.
        try:
            os.chmod(pins_path, 0o600)
        except OSError as exc:
            # Windows does not honor POSIX mode bits; log at DEBUG.
            _runtime_logger().debug(
                "pin_store_chmod_failed",
                path=str(pins_path),
                error=type(exc).__name__,
            )
    except Exception:
        # Best-effort cleanup of the tmp file on any error so no orphan remains.
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def save_pin_store(store: dict[str, dict[str, Any]]) -> None:
    """Persist *store* to ``.trw/runtime/pins.json`` atomically.

    Concurrency (must match exactly):

    1. Acquire the module-level threading lock.
    2. Open/create the lock-file sentinel and acquire LOCK_EX on its FD.
    3. Atomic write + chmod 0o600.
    4. **Invalidate the 1-second cache IMMEDIATELY** — non-negotiable:
       skipping this step reintroduces the write-after-read isolation
       bug that PRD-CORE-141 exists to fix.  See the module docstring.
    5. Release the file lock; close the lock FD.
    6. Release the threading lock.
    """
    with _pin_store_threading_lock:
        _save_pin_store_locked(store)


def _save_pin_store_locked(store: dict[str, dict[str, Any]]) -> None:
    """Save path executed with ``_pin_store_threading_lock`` already held.

    Factored out so read-modify-write helpers (``upsert_pin_entry``,
    ``remove_pin_entry``) can hold the threading lock across the load
    → mutate → save cycle without releasing-then-reacquiring, which
    would allow an interleaving thread to lose its update.
    """
    pins_path = pin_store_path()
    lock_path = pin_store_lock_path()

    # Lazy directory creation (``.trw/runtime/`` does not exist on a fresh
    # project).  ``exist_ok=True`` makes the call idempotent.
    os.makedirs(pins_path.parent, exist_ok=True)

    # Open the lock sentinel.  "a+" creates the file if missing and
    # never truncates — the file's content is irrelevant (we only use
    # its FD for flock).
    lock_fd = open(lock_path, "a+")  # noqa: SIM115 — FD ownership is manual so the file lock spans the write
    try:
        _lock_ex(lock_fd.fileno())
        try:
            _atomic_write_json(pins_path, store)
            # CRITICAL (FR04): invalidate the 1-second cache
            # immediately after os.replace completes.  Without this,
            # a concurrent reader may serve stale state for up to 1
            # second, reintroducing the pin-collision race condition.
            global _pin_store_cache, _pin_store_cache_ts
            _pin_store_cache = None
            _pin_store_cache_ts = 0.0
        finally:
            _lock_un(lock_fd.fileno())
    finally:
        lock_fd.close()


def _load_pin_store_uncached() -> dict[str, dict[str, Any]]:
    """Unconditional disk read (bypasses the 1-second cache).

    Used inside ``upsert_pin_entry`` / ``remove_pin_entry`` to guarantee
    read-modify-write consistency: once the threading lock is held, we
    want the authoritative current state, not a possibly-stale cached
    snapshot from another thread's pre-invalidation moment.
    """
    global _pin_store_cache, _pin_store_cache_ts
    _pin_store_cache = None
    _pin_store_cache_ts = 0.0
    return load_pin_store()


# --- Mutation helpers used by _paths pin-helper shims -----------------------


def _iso_now() -> str:
    """Return current UTC time as an ISO8601 string with ``Z`` suffix."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


def upsert_pin_entry(
    pin_key: str,
    run_path: Path,
    *,
    client_hint: str | None = None,
) -> dict[str, Any]:
    """Upsert the record for *pin_key* and persist the store.

    Preserves ``created_ts`` on update; refreshes ``last_heartbeat_ts``
    and ``pid`` every time.

    Holds :data:`_pin_store_threading_lock` across the entire
    load-mutate-save cycle so concurrent writers do not lose each
    other's updates via interleaved reads of a stale snapshot.
    """
    now = _iso_now()
    with _pin_store_threading_lock:
        store = _load_pin_store_uncached()
        existing = store.get(pin_key)
        created_ts = now
        if isinstance(existing, dict):
            prior_created = existing.get("created_ts")
            if isinstance(prior_created, str) and prior_created:
                created_ts = prior_created
        record: dict[str, Any] = {
            "run_path": str(run_path.resolve()),
            "created_ts": created_ts,
            "last_heartbeat_ts": now,
            "client_hint": client_hint,
            "pid": os.getpid(),
        }
        store[pin_key] = record
        _save_pin_store_locked(store)
    return record


def remove_pin_entry(pin_key: str) -> bool:
    """Drop *pin_key* from the store.  Returns ``True`` iff an entry was removed.

    Holds :data:`_pin_store_threading_lock` across the load-mutate-save
    cycle (see :func:`upsert_pin_entry` for the rationale).
    """
    with _pin_store_threading_lock:
        store = _load_pin_store_uncached()
        if pin_key not in store:
            return False
        store.pop(pin_key, None)
        _save_pin_store_locked(store)
    return True


def get_pin_entry(pin_key: str) -> dict[str, Any] | None:
    """Return the record for *pin_key* (post-eviction) or ``None``."""
    store = load_pin_store()
    entry = store.get(pin_key)
    if isinstance(entry, dict):
        return entry
    return None
