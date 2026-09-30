"""The sweep's early full-parse checks are NOT redundant with the final re-read (GC-SWEEP-SIMPLIFY finding).

The final re-read only guards the ABANDON path. The early terminal / non-active / protected checks also decide runs
that end in the fresh-activity or grace-window branches, which never reach it: deleting them made a protected run
report as near-stale (with a warning) and left a fresh terminal run uncounted. These pin that.
"""

from __future__ import annotations

from pathlib import Path

from tests.test_run_gc import _make_run
from tests.test_run_gc_preservation import NOW, _push_past_prefilter
from trw_mcp.state import _run_gc


def test_a_protected_run_in_the_grace_window_is_preserved_not_reported_near_stale(tmp_path: Path) -> None:
    runs_root = tmp_path / "runs"
    run_dir = _make_run(runs_root, "t", "r1", events_age_hours=54, run_yaml_age_hours=54, protected=True, now=NOW)
    _push_past_prefilter(run_dir)
    report = _run_gc.sweep_stale_runs(runs_root, 48, 12, [], dry_run=True, _now=NOW)
    assert report.runs_preserved_protected == 1
    assert report.runs_in_grace_window == 0


def test_a_terminal_run_with_fresh_activity_is_counted_terminal(tmp_path: Path) -> None:
    runs_root = tmp_path / "runs"
    run_dir = _make_run(runs_root, "t", "r1", events_age_hours=1, run_yaml_age_hours=1, status="complete", now=NOW)
    _push_past_prefilter(run_dir)
    report = _run_gc.sweep_stale_runs(runs_root, 48, 12, [], dry_run=True, _now=NOW)
    assert report.runs_skipped_terminal == 1
