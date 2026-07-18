"""Behavior tests for the background dispatch job registry.

Most cases write the job + result JSON files directly into a tmp ``trw_dir`` and
assert ``get_status`` reconciles them correctly — no real subprocess. One
end-to-end test actually runs ``start_background``, whose detached ``_run_job``
child dispatches a request to a STUB ``codex`` binary planted on ``PATH``, and
polls ``get_status`` until terminal.
"""

from __future__ import annotations

import os
import signal
import stat
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from trw_mcp.dispatch._jobs import (
    DispatchJob,
    cancel_job,
    get_result,
    get_status,
    start_background,
)
from trw_mcp.dispatch._private_io import write_private_atomic
from trw_mcp.dispatch._types import DispatchRequest, DispatchResult


def _write_job(
    trw_dir: Path,
    job_id: str,
    *,
    status: str = "running",
    pid: int | None = 999_999_999,
    created_at: str | None = None,
    timeout_s: int = 600,
    write_req: bool = False,
) -> DispatchJob:
    jobs_dir = trw_dir / "runtime" / "dispatch-jobs"
    jobs_dir.mkdir(parents=True, exist_ok=True)
    job = DispatchJob(
        job_id=job_id,
        client="codex",
        status=status,  # type: ignore[arg-type]
        created_at=created_at or datetime.now(timezone.utc).isoformat(),
        timeout_s=timeout_s,
        pid=pid,
        argv_redacted=["codex", "exec", "<prompt:5 chars>"],
        result_path=str(jobs_dir / f"{job_id}.result.json"),
        job_path=str(jobs_dir / f"{job_id}.json"),
    )
    (jobs_dir / f"{job_id}.json").write_text(job.model_dump_json(indent=2), encoding="utf-8")
    if write_req:
        (jobs_dir / f"{job_id}.req.json").write_text("{}", encoding="utf-8")
    return job


def _write_result(
    trw_dir: Path,
    job_id: str,
    *,
    exit_code: int | None,
    timed_out: bool,
    text: str,
) -> None:
    jobs_dir = trw_dir / "runtime" / "dispatch-jobs"
    result = DispatchResult(
        client="codex",
        argv_redacted=["codex", "exec", "<prompt:5 chars>"],
        read_only_enforced=True,
        exit_code=exit_code,
        timed_out=timed_out,
        duration_s=0.1,
        text=text,
        raw_stdout=text,
        raw_stderr="",
        structured=None,
    )
    (jobs_dir / f"{job_id}.result.json").write_text(result.model_dump_json(indent=2), encoding="utf-8")


# --- get_status transitions (no subprocess) ---


def test_running_to_succeeded_when_ok_result_appears(tmp_path: Path) -> None:
    _write_job(tmp_path, "job-ok")
    _write_result(tmp_path, "job-ok", exit_code=0, timed_out=False, text="all good")
    job = get_status("job-ok", trw_dir=tmp_path)
    assert job.status == "succeeded"


def test_running_to_failed_when_result_not_ok(tmp_path: Path) -> None:
    # exit_code != 0 -> result.ok is False -> failed.
    _write_job(tmp_path, "job-bad")
    _write_result(tmp_path, "job-bad", exit_code=1, timed_out=False, text="")
    job = get_status("job-bad", trw_dir=tmp_path)
    assert job.status == "failed"


def test_running_to_timed_out_when_result_timed_out(tmp_path: Path) -> None:
    _write_job(tmp_path, "job-to")
    _write_result(tmp_path, "job-to", exit_code=None, timed_out=True, text="")
    job = get_status("job-to", trw_dir=tmp_path)
    assert job.status == "timed_out"


def test_running_to_failed_when_pid_dead_and_no_result(tmp_path: Path) -> None:
    # A pid that is not alive (we use an almost-certainly-dead high pid) with no
    # result file means the child crashed.
    _write_job(tmp_path, "job-crash", pid=2_000_000_001)
    job = get_status("job-crash", trw_dir=tmp_path)
    assert job.status == "failed"


