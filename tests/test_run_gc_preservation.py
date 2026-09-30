"""Preservation and refusal branches of the stale-run sweep and ``trw-mcp gc``.

Every test here pins a way the sweep must NOT abandon (or must not lose) a
run: protected runs found via the authoritative-parse path, a writer that wins
the run.yaml lock race, a failed write, a fresh heartbeat seen at the final
read, and the ``gc`` CLI's live-pin / missing-root handling.
"""

from __future__ import annotations

import argparse
import io
import json
import time
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from tests.test_run_gc import _make_run, _read_status, _set_mtime
from trw_mcp.exceptions import StateError
from trw_mcp.state import _run_gc
from trw_mcp.state.persistence import FileStateWriter

NOW = 1_700_000_000.0
# A 5000-char comment header pushes `status:` past the 4KB prefilter window, so
# the sweep must fall back to the authoritative YAML parse.
_BIG_HEADER = "# " + "x" * 5000 + "\n"


def _stale_run(runs_root: Path, run_id: str = "r1") -> Path:
    return _make_run(runs_root, "task-a", run_id, events_age_hours=72, run_yaml_age_hours=72, now=NOW)


def _push_past_prefilter(run_dir: Path) -> None:
    run_yaml = run_dir / "meta" / "run.yaml"
    run_yaml.write_text(_BIG_HEADER + run_yaml.read_text(encoding="utf-8"), encoding="utf-8")
    _set_mtime(run_yaml, NOW - 72 * 3600)


def _events_text(run_dir: Path) -> str:
    events = run_dir / "meta" / "events.jsonl"
    return events.read_text(encoding="utf-8") if events.exists() else ""


# ---------------------------------------------------------------------------
# Authoritative-parse fallback (status not resolvable from the 4KB header)
# ---------------------------------------------------------------------------


def test_protected_run_found_by_full_parse_is_never_abandoned(tmp_path: Path) -> None:
    runs_root = tmp_path / "runs"
    run_dir = _make_run(
        runs_root, "task-a", "r1", events_age_hours=500, run_yaml_age_hours=500, protected=True, now=NOW
    )
    _push_past_prefilter(run_dir)

    report = _run_gc.sweep_stale_runs(runs_root, 48, 12, [], dry_run=False, _now=NOW)

    assert report.runs_preserved_protected == 1
    assert report.runs_abandoned == 0
    assert _read_status(run_dir) == "active"
    assert "run_auto_abandoned" not in _events_text(run_dir)


def test_unprotected_run_found_by_full_parse_is_still_abandoned(tmp_path: Path) -> None:
    """Negative twin: the fallback path does not over-preserve."""
    runs_root = tmp_path / "runs"
    run_dir = _stale_run(runs_root)
    _push_past_prefilter(run_dir)

    report = _run_gc.sweep_stale_runs(runs_root, 48, 12, [], dry_run=False, _now=NOW)

    assert report.runs_abandoned == 1
    assert _read_status(run_dir) == "abandoned"


@pytest.mark.parametrize("status", ["complete", "abandoned", "paused-by-user"])
def test_full_parse_path_skips_non_active_runs(tmp_path: Path, status: str) -> None:
    runs_root = tmp_path / "runs"
    run_dir = _make_run(runs_root, "task-a", "r1", status=status, events_age_hours=500, now=NOW)
    run_yaml = run_dir / "meta" / "run.yaml"
    run_yaml.write_text(_BIG_HEADER + run_yaml.read_text(encoding="utf-8"), encoding="utf-8")
    _set_mtime(run_yaml, NOW - 500 * 3600)

    report = _run_gc.sweep_stale_runs(runs_root, 48, 12, [], dry_run=False, _now=NOW)

    assert report.runs_abandoned == 0
    assert report.runs_skipped_terminal == 1
    assert _read_status(run_dir) == status
    assert "run_auto_abandoned" not in _events_text(run_dir)


def test_missing_runs_root_returns_an_empty_report(tmp_path: Path) -> None:
    report = _run_gc.sweep_stale_runs(tmp_path / "no-such-runs", 48, 12, [], dry_run=False, _now=NOW)

    assert report == _run_gc.StaleRunReport(duration_ms=report.duration_ms)
    assert report.runs_scanned == 0


def test_protected_true_with_trailing_comment_is_still_preserved(tmp_path: Path) -> None:
    """The header regex cannot see `protected: true # why`; the final read must."""
    runs_root = tmp_path / "runs"
    run_dir = _stale_run(runs_root)
    run_yaml = run_dir / "meta" / "run.yaml"
    run_yaml.write_text(
        run_yaml.read_text(encoding="utf-8").replace("protected: false", "protected: true  # keep me"),
        encoding="utf-8",
    )
    _set_mtime(run_yaml, NOW - 72 * 3600)

    report = _run_gc.sweep_stale_runs(runs_root, 48, 12, [], dry_run=False, _now=NOW)

    assert report.runs_abandoned == 0
    assert report.runs_preserved_protected == 1
    assert _read_status(run_dir) == "active"


# ---------------------------------------------------------------------------
# Final-read (mutation boundary) refusals
# ---------------------------------------------------------------------------


