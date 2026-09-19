"""Single-flight, recovery, coalescing and the budget (PRD-INFRA-186).

The defect these cover was measured live on 2026-09-16 (sub_lynArGCloVDuWffm):
24 commits in three hours produced 27 concurrent full-store sweep workers,
elapsed up to 4h50m, each registering as a store writer until ``trw_learn``
reported ``writer_pressure`` at ``writer_count=30`` against a threshold of 8.

Coalescing cannot be demonstrated with consecutive commits: the installed hook's
``TRW_POST_COMMIT_SYNC=1`` path WAITS for each worker, so sequential invocations
never overlap by construction. Every test here overlaps deliberately, by holding
the lock or by re-entering from inside a stubbed maintenance step.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

import pytest

from trw_mcp.tools import _post_commit as pc


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(pc.HEAD_ENV_VAR, raising=False)
    monkeypatch.delenv(pc.BUDGET_ENV_VAR, raising=False)


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A project root whose sweep directory is its own ``.trw``."""
    root = tmp_path / "repo"
    (root / ".trw" / "runtime").mkdir(parents=True)
    monkeypatch.setattr(pc, "_sweep_trw_dir", lambda _root: root / ".trw")
    monkeypatch.setattr(pc, "_head_sha", lambda _root: "cafebabe")
    return root


def _stub_passes(monkeypatch: pytest.MonkeyPatch, *, on_pass: Any = None) -> list[int]:
    """Replace the real maintenance work with a counter."""
    passes: list[int] = []

    def _fake_pass(_repo: Path, _env: Any, receipt: pc.PostCommitReceipt) -> None:
        passes.append(1)
        receipt.verify_entries_processed = len(passes)
        if on_pass is not None:
            on_pass(len(passes))

    monkeypatch.setattr(pc, "_run_pass", _fake_pass)
    return passes


def _lock(repo: Path) -> Path:
    return repo / ".trw" / pc.LOCK_REL_PATH


def _pending(repo: Path) -> Path:
    return repo / ".trw" / pc.PENDING_REL_PATH


def _write_lock(repo: Path, record: object) -> Path:
    path = _lock(repo)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(record if isinstance(record, str) else json.dumps(record), encoding="utf-8")
    return path


# --- FR01: one sweep per repository at a time -------------------------------


