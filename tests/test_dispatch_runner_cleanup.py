"""Direct-child ownership on every dispatch exit; no provider calls."""

from __future__ import annotations

import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest


@pytest.mark.parametrize("failure_site", ["identity", "callback", "communicate", "decode", "interrupt"])
def test_dispatch_abnormal_exit_kills_reaps_and_closes_child(monkeypatch, failure_site):
    from trw_mcp.dispatch import _runner
    from trw_mcp.dispatch._types import DispatchRequest

    children = []
    original_popen = subprocess.Popen
    error = KeyboardInterrupt("cancelled") if failure_site == "interrupt" else RuntimeError("injected")
    if failure_site == "decode":
        error = UnicodeDecodeError("utf8", b"\xff", 0, 1, "invalid byte")

    def fail(*args, **kwargs):
        raise error

    def spawn(*args, **kwargs):
        child = original_popen(*args, **kwargs)
        children.append(child)
        if failure_site in ("communicate", "decode"):
            monkeypatch.setattr(child, "communicate", fail)
        return child

    monkeypatch.setattr(_runner, "subprocess", SimpleNamespace(**{**vars(subprocess), "Popen": spawn}))
    monkeypatch.setattr(
        _runner, "build_command", lambda req, *, confined=False: [sys.executable, "-c", "import time; time.sleep(60)"]
    )
    if failure_site == "identity":
        monkeypatch.setattr(_runner, "capture_identity", fail)
    callback = fail if failure_site in ("callback", "interrupt") else None
    try:
        with pytest.raises(type(error)) as raised:
            _runner.dispatch(DispatchRequest(client="claude", prompt="x"), pid_callback=callback)
        assert raised.value is error
        child = children[0]
        # Do not poll here: poll() would reap a zombie and conceal missing cleanup.
        assert child.returncode is not None
        assert child.stdout.closed and child.stderr.closed
    finally:
        for child in children:
            child.kill()
            child.wait(timeout=5)
            child.stdout.close()
            child.stderr.close()


def test_dispatch_normal_exit_reaps_and_closes_child(monkeypatch):
    from trw_mcp.dispatch import _runner
    from trw_mcp.dispatch._types import DispatchRequest

    children = []
    original_popen = subprocess.Popen

    def spawn(*args, **kwargs):
        child = original_popen(*args, **kwargs)
        children.append(child)
        return child

    monkeypatch.setattr(_runner, "subprocess", SimpleNamespace(**{**vars(subprocess), "Popen": spawn}))
    monkeypatch.setattr(_runner, "build_command", lambda req, *, confined=False: [sys.executable, "-c", "print('ok')"])
    result = _runner.dispatch(DispatchRequest(client="claude", prompt="x"))
    assert result.exit_code == 0
    assert children[0].returncode == 0
    assert children[0].stdout.closed and children[0].stderr.closed


@pytest.mark.parametrize("failure", ["drain", "wait", "close", "kill"])
def test_dispatch_reports_cleanup_failures_and_still_releases_other_resources(monkeypatch, failure):
    from structlog.testing import capture_logs

    from trw_mcp.dispatch import _runner
    from trw_mcp.dispatch._types import DispatchRequest

    child = MagicMock(pid=12345, returncode=0)
    child.communicate.return_value = ("ok", "")
    child.wait.return_value = 0
    if failure in ("drain", "kill"):
        child.communicate.side_effect = subprocess.TimeoutExpired("test", 1)
    if failure == "wait":
        child.wait.side_effect = subprocess.TimeoutExpired("test", 5)
    if failure == "close":
        child.stdout.close.side_effect = OSError("injected close failure")
    if failure == "kill":
        child.kill.side_effect = PermissionError("injected kill failure")
        child.wait.side_effect = subprocess.TimeoutExpired("test", 5)
    monkeypatch.setattr(_runner, "subprocess", SimpleNamespace(**{**vars(subprocess), "Popen": lambda *a, **k: child}))
    monkeypatch.setattr(_runner, "build_command", lambda req, *, confined=False: [sys.executable])
    monkeypatch.setattr(_runner, "capture_identity", lambda pid: None)
    monkeypatch.setattr(_runner, "signal_group", lambda identity, sig: False)
    with capture_logs() as logs:
        result = _runner.dispatch(DispatchRequest(client="claude", prompt="x", timeout_s=1))
    assert not result.ok
    assert "cleanup incomplete" in result.raw_stderr
    assert any(entry["event"] == "dispatch_cleanup_incomplete" for entry in logs)
    child.wait.assert_called_once_with(timeout=5)
    child.stdout.close.assert_called_once()
    child.stderr.close.assert_called_once()
    if failure in ("drain", "kill"):
        assert result.timed_out
        assert child.communicate.call_count == 2
        assert child.communicate.call_args.kwargs == {"timeout": 5}
        child.kill.assert_called()
    else:
        assert result.exit_code == -1
        child.kill.assert_not_called()
    if failure == "kill":
        assert any(entry["event"] == "dispatch_child_kill_failed" for entry in logs)