def test_stays_running_when_pid_alive_and_no_result(tmp_path: Path) -> None:
    # Our own pid is definitely alive; no result -> still running.
    _write_job(tmp_path, "job-live", pid=os.getpid())
    job = get_status("job-live", trw_dir=tmp_path)
    assert job.status == "running"


def test_terminal_status_returned_unchanged(tmp_path: Path) -> None:
    # An already-cancelled job is not re-derived from the filesystem.
    _write_job(tmp_path, "job-done", status="cancelled", pid=os.getpid())
    job = get_status("job-done", trw_dir=tmp_path)
    assert job.status == "cancelled"


def test_status_persists_transition(tmp_path: Path) -> None:
    _write_job(tmp_path, "job-persist")
    _write_result(tmp_path, "job-persist", exit_code=0, timed_out=False, text="ok")
    get_status("job-persist", trw_dir=tmp_path)
    # Re-read the record straight off disk: the succeeded status must be durable.
    record = (tmp_path / "runtime" / "dispatch-jobs" / "job-persist.json").read_text()
    assert '"succeeded"' in record


def test_get_status_unknown_job_raises_keyerror(tmp_path: Path) -> None:
    with pytest.raises(KeyError):
        get_status("nope", trw_dir=tmp_path)


@pytest.mark.parametrize("job_id", ["../escape", "../../escape", "/tmp/escape", "bad/id", "bad\\id", ""])
def test_job_ids_reject_path_syntax(tmp_path: Path, job_id: str) -> None:
    with pytest.raises(ValueError, match="invalid dispatch job_id"):
        get_status(job_id, trw_dir=tmp_path)


def test_tampered_record_paths_are_rejected_before_persist(tmp_path: Path) -> None:
    job = _write_job(tmp_path, "job-tampered")
    outside = tmp_path / "outside.json"
    path = Path(job.job_path)
    path.write_text(job.model_copy(update={"job_path": str(outside)}).model_dump_json(), encoding="utf-8")
    _write_result(tmp_path, job.job_id, exit_code=0, timed_out=False, text="ok")

    with pytest.raises(ValueError, match="record path mismatch"):
        get_status(job.job_id, trw_dir=tmp_path)

    assert not outside.exists()


# --- get_result ---


def test_get_result_none_when_absent(tmp_path: Path) -> None:
    _write_job(tmp_path, "job-noresult")
    assert get_result("job-noresult", trw_dir=tmp_path) is None


def test_get_result_parses_present_file(tmp_path: Path) -> None:
    _write_job(tmp_path, "job-res")
    _write_result(tmp_path, "job-res", exit_code=0, timed_out=False, text="hi there")
    result = get_result("job-res", trw_dir=tmp_path)
    assert result is not None
    assert result.text == "hi there"
    assert result.ok is True


# --- cancel + list ---


def test_cancel_sets_cancelled(tmp_path: Path) -> None:
    # pid=None so cancel does not attempt a real kill.
    _write_job(tmp_path, "job-cancel", pid=None)
    job = cancel_job("job-cancel", trw_dir=tmp_path)
    assert job.status == "cancelled"
    record = (tmp_path / "runtime" / "dispatch-jobs" / "job-cancel.json").read_text()
    assert '"cancelled"' in record


_POSIX_SESSIONS = hasattr(os, "killpg") and hasattr(os, "setsid")


