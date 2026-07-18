"""Behavior tests for the dispatch subprocess runner.

Hermetic: these never invoke a real codex/claude/agy/opencode. They point the
runner at tiny stub scripts written to tmp_path by overriding the argv the
runner executes (monkeypatching the runner's ``build_command`` binding).
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from trw_mcp.dispatch import dispatch
from trw_mcp.dispatch._types import DispatchRequest


def _write_stub(tmp_path: Path, name: str, body: str) -> Path:
    """Write an executable shell stub and return its path."""
    script = tmp_path / name
    script.write_text("#!/usr/bin/env bash\n" + body)
    script.chmod(script.stat().st_mode | stat.S_IEXEC | stat.S_IRUSR)
    return script


def _patch_argv(monkeypatch: pytest.MonkeyPatch, argv: list[str]) -> None:
    """Make the runner execute *argv* regardless of the request's client."""

    def _fixed(_req: DispatchRequest) -> list[str]:
        return argv

    monkeypatch.setattr("trw_mcp.dispatch._runner.build_command", _fixed)


def test_successful_run_extracts_text_and_marks_ok(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    payload = json.dumps({"result": "Found one P1 issue."})
    stub = _write_stub(tmp_path, "fake-claude", f"echo '{payload}'\n")
    _patch_argv(monkeypatch, [str(stub)])

    result = dispatch(DispatchRequest(client="claude", prompt="review please"))

    assert result.ok is True
    assert result.exit_code == 0
    assert result.timed_out is False
    assert result.text == "Found one P1 issue."
    assert result.structured == {"result": "Found one P1 issue."}
    assert result.duration_s >= 0.0


def test_argv_redacted_never_contains_raw_prompt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    secret_prompt = "TOP-SECRET-AUDIT-INSTRUCTIONS-12345"
    stub = _write_stub(tmp_path, "fake-agy", 'echo "done"\n')
    # prompt appears as a real argv token, like a real command would have it
    _patch_argv(monkeypatch, [str(stub), "-p", secret_prompt])

    result = dispatch(DispatchRequest(client="agy", prompt=secret_prompt))

    assert secret_prompt not in result.argv_redacted
    assert any("<prompt:" in tok for tok in result.argv_redacted)
    # the length is encoded in the placeholder
    assert f"<prompt:{len(secret_prompt)} chars>" in result.argv_redacted


def test_nonzero_exit_is_not_ok(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    stub = _write_stub(tmp_path, "fake-fail", "echo 'partial'\nexit 3\n")
    _patch_argv(monkeypatch, [str(stub)])

    result = dispatch(DispatchRequest(client="codex", prompt="x"))

    assert result.exit_code == 3
    assert result.ok is False


def test_zero_exit_empty_output_is_not_ok(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # mirrors agy's non-TTY stdout drop: clean exit but no answer
    stub = _write_stub(tmp_path, "fake-empty", "exit 0\n")
    _patch_argv(monkeypatch, [str(stub)])

    result = dispatch(DispatchRequest(client="agy", prompt="x"))

    assert result.exit_code == 0
    assert result.text == ""
    assert result.ok is False


def test_timeout_sets_timed_out_and_none_exit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    stub = _write_stub(tmp_path, "fake-slow", "sleep 5\necho late\n")
    _patch_argv(monkeypatch, [str(stub)])

    result = dispatch(DispatchRequest(client="codex", prompt="x", timeout_s=1))

    assert result.timed_out is True
    assert result.exit_code is None
    assert result.ok is False


def test_stderr_captured(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    stub = _write_stub(tmp_path, "fake-warn", "echo 'answer'\necho 'a warning' >&2\n")
    _patch_argv(monkeypatch, [str(stub)])

    result = dispatch(DispatchRequest(client="claude", prompt="x"))

    assert "a warning" in result.raw_stderr


def test_cwd_is_passed_to_child(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workdir = tmp_path / "work"
    workdir.mkdir()
    stub = _write_stub(tmp_path, "fake-pwd", "pwd\n")
    _patch_argv(monkeypatch, [str(stub)])

    result = dispatch(DispatchRequest(client="agy", prompt="x", cwd=workdir))

    assert os.path.realpath(result.raw_stdout.strip()) == os.path.realpath(str(workdir))


def test_sanitized_env_excludes_host_secret(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "LEAK-ME")
    # child echoes whatever it sees in that var; allowlist should blank it
    stub = _write_stub(tmp_path, "fake-probe", 'echo "secret=[${AWS_SECRET_ACCESS_KEY:-absent}]"\n')
    _patch_argv(monkeypatch, [str(stub)])

    result = dispatch(DispatchRequest(client="codex", prompt="x"))

    assert "LEAK-ME" not in result.raw_stdout
    assert "secret=[absent]" in result.raw_stdout


def test_pty_wrapping_invoked_when_use_pty(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # We can't easily assert the script wrapper ran, but we can assert the run
    # still completes and the answer survives PTY ANSI stripping in normalize.
    stub = _write_stub(tmp_path, "fake-agy-pty", "echo 'pty answer'\n")
    _patch_argv(monkeypatch, [str(stub)])

    result = dispatch(DispatchRequest(client="agy", prompt="x", use_pty=True))

    # script(1) may not exist in all CI images; tolerate that by checking that
    # IF it ran the answer is present, otherwise it timed out / failed cleanly.
    assert isinstance(result.text, str)
    if result.ok:
        assert "pty answer" in result.text


def test_pty_redacts_prompt_inside_script_wrapper(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Regression: PTY wraps the argv inside a `script -qec '<cmd>'` string, so a
    # naive redactor (token == prompt) misses the embedded prompt. The displayed
    # argv must redact the prompt even through the wrapper.
    secret_prompt = "SECRET-PTY-PROMPT-99"
    stub = _write_stub(tmp_path, "fake-agy-redact", "echo 'ok'\n")
    _patch_argv(monkeypatch, [str(stub), "-p", secret_prompt])

    result = dispatch(DispatchRequest(client="agy", prompt=secret_prompt, use_pty=True))

    joined = " ".join(result.argv_redacted)
    assert secret_prompt not in joined
    assert result.argv_redacted[0] == "script"  # displayed form is the PTY wrapper
    assert f"<prompt:{len(secret_prompt)} chars>" in joined


# --- read_only_enforced (P1-1) ------------------------------------------------


def test_read_only_enforced_reflects_request_true(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    stub = _write_stub(tmp_path, "fake-ro", "echo 'x'\n")
    _patch_argv(monkeypatch, [str(stub)])
    result = dispatch(DispatchRequest(client="codex", prompt="x", read_only=True))
    assert result.read_only_enforced is True


def test_read_only_enforced_reflects_request_false(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    stub = _write_stub(tmp_path, "fake-rw", "echo 'x'\n")
    _patch_argv(monkeypatch, [str(stub)])
    result = dispatch(DispatchRequest(client="codex", prompt="x", read_only=False))
    assert result.read_only_enforced is False


# --- missing binary never raises (P1-4) ---------------------------------------


def test_missing_binary_returns_clean_failure_no_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_argv(monkeypatch, ["/nonexistent/xyz-does-not-exist"])
    # Must NOT raise — returns a clean failure result.
    result = dispatch(DispatchRequest(client="agy", prompt="x"))
    assert result.ok is False
    assert result.exit_code == -127
    assert result.timed_out is False
    assert "Failed to launch" in result.raw_stderr
    assert result.read_only_enforced is True


# --- timeout kills the whole process tree (P1-3) ------------------------------


def test_timeout_kills_process_tree_no_orphan(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sentinel = tmp_path / "sentinel.txt"
    # The stub backgrounds a child that, after a delay, writes the sentinel — then
    # the parent sleeps long. On timeout the whole group must be killed before the
    # child can write the sentinel.
    body = f"( sleep 3; echo alive > {sentinel} ) &\nsleep 30\n"
    stub = _write_stub(tmp_path, "fake-tree", body)
    _patch_argv(monkeypatch, [str(stub)])

    result = dispatch(DispatchRequest(client="codex", prompt="x", timeout_s=1))

    assert result.timed_out is True
    assert result.exit_code is None
    # Wait past the child's 3s delay; if the tree was killed the sentinel never
    # appears. (On a POSIX box with killpg this is the real assertion; the
    # timed_out/None-exit checks above hold on any platform.)
    import time as _time

    _time.sleep(4)
    if hasattr(os, "killpg"):
        assert not sentinel.exists(), "orphaned grandchild survived the timeout kill"


# --- pid_callback (F-11) ------------------------------------------------------


def test_pid_callback_invoked_with_positive_pid(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # The callback must fire with the spawned child's real pid (> 0) right after
    # Popen succeeds — this is how the background path records the foreign pid.
    stub = _write_stub(tmp_path, "fake-pidcb", "echo 'ok'\n")
    _patch_argv(monkeypatch, [str(stub)])

    seen: list[int] = []
    result = dispatch(
        DispatchRequest(client="codex", prompt="x"),
        pid_callback=seen.append,
    )

    assert result.ok is True
    assert len(seen) == 1
    assert isinstance(seen[0], int)
    assert seen[0] > 0


def test_pid_callback_not_invoked_when_launch_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A pre-spawn launch failure must NOT call the callback (no child exists).
    _patch_argv(monkeypatch, ["/nonexistent/xyz-does-not-exist"])
    seen: list[int] = []
    result = dispatch(
        DispatchRequest(client="agy", prompt="x"),
        pid_callback=seen.append,
    )
    assert result.exit_code == -127
    assert seen == []


# --- output cap (P2-1) --------------------------------------------------------


def test_output_is_capped_with_truncation_marker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Emit ~11MB of 'A' so we exceed the 10MB cap. `yes` + head is fast and avoids
    # building the giant string in Python.
    body = "yes A | head -c 11000000\n"
    stub = _write_stub(tmp_path, "fake-flood", body)
    _patch_argv(monkeypatch, [str(stub)])

    result = dispatch(DispatchRequest(client="agy", prompt="x"))

    # Capped to the ceiling plus the appended marker line.
    assert len(result.raw_stdout) <= 10_000_000 + 64
    assert "…[truncated" in result.raw_stdout


# --- cwd validation in the runner (P2-3) --------------------------------------


def test_missing_cwd_returns_failure_without_spawning(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Point at a stub that writes a side-effect file if it ever runs. A pre-spawn
    # cwd rejection must return -1 WITHOUT executing the stub.
    side_effect = tmp_path / "ran.txt"
    stub = _write_stub(tmp_path, "fake-side-effect", f"echo ran > {side_effect}\n")
    _patch_argv(monkeypatch, [str(stub)])

    missing = tmp_path / "nope"
    result = dispatch(DispatchRequest(client="codex", prompt="x", cwd=missing))

    assert result.ok is False
    assert result.exit_code == -1
    assert result.timed_out is False
    assert "cwd is not a directory" in result.raw_stderr
    # Proof no child spawned: the stub's side-effect file was never created.
    assert not side_effect.exists()
