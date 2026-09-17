"""Dispatch stale-PID safety without delivery of real process signals."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from trw_mcp.dispatch import _jobs
from trw_mcp.dispatch import _process_identity as identity


@pytest.fixture
def sender(monkeypatch):
    fake = SimpleNamespace(**vars(os))
    fake.killpg = MagicMock()
    monkeypatch.setattr(identity, "os", fake)
    return fake.killpg


# os.getpid()/os.getpgrp() are evaluated at COLLECTION time, so an unlabelled
# parametrize gives every xdist worker a different test id -- `[736659]` on one
# worker, `[736650]` on another -- and xdist aborts the whole run with
# "Different tests were collected between gw3 and gw0" before a single test
# executes. Sequential runs are unaffected, which is why it landed. Explicit
# stable ids keep the values dynamic and the identifiers fixed.
@pytest.mark.parametrize(
    "pid",
    [
        0,
        1,
        -1,
        True,
        None,
        "4242",
        pytest.param(os.getpid(), id="own_pid"),
        pytest.param(os.getpgrp(), id="own_process_group"),
    ],
)
def test_invalid_or_own_pid_is_not_captured(pid):
    assert identity.capture_identity(pid) is None


@pytest.mark.parametrize("record", [None, {}, {"pid": 0}, {"pid": -1}, {"pid": "4242"}])
def test_unverified_identity_cannot_signal(record, sender):
    assert not identity.signal_group(record, signal.SIGKILL)
    sender.assert_not_called()


@pytest.mark.parametrize("field", ["boot_id", "starttime"])
def test_reused_identity_is_refused(field, sender, monkeypatch):
    original = {"pid": 4242, "boot_id": "boot", "starttime": "100"}
    current = {**original, field: "different"}
    monkeypatch.setattr(identity, "capture_identity", lambda pid: current)
    assert not identity.signal_group(original, signal.SIGKILL)
    sender.assert_not_called()


def test_verified_identity_targets_pid_not_foreign_group(sender, monkeypatch):
    record = {"pid": 4242, "boot_id": "boot", "starttime": "100"}
    monkeypatch.setattr(identity, "capture_identity", lambda pid: record)
    assert identity.signal_group(record, signal.SIGKILL)
    sender.assert_called_once_with(4242, signal.SIGKILL)


@pytest.mark.parametrize("content", ["4242", "0", "{broken", "null"])
def test_legacy_or_corrupt_sidecar_cannot_signal(tmp_path, sender, content):
    (tmp_path / "job.child.pid").write_text(content)
    _jobs._kill_child_tree(tmp_path, "job")
    sender.assert_not_called()


def test_matching_sidecar_passes_identity(tmp_path, sender, monkeypatch):
    record = {"pid": 4242, "boot_id": "boot", "starttime": "100"}
    (tmp_path / "job.child.pid").write_text(json.dumps(record))
    monkeypatch.setattr(identity, "capture_identity", lambda pid: record)
    _jobs._kill_child_tree(tmp_path, "job")
    sender.assert_called_once_with(4242, signal.SIGKILL)


def test_real_session_identity_with_mocked_signal_and_eof_exit(sender):
    process = subprocess.Popen(
        [sys.executable, "-c", "import sys; sys.stdin.read()"],
        stdin=subprocess.PIPE,
        start_new_session=True,
    )
    try:
        record = identity.capture_identity(process.pid)
        assert record is not None
        assert identity.signal_group(record, signal.SIGTERM)
        sender.assert_called_once_with(process.pid, signal.SIGTERM)
    finally:
        process.stdin.close()
        process.wait(timeout=10)
    assert identity.capture_identity(process.pid) is None


def test_runner_refused_timeout_has_bounded_drain(monkeypatch, sender):
    from trw_mcp.dispatch import _runner
    from trw_mcp.dispatch._types import DispatchRequest

    process = MagicMock(pid=0)
    process.communicate.side_effect = subprocess.TimeoutExpired("stub", 1)
    fake_subprocess = SimpleNamespace(**vars(subprocess))
    fake_subprocess.Popen = MagicMock(return_value=process)
    monkeypatch.setattr(_runner, "subprocess", fake_subprocess)
    # Keyword-only ``confined`` mirrors the real builder (PRD-CORE-277-FR02): the
    # runner passes it on every call, so a stub without it raises a TypeError that
    # reads like a runner bug.
    # argv[0] must be a REAL executable: on a host where agy's writes can be
    # confined the runner resolves the client binary before wrapping, so that a
    # missing binary still reports -127 instead of the wrapper's own exit code
    # (PRD-CORE-277-FR02). Popen itself is mocked, so nothing is launched.
    monkeypatch.setattr(_runner, "build_command", lambda req, *, confined=False: [sys.executable])
    # ``posture`` is keyword-only on the real builder; a stub without it raises a
    # TypeError from inside dispatch() that reads like a production bug. (This
    # stub was already stale at HEAD 417b4d1b3 — both tests in this file failed
    # the same way before PRD-CORE-277 touched the runner.)
    monkeypatch.setattr(_runner, "build_subprocess_env", lambda client, *, posture="default": {})
    result = _runner.dispatch(DispatchRequest(client="agy", prompt="x", timeout_s=1))
    assert result.timed_out
    assert process.communicate.call_args_list[0].kwargs == {"timeout": 1}
    assert process.communicate.call_args_list[1].kwargs == {"timeout": 5}
    assert "cleanup incomplete" in result.raw_stderr
    sender.assert_not_called()
    process.stdout.close.assert_called_once()
    process.stderr.close.assert_called_once()


def test_legacy_job_record_cannot_signal(tmp_path, sender):
    job = _jobs.DispatchJob(
        job_id="legacy",
        client="agy",
        status="running",
        created_at="2026-09-15T00:00:00Z",
        pid=4242,
        result_path=str(tmp_path / "legacy.result.json"),
        job_path=str(tmp_path / "legacy.json"),
    )
    _jobs._kill_job_tree(job, tmp_path)
    sender.assert_not_called()


@pytest.mark.parametrize("field", [2, 3])
def test_capture_rejects_foreign_group_or_session(monkeypatch, field):
    monkeypatch.setattr(identity.sys, "platform", "linux")
    fields = ["S", "1", "4242", "4242"] + ["0"] * 16
    fields[field] = "4231"
    stat = "4242 (name with ) parentheses) " + " ".join(fields)
    monkeypatch.setattr(identity, "Path", lambda path: SimpleNamespace(read_text=lambda: stat))
    assert identity.capture_identity(4242) is None


@pytest.mark.parametrize("stat", ["", "4242 (broken", "4242 (x) S 1 4242 4242"])
def test_capture_malformed_stat_fails_closed(monkeypatch, stat):
    monkeypatch.setattr(identity.sys, "platform", "linux")
    monkeypatch.setattr(identity, "Path", lambda path: SimpleNamespace(read_text=lambda: stat))
    assert identity.capture_identity(4242) is None


def test_capture_empty_boot_id_fails_closed(monkeypatch):
    monkeypatch.setattr(identity.sys, "platform", "linux")
    fields = ["S", "1", "4242", "4242"] + ["0"] * 16
    stat = "4242 (x) " + " ".join(fields)
    monkeypatch.setattr(
        identity,
        "Path",
        lambda path: SimpleNamespace(read_text=lambda: stat if path.endswith("/stat") else " \n"),
    )
    assert identity.capture_identity(4242) is None


def test_run_job_writes_verified_identity_sidecar(tmp_path, monkeypatch):
    from trw_mcp.dispatch import _run_job
    from trw_mcp.dispatch._types import DispatchRequest, DispatchResult

    record = {"pid": 4242, "boot_id": "boot", "starttime": "100"}
    monkeypatch.setattr(_run_job, "capture_identity", lambda pid: record if pid == 4242 else None)

    def dispatch(req, *, pid_callback):
        pid_callback(4242)
        return DispatchResult(
            client="agy",
            argv_redacted=[],
            read_only_enforced=True,
            exit_code=0,
            timed_out=False,
            duration_s=0,
            text="ok",
            raw_stdout="ok",
            raw_stderr="",
            structured=None,
        )

    monkeypatch.setattr(_run_job, "dispatch", dispatch)
    request, result, sidecar = [tmp_path / name for name in ("request.json", "result.json", "child.pid")]
    request.write_text(DispatchRequest(client="agy", prompt="x").model_dump_json())
    assert _run_job.main([str(request), str(result), str(sidecar)]) == 0
    assert json.loads(sidecar.read_text()) == record


def test_job_identity_must_match_recorded_pid(tmp_path, sender, monkeypatch):
    record = {"pid": 4243, "boot_id": "boot", "starttime": "100"}
    monkeypatch.setattr(identity, "capture_identity", lambda pid: record)
    job = _jobs.DispatchJob(
        job_id="mismatch",
        client="agy",
        status="running",
        created_at="2026-09-15T00:00:00Z",
        pid=4242,
        process_identity=record,
        result_path=str(tmp_path / "mismatch.result.json"),
        job_path=str(tmp_path / "mismatch.json"),
    )
    _jobs._kill_job_tree(job, tmp_path)
    sender.assert_not_called()
