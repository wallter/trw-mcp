"""Multi-MCP sync coordination — PRD-INFRA-051-FR08.

Uses fcntl.flock (via _locking.py shim) for process coordination.
Tracks last sync time in .trw/sync-state.json.
Only one MCP server syncs at a time; others skip if recent.
"""

from __future__ import annotations

import json
import os
from collections.abc import Generator
from contextlib import contextmanager, suppress
from datetime import datetime, timezone
from pathlib import Path

import structlog

from trw_mcp._locking import _lock_ex_nb, _lock_un

logger = structlog.get_logger(__name__)

_STATE_FILE = "sync-state.json"
_LOCK_FILE = "sync.lock"


class SyncCoordinator:
    """Manages multi-MCP lock acquisition and sync state."""

    def __init__(self, trw_dir: Path, sync_interval: int = 300) -> None:
        self._trw_dir = trw_dir
        self._sync_interval = sync_interval
        self._state_path = trw_dir / _STATE_FILE
        self._lock_path = trw_dir / _LOCK_FILE

    def should_sync(self, sync_interval: float | None = None) -> bool:
        """Check sync-state.json: is it time for a sync cycle?"""
        if not self._state_path.exists():
            return True
        try:
            state = json.loads(self._state_path.read_text())
            last_push_at = state.get("last_push_at")
            if not last_push_at:
                return True
            last_dt = datetime.fromisoformat(last_push_at)
            elapsed = (datetime.now(tz=timezone.utc) - last_dt).total_seconds()
            required_interval = float(sync_interval) if sync_interval is not None else float(self._sync_interval)
            return elapsed >= required_interval
        except (json.JSONDecodeError, ValueError, KeyError):
            return True

    @contextmanager
    def acquire_sync_lock(self) -> Generator[bool, None, None]:
        """Try to acquire .trw/sync.lock (LOCK_NB). Yields True if acquired."""
        self._lock_path.parent.mkdir(parents=True, exist_ok=True)
        fd = None
        try:
            fd = os.open(str(self._lock_path), os.O_CREAT | os.O_RDWR)
            _lock_ex_nb(fd)
            logger.debug("sync_lock_acquired", pid=os.getpid())
            yield True
        except OSError:
            logger.debug("sync_lock_skipped", reason="held by another MCP")
            if fd is not None:
                os.close(fd)
                fd = None
            yield False
        finally:
            if fd is not None:
                with suppress(OSError):
                    _lock_un(fd)
                os.close(fd)

    def record_sync_success(
        self,
        pushed: int,
        pulled: int,
        push_seq: int | None = None,
        pull_seq: int | None = None,
        *,
        pull_completed: bool = False,
    ) -> None:
        """Update sync-state.json with success info."""
        state = self._read_state()
        now = datetime.now(tz=timezone.utc).isoformat()
        state["last_push_at"] = now
        state["last_push_seq"] = max(self._int_field(state, "last_push_seq"), push_seq or 0)
        state["push_count"] = self._int_field(state, "push_count") + 1
        if pulled > 0 or pull_completed:
            state["last_pull_at"] = now
            state["last_pull_seq"] = max(self._int_field(state, "last_pull_seq"), pull_seq or 0)
            state["pull_count"] = self._int_field(state, "pull_count") + 1
        state["last_error"] = None
        state["version"] = 1
        self._write_state(state)

    def record_sync_failure(self, error: str) -> None:
        """Update sync-state.json with failure info."""
        state = self._read_state()
        state["last_error"] = error[:500]
        state["last_error_at"] = datetime.now(tz=timezone.utc).isoformat()
        state["consecutive_failures"] = self._int_field(state, "consecutive_failures") + 1
        state["version"] = 1
        self._write_state(state)

    def get_last_push_seq(self) -> int:
        """Read the last push sequence number from state."""
        state = self._read_state()
        return self._int_field(state, "last_push_seq")

    def get_last_pull_seq(self) -> int:
        """Read the last pull sequence number from state."""
        state = self._read_state()
        return self._int_field(state, "last_pull_seq")

    @staticmethod
    def _int_field(state: dict[str, object], key: str) -> int:
        """Extract an integer field from state, defaulting to 0 if missing/invalid."""
        raw = state.get(key, 0)
        return int(raw) if isinstance(raw, (int, float)) else 0

    def _read_state(self) -> dict[str, object]:
        if not self._state_path.exists():
            return {}
        try:
            raw: object = json.loads(self._state_path.read_text())
            if isinstance(raw, dict):
                return raw
            return {}
        except (json.JSONDecodeError, OSError):
            return {}

    def _write_state(self, state: dict[str, object]) -> None:
        """Atomically write state using write-then-rename."""
        tmp = self._state_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(state, indent=2, default=str))
        os.replace(str(tmp), str(self._state_path))