@pytest.mark.skipif(not _POSIX_SESSIONS, reason="requires POSIX process-group primitives")
def test_cancel_kills_foreign_tree_via_child_pid_sidecar(tmp_path: Path) -> None:
    """F-11: cancel reaches the foreign agent recorded in the child.pid sidecar.

    We spawn a real ``sleep 30`` as its OWN session leader (start_new_session) to
    stand in for the foreign agent, write its pid to the job's child.pid sidecar,
    then cancel and assert the sleeper is reaped and the sidecar is cleaned up.
    """
    import subprocess as _sp

    jobs_dir = tmp_path / "runtime" / "dispatch-jobs"
    _write_job(tmp_path, "job-foreign", pid=None)  # no intermediate to kill

    foreign = _sp.Popen(["sleep", "30"], start_new_session=True)
    try:
        child_pid_path = jobs_dir / "job-foreign.child.pid"
        child_pid_path.write_text(str(foreign.pid), encoding="utf-8")

        job = cancel_job("job-foreign", trw_dir=tmp_path)
        assert job.status == "cancelled"

        # Poll briefly for the foreign process to terminate from the SIGKILL.
        # foreign.poll() reaps the child (we are its parent) so it does not linger
        # as a zombie — a bare os.kill(pid, 0) would still succeed on a zombie.
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if foreign.poll() is not None:
                break
            time.sleep(0.05)
        assert foreign.poll() is not None, "foreign agent (sleep 30) survived cancel"
        # SIGKILL shows up as a negative returncode (-signal.SIGKILL).
        assert foreign.returncode == -signal.SIGKILL

        # The sidecar is removed by the terminal cleanup.
        assert not child_pid_path.exists()
    finally:
        # Defensive: if the assert failed, still tear down the sleeper.
        try:
            os.killpg(os.getpgid(foreign.pid), 9)
        except (ProcessLookupError, PermissionError, OSError):
            pass
        foreign.wait(timeout=5)


# --- F-04: stuck-running TTL ---


def test_stuck_running_past_ttl_becomes_failed(tmp_path: Path) -> None:
    # A backdated created_at with a tiny timeout: now > created + timeout*1.5.
    # pid=None models the unreliable/unknown-pid degrade path (_pid_alive returns
    # True, so the pid-death branch cannot fire) — the exact scenario the TTL
    # guard exists for — so only the TTL path can declare it failed. (We do NOT
    # use os.getpid() here: the stuck-TTL branch now KILLS the pid's group, which
    # would SIGKILL the test runner.)
    old = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    _write_job(tmp_path, "job-stuck", pid=None, created_at=old, timeout_s=5)
    job = get_status("job-stuck", trw_dir=tmp_path)
    assert job.status == "failed"


def test_recent_running_within_ttl_stays_running(tmp_path: Path) -> None:
    # Fresh job, alive pid, no result -> still running (TTL has not elapsed).
    _write_job(tmp_path, "job-fresh", pid=os.getpid(), timeout_s=600)
    job = get_status("job-fresh", trw_dir=tmp_path)
    assert job.status == "running"


@pytest.mark.skipif(not _POSIX_SESSIONS, reason="requires POSIX process-group primitives")
def test_stuck_running_past_ttl_reaps_child_tree(tmp_path: Path) -> None:
    """P1: the stuck-running TTL branch must KILL the wedged child, not orphan it.

    A backdated job whose pid probe still reports "alive" (a REAL long-lived
    subprocess) is past its TTL. Marking it failed without signaling would leak
    ``_run_job`` and its foreign-agent child permanently. We stand in for the
    intermediate child with a real ``sleep 30`` (its own session leader) and for
    the foreign agent with a second one recorded in the ``child.pid`` sidecar,
    drive the TTL path, and assert BOTH are reaped and the sidecar is cleaned up.
    """
    import subprocess as _sp

    jobs_dir = tmp_path / "runtime" / "dispatch-jobs"
    jobs_dir.mkdir(parents=True, exist_ok=True)

    # Intermediate _run_job stand-in (job.pid) and foreign-agent stand-in (sidecar),
    # each its own session so killpg(getpgid(pid)) reaches its group.
    intermediate = _sp.Popen(["sleep", "30"], start_new_session=True)
    foreign = _sp.Popen(["sleep", "30"], start_new_session=True)
    try:
        old = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        _write_job(tmp_path, "job-stuck-kill", pid=intermediate.pid, created_at=old, timeout_s=5)
        child_pid_path = jobs_dir / "job-stuck-kill.child.pid"
        child_pid_path.write_text(str(foreign.pid), encoding="utf-8")

        job = get_status("job-stuck-kill", trw_dir=tmp_path)
        assert job.status == "failed"

        # Both stand-ins must terminate from SIGKILL (poll() reaps them so they do
        # not linger as zombies that a bare os.kill(pid, 0) would still see).
        for proc, label in ((intermediate, "intermediate _run_job"), (foreign, "foreign agent")):
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline:
                if proc.poll() is not None:
                    break
                time.sleep(0.05)
            assert proc.poll() is not None, f"{label} survived the stuck-TTL reap"
            assert proc.returncode == -signal.SIGKILL

        # Terminal cleanup removes the sidecar.
        assert not child_pid_path.exists()
    finally:
        for proc in (intermediate, foreign):
            try:
                os.killpg(os.getpgid(proc.pid), 9)
            except (ProcessLookupError, PermissionError, OSError):
                pass
            proc.wait(timeout=5)


