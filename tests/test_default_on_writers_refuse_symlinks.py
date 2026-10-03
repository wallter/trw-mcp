"""UF-PRD-04 slice 2a (AIKIDO census, group A + B): default-on writers refuse a planted symlink.

Each writer is driven through the function its production caller calls, with a symlink planted at the path
it writes (group A: the leaf it appends to or rewrites; group B: the predictable ``.tmp`` name it used to
open). The file behind the link must keep its bytes and the link must stay a link. Fail-open writers must
also not raise. ``append_checkout_file(lock=True)`` is pinned against a reader holding a shared flock.
"""

from __future__ import annotations

import json
import sys
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from trw_memory.exceptions import UnsafeWriteError

from tests._planted_symlink import assert_untouched, plant_symlink

# --- the adapter: append_checkout_file(lock=True) ------------------------------------------------------


def test_locked_append_refuses_a_symlinked_leaf(tmp_path: Path) -> None:
    from trw_mcp._checkout_write import append_checkout_file

    root = tmp_path / "project"
    link = root / "logs" / "x.jsonl"
    victim = plant_symlink(link, tmp_path / "outside")

    with pytest.raises(UnsafeWriteError) as refused:
        append_checkout_file(root, link, "row\n", lock=True)

    assert refused.value.reason == "symlink_leaf"
    assert_untouched(link, victim)


@pytest.mark.skipif(sys.platform == "win32", reason="flock and dir_fd are POSIX; Windows appends unlocked")
def test_locked_append_waits_for_a_shared_lock_reader_then_appends(tmp_path: Path) -> None:
    """The writer's exclusive flock is taken on the fd it writes through, so a reader's shared flock holds it."""
    import fcntl

    from trw_mcp._checkout_write import append_checkout_file

    root = tmp_path / "project"
    target = root / "logs" / "x.jsonl"
    target.parent.mkdir(parents=True)
    target.write_text("first\n", encoding="utf-8")
    done = threading.Event()

    def _writer() -> None:
        append_checkout_file(root, target, "second\n", lock=True)
        done.set()

    with target.open("r", encoding="utf-8") as reader:
        fcntl.flock(reader.fileno(), fcntl.LOCK_SH)
        thread = threading.Thread(target=_writer)
        thread.start()
        thread.join(timeout=0.5)
        assert not done.is_set(), "the append must wait while a reader holds a shared lock"
        assert target.read_text(encoding="utf-8") == "first\n"
        fcntl.flock(reader.fileno(), fcntl.LOCK_UN)
    thread.join(timeout=10)

    assert done.is_set()
    assert target.read_text(encoding="utf-8") == "first\nsecond\n"


# --- group A: appends and whole-file writes that followed a leaf link ----------------------------------


def test_recall_receipts_refuse_a_symlinked_tracking_log(tmp_path: Path) -> None:
    from trw_mcp.state.recall_tracking import append_receipts

    trw_dir = tmp_path / ".trw"
    link = trw_dir / "logs" / "recall_tracking.jsonl"
    victim = plant_symlink(link, tmp_path / "outside")

    with pytest.raises(UnsafeWriteError):
        append_receipts(["L-1"], "query", surface="recall", trw_dir=trw_dir)

    assert_untouched(link, victim)


def test_recall_receipts_still_append_one_row_per_id(tmp_path: Path) -> None:
    from trw_mcp.state.recall_tracking import append_receipts

    trw_dir = tmp_path / ".trw"
    assert append_receipts(["L-1", "L-2"], "query", surface="recall", trw_dir=trw_dir) == 2

    rows = [json.loads(line) for line in (trw_dir / "logs" / "recall_tracking.jsonl").read_text().splitlines()]
    assert [r["learning_id"] for r in rows] == ["L-1", "L-2"]


def test_surface_event_log_refuses_a_symlinked_log(tmp_path: Path) -> None:
    from trw_mcp.state.surface_tracking import log_surface_event

    trw_dir = tmp_path / ".trw"
    link = trw_dir / "logs" / "surface_tracking.jsonl"
    victim = plant_symlink(link, tmp_path / "outside")

    log_surface_event(trw_dir, learning_id="L-1", surface_type="recall")  # fail-open: no raise

    assert_untouched(link, victim)


def test_build_check_session_observation_refuses_a_symlinked_log(tmp_path: Path) -> None:
    from trw_mcp.models.build import BuildStatus
    from trw_mcp.tools.build._registration import _record_session_observation

    trw_dir = tmp_path / ".trw"
    link = trw_dir / "logs" / "session_outcomes.jsonl"
    victim = plant_symlink(link, tmp_path / "outside")

    _record_session_observation(trw_dir, BuildStatus(tests_passed=True, test_count=3, scope="unit"))

    assert_untouched(link, victim)


def test_deferred_deliver_log_refuses_a_symlinked_log(tmp_path: Path) -> None:
    from trw_mcp.tools._deferred_persistence import log_deferred_result

    trw_dir = tmp_path / ".trw"
    link = trw_dir / "logs" / "deferred-deliver.jsonl"
    victim = plant_symlink(link, tmp_path / "outside")

    log_deferred_result(trw_dir, {"step": "ok"}, [])

    assert_untouched(link, victim)


