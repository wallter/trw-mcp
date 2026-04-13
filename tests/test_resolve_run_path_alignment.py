"""Tests for resolve_run_path + find_active_run alignment (PRD-FIX-077).

Reported bug (cursor-ide audit): trw_status and trw_session_start disagreed
on which run is "current" — session_start pinned one run, but trw_status
went through resolve_run_path() which picked a different, abandoned run by
latest mtime.

Fix: resolve_run_path now delegates to find_active_run() first (which honors
the per-session pin + status-aware scan), and only falls back to
_find_latest_run_dir on miss. These tests lock the contract in place.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml


def _make_run(
    runs_root: Path,
    task: str,
    run_id: str,
    *,
    status: str = "active",
    touch_ts: float | None = None,
) -> Path:
    """Build a minimal run directory with a valid run.yaml.

    The run.yaml's mtime is pinned via os.utime if touch_ts is given, so we
    can build the "stale mtime" scenario the bug report describes.
    """
    import os as _os

    run_dir = runs_root / task / run_id
    (run_dir / "meta").mkdir(parents=True, exist_ok=True)
    run_yaml = run_dir / "meta" / "run.yaml"
    run_yaml.write_text(
        yaml.safe_dump(
            {
                "run_id": run_id,
                "task_name": task,
                "status": status,
                "created": datetime.now(timezone.utc).isoformat(),
            }
        ),
        encoding="utf-8",
    )
    if touch_ts is not None:
        _os.utime(run_yaml, (touch_ts, touch_ts))
    return run_dir


@pytest.fixture
def project_with_runs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A fake project root with a .trw/runs/ dir + reset pin state."""
    monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
    # Reset session pin + singletons between tests
    from trw_mcp.state import _paths as _paths_mod

    _paths_mod._pinned_runs.clear()
    _paths_mod._reset_session_id()

    # Ensure TRWConfig singleton is refreshed so config.runs_root resolves
    from trw_mcp.models.config import _main as _config_main

    _config_main._CONFIG_CACHE = None  # type: ignore[attr-defined]

    runs_dir = tmp_path / ".trw" / "runs"
    runs_dir.mkdir(parents=True)
    return tmp_path


