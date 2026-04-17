"""Tests for PRD-CORE-141 Wave 4 Track A — stale-run sweep (FR09/FR10).

Covers the staleness formula, grace window, protected/pinned preservation,
skip/malformed handling, idempotence, dry-run, and boot-sequence behavior.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from structlog.testing import capture_logs

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_run_yaml(run_dir: Path, status: str = "active", **extra: Any) -> Path:
    """Create ``run_dir/meta/run.yaml`` with the given fields."""
    meta = run_dir / "meta"
    meta.mkdir(parents=True, exist_ok=True)
    run_yaml = meta / "run.yaml"
    lines = [
        f"run_id: {run_dir.name}",
        f"task: {run_dir.parent.name}",
        "framework: v24.5_TRW",
        f"status: {status}",
        "phase: implement",
        "confidence: medium",
    ]
    for key, value in extra.items():
        if isinstance(value, bool):
            lines.append(f"{key}: {'true' if value else 'false'}")
        else:
            lines.append(f"{key}: {value}")
    run_yaml.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return run_yaml


def _set_mtime(path: Path, mtime: float) -> None:
    """Force the access + modification time of *path* to *mtime* seconds since epoch."""
    os.utime(path, (mtime, mtime))


def _make_run(
    runs_root: Path,
    task: str,
    run_id: str,
    *,
    status: str = "active",
    events_age_hours: float | None = None,
    run_yaml_age_hours: float | None = None,
    checkpoints_age_hours: float | None = None,
    heartbeat_age_hours: float | None = None,
    protected: bool = False,
    now: float | None = None,
) -> Path:
    """Build a run dir under ``runs_root/task/run_id`` with controlled mtimes."""
    now_ts = now if now is not None else time.time()
    run_dir = runs_root / task / run_id
    run_yaml = _write_run_yaml(run_dir, status=status, protected=protected)

    meta = run_dir / "meta"
    events = meta / "events.jsonl"
    checkpoints = meta / "checkpoints.jsonl"
    heartbeat = meta / "heartbeat"

    if events_age_hours is not None:
        events.write_text("", encoding="utf-8")
        _set_mtime(events, now_ts - events_age_hours * 3600)
    if checkpoints_age_hours is not None:
        checkpoints.write_text("", encoding="utf-8")
        _set_mtime(checkpoints, now_ts - checkpoints_age_hours * 3600)
    if heartbeat_age_hours is not None:
        heartbeat.write_text("", encoding="utf-8")
        _set_mtime(heartbeat, now_ts - heartbeat_age_hours * 3600)

    # Default run.yaml age to max(events, checkpoints) age when unset, so tests
    # asking for "stale activity" actually produce a stale run_yaml mtime too
    # (staleness formula is max of four file mtimes).
    if run_yaml_age_hours is None:
        default_age = max(
            events_age_hours or 0.0,
            checkpoints_age_hours or 0.0,
            heartbeat_age_hours or 0.0,
        )
        if default_age > 0.0:
            _set_mtime(run_yaml, now_ts - default_age * 3600)
    else:
        _set_mtime(run_yaml, now_ts - run_yaml_age_hours * 3600)

    return run_dir


def _read_status(run_dir: Path) -> str:
    """Return the ``status:`` field from run.yaml (simple line parse)."""
    run_yaml = run_dir / "meta" / "run.yaml"
    for line in run_yaml.read_text(encoding="utf-8").splitlines():
        if line.startswith("status:"):
            return line.split(":", 1)[1].strip()
    return ""


# ---------------------------------------------------------------------------
# FR09 — sweep_stale_runs behavior
# ---------------------------------------------------------------------------


def test_sweep_marks_stale_active_abandoned(tmp_path: Path) -> None:
    from trw_mcp.state._run_gc import sweep_stale_runs

    runs_root = tmp_path / "runs"
    now = time.time()
    # 72h age — outside both the 48h staleness threshold AND the 60h grace
    # window ceiling (48h + 12h grace).  Beyond grace means abandonment.
    run_dir = _make_run(
        runs_root,
        "task-a",
        "r1",
        events_age_hours=72.0,
        run_yaml_age_hours=72.0,
        now=now,
    )

    report = sweep_stale_runs(runs_root, 48, 12, [], dry_run=False, _now=now)
    assert report.runs_abandoned == 1
    assert report.abandoned_run_ids == ["r1"]
    assert _read_status(run_dir) == "abandoned"

    # Event was appended to events.jsonl
    events_content = (run_dir / "meta" / "events.jsonl").read_text(encoding="utf-8")
    assert "run_auto_abandoned" in events_content


def test_sweep_preserves_pinned_run_regardless_of_age(tmp_path: Path) -> None:
    from trw_mcp.state._run_gc import sweep_stale_runs

    runs_root = tmp_path / "runs"
    now = time.time()
    run_dir = _make_run(runs_root, "task-a", "r1", events_age_hours=72.0, now=now)

    report = sweep_stale_runs(runs_root, 48, 12, [run_dir], dry_run=False, _now=now)
    assert report.runs_abandoned == 0
    assert report.runs_preserved_pinned == 1
    assert _read_status(run_dir) == "active"


def test_sweep_preserves_protected_true_regardless_of_age(tmp_path: Path) -> None:
    from trw_mcp.state._run_gc import sweep_stale_runs

    runs_root = tmp_path / "runs"
    now = time.time()
    run_dir = _make_run(
        runs_root,
        "task-a",
        "r1",
        events_age_hours=100.0,
        protected=True,
        now=now,
    )

    report = sweep_stale_runs(runs_root, 48, 12, [], dry_run=False, _now=now)
    assert report.runs_abandoned == 0
    assert report.runs_preserved_protected == 1
    assert _read_status(run_dir) == "active"


def test_sweep_preserves_run_with_fresh_heartbeat_but_stale_events(
    tmp_path: Path,
) -> None:
    from trw_mcp.state._run_gc import sweep_stale_runs

    runs_root = tmp_path / "runs"
    now = time.time()
    run_dir = _make_run(
        runs_root,
        "task-a",
        "r1",
        events_age_hours=72.0,
        run_yaml_age_hours=72.0,
        checkpoints_age_hours=72.0,
        heartbeat_age_hours=0.083,  # ~5 minutes
        now=now,
    )

    report = sweep_stale_runs(runs_root, 48, 12, [], dry_run=False, _now=now)
    assert report.runs_abandoned == 0
    assert _read_status(run_dir) == "active"


def test_sweep_emits_near_stale_warning_in_grace_window(tmp_path: Path) -> None:
    from trw_mcp.state._run_gc import sweep_stale_runs

    runs_root = tmp_path / "runs"
    now = time.time()
    # Age ~55h — inside the 48h..60h grace window (48 + 12).
    run_dir = _make_run(
        runs_root,
        "task-a",
        "r1",
        events_age_hours=55.0,
        run_yaml_age_hours=55.0,
        now=now,
    )

    with capture_logs() as captured:
        report = sweep_stale_runs(runs_root, 48, 12, [], dry_run=False, _now=now)

    assert report.runs_abandoned == 0
    assert report.runs_in_grace_window == 1
    assert report.near_stale_run_ids == ["r1"]
    assert _read_status(run_dir) == "active"
    near_stale_events = [e for e in captured if e.get("event") == "run_near_stale_warning"]
    assert near_stale_events, "expected run_near_stale_warning log"
    assert "grace_hours_remaining" in near_stale_events[0]


def test_sweep_skips_terminal_status_runs(tmp_path: Path) -> None:
    from trw_mcp.state._run_gc import sweep_stale_runs

    runs_root = tmp_path / "runs"
    now = time.time()
    for i, status in enumerate(("complete", "failed", "delivered", "abandoned")):
        _make_run(
            runs_root,
            "task-a",
            f"r-{status}-{i}",
            status=status,
            events_age_hours=100.0,
            now=now,
        )

    report = sweep_stale_runs(runs_root, 48, 12, [], dry_run=False, _now=now)
    assert report.runs_abandoned == 0
    assert report.runs_skipped_terminal == 4


def test_sweep_skips_malformed_run_yaml(tmp_path: Path) -> None:
    from trw_mcp.state._run_gc import sweep_stale_runs

    runs_root = tmp_path / "runs"
    now = time.time()

    # One malformed run
    bad_run = runs_root / "task-a" / "r-bad"
    (bad_run / "meta").mkdir(parents=True)
    (bad_run / "meta" / "run.yaml").write_text("this: is: not: valid: yaml: [unclosed\n", encoding="utf-8")

    # One good stale run that SHOULD be abandoned — the sweep must continue past the bad entry.
    good_run = _make_run(runs_root, "task-a", "r-good", events_age_hours=72.0, now=now)

    report = sweep_stale_runs(runs_root, 48, 12, [], dry_run=False, _now=now)
    assert report.runs_skipped_malformed >= 1
    assert report.runs_abandoned == 1
    assert _read_status(good_run) == "abandoned"


def test_sweep_idempotent(tmp_path: Path) -> None:
    from trw_mcp.state._run_gc import sweep_stale_runs

    runs_root = tmp_path / "runs"
    now = time.time()
    _make_run(runs_root, "task-a", "r1", events_age_hours=72.0, now=now)

    first = sweep_stale_runs(runs_root, 48, 12, [], dry_run=False, _now=now)
    assert first.runs_abandoned == 1

    second = sweep_stale_runs(runs_root, 48, 12, [], dry_run=False, _now=now)
    assert second.runs_abandoned == 0
    assert second.runs_skipped_terminal == 1


def test_sweep_dry_run_does_not_mutate(tmp_path: Path) -> None:
    from trw_mcp.state._run_gc import sweep_stale_runs

    runs_root = tmp_path / "runs"
    now = time.time()
    run_dir = _make_run(runs_root, "task-a", "r1", events_age_hours=72.0, now=now)

    report = sweep_stale_runs(runs_root, 48, 12, [], dry_run=True, _now=now)
    assert report.runs_abandoned == 1
    assert report.abandoned_run_ids == ["r1"]
    # Status unchanged on disk despite the report count.
    assert _read_status(run_dir) == "active"
    assert not (run_dir / "meta" / "events.jsonl").exists() or "run_auto_abandoned" not in (
        run_dir / "meta" / "events.jsonl"
    ).read_text(encoding="utf-8")


def test_sweep_staleness_formula_uses_max_of_four_files(tmp_path: Path) -> None:
    """Three files are stale (72h) but the fourth (heartbeat) is fresh — preserve."""
    from trw_mcp.state._run_gc import sweep_stale_runs

    runs_root = tmp_path / "runs"
    now = time.time()
    run_dir = _make_run(
        runs_root,
        "task-a",
        "r1",
        events_age_hours=72.0,
        run_yaml_age_hours=72.0,
        checkpoints_age_hours=72.0,
        heartbeat_age_hours=1.0,
        now=now,
    )

    report = sweep_stale_runs(runs_root, 48, 12, [], dry_run=False, _now=now)
    assert report.runs_abandoned == 0
    assert _read_status(run_dir) == "active"


def test_sweep_handles_missing_files_as_mtime_zero(tmp_path: Path) -> None:
    """run.yaml is recent; events/checkpoints/heartbeat do not exist — preserved."""
    from trw_mcp.state._run_gc import sweep_stale_runs

    runs_root = tmp_path / "runs"
    now = time.time()
    # Only run.yaml exists; its mtime is fresh (just created).
    run_dir = _make_run(runs_root, "task-a", "r1", now=now)

    report = sweep_stale_runs(runs_root, 48, 12, [], dry_run=False, _now=now)
    assert report.runs_abandoned == 0
    assert _read_status(run_dir) == "active"


# ---------------------------------------------------------------------------
# FR10 — protected field round-trip
# ---------------------------------------------------------------------------


def test_protected_field_round_trip() -> None:
    """RunState with protected=True round-trips through YAML preserved."""
    import io

    from ruamel.yaml import YAML

    from trw_mcp.models.run import RunState
    from trw_mcp.state.persistence import model_to_dict

    state = RunState(run_id="r1", task="t", protected=True)
    data = model_to_dict(state)
    assert data["protected"] is True

    buf = io.StringIO()
    YAML(typ="rt").dump(data, buf)
    loaded = YAML(typ="safe").load(buf.getvalue())
    assert loaded["protected"] is True


def test_protected_field_default_false_when_absent() -> None:
    """YAML without protected key → Pydantic default is False."""
    from trw_mcp.models.run import RunState

    state = RunState(run_id="r1", task="t")
    assert state.protected is False


# ---------------------------------------------------------------------------
# Boot-sequence behavior (FR09 integration)
# ---------------------------------------------------------------------------


def test_boot_sequence_respects_cleanup_on_boot_false(monkeypatch: pytest.MonkeyPatch) -> None:
    from trw_mcp.models.config import TRWConfig
    from trw_mcp.server._cli import _boot_sequence

    config = TRWConfig(cleanup_on_boot=False)
    called = {"sweep": False}

    def _sentinel_sweep(*_a: Any, **_kw: Any) -> Any:
        called["sweep"] = True
        raise AssertionError("sweep should not be called when cleanup_on_boot=False")

    monkeypatch.setattr("trw_mcp.state._run_gc.sweep_stale_runs", _sentinel_sweep)

    log = MagicMock()
    _boot_sequence(config, log)

    assert called["sweep"] is False
    # info(...) was called with "boot_gc_skipped_config" as the event name.
    info_calls = [c.args[0] for c in log.info.call_args_list if c.args]
    assert "boot_gc_skipped_config" in info_calls


def test_boot_sequence_fail_open_on_sweep_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from trw_mcp.models.config import TRWConfig
    from trw_mcp.server._cli import _boot_sequence

    config = TRWConfig(cleanup_on_boot=True)

    def _raising_sweep(*_a: Any, **_kw: Any) -> Any:
        raise RuntimeError("sweep blew up")

    monkeypatch.setattr("trw_mcp.state._run_gc.sweep_stale_runs", _raising_sweep)

    log = MagicMock()
    # Must not raise.
    _boot_sequence(config, log)
    # Confirm the failure was logged.
    assert log.warning.called
    call_args = log.warning.call_args
    assert call_args.args[0] == "boot_gc_failed"
