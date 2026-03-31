"""Tests for tool invocation heartbeat (PRD-QUAL-050-FR01/FR02).

FR-01: touch_heartbeat() touches meta/heartbeat on every MCP tool invocation.
FR-02: _get_last_activity_timestamp() considers heartbeat mtime alongside checkpoints.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from trw_mcp.state._paths import touch_heartbeat
from trw_mcp.state.analytics.report import _get_last_activity_timestamp, _is_run_stale
from trw_mcp.state.persistence import FileStateWriter

_writer = FileStateWriter()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_run_dir(tmp_path: Path, task: str = "test-task", run_id: str = "run-001") -> Path:
    """Create a minimal run directory structure with meta/ and run.yaml."""
    run_dir = tmp_path / ".trw" / "runs" / task / run_id
    meta = run_dir / "meta"
    meta.mkdir(parents=True)
    _writer.write_yaml(
        meta / "run.yaml",
        {"run_id": run_id, "task": task, "status": "active", "phase": "implement"},
    )
    return run_dir


def _make_run_id_hours_ago(hours_ago: float) -> str:
    """Build a run_id whose embedded timestamp is hours_ago hours in the past."""
    dt = datetime.now(timezone.utc) - timedelta(hours=hours_ago)
    ts = dt.strftime("%Y%m%dT%H%M%SZ")
    return f"{ts}-abcd1234"


def _add_checkpoint(run_dir: Path, hours_ago: float) -> None:
    """Add a checkpoint entry to a run's checkpoints.jsonl."""
    ts = (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat()
    checkpoint = {"ts": ts, "message": "test checkpoint", "state": {}}
    checkpoints_path = run_dir / "meta" / "checkpoints.jsonl"
    line = json.dumps(checkpoint) + "\n"
    with checkpoints_path.open("a", encoding="utf-8") as fh:
        fh.write(line)


# ---------------------------------------------------------------------------
# FR-01: touch_heartbeat()
# ---------------------------------------------------------------------------


class TestTouchHeartbeat:
    """FR-01: touch_heartbeat creates/updates meta/heartbeat in active run dir."""

    def test_touch_heartbeat_creates_file(self, tmp_path: Path) -> None:
        """Heartbeat file is created when touch_heartbeat is called with an active run."""
        run_dir = _make_run_dir(tmp_path)

        with patch("trw_mcp.state._paths.find_active_run", return_value=run_dir):
            touch_heartbeat()

        heartbeat = run_dir / "meta" / "heartbeat"
        assert heartbeat.exists(), "heartbeat file should be created"

    def test_touch_heartbeat_updates_mtime(self, tmp_path: Path) -> None:
        """Subsequent touches update the heartbeat file's mtime."""
        run_dir = _make_run_dir(tmp_path)
        heartbeat = run_dir / "meta" / "heartbeat"

        with patch("trw_mcp.state._paths.find_active_run", return_value=run_dir):
            touch_heartbeat()
            first_mtime = heartbeat.stat().st_mtime

            # Small sleep to ensure mtime difference is measurable
            time.sleep(0.05)

            touch_heartbeat()
            second_mtime = heartbeat.stat().st_mtime

        assert second_mtime > first_mtime, "mtime should increase on subsequent touch"

    def test_touch_heartbeat_no_active_run(self) -> None:
        """No error is raised when no active run exists."""
        with patch("trw_mcp.state._paths.find_active_run", return_value=None):
            # Should not raise
            touch_heartbeat()

    def test_touch_heartbeat_failopen(self, tmp_path: Path) -> None:
        """Exceptions from filesystem operations do not propagate."""
        run_dir = _make_run_dir(tmp_path)

        with (
            patch("trw_mcp.state._paths.find_active_run", return_value=run_dir),
            patch("pathlib.Path.touch", side_effect=OSError("disk full")),
        ):
            # Should not raise -- fail-open
            touch_heartbeat()

    def test_touch_heartbeat_uses_pinned_run_first(self, tmp_path: Path) -> None:
        """touch_heartbeat respects get_pinned_run for fast path."""
        run_dir = _make_run_dir(tmp_path)

        with (
            patch("trw_mcp.state._paths.get_pinned_run", return_value=run_dir),
            # find_active_run should NOT be called when pinned run exists
            patch("trw_mcp.state._paths.find_active_run") as mock_find,
        ):
            touch_heartbeat()

        mock_find.assert_not_called()
        heartbeat = run_dir / "meta" / "heartbeat"
        assert heartbeat.exists()

    def test_touch_heartbeat_falls_back_to_find_active_run(self, tmp_path: Path) -> None:
        """When no pinned run, falls back to find_active_run."""
        run_dir = _make_run_dir(tmp_path)

        with (
            patch("trw_mcp.state._paths.get_pinned_run", return_value=None),
            patch("trw_mcp.state._paths.find_active_run", return_value=run_dir),
        ):
            touch_heartbeat()

        heartbeat = run_dir / "meta" / "heartbeat"
        assert heartbeat.exists()


# ---------------------------------------------------------------------------
# FR-02: Heartbeat-aware staleness detection
# ---------------------------------------------------------------------------


class TestHeartbeatAwareStaleness:
    """FR-02: _get_last_activity_timestamp and _is_run_stale consider heartbeat mtime."""

    def test_get_last_activity_uses_heartbeat(self, tmp_path: Path) -> None:
        """When heartbeat is newer than checkpoint, returns heartbeat time."""
        run_dir = _make_run_dir(tmp_path)
        # Old checkpoint: 50 hours ago
        _add_checkpoint(run_dir, hours_ago=50)

        # Recent heartbeat: touch now
        heartbeat = run_dir / "meta" / "heartbeat"
        heartbeat.touch()

        result = _get_last_activity_timestamp(run_dir)
        assert result is not None

        # Result should be very recent (heartbeat was just touched)
        age_seconds = (datetime.now(timezone.utc) - result).total_seconds()
        assert age_seconds < 10, f"Expected recent timestamp, got {age_seconds}s ago"

    def test_get_last_activity_fallback_no_heartbeat(self, tmp_path: Path) -> None:
        """Without heartbeat file, falls back to checkpoint-only (backward compat)."""
        run_dir = _make_run_dir(tmp_path)
        _add_checkpoint(run_dir, hours_ago=5)

        # No heartbeat file
        assert not (run_dir / "meta" / "heartbeat").exists()

        result = _get_last_activity_timestamp(run_dir)
        assert result is not None

        # Should be approximately 5 hours ago (checkpoint only)
        age_hours = (datetime.now(timezone.utc) - result).total_seconds() / 3600
        assert 4.5 < age_hours < 5.5

    def test_get_last_activity_heartbeat_older_than_checkpoint(self, tmp_path: Path) -> None:
        """When checkpoint is newer than heartbeat, uses checkpoint."""
        run_dir = _make_run_dir(tmp_path)

        # Create old heartbeat: set mtime to 48 hours ago
        heartbeat = run_dir / "meta" / "heartbeat"
        heartbeat.touch()
        old_time = time.time() - (48 * 3600)
        import os

        os.utime(heartbeat, (old_time, old_time))

        # Recent checkpoint: 2 hours ago
        _add_checkpoint(run_dir, hours_ago=2)

        result = _get_last_activity_timestamp(run_dir)
        assert result is not None

        # Should be approximately 2 hours ago (checkpoint is newer)
        age_hours = (datetime.now(timezone.utc) - result).total_seconds() / 3600
        assert 1.5 < age_hours < 2.5

    def test_get_last_activity_heartbeat_only_no_checkpoints(self, tmp_path: Path) -> None:
        """Heartbeat alone (no checkpoints) provides activity timestamp."""
        run_dir = _make_run_dir(tmp_path)

        # No checkpoints, but heartbeat exists
        heartbeat = run_dir / "meta" / "heartbeat"
        heartbeat.touch()

        result = _get_last_activity_timestamp(run_dir)
        assert result is not None

        # Should be very recent
        age_seconds = (datetime.now(timezone.utc) - result).total_seconds()
        assert age_seconds < 10

    def test_is_run_stale_uses_heartbeat(self, tmp_path: Path) -> None:
        """Run with recent heartbeat but old checkpoint is NOT stale."""
        run_id = _make_run_id_hours_ago(72)
        run_dir = tmp_path / ".trw" / "runs" / "test-task" / run_id
        meta = run_dir / "meta"
        meta.mkdir(parents=True)
        _writer.write_yaml(
            meta / "run.yaml",
            {"run_id": run_id, "task": "test-task", "status": "active", "phase": "implement"},
        )

        # Old checkpoint: 60 hours ago (would normally be stale at 48h TTL)
        _add_checkpoint(run_dir, hours_ago=60)

        # Recent heartbeat: touch now
        (meta / "heartbeat").touch()

        now = datetime.now(timezone.utc)
        run_data = {"run_id": run_id, "task": "test-task", "status": "active"}

        # With 48-hour TTL, this should NOT be stale because heartbeat is recent
        assert not _is_run_stale(run_dir, run_data, ttl_hours=48, now=now)

    def test_is_run_stale_fallback_no_heartbeat(self, tmp_path: Path) -> None:
        """Without heartbeat, stale detection uses checkpoint only (backward compat)."""
        run_id = _make_run_id_hours_ago(72)
        run_dir = tmp_path / ".trw" / "runs" / "test-task" / run_id
        meta = run_dir / "meta"
        meta.mkdir(parents=True)
        _writer.write_yaml(
            meta / "run.yaml",
            {"run_id": run_id, "task": "test-task", "status": "active", "phase": "implement"},
        )

        # Old checkpoint: 60 hours ago
        _add_checkpoint(run_dir, hours_ago=60)

        # No heartbeat file
        assert not (meta / "heartbeat").exists()

        now = datetime.now(timezone.utc)
        run_data = {"run_id": run_id, "task": "test-task", "status": "active"}

        # Should be stale: 60h > 48h TTL, no heartbeat to rescue
        assert _is_run_stale(run_dir, run_data, ttl_hours=48, now=now)