@pytest.mark.integration
class TestResolveRunPathAlignment:
    """resolve_run_path MUST agree with find_active_run / trw_session_start."""

    def test_pinned_run_wins_over_latest_mtime_run(
        self, project_with_runs: Path
    ) -> None:
        """The bug scenario: abandoned run has newer mtime; pinned run wins.

        trw_session_start pins run A. Run B is marked abandoned but its
        run.yaml has a newer mtime (e.g. another session just wrote a
        summary). Pre-fix: trw_status picked B. Post-fix: trw_status picks A.
        """
        from trw_mcp.state._paths import (
            pin_active_run,
            resolve_run_path,
        )

        runs_root = project_with_runs / ".trw" / "runs"
        # Build run A (active, older mtime) and run B (abandoned, newer mtime)
        run_a = _make_run(
            runs_root, "current-task", "20260413T100000Z-a000",
            status="active", touch_ts=1_000_000.0,
        )
        _make_run(
            runs_root, "stale-task", "20260413T110000Z-b000",
            status="abandoned", touch_ts=9_000_000.0,  # newer mtime
        )

        pin_active_run(run_a)
        resolved = resolve_run_path()
        assert resolved == run_a, (
            f"Expected pinned run {run_a.name}, got {resolved.name}"
        )

    def test_active_run_wins_over_complete_run_without_pin(
        self, project_with_runs: Path
    ) -> None:
        """No pin: find_active_run's status filter picks the active run even
        when a completed run has a newer mtime."""
        from trw_mcp.state._paths import resolve_run_path

        runs_root = project_with_runs / ".trw" / "runs"
        run_active = _make_run(
            runs_root, "task-a", "20260413T100000Z-active",
            status="active", touch_ts=1_000_000.0,
        )
        _make_run(
            runs_root, "task-b", "20260413T110000Z-complete",
            status="complete", touch_ts=9_000_000.0,  # newer mtime, but complete
        )

        resolved = resolve_run_path()
        assert resolved == run_active, (
            "Active run must win over later-mtime complete run"
        )

    def test_mtime_fallback_when_no_active_runs_exist(
        self, project_with_runs: Path
    ) -> None:
        """All runs complete/failed → fall back to latest mtime (preserves
        backward compat for post-mortem discovery tooling)."""
        from trw_mcp.state._paths import resolve_run_path

        runs_root = project_with_runs / ".trw" / "runs"
        _make_run(
            runs_root, "task-a", "20260413T100000Z-old",
            status="complete", touch_ts=1_000_000.0,
        )
        run_newer = _make_run(
            runs_root, "task-b", "20260413T110000Z-new",
            status="failed", touch_ts=9_000_000.0,
        )

        resolved = resolve_run_path()
        # Both are non-active → mtime fallback kicks in → newer wins
        assert resolved == run_newer

    def test_explicit_run_path_still_honored(
        self, project_with_runs: Path
    ) -> None:
        """Passing an explicit run_path bypasses pin + active-run resolution."""
        from trw_mcp.state._paths import pin_active_run, resolve_run_path

        runs_root = project_with_runs / ".trw" / "runs"
        run_pinned = _make_run(
            runs_root, "pinned-task", "20260413T100000Z-pinned",
            status="active",
        )
        run_explicit = _make_run(
            runs_root, "other-task", "20260413T110000Z-explicit",
            status="active",
        )
        pin_active_run(run_pinned)

        # Explicit path must win over pin
        resolved = resolve_run_path(str(run_explicit))
        assert resolved == run_explicit

    def test_raises_when_no_runs_and_no_pin(
        self, project_with_runs: Path
    ) -> None:
        """Empty runs/ directory → StateError (no active runs)."""
        from trw_mcp.exceptions import StateError
        from trw_mcp.state._paths import resolve_run_path

        # runs/ dir exists but is empty (project_with_runs fixture created it)
        with pytest.raises(StateError, match="No active runs found"):
            resolve_run_path()

    def test_trw_status_agrees_with_trw_session_start(
        self, project_with_runs: Path
    ) -> None:
        """The end-to-end scenario from the bug report.

        Simulates: trw_session_start pins run A. trw_status (called with no
        run_path) must return run A, not a different run with newer mtime.
        Without the fix, trw_status returned the abandoned run with later
        mtime. With the fix, both converge on the pinned run.
        """
        from trw_mcp.state._paths import (
            find_active_run,
            pin_active_run,
            resolve_run_path,
        )

        runs_root = project_with_runs / ".trw" / "runs"
        run_session = _make_run(
            runs_root, "cursor-cli-integration", "20260413T005703Z-12a40859",
            status="active", touch_ts=1_000_000.0,
        )
        _make_run(
            runs_root, "sprint-88-aaref-plan-fidelity", "20260411T014550Z-221fd1a1",
            status="complete", touch_ts=9_000_000.0,  # newer mtime
        )

        # Simulate trw_session_start pinning the run
        pin_active_run(run_session)

        # Both APIs must return the same run
        from_session = find_active_run()
        from_status = resolve_run_path()
        assert from_session == from_status == run_session, (
            f"trw_session_start returned {from_session.name if from_session else None}, "
            f"trw_status returned {from_status.name} — they must agree"
        )


from tests._structlog_capture import captured_structlog  # noqa: F401


@pytest.mark.integration
class TestObservability:
    """mtime fallback emits a structured log so stale-run resolution is visible."""

    def test_mtime_fallback_logs_warning(
        self, project_with_runs: Path, captured_structlog: list[dict]
    ) -> None:
        """When mtime fallback is used, a structlog event is emitted."""
        from trw_mcp.state._paths import resolve_run_path

        runs_root = project_with_runs / ".trw" / "runs"
        run_complete = _make_run(
            runs_root, "task-x", "20260413T100000Z-x",
            status="complete",
        )

        resolved = resolve_run_path()

        assert resolved == run_complete  # mtime fallback fired
        fallback_logs = [
            le for le in captured_structlog
            if le.get("event") == "resolve_run_path_mtime_fallback"
        ]
        assert len(fallback_logs) == 1
        assert fallback_logs[0]["reason"] == "no_pinned_or_active_run"