def test_run_yaml_unreadable_at_abandon_time_is_kept(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    runs_root = tmp_path / "runs"
    run_dir = _stale_run(runs_root)
    monkeypatch.setattr(_run_gc, "_load_run_yaml", lambda path: None)

    report = _run_gc.sweep_stale_runs(runs_root, 48, 12, [], dry_run=False, _now=NOW)

    assert report.runs_abandoned == 0
    assert report.runs_skipped_malformed == 1
    assert _read_status(run_dir) == "active"


def test_protect_landing_before_final_read_preserves_the_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    runs_root = tmp_path / "runs"
    run_dir = _stale_run(runs_root)
    original_load = _run_gc._load_run_yaml

    def _protect_then_read(path: Path) -> Any:
        data = original_load(path)
        assert data is not None
        data["protected"] = True
        FileStateWriter().write_yaml(path, data)
        return original_load(path)

    monkeypatch.setattr(_run_gc, "_load_run_yaml", _protect_then_read)

    report = _run_gc.sweep_stale_runs(runs_root, 48, 12, [], dry_run=False, _now=NOW)

    assert report.runs_abandoned == 0
    assert report.runs_preserved_protected == 1
    assert _read_status(run_dir) == "active"
    assert "run_auto_abandoned" not in _events_text(run_dir)


def test_heartbeat_into_grace_window_before_final_read_is_kept_and_warned(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runs_root = tmp_path / "runs"
    run_dir = _stale_run(runs_root)
    original_load = _run_gc._load_run_yaml

    def _grace_heartbeat(path: Path) -> Any:
        heartbeat = run_dir / "meta" / "heartbeat"
        heartbeat.write_text("", encoding="utf-8")
        _set_mtime(heartbeat, NOW - 50 * 3600)  # stale (>48h) but inside the 12h grace window
        return original_load(path)

    monkeypatch.setattr(_run_gc, "_load_run_yaml", _grace_heartbeat)

    report = _run_gc.sweep_stale_runs(runs_root, 48, 12, [], dry_run=False, _now=NOW)

    assert report.runs_abandoned == 0
    assert report.near_stale_run_ids == ["r1"]
    assert _read_status(run_dir) == "active"


# ---------------------------------------------------------------------------
# Lock race: a writer wins between the final read and the locked write
# ---------------------------------------------------------------------------


def _writer_wins_lock(monkeypatch: pytest.MonkeyPatch, mutate: Any) -> None:
    """Make a competing writer change run.yaml just before the sweep's locked update."""
    real_update = _run_gc.update_run_yaml

    def _racing_update(run_dir: Path, fn: Any) -> Any:
        real_update(run_dir, mutate)  # the competing writer, through the same lock
        return real_update(run_dir, fn)

    monkeypatch.setattr(_run_gc, "update_run_yaml", _racing_update)


@pytest.mark.parametrize(
    ("winner", "expect_status", "expect_protected"),
    [
        (lambda d: d.__setitem__("status", "complete"), "complete", False),
        (lambda d: d.__setitem__("protected", True), "active", True),  # GC-SWEEP-SIMPLIFY: counted as protected
    ],
    ids=["writer-completed-run", "writer-protected-run"],
)
def test_lock_race_writer_wins_and_run_is_not_abandoned(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    winner: Any,
    expect_status: str,
    expect_protected: bool,
) -> None:
    runs_root = tmp_path / "runs"
    run_dir = _stale_run(runs_root)
    _writer_wins_lock(monkeypatch, winner)

    report = _run_gc.sweep_stale_runs(runs_root, 48, 12, [], dry_run=False, _now=NOW)

    assert report.runs_abandoned == 0
    assert report.abandoned_run_ids == []
    assert report.runs_preserved_protected == (1 if expect_protected else 0)
    assert report.runs_skipped_terminal == (0 if expect_protected else 1)
    assert _read_status(run_dir) == expect_status
    assert ("protected: true" in (run_dir / "meta" / "run.yaml").read_text(encoding="utf-8")) is expect_protected
    assert "run_auto_abandoned" not in _events_text(run_dir)


def test_abandon_lands_when_nobody_races(tmp_path: Path) -> None:
    """Negative twin of the race tests: with no competing writer the abandon lands and is audited."""
    runs_root = tmp_path / "runs"
    run_dir = _stale_run(runs_root)

    report = _run_gc.sweep_stale_runs(runs_root, 48, 12, [], dry_run=False, _now=NOW)

    assert report.abandoned_run_ids == ["r1"]
    assert "run_auto_abandoned" in _events_text(run_dir)


# ---------------------------------------------------------------------------
# Write failure: the run stays active and the sweep continues
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("exc", [OSError("disk full"), StateError("lock timeout")], ids=["oserror", "stateerror"])
def test_write_failure_keeps_run_active_and_sweep_continues(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, exc: Exception
) -> None:
    runs_root = tmp_path / "runs"
    failing = _make_run(runs_root, "task-a", "r1", events_age_hours=72, run_yaml_age_hours=72, now=NOW)
    healthy = _make_run(runs_root, "task-a", "r2", events_age_hours=72, run_yaml_age_hours=72, now=NOW)
    real_update = _run_gc.update_run_yaml

    def _fail_r1(run_dir: Path, fn: Any) -> Any:
        if run_dir.name == "r1":
            raise exc
        return real_update(run_dir, fn)

    monkeypatch.setattr(_run_gc, "update_run_yaml", _fail_r1)

    report = _run_gc.sweep_stale_runs(runs_root, 48, 12, [], dry_run=False, _now=NOW)

    assert _read_status(failing) == "active"
    assert "run_auto_abandoned" not in _events_text(failing)
    assert _read_status(healthy) == "abandoned"
    assert report.abandoned_run_ids == ["r2"]
    assert report.runs_abandoned == 1
    assert report.runs_skipped_malformed == 1


# ---------------------------------------------------------------------------
# ``trw-mcp gc`` CLI handler
# ---------------------------------------------------------------------------


def _run_cli(
    project_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    pins: dict[str, dict[str, Any]] | None = None,
    dry_run: bool = False,
    as_json: bool = True,
) -> tuple[int | None, str]:
    from trw_mcp.server import _subcommands
    from trw_mcp.state import _paths, _pin_store

    monkeypatch.setattr(_paths, "resolve_project_root", lambda: project_root)
    monkeypatch.setattr(_pin_store, "load_pin_store", lambda: pins or {})
    args = argparse.Namespace(staleness_hours=48, grace_hours=12, dry_run=dry_run, as_json=as_json)
    buf = io.StringIO()
    with redirect_stdout(buf), pytest.raises(SystemExit) as exit_info:
        _subcommands._run_gc(args)
    return exit_info.value.code, buf.getvalue()


def _payload(out: str) -> dict[str, Any]:
    return json.loads(out[out.index("{") :])  # type: ignore[no-any-return]


def _iso(hours_ago: float) -> str:
    ts = datetime.now(timezone.utc) - timedelta(hours=hours_ago)
    return ts.strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


def _cli_stale_run(project_root: Path, run_id: str) -> Path:
    runs_root = project_root / ".trw" / "runs"
    return _make_run(runs_root, "task-a", run_id, events_age_hours=200, run_yaml_age_hours=200, now=time.time())


@pytest.mark.parametrize("as_json", [True, False])
def test_cli_refuses_when_runs_root_is_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], as_json: bool
) -> None:
    code, out = _run_cli(tmp_path, monkeypatch, as_json=as_json)

    assert code == 1
    if as_json:
        assert "runs_root not found" in json.loads(out)["error"]
    else:
        assert "runs_root not found" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("heartbeat", "expect_kept"),
    [
        (lambda: _iso(1), True),  # live pin
        (lambda: "not-a-timestamp", True),  # malformed heartbeat: conservative, keep
        (lambda: _iso(24 * 30), False),  # long-expired pin no longer protects
    ],
    ids=["live-pin", "malformed-heartbeat", "expired-pin"],
)
def test_cli_live_pins_protect_the_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, heartbeat: Any, expect_kept: bool
) -> None:
    run_dir = _cli_stale_run(tmp_path, "r1")
    pins = {"sess": {"run_path": str(run_dir), "last_heartbeat_ts": heartbeat()}}

    code, out = _run_cli(tmp_path, monkeypatch, pins=pins)

    assert code == 0
    payload = _payload(out)
    assert (payload["runs_preserved_pinned"] == 1) is expect_kept
    assert (_read_status(run_dir) == "active") is expect_kept
    assert (payload["runs_abandoned"] == 0) is expect_kept