def test_a_second_run_defers_while_a_live_owner_holds_the_lock(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    passes = _stub_passes(monkeypatch)
    _write_lock(repo, {"pid": os.getpid(), "started_at": "now", "head_sha": "deadbeef"})
    receipt_path = repo / pc.RECEIPT_REL_PATH
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    receipt_path.write_text('{"marker": "owner"}', encoding="utf-8")

    receipt = pc.run_post_commit(repo)

    assert receipt.lock_state == "deferred"
    assert passes == [], "a deferred arrival must not sweep"
    assert receipt.pending_marked is True
    assert receipt_path.read_text(encoding="utf-8") == '{"marker": "owner"}', (
        "a deferred run must not overwrite the running owner's receipt"
    )
    assert _lock(repo).exists(), "the live owner's lock survives a deferred arrival"


def test_the_lock_is_released_even_when_a_pass_raises(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(_repo: Path, _env: Any, _receipt: pc.PostCommitReceipt) -> None:
        raise RuntimeError("sweep exploded")

    monkeypatch.setattr(pc, "_run_pass", _boom)
    with pytest.raises(RuntimeError):
        pc.run_post_commit(repo)
    assert not _lock(repo).exists(), "a raising pass must not leave the lock behind"


def test_lock_and_marker_live_under_trw_runtime(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """NFR03 — two files beside the receipt, no new config key or store table."""
    assert pc.LOCK_REL_PATH == Path("runtime") / "post-commit.lock"
    assert pc.PENDING_REL_PATH == Path("runtime") / "post-commit-pending.json"

    _stub_passes(monkeypatch)
    before = {p for p in (repo / ".trw").rglob("*") if p.is_file()}
    pc.run_post_commit(repo)
    after = {p for p in (repo / ".trw").rglob("*") if p.is_file()}
    assert after - before == {repo / pc.RECEIPT_REL_PATH}, "only the receipt persists after a clean run"


def test_the_lock_follows_the_resolved_store_not_the_repository(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The sweep opens ``resolve_trw_dir()``; locking the repo would miss it."""
    root = tmp_path / "repo"
    store = tmp_path / "elsewhere" / ".trw"
    store.mkdir(parents=True)
    (root / ".trw").mkdir(parents=True)
    monkeypatch.setattr(pc, "_sweep_trw_dir", lambda _root: store)
    monkeypatch.setattr(pc, "_head_sha", lambda _root: "sha")
    seen: list[Path] = []
    monkeypatch.setattr(pc, "_run_pass", lambda *_a: seen.append(store))

    pc.run_post_commit(root)

    assert seen == [store]
    assert not (root / ".trw" / pc.LOCK_REL_PATH).exists()


def test_an_unwritable_runtime_dir_still_sweeps_and_says_so(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail-open: no lock is not a reason to skip maintenance, but the receipt is honest."""
    passes = _stub_passes(monkeypatch)
    monkeypatch.setattr(pc.os, "open", lambda *_a, **_k: (_ for _ in ()).throw(PermissionError("ro")))

    receipt = pc.run_post_commit(repo)

    assert receipt.lock_state == "unlocked"
    assert passes == [1]


# --- FR02: crashed / wedged owner recovery ---------------------------------


def _dead_pid() -> int:
    """A PID that has certainly exited: fork a child and reap it."""
    pid = os.fork()
    if pid == 0:  # pragma: no cover - child never returns
        os._exit(0)
    os.waitpid(pid, 0)
    return pid


def test_a_crashed_owner_lock_is_reclaimed(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    passes = _stub_passes(monkeypatch)
    _write_lock(repo, {"pid": _dead_pid(), "started_at": "old", "head_sha": "gone"})

    receipt = pc.run_post_commit(repo)

    assert receipt.lock_state == "reclaimed"
    assert passes == [1], "a dead owner must not deadlock the sweep forever"
    assert not _lock(repo).exists()


def test_an_unreadable_lock_is_reclaimed_only_once_it_is_old(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The create/write window must not read as abandonment."""
    passes = _stub_passes(monkeypatch)
    path = _write_lock(repo, "")

    fresh = pc.run_post_commit(repo)
    assert fresh.lock_state == "deferred", "an empty lock written a moment ago may be mid-publication"
    assert passes == []

    stale = time.time() - pc._UNREADABLE_LOCK_GRACE_SECONDS - 5
    os.utime(path, (stale, stale))
    reclaimed = pc.run_post_commit(repo)
    assert reclaimed.lock_state == "reclaimed"
    assert passes == [1]


def test_a_live_owner_is_never_stolen_on_age(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A long sweep is a long sweep, not an abandoned one."""
    passes = _stub_passes(monkeypatch)
    path = _write_lock(repo, {"pid": os.getpid(), "started_at": "ages ago", "head_sha": "x"})
    ancient = time.time() - 86_400
    os.utime(path, (ancient, ancient))

    receipt = pc.run_post_commit(repo)

    assert receipt.lock_state == "deferred"
    assert passes == []


def test_release_does_not_strip_another_owners_lock(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """If our lock was reclaimed mid-run, the reclaimer keeps it."""

    def _steal(_repo: Path, _env: Any, _receipt: pc.PostCommitReceipt) -> None:
        _write_lock(repo, {"pid": os.getpid() + 1, "started_at": "now", "head_sha": "other"})

    monkeypatch.setattr(pc, "_run_pass", _steal)
    pc.run_post_commit(repo)

    assert _lock(repo).exists(), "a lock that no longer names us is not ours to delete"


@pytest.mark.parametrize("pid", [0, -1])
def test_a_nonsense_pid_is_not_alive(pid: int) -> None:
    assert pc._owner_is_alive(pid) is False


def test_a_permission_error_means_alive(monkeypatch: pytest.MonkeyPatch) -> None:
    """A PID owned by another user is running, not absent."""

    def _kill(_pid: int, _sig: int) -> None:
        raise PermissionError("not yours")

    monkeypatch.setattr(pc.os, "kill", _kill)
    assert pc._owner_is_alive(4242) is True


# --- FR03: exactly one follow-up -------------------------------------------


def test_a_commit_during_a_sweep_triggers_exactly_one_follow_up(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Three arrivals against one owner produce two passes, not four."""
    arrivals: list[pc.PostCommitReceipt] = []

    def _arrive(pass_number: int) -> None:
        # Two commits land during the first pass; a third during the follow-up.
        if pass_number == 1:
            arrivals.append(pc.run_post_commit(repo))
            arrivals.append(pc.run_post_commit(repo))
        elif pass_number == 2:
            arrivals.append(pc.run_post_commit(repo))

    passes = _stub_passes(monkeypatch, on_pass=_arrive)

    receipt = pc.run_post_commit(repo)

    assert len(passes) == 2, "one follow-up, whatever the arrival rate"
    assert receipt.follow_up_ran is True
    assert [r.lock_state for r in arrivals] == ["deferred", "deferred", "deferred"]
    assert _pending(repo).is_file(), "the arrival during the follow-up waits for the next commit"
    assert json.loads(_pending(repo).read_text(encoding="utf-8"))["head_sha"] == "cafebabe"


def test_a_marker_left_behind_is_consumed_by_the_next_sweep(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """NFR02 — coalescing is not discarding."""
    passes = _stub_passes(monkeypatch)
    _pending(repo).parent.mkdir(parents=True, exist_ok=True)
    _pending(repo).write_text(json.dumps({"head_sha": "earlier", "marked_at": "then"}), encoding="utf-8")

    receipt = pc.run_post_commit(repo)

    assert passes == [1]
    assert receipt.follow_up_ran is False, "the marker was this run's OWN work, not an extra pass"
    assert not _pending(repo).exists()


def test_no_marker_means_no_follow_up(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    passes = _stub_passes(monkeypatch)
    receipt = pc.run_post_commit(repo)
    assert passes == [1]
    assert receipt.follow_up_ran is False


def test_an_unreadable_marker_is_treated_as_absent(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    passes = _stub_passes(monkeypatch)

    def _explode(_self: Path) -> bool:
        raise OSError("marker unreadable")

    monkeypatch.setattr(Path, "is_file", _explode)
    receipt = pc.run_post_commit(repo)

    assert passes == [1], "an unreadable marker must not abort maintenance"
    assert receipt.follow_up_ran is False


# --- FR04: the wall-clock budget -------------------------------------------


@pytest.fixture
def unarmed_timer(monkeypatch: pytest.MonkeyPatch) -> None:
    """Let the budget arm ITIMER_REAL under pytest.

    ``--timeout=120`` (pytest-timeout, signal method) already has ITIMER_REAL
    armed for every test, and ``_deadline`` deliberately declines to touch a
    timer it did not set -- rescheduling someone else's relative timer would
    postpone their deadline. In the shipped path the worker is a fresh
    ``python -c`` process with no timer, so the hard stop is live; here the probe
    is stubbed to report what that process sees. ``_deadline`` disarms on exit,
    which cancels pytest-timeout's alarm for the rest of THIS test only.
    """
    monkeypatch.setattr(pc.signal, "getitimer", lambda _which: (0.0, 0.0))


@pytest.mark.perf
def test_the_sweep_stops_at_its_budget(repo: Path, monkeypatch: pytest.MonkeyPatch, unarmed_timer: None) -> None:
    monkeypatch.setenv(pc.BUDGET_ENV_VAR, "0.2")
    started: list[float] = []

    def _slow(_repo: Path, _env: Any, _receipt: pc.PostCommitReceipt) -> None:
        started.append(time.monotonic())
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            time.sleep(0.01)

    monkeypatch.setattr(pc, "_run_pass", _slow)

    receipt = pc.run_post_commit(repo)

    assert receipt.bounded_stop is True
    assert receipt.duration_ms >= 200
    assert receipt.duration_ms < 9_000, "the budget must actually cut the sweep short"
    assert not _lock(repo).exists()


def test_the_deadline_is_not_swallowed_by_a_broad_except(
    repo: Path, monkeypatch: pytest.MonkeyPatch, unarmed_timer: None
) -> None:
    """``run_maintain_verify`` catches ``Exception`` per entry.

    An ``Exception``-derived deadline would be eaten by that guard while the
    one-shot timer stayed exhausted, and the budget would silently not exist.
    """
    monkeypatch.setenv(pc.BUDGET_ENV_VAR, "0.2")

    def _guarded(_repo: Path, _env: Any, _receipt: pc.PostCommitReceipt) -> None:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                time.sleep(0.01)
            except Exception:  # trw-fail-silent-allow: this IS the swallowing guard under test -- it stands in for run_maintain_verify's per-entry handler and must not catch the deadline
                pass

    monkeypatch.setattr(pc, "_run_pass", _guarded)

    receipt = pc.run_post_commit(repo)

    assert receipt.bounded_stop is True
    assert not issubclass(pc._SweepDeadline, Exception)


def test_a_bounded_stop_skips_the_follow_up(repo: Path, monkeypatch: pytest.MonkeyPatch, unarmed_timer: None) -> None:
    """After a hard stop the worker does no further database work."""
    monkeypatch.setenv(pc.BUDGET_ENV_VAR, "0.2")
    passes: list[int] = []

    def _slow(_repo: Path, _env: Any, _receipt: pc.PostCommitReceipt) -> None:
        passes.append(1)
        pc._mark_pending(_pending(repo), "later")
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            time.sleep(0.01)

    monkeypatch.setattr(pc, "_run_pass", _slow)
    receipt = pc.run_post_commit(repo)

    assert passes == [1]
    assert receipt.follow_up_ran is False
    assert _pending(repo).is_file(), "the interrupted run leaves the marker for the next commit"


@pytest.mark.parametrize("value", ["", "0", "-5", "nonsense", "inf", "nan"])
def test_an_invalid_budget_falls_back_to_the_default(value: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(pc.BUDGET_ENV_VAR, value)
    assert pc._budget_seconds() == pc._DEFAULT_BUDGET_SECONDS


def test_a_valid_budget_is_honoured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(pc.BUDGET_ENV_VAR, "12.5")
    assert pc._budget_seconds() == 12.5


def test_no_sigalrm_degrades_to_boundary_bounding_without_claiming_a_hard_stop(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delattr(pc.signal, "setitimer", raising=False)
    passes = _stub_passes(monkeypatch)

    receipt = pc.run_post_commit(repo)

    assert passes == [1]
    assert receipt.bounded_stop is False, "a bound that did not fire must not be reported as one"


def test_an_already_armed_timer_is_left_alone(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Restoring a saved relative timer would postpone its original deadline."""
    monkeypatch.setattr(pc.signal, "getitimer", lambda _which: (30.0, 0.0))
    set_calls: list[object] = []
    monkeypatch.setattr(pc.signal, "setitimer", lambda *a: set_calls.append(a))
    _stub_passes(monkeypatch)

    pc.run_post_commit(repo)

    assert set_calls == [], "someone else's timer is not ours to reschedule"


# --- FR05: the receipt reports what the worker did --------------------------


def test_the_receipt_round_trips_the_single_flight_fields(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_passes(monkeypatch)
    pc.run_post_commit(repo)

    stored = pc.read_receipt(repo)
    assert stored is not None
    for key in ("lock_state", "pending_marked", "follow_up_ran", "bounded_stop", "duration_ms"):
        assert key in stored, f"the receipt must report {key}"
    assert stored["lock_state"] == "acquired"
    assert stored["head_sha"] == "cafebabe"


# --- FR06: the hook's triggering HEAD ---------------------------------------


def test_the_passed_head_wins_over_a_later_git_rev_parse(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A late-starting worker attributes its run to the commit that summoned it."""
    monkeypatch.setenv(pc.HEAD_ENV_VAR, "  1111111  ")
    monkeypatch.setattr(
        "trw_mcp.tools._sidecar_substrate.resolve_git_sha",
        lambda _root: "2222222",
    )
    assert pc._head_sha(tmp_path) == "1111111"


def test_an_absent_head_env_falls_back_to_git(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "trw_mcp.tools._sidecar_substrate.resolve_git_sha",
        lambda _root: "2222222",
    )
    assert pc._head_sha(tmp_path) == "2222222"


def test_the_bundled_hook_exports_the_triggering_head() -> None:
    from trw_mcp.bootstrap._git_hooks import BUNDLED_HOOK

    script = BUNDLED_HOOK.read_text(encoding="utf-8")
    assert "TRW_POST_COMMIT_HEAD" in script
    assert 'git -C "$_repo" rev-parse HEAD' in script
    assert "single-flight" in script.lower()
