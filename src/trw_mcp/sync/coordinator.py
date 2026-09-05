"""Multi-MCP sync coordination — PRD-INFRA-051-FR08.

Uses fcntl.flock (via _locking.py shim) for process coordination.
Tracks last sync time in .trw/sync-state.json.
Only one MCP server syncs at a time; others skip if recent.

PRD-FIX-125-FR01 — what the counter describes: ``consecutive_failures``,
``last_push_at`` and ``push_count`` describe the PRIMARY sync target only
(``resolved_sync_targets[0]``). Non-primary targets are best-effort replicas
whose health is reported separately under ``secondary_targets`` and can never
move the counter. Before that change one permanently-401 local dev secondary
pinned ``consecutive_failures`` for 134 days while the production primary
succeeded in 161 of 161 measured cycles.
"""

from __future__ import annotations

import json
import os
from collections.abc import Generator, Mapping
from contextlib import contextmanager, suppress
from datetime import datetime, timezone
from pathlib import Path

import structlog

from trw_mcp._locking import _lock_ex_nb, _lock_un

logger = structlog.get_logger(__name__)

_STATE_FILE = "sync-state.json"
_LOCK_FILE = "sync.lock"

#: Upper bound on any persisted remote-error string. ``sync-state.json`` is a
#: hot-path repo-local artifact; an unbounded remote error would bloat it.
_MAX_ERROR_CHARS = 500


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
        state["last_pull_at"] = state.get("last_pull_at")
        state["last_pull_seq"] = self._int_field(state, "last_pull_seq")
        state["pull_count"] = self._int_field(state, "pull_count")
        if pulled > 0 or pull_completed:
            state["last_pull_at"] = now
            state["last_pull_seq"] = max(self._int_field(state, "last_pull_seq"), pull_seq or 0)
            state["pull_count"] = self._int_field(state, "pull_count") + 1
        state["last_error"] = None
        state["last_error_at"] = None
        state["consecutive_failures"] = 0
        state["last_outcome_line"] = self._int_field(state, "last_outcome_line")
        state["version"] = 1
        self._write_state(state)

    def record_sync_failure(self, error: str) -> None:
        """Update sync-state.json with failure info.

        PRD-FIX-125-FR01: only a PRIMARY-target failure reaches here. A secondary
        target's status is recorded by :meth:`record_target_health` and never
        increments ``consecutive_failures``.
        """
        state = self._read_state()
        state["last_error"] = error[:_MAX_ERROR_CHARS]
        state["last_error_at"] = datetime.now(tz=timezone.utc).isoformat()
        state["consecutive_failures"] = self._int_field(state, "consecutive_failures") + 1
        state["version"] = 1
        self._write_state(state)

    def record_pull_success(self, pull_seq: int | None = None) -> None:
        """Record a successful pull without overwriting push failure state."""
        state = self._read_state()
        now = datetime.now(tz=timezone.utc).isoformat()
        state["last_push_seq"] = self._int_field(state, "last_push_seq")
        state["push_count"] = self._int_field(state, "push_count")
        state["last_pull_at"] = now
        state["last_pull_seq"] = max(self._int_field(state, "last_pull_seq"), pull_seq or 0)
        state["pull_count"] = self._int_field(state, "pull_count") + 1
        state["last_outcome_line"] = self._int_field(state, "last_outcome_line")
        state["version"] = 1
        self._write_state(state)

    def record_outcome_push_success(self, last_outcome_line: int) -> None:
        """Persist the append-only outcome high-water mark after a clean push."""
        state = self._read_state()
        state["last_outcome_line"] = max(self._int_field(state, "last_outcome_line"), last_outcome_line)
        state["version"] = 1
        self._write_state(state)

    def record_target_health(
        self,
        *,
        primary_target_label: str | None,
        secondary_targets: Mapping[str, Mapping[str, object]] | None = None,
    ) -> None:
        """Persist which target the counter describes, plus per-secondary health.

        PRD-FIX-125-FR01. Writes exactly two additive keys:

        * ``primary_target_label`` — so a reader of ``sync-state.json`` can tell
          which target ``consecutive_failures`` / ``last_push_at`` describe.
        * ``secondary_targets`` — ``{label: {status, failed, last_error,
          last_error_at}}`` for every non-primary target in the cycle report.

        This method NEVER reads or writes ``consecutive_failures``,
        ``last_push_at`` or ``push_count``: secondary divergence is reported, not
        gating. The map is replaced (not merged) each cycle so a target dropped
        from ``platform_urls`` does not leave a stale entry behind.
        """
        state = self._read_state()
        if primary_target_label is not None:
            state["primary_target_label"] = primary_target_label
        state["secondary_targets"] = {
            label: self._secondary_entry(entry) for label, entry in (secondary_targets or {}).items()
        }
        state["version"] = 1
        self._write_state(state)

    @staticmethod
    def _secondary_entry(entry: Mapping[str, object]) -> dict[str, object]:
        """Normalize one secondary-target health record for persistence."""
        raw_failed = entry.get("failed", 0)
        raw_error = entry.get("last_error")
        raw_error_at = entry.get("last_error_at")
        return {
            "status": str(entry.get("status", "")),
            "failed": int(raw_failed) if isinstance(raw_failed, (int, float)) else 0,
            "last_error": str(raw_error)[:_MAX_ERROR_CHARS] if raw_error else None,
            "last_error_at": str(raw_error_at) if raw_error_at else None,
        }

    def get_primary_target_label(self) -> str | None:
        """Read the label of the target the failure counter describes (FR01)."""
        raw = self._read_state().get("primary_target_label")
        return raw if isinstance(raw, str) and raw else None

    def get_last_push_at(self) -> str | None:
        """Read the ISO timestamp of the last PRIMARY-target push success."""
        raw = self._read_state().get("last_push_at")
        return raw if isinstance(raw, str) and raw else None

    def get_last_push_seq(self) -> int:
        """Read the last push sequence number from state."""
        state = self._read_state()
        return self._int_field(state, "last_push_seq")

    def get_last_pull_seq(self) -> int:
        """Read the last pull sequence number from state."""
        state = self._read_state()
        return self._int_field(state, "last_pull_seq")

    def record_company_pull_seq(self, company_seq: int | None = None) -> None:
        """Advance the INDEPENDENT company-tier pull cursor (PRD-INFRA-139 P1-B).

        Company-tier rows page on a per-company sequence that is disjoint from the
        org's ``last_pull_seq``; folding the two into one cursor permanently hid
        company rows once the org cursor outgrew the small company sequence. This
        cursor advances on its own high-water mark and never regresses.
        """
        state = self._read_state()
        state["last_company_pull_seq"] = max(self._int_field(state, "last_company_pull_seq"), company_seq or 0)
        state["version"] = 1
        self._write_state(state)

    def get_last_company_pull_seq(self) -> int:
        """Read the independent company-tier pull cursor (PRD-INFRA-139 P1-B)."""
        state = self._read_state()
        return self._int_field(state, "last_company_pull_seq")

    def get_last_outcome_line(self) -> int:
        """Read the last successfully pushed local outcome line number."""
        state = self._read_state()
        return self._int_field(state, "last_outcome_line")

    def get_consecutive_failures(self) -> int:
        """Read the current consecutive sync failure count."""
        state = self._read_state()
        return self._int_field(state, "consecutive_failures")

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