# --- F-05: full uuid job_id ---


def test_start_background_uses_full_uuid_job_id(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Stub Popen so no real child spawns; we only assert the job_id width.
    import trw_mcp.dispatch._jobs as jobs_mod

    class _FakeProc:
        pid = 4242

        def wait(self) -> int:
            return 0

    monkeypatch.setattr(jobs_mod.subprocess, "Popen", lambda *a, **k: _FakeProc())
    req = DispatchRequest(client="codex", prompt="hi", timeout_s=30, read_only=True)
    job = start_background(req, trw_dir=tmp_path / ".trw")
    assert len(job.job_id) == 32  # full uuid4().hex, not the old [:12]
    jobs_dir = tmp_path / ".trw" / "runtime" / "dispatch-jobs"
    assert stat.S_IMODE(jobs_dir.stat().st_mode) == 0o700
    assert stat.S_IMODE(Path(job.job_path).stat().st_mode) == 0o600
    assert stat.S_IMODE((jobs_dir / f"{job.job_id}.req.json").stat().st_mode) == 0o600


def test_start_background_spawn_failure_removes_request(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import trw_mcp.dispatch._jobs as jobs_mod

    def _fail_spawn(*_args: object, **_kwargs: object) -> object:
        raise OSError("spawn failed")

    monkeypatch.setattr(jobs_mod.subprocess, "Popen", _fail_spawn)
    req = DispatchRequest(client="codex", prompt="secret prompt", timeout_s=30, read_only=True)

    with pytest.raises(OSError, match="spawn failed"):
        start_background(req, trw_dir=tmp_path / ".trw")

    jobs_dir = tmp_path / ".trw" / "runtime" / "dispatch-jobs"
    assert not list(jobs_dir.glob("*.req.json"))
    assert not list(jobs_dir.glob("*.json"))


def test_private_atomic_replacement_is_owner_only(tmp_path: Path) -> None:
    path = tmp_path / "result.json"
    path.write_text("old", encoding="utf-8")
    path.chmod(0o664)

    write_private_atomic(path, "new")

    assert path.read_text(encoding="utf-8") == "new"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


# --- F-06: job cleanup (req.json removed on terminal + sweep) ---


def test_req_file_removed_on_terminal_transition(tmp_path: Path) -> None:
    _write_job(tmp_path, "job-clean", write_req=True)
    _write_result(tmp_path, "job-clean", exit_code=0, timed_out=False, text="ok")
    req_path = tmp_path / "runtime" / "dispatch-jobs" / "job-clean.req.json"
    assert req_path.exists()
    get_status("job-clean", trw_dir=tmp_path)
    assert not req_path.exists()  # prompt no longer lingers on disk


def test_sweep_removes_old_terminal_keeps_fresh_and_running(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from trw_mcp.dispatch._jobs import _sweep_old_jobs

    jobs_dir = tmp_path / "runtime" / "dispatch-jobs"
    old = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    # Old terminal job (all 3 files) -> should be removed.
    _write_job(tmp_path, "old-done", status="succeeded", created_at=old, write_req=True)
    _write_result(tmp_path, "old-done", exit_code=0, timed_out=False, text="x")
    # Fresh terminal job -> kept (inside retention).
    _write_job(tmp_path, "new-done", status="succeeded", write_req=True)
    # Old but genuinely live and within its configured TTL -> kept.
    _write_job(
        tmp_path,
        "old-running",
        status="running",
        pid=os.getpid(),
        created_at=old,
        timeout_s=3_000_000,
        write_req=True,
    )

    _sweep_old_jobs(jobs_dir)

    assert not (jobs_dir / "old-done.json").exists()
    assert not (jobs_dir / "old-done.result.json").exists()
    assert not (jobs_dir / "old-done.req.json").exists()
    assert (jobs_dir / "new-done.json").exists()
    assert (jobs_dir / "old-running.json").exists()


def test_sweep_reconciles_old_running_job_with_result(tmp_path: Path) -> None:
    from trw_mcp.dispatch._jobs import _sweep_old_jobs

    old = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    jobs_dir = tmp_path / "runtime" / "dispatch-jobs"
    _write_job(tmp_path, "unpolled-done", created_at=old, write_req=True)
    _write_result(tmp_path, "unpolled-done", exit_code=0, timed_out=False, text="secret output")

    _sweep_old_jobs(jobs_dir)

    assert not list(jobs_dir.glob("unpolled-done*"))


def test_sweep_reconciles_old_dead_job_without_result(tmp_path: Path) -> None:
    from trw_mcp.dispatch._jobs import _sweep_old_jobs

    old = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    jobs_dir = tmp_path / "runtime" / "dispatch-jobs"
    _write_job(tmp_path, "unpolled-crash", pid=2_000_000_001, created_at=old, write_req=True)

    _sweep_old_jobs(jobs_dir)

    assert not list(jobs_dir.glob("unpolled-crash*"))


# --- _run_job module-entry runner (in-process) ---


def test_run_job_main_reads_dispatches_writes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """main() reads a request, runs dispatch(), and writes the result file."""
    from trw_mcp.dispatch import _run_job

    req = DispatchRequest(client="codex", prompt="hi", timeout_s=5, read_only=True)
    req_path = tmp_path / "r.req.json"
    result_path = tmp_path / "r.result.json"
    child_pid_path = tmp_path / "r.child.pid"
    req_path.write_text(req.model_dump_json(), encoding="utf-8")

    # Stub the runner so no child process spawns; just prove the IO contract.
    def _fake_dispatch(r: DispatchRequest, *, pid_callback: object = None) -> DispatchResult:
        assert r.prompt == "hi"
        return DispatchResult(
            client=r.client,
            argv_redacted=["codex", "exec", "<prompt:2 chars>"],
            read_only_enforced=True,
            exit_code=0,
            timed_out=False,
            duration_s=0.0,
            text="done",
            raw_stdout="done",
            raw_stderr="",
            structured=None,
        )

    monkeypatch.setattr(_run_job, "dispatch", _fake_dispatch)
    rc = _run_job.main([str(req_path), str(result_path), str(child_pid_path)])
    assert rc == 0
    assert not req_path.exists()
    written = DispatchResult.model_validate_json(result_path.read_text())
    assert written.text == "done"


def test_run_job_main_writes_child_pid_via_callback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """F-11: _run_job passes a pid_callback that records the foreign pid to disk."""
    from trw_mcp.dispatch import _run_job

    req = DispatchRequest(client="codex", prompt="hi", timeout_s=5, read_only=True)
    req_path = tmp_path / "r.req.json"
    result_path = tmp_path / "r.result.json"
    child_pid_path = tmp_path / "r.child.pid"
    req_path.write_text(req.model_dump_json(), encoding="utf-8")

    def _fake_dispatch(r: DispatchRequest, *, pid_callback: object = None) -> DispatchResult:
        # Drive the callback the way the real runner does (right after Popen).
        assert callable(pid_callback)
        pid_callback(4242)
        return DispatchResult(
            client=r.client,
            argv_redacted=[],
            read_only_enforced=True,
            exit_code=0,
            timed_out=False,
            duration_s=0.0,
            text="ok",
            raw_stdout="ok",
            raw_stderr="",
            structured=None,
        )

    monkeypatch.setattr(_run_job, "dispatch", _fake_dispatch)
    rc = _run_job.main([str(req_path), str(result_path), str(child_pid_path)])
    assert rc == 0
    assert child_pid_path.read_text(encoding="utf-8") == "4242"


def test_run_job_main_bad_args_returns_2() -> None:
    from trw_mcp.dispatch import _run_job

    # Two args is now too few (the signature requires three).
    assert _run_job.main(["only-one-arg", "two"]) == 2


def test_run_job_main_malformed_req_writes_failure_result(tmp_path: Path) -> None:
    """F-10: a malformed req.json still produces an ok=False result file (no crash)."""
    from trw_mcp.dispatch import _run_job

    req_path = tmp_path / "bad.req.json"
    result_path = tmp_path / "bad.result.json"
    child_pid_path = tmp_path / "bad.child.pid"
    req_path.write_text("{ this is not valid json", encoding="utf-8")

    rc = _run_job.main([str(req_path), str(result_path), str(child_pid_path)])
    assert rc == 1
    assert result_path.exists()
    written = DispatchResult.model_validate_json(result_path.read_text())
    assert written.ok is False
    assert written.exit_code == -1
    assert "_run_job failed" in written.raw_stderr
    assert not req_path.exists()


def test_run_job_main_dispatch_raises_writes_failure_result(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """F-10: an exception from dispatch() is captured into a failure result file."""
    from trw_mcp.dispatch import _run_job

    req = DispatchRequest(client="codex", prompt="hi", timeout_s=5, read_only=True)
    req_path = tmp_path / "r.req.json"
    result_path = tmp_path / "r.result.json"
    child_pid_path = tmp_path / "r.child.pid"
    req_path.write_text(req.model_dump_json(), encoding="utf-8")

    def _boom(_r: DispatchRequest, *, pid_callback: object = None) -> DispatchResult:
        raise RuntimeError("kaboom")

    monkeypatch.setattr(_run_job, "dispatch", _boom)
    rc = _run_job.main([str(req_path), str(result_path), str(child_pid_path)])
    assert rc == 1
    written = DispatchResult.model_validate_json(result_path.read_text())
    assert written.ok is False
    assert written.client == "codex"  # recovered from the (valid) req
    assert "kaboom" in written.raw_stderr
    assert not req_path.exists()


# --- one real-subprocess end-to-end test (stub binary on PATH) ---


@pytest.mark.slow
def test_start_background_runs_real_child_against_stub(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """start_background spawns _run_job which dispatches to a stub ``codex``.

    The detached child inherits os.environ (incl. the monkeypatched PATH), and
    ``dispatch()`` resolves the ``codex`` binary from PATH — so a stub script that
    prints a known answer drives the whole job to ``succeeded``.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "codex"
    # codex argv is ["codex", "exec", ...flags..., "<prompt>"] — the stub ignores
    # everything and prints a stable answer (codex normalize-output reads stdout).
    stub.write_text("#!/usr/bin/env bash\necho 'STUB ANSWER FROM CODEX'\n", encoding="utf-8")
    stub.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")

    trw_dir = tmp_path / ".trw"
    req = DispatchRequest(client="codex", prompt="hello", timeout_s=30, read_only=True)

    job = start_background(req, trw_dir=trw_dir)
    assert job.status == "running"
    assert job.pid is not None
    # The redacted argv must NOT contain the raw prompt.
    assert "hello" not in " ".join(job.argv_redacted)

    # Poll until terminal (the child is a fast bash stub).
    deadline = time.monotonic() + 20.0
    final = job
    while time.monotonic() < deadline:
        final = get_status(job.job_id, trw_dir=trw_dir)
        if final.status != "running":
            break
        time.sleep(0.1)

    assert final.status == "succeeded", f"unexpected status {final.status!r}"
    result = get_result(job.job_id, trw_dir=trw_dir)
    assert result is not None
    assert "STUB ANSWER FROM CODEX" in result.text