def test_cli_ignores_pin_entries_without_a_string_run_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    run_dir = _cli_stale_run(tmp_path, "r1")
    pins = {"sess": {"run_path": 123, "last_heartbeat_ts": _iso(1)}}

    code, out = _run_cli(tmp_path, monkeypatch, pins=pins)

    assert code == 0
    assert _payload(out)["runs_abandoned"] == 1
    assert _read_status(run_dir) == "abandoned"


def test_cli_dry_run_writes_nothing_and_text_report_names_the_would_be_abandons(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_dir = _cli_stale_run(tmp_path, "r-stale")
    grace = _make_run(
        tmp_path / ".trw" / "runs", "task-a", "r-grace", events_age_hours=54, run_yaml_age_hours=54, now=time.time()
    )

    code, out = _run_cli(tmp_path, monkeypatch, dry_run=True, as_json=False)

    assert code == 0
    assert "DRY-RUN" in out
    assert "- r-stale" in out
    assert "near_stale_run_ids" in out and "- r-grace" in out
    assert _read_status(run_dir) == "active"
    assert _read_status(grace) == "active"
    assert "run_auto_abandoned" not in _events_text(run_dir)


def test_cli_wet_text_report_lists_abandoned_ids(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    run_dir = _cli_stale_run(tmp_path, "r-stale")

    code, out = _run_cli(tmp_path, monkeypatch, dry_run=False, as_json=False)

    assert code == 0
    assert "SWEEP COMPLETE" in out
    assert "- r-stale" in out
    assert _read_status(run_dir) == "abandoned"