def test_nudge_shown_event_refuses_a_symlinked_session_events_log(tmp_path: Path) -> None:
    from trw_mcp.state._ceremony_progress_state import record_nudge_shown

    trw_dir = tmp_path / ".trw"
    link = trw_dir / "context" / "session-events.jsonl"
    victim = plant_symlink(link, tmp_path / "outside")

    record_nudge_shown(trw_dir, "L-1", "implement", 3)

    assert_untouched(link, victim)


def test_clear_score_step_refuses_a_symlinked_score_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from trw_mcp.scoring import clear as clear_mod
    from trw_mcp.tools._ceremony_deliver_steps import step_clear_score

    score = SimpleNamespace(
        cost=1.0,
        latency=1.0,
        efficacy=1.0,
        assurance=1.0,
        reliability=1.0,
        model_dump=lambda mode="json": {"cost": 1.0},
    )
    monkeypatch.setattr(clear_mod, "load_and_score_run", lambda _sid, _run: score)
    run = tmp_path / "run-1"
    link = run / "meta" / "session_clear_score.json"
    victim = plant_symlink(link, tmp_path / "outside")
    results: dict[str, Any] = {}

    step_clear_score(run, results)  # type: ignore[arg-type]

    assert_untouched(link, victim)


def test_session_changelog_refuses_a_symlinked_report(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from trw_mcp.state import _session_changelog as mod

    run = tmp_path / "run-1"
    stub = mod.SessionChangelogResult(markdown="# changes\n", run_path=str(run))
    monkeypatch.setattr(mod, "build_session_changelog", lambda *_a, **_k: stub)
    link = run / "reports" / mod.SESSION_CHANGELOG_FILENAME
    victim = plant_symlink(link, tmp_path / "outside")

    with pytest.raises(UnsafeWriteError):
        mod.write_session_changelog(run, tmp_path / ".trw")

    assert_untouched(link, victim)


def test_first_session_marker_refuses_a_dangling_symlink(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``exists()`` is False for a DANGLING link, so the old write_text created the file the link names."""
    from trw_mcp.telemetry.client import TelemetryClient
    from trw_mcp.tools import _ceremony_telemetry as mod

    trw_dir = tmp_path / ".trw"
    monkeypatch.setattr(mod, "_resolve_trw_dir_compat", lambda: trw_dir)
    monkeypatch.setattr(
        TelemetryClient,
        "from_config",
        classmethod(lambda cls: SimpleNamespace(record_event=lambda _e: None, flush=lambda: None)),
    )
    link = trw_dir / "state" / "first_session_emitted"
    link.parent.mkdir(parents=True)
    named = tmp_path / "outside" / "created-through-the-link"
    named.parent.mkdir()
    link.symlink_to(named)

    assert mod.step_first_session_marker() is False  # fail-open

    assert not named.exists(), "the marker write created the file a dangling link names"
    assert link.is_symlink()


def test_crash_log_refusal_keeps_the_crash_on_stderr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from trw_mcp.server.__main__ import _crash_log

    monkeypatch.chdir(tmp_path)
    link = tmp_path / ".trw" / "logs" / "crash.log"
    victim = plant_symlink(link, tmp_path / "outside")

    try:
        raise RuntimeError("original crash")
    except RuntimeError as exc:
        _crash_log(exc)  # must not raise: a refusal never masks the crash

    assert_untouched(link, victim)
    err = capsys.readouterr().err
    assert "original crash" in err
    assert "crash.log" in err  # the refusal itself is reported on stderr


# --- group B: a predictable .tmp name the writer opened by name ----------------------------------------


def test_pin_store_save_refuses_a_symlink_at_its_tmp_name(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from trw_mcp.state import _pin_store

    pins = tmp_path / ".trw" / "runtime" / "pins.json"
    monkeypatch.setattr(_pin_store, "pin_store_path", lambda: pins)
    monkeypatch.setattr(_pin_store, "pin_store_lock_path", lambda: pins.with_suffix(".lock"))
    _pin_store.invalidate_pin_store_cache()
    link = pins.parent / "pins.json.tmp"
    victim = plant_symlink(link, tmp_path / "outside")

    _pin_store.upsert_pin_entry("pin-1", tmp_path / "run-1")

    assert_untouched(link, victim)
    assert json.loads(pins.read_text(encoding="utf-8"))["pin-1"]["run_path"] == str((tmp_path / "run-1").resolve())
    assert pins.stat().st_mode & 0o777 == 0o600


def test_graph_backfill_state_refuses_a_symlink_at_its_tmp_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from trw_mcp.state import _graph_backfill as mod
    from trw_mcp.state import _store_selection

    page = {"next": None, "complete": True, "processed": 0, "edges_built": 0, "skipped": 0, "failed": 0}
    fake = SimpleNamespace(graph_backfill=lambda *_a: page)
    monkeypatch.setattr(_store_selection, "selected_store", lambda _trw: (fake, "ns"))
    trw_dir = tmp_path / ".trw"
    link = trw_dir / "memory" / "graph-backfill.json.tmp"
    victim = plant_symlink(link, tmp_path / "outside")

    mod.backfill_graph(trw_dir)

    assert_untouched(link, victim)
    state = json.loads((trw_dir / "memory" / "graph-backfill.json").read_text(encoding="utf-8"))
    assert state["namespaces"]["ns"]["complete"] is True
