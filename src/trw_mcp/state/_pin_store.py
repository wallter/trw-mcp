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
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast

import structlog

from trw_mcp._locking import _lock_ex, _lock_un
from trw_mcp.exceptions import StateError

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
    "prune_pin_store_orphans",
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


# --- Filesystem helpers ------------------------------------------------------


def _safe_mtime_ns(path: Path) -> int | None:
    """Return ``path.stat().st_mtime_ns`` or ``None`` on any OSError.

    A concurrent deletion between the preceding open/json.load and this call
    raises ``FileNotFoundError`` (a subclass of OSError).  The cache falls
    back to ``None`` rather than crashing so that ``load_pin_store`` honors
    its fail-open contract even under concurrent GC processes.
    """
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return None


# --- Load path ---------------------------------------------------------------


def _apply_eviction_passes(raw: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Drop malformed entries and pins whose ``run_path`` disappeared.

    Creator PIDs are diagnostic only: pins intentionally survive MCP process
    restarts. Heartbeat expiry and explicit adoption govern live ownership.
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

        survivors[pin_key] = entry
    return survivors


def load_pin_store() -> dict[str, dict[str, Any]]:
    """Return the current pin store, honoring the 1-second read cache.

    Fail-open on malformed JSON: logs ``pin_store_malformed_fallback``
    at WARN level and returns ``{}``.  Each load applies the stale-path
    and stale-path eviction before caching.
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
    except (json.JSONDecodeError, UnicodeDecodeError, OSError) as exc:
        # UnicodeDecodeError (a ValueError, not OSError) fires when pins.json
        # holds non-UTF-8 bytes — a torn write or disk corruption. It must be
        # caught alongside JSONDecodeError/OSError so the documented fail-open
        # contract holds: load_pin_store sits on the hot session_start path and
        # must never crash the caller on a corrupt store.
        _runtime_logger().warning(
            "pin_store_malformed_fallback",
            path=str(pins_path),
            error=type(exc).__name__,
            detail=str(exc),
        )
        _pin_store_cache = {}
        _pin_store_cache_ts = time.monotonic()
        _pin_store_cache_mtime_ns = _safe_mtime_ns(pins_path)
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
        _pin_store_cache_mtime_ns = _safe_mtime_ns(pins_path)
        return {}

    # The json module can only produce str keys for root dicts, but cast
    # to satisfy mypy --strict (raw is typed as object via json.load).
    typed = cast("dict[str, dict[str, Any]]", raw)
    survivors = _apply_eviction_passes(typed)
    _pin_store_cache = survivors
    _pin_store_cache_ts = time.monotonic()
    _pin_store_cache_mtime_ns = _safe_mtime_ns(pins_path)
    return dict(survivors)


def prune_pin_store_orphans() -> int:
    """Persist eviction of malformed or stale-path entries to disk.

    ``load_pin_store`` evicts stale entries in-memory only, so one keeps
    showing up in every load (with a fresh warning)
    until something writes the store. This helper performs an explicit
    load → diff → save cycle so the orphan disappears from the file.

    Returns the number of entries removed. Returns ``0`` (and writes
    nothing) when the disk state already matches the post-eviction view.

    Fail-open: filesystem errors are logged at WARNING and the function
    returns ``0`` instead of raising. Callers (boot sweep, periodic
    maintenance) must never crash because pin pruning failed.
    """
    pins_path = pin_store_path()
    if not pins_path.exists():
        return 0
    with _pin_store_threading_lock, _pin_store_file_lock():
        try:
            with pins_path.open("r", encoding="utf-8") as fh:
                raw = json.load(fh)
        except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            # See load_pin_store: non-UTF-8 bytes raise UnicodeDecodeError, which
            # is a ValueError (not OSError) and would otherwise crash this
            # fail-open prune path called from the boot sweep.
            _runtime_logger().warning(
                "pin_store_prune_read_failed",
                path=str(pins_path),
                error=type(exc).__name__,
                detail=str(exc),
            )
            return 0
        if not isinstance(raw, dict):
            return 0
        typed = cast("dict[str, dict[str, Any]]", raw)
        survivors = _apply_eviction_passes(typed)
        removed = len(typed) - len(survivors)
        if removed <= 0:
            return 0
        try:
            _write_pin_store_locked(survivors)
        except OSError as exc:
            _runtime_logger().warning(
                "pin_store_prune_save_failed",
                path=str(pins_path),
                error=type(exc).__name__,
                detail=str(exc),
            )
            return 0
        _runtime_logger().info(
            "pin_store_orphans_pruned",
            path=str(pins_path),
            removed=removed,
            survivors=len(survivors),
        )
        return removed


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


@contextmanager
def _pin_store_file_lock() -> Iterator[None]:
    """Hold the cross-process pin-store lock for one disk transaction."""
    pins_path = pin_store_path()
    os.makedirs(pins_path.parent, exist_ok=True)
    lock_fd = open(pin_store_lock_path(), "a+")  # noqa: SIM115
    try:
        _lock_ex(lock_fd.fileno())
        yield
    finally:
        try:
            _lock_un(lock_fd.fileno())
        finally:
            lock_fd.close()


def _write_pin_store_locked(store: dict[str, dict[str, Any]]) -> None:
    """Write while both the process and file locks are already held."""
    _atomic_write_json(pin_store_path(), store)
    global _pin_store_cache, _pin_store_cache_ts, _pin_store_cache_mtime_ns
    _pin_store_cache = None
    _pin_store_cache_ts = 0.0
    _pin_store_cache_mtime_ns = None


def _save_pin_store_locked(store: dict[str, dict[str, Any]]) -> None:
    """Save path executed with ``_pin_store_threading_lock`` already held.

    Factored out so read-modify-write helpers (``upsert_pin_entry``,
    ``remove_pin_entry``) can hold the threading lock across the load
    → mutate → save cycle without releasing-then-reacquiring, which
    would allow an interleaving thread to lose its update.
    """
    with _pin_store_file_lock():
        _write_pin_store_locked(store)


def _load_pin_store_uncached() -> dict[str, dict[str, Any]]:
    """Unconditional disk read (bypasses the 1-second cache).

    Used inside ``upsert_pin_entry`` / ``remove_pin_entry`` to guarantee
    read-modify-write consistency: once the threading lock is held, we
    want the authoritative current state, not a possibly-stale cached
    snapshot from another thread's pre-invalidation moment.
    """
    global _pin_store_cache, _pin_store_cache_ts, _pin_store_cache_mtime_ns
    _pin_store_cache = None
    _pin_store_cache_ts = 0.0
    _pin_store_cache_mtime_ns = None
    return load_pin_store()


def _load_pin_store_strict_locked() -> dict[str, dict[str, Any]]:
    """Read and validate ownership state while the file lock is held."""
    pins_path = pin_store_path()
    if not pins_path.exists():
        return {}
    try:
        raw: object = json.loads(pins_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError, OSError) as exc:
        raise StateError(f"pin ownership marker is unreadable: {exc}", path=str(pins_path)) from exc
    if not isinstance(raw, dict):
        raise StateError(
            f"pin ownership marker root must be a mapping, got {type(raw).__name__}",
            path=str(pins_path),
        )
    store: dict[str, dict[str, Any]] = {}
    for pin_key, entry in raw.items():
        if not isinstance(pin_key, str) or not pin_key:
            raise StateError("pin ownership marker contains an invalid pin key", path=str(pins_path))
        if not isinstance(entry, dict):
            raise StateError(f"pin ownership marker for {pin_key} must be a mapping", path=str(pins_path))
        run_path = entry.get("run_path")
        if not isinstance(run_path, str) or not run_path:
            raise StateError(f"pin ownership marker for {pin_key} missing run_path", path=str(pins_path))
        store[pin_key] = dict(entry)
    return store


def transfer_pin_entry(
    caller_pin_key: str,
    run_path: Path,
    *,
    force: bool,
    pin_ttl_hours: float,
) -> tuple[dict[str, Any], str | None, float | None, bool]:
    """Validate ownership and transfer a run pin in one disk transaction."""
    target = str(run_path.resolve())
    now_dt = datetime.now(timezone.utc)
    now = _iso_now()
    with _pin_store_threading_lock, _pin_store_file_lock():
        store = _load_pin_store_strict_locked()
        previous_pin_key: str | None = None
        previous_entry: dict[str, Any] | None = None
        for pin_key, entry in store.items():
            if str(Path(str(entry["run_path"])).resolve()) == target:
                previous_pin_key = pin_key
                previous_entry = entry
                break

        heartbeat_age_hours: float | None = None
        owner_was_live = False
        if previous_entry is not None:
            heartbeat = previous_entry.get("last_heartbeat_ts")
            if not isinstance(heartbeat, str):
                raise StateError("pin ownership marker has invalid last_heartbeat_ts", path=str(pin_store_path()))
            try:
                parsed = datetime.fromisoformat(heartbeat.replace("Z", "+00:00"))
                if parsed.tzinfo is None:
                    raise ValueError("timezone missing")
            except ValueError as exc:
                raise StateError(
                    "pin ownership marker has invalid last_heartbeat_ts",
                    path=str(pin_store_path()),
                    pin_key=previous_pin_key,
                ) from exc
            heartbeat_age_hours = (now_dt - parsed.astimezone(timezone.utc)).total_seconds() / 3600.0
            owner_was_live = heartbeat_age_hours < pin_ttl_hours
        if owner_was_live and previous_pin_key != caller_pin_key and not force:
            raise StateError(
                "run is actively held by a live pin; pass force=True to override",
                path=target,
                pin_key=previous_pin_key,
            )

        existing = store.get(caller_pin_key)
        created_ts = existing.get("created_ts") if isinstance(existing, dict) else None
        record: dict[str, Any] = {
            "run_path": target,
            "created_ts": created_ts if isinstance(created_ts, str) and created_ts else now,
            "last_heartbeat_ts": now,
            "client_hint": None,
            "pid": os.getpid(),
        }
        if previous_pin_key is not None and previous_pin_key != caller_pin_key:
            store.pop(previous_pin_key, None)
        store[caller_pin_key] = record
        _write_pin_store_locked(store)
    return record, previous_pin_key, heartbeat_age_hours, owner_was_live


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
    with _pin_store_threading_lock, _pin_store_file_lock():
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
        _write_pin_store_locked(store)
    return record


def remove_pin_entry(pin_key: str) -> bool:
    """Drop *pin_key* from the store.  Returns ``True`` iff an entry was removed.

    Holds :data:`_pin_store_threading_lock` across the load-mutate-save
    cycle (see :func:`upsert_pin_entry` for the rationale).
    """
    with _pin_store_threading_lock, _pin_store_file_lock():
        store = _load_pin_store_uncached()
        if pin_key not in store:
            return False
        store.pop(pin_key, None)
        _write_pin_store_locked(store)
    return True


def get_pin_entry(pin_key: str) -> dict[str, Any] | None:
    """Return the record for *pin_key* (post-eviction) or ``None``."""
    store = load_pin_store()
    entry = store.get(pin_key)
    if isinstance(entry, dict):
        return entry
    return None
