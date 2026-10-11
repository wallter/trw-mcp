"""What the installer says about its children: the doctor's remedy, update-project's status, the assess line.

Three reports from one 9.5.0 install (sub_GOYE8ssHqS-5q3hE, sub_IALSw8f9Fs0cbbgE): a "Fix: run 'trw-mcp init-project
.'" printed in a git repository right after the doctor said ``update-project``; "Status: ready, N item(s) need
attention (see above)" relayed before "health check FAILED"; and the ``assess install-check`` line printed outside
the step formatting with an absolute path under the user's home.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

pytestmark = pytest.mark.integration

_TEMPLATE = Path(__file__).resolve().parent.parent / "scripts" / "install-trw.template.py"
_HOOK_REMEDY = "Remedy: trw-mcp update-project (outside a git repository: trw-mcp init-project)."


@pytest.fixture(scope="module")
def installer() -> ModuleType:
    spec = importlib.util.spec_from_file_location("install_trw_relayed_child_output", _TEMPLATE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _doctor(
    installer: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    checks: list[dict[str, Any]],
    assess: Any = None,
    *,
    interactive: bool = False,
) -> bool | None:
    """Run ``run_install_doctor`` with a real UI; *assess* answers the install-check call (a result or an exception)."""

    def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        if "install-check" in cmd:
            # The relay rests on these two: without the capture the child prints past the installer's steps, and
            # without the bound a child that never returns holds the install.
            assert kwargs.get("capture_output") is True, kwargs
            assert kwargs.get("timeout") == installer._ASSESS_CHECK_TIMEOUT_SECONDS, kwargs
            if isinstance(assess, BaseException):
                raise assess
            return assess or subprocess.CompletedProcess(cmd, 0, "", "")
        if "doctor" in cmd:
            return subprocess.CompletedProcess(cmd, 1, json.dumps({"checks": checks}), "")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(installer.subprocess, "run", fake_run)
    monkeypatch.setattr(installer, "find_trw_cmd", lambda *_a, **_k: ["trw-mcp"])
    monkeypatch.setattr(installer, "run_dispatch_step", lambda *_a, **_k: None)
    return installer.run_install_doctor(installer.UI(interactive=interactive), "python3", Path("/tmp/project"))


def _fix_lines(out: str) -> list[str]:
    return [line.partition("Fix:")[2].strip() for line in out.splitlines() if "Fix:" in line]


def test_doctor_fail_with_a_remedy_sentence_prints_that_remedy_and_no_init_project_line(
    installer: ModuleType, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    message = f"hook python: .claude/hooks/lib-trw.sh resolves no interpreter. {_HOOK_REMEDY}"

    ok = _doctor(installer, monkeypatch, [{"name": "hook_python", "status": "FAIL", "message": message}])

    out = capsys.readouterr().out
    assert ok is False
    assert _fix_lines(out) == ["trw-mcp update-project (outside a git repository: trw-mcp init-project)."]
    assert "init-project ." not in out and "git init" not in out


def test_doctor_fail_without_a_remedy_sentence_gets_the_fallback_that_names_no_command(
    installer: ModuleType, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """An older installed trw-mcp: its message mentions init-project in prose and carries no ``Remedy:`` sentence."""
    old = "runtime frameworks not installed yet; run 'trw-mcp init-project .'"

    ok = _doctor(installer, monkeypatch, [{"name": "framework_integrity", "status": "FAIL", "message": old}])

    out = capsys.readouterr().out
    assert ok is False and old in out  # the row's own words are still shown
    assert _fix_lines(out) == ["see the message above, then run 'trw-mcp doctor' again to confirm."]


def test_doctor_remedy_with_dots_in_its_paths_and_commands_is_lifted_whole_to_the_end_of_the_message(
    installer: ModuleType, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    remedy = (
        "delete .cursor/hooks/lib-distill-hint.sh if you do not use cursor-ide in this project or else run "
        "trw-mcp update-project --ide cursor-ide to add that client to the install record."
    )
    message = f"lib-distill-hint.sh is not in the install record, so update-project leaves it alone. Remedy: {remedy}"

    _doctor(installer, monkeypatch, [{"name": "hook_family", "status": "FAIL", "message": message}])

    assert _fix_lines(capsys.readouterr().out) == [remedy]


def test_doctor_two_failing_rows_each_get_their_own_fix_and_a_shared_one_is_printed_once(
    installer: ModuleType, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    checks = [
        {"name": "hook_python", "status": "FAIL", "message": f"a. {_HOOK_REMEDY}"},
        {"name": "hook_family", "status": "FAIL", "message": f"b. {_HOOK_REMEDY}"},
        {"name": "environment", "status": "FAIL", "message": "c. Remedy: trw-mcp update-project."},
    ]

    _doctor(installer, monkeypatch, checks)

    assert _fix_lines(capsys.readouterr().out) == [
        "trw-mcp update-project (outside a git repository: trw-mcp init-project).",
        "trw-mcp update-project.",
    ]


@pytest.mark.parametrize("interactive", [True, False])
def test_relayed_update_status_says_neither_ready_nor_see_above_before_a_doctor_fail(
    installer: ModuleType, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], interactive: bool
) -> None:
    child = "print('Status: ready, 3 item(s) need attention (see above)', flush=True)"
    ui = installer.UI(interactive=interactive)

    assert installer.run_with_progress(ui, "Updating", [sys.executable, "-c", child])
    ui.stop_spinner(True, "configured")
    ok = _doctor(installer, monkeypatch, [{"name": "hook_python", "status": "FAIL", "message": f"x. {_HOOK_REMEDY}"}])

    out = capsys.readouterr().out
    assert ok is False and "problems with the installed framework" in out
    assert "Update finished, 3 item(s) need attention" in out
    assert "ready" not in out.lower() and "(see above)" not in out


def test_relayed_plain_status_line_is_not_ready_either_and_other_lines_are_untouched(installer: ModuleType) -> None:
    assert installer._relayed("Status: ready") == "Update finished"
    assert installer._relayed("AGENTS.md: refreshed the TRW block") == "AGENTS.md: refreshed the TRW block"
    assert installer._relayed("Note: Status: ready, said a hook") == "Note: Status: ready, said a hook"


@pytest.mark.parametrize("home_root", ["/Users/somebody", "/home/somebody"])
def test_install_check_line_is_a_warning_step_with_the_home_directory_written_as_a_tilde(
    installer: ModuleType, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], home_root: str
) -> None:
    monkeypatch.setattr(installer.Path, "home", classmethod(lambda _cls: Path(home_root)))
    line = (
        "trw_assess: the OpenRouter key is only in this project's .env; to share it with every project run: "
        f"trw-mcp assess configure --from-env-file {home_root}/work/app/.env"
    )
    checks = [{"name": "runtime", "status": "WARN", "message": "something"}]

    _doctor(installer, monkeypatch, checks, subprocess.CompletedProcess([], 0, line + "\n", ""), interactive=True)

    rows = capsys.readouterr().out.splitlines()
    (assess_row,) = [row for row in rows if "trw_assess:" in row]
    (other_warning,) = [row for row in rows if "Framework health check" in row]
    marker = assess_row[: assess_row.index("trw_assess:")]
    assert marker.strip() and marker == other_warning[: other_warning.index("Framework health check")]
    assert assess_row.endswith("--from-env-file ~/work/app/.env")
    assert "/Users/" not in assess_row and "/home/" not in assess_row


def test_install_check_that_exits_nonzero_still_shows_its_output_and_the_install_goes_on(
    installer: ModuleType, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    failed = subprocess.CompletedProcess([], 3, "trw_assess: half a line\n", "ValueError: the store is unreadable\n")

    ok = _doctor(installer, monkeypatch, [{"name": "runtime", "status": "PASS", "message": "ok"}], failed)

    out = capsys.readouterr().out
    assert ok is True and "health check passed" in out  # the doctor still ran and the verdict is its own
    assert "trw_assess: half a line" in out and "ValueError: the store is unreadable" in out
    assert "'trw-mcp assess install-check' exited 3; the install continues" in out


def test_install_check_that_times_out_still_shows_its_output_and_the_install_goes_on(
    installer: ModuleType, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # A timed-out capture hands its partial output back as bytes, even under text=True.
    timed_out = subprocess.TimeoutExpired(["trw-mcp"], 120, output=b"trw_assess: said this first\n", stderr=None)

    ok = _doctor(installer, monkeypatch, [{"name": "runtime", "status": "PASS", "message": "ok"}], timed_out)

    out = capsys.readouterr().out
    assert ok is True and "health check passed" in out
    assert "trw_assess: said this first" in out
    assert "'trw-mcp assess install-check' did not complete in 120s; the install continues" in out


def test_install_check_that_cannot_start_is_reported_and_the_install_goes_on(
    installer: ModuleType, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    ok = _doctor(
        installer, monkeypatch, [{"name": "runtime", "status": "PASS", "message": "ok"}], FileNotFoundError("trw-mcp")
    )

    out = capsys.readouterr().out
    assert ok is True and "Could not run 'trw-mcp assess install-check'" in out


@pytest.mark.parametrize(
    ("text", "home", "shown"),
    [
        ("see /Users/user/work/app/.env", "/Users/user", "see ~/work/app/.env"),
        ("see /Users/username/work/app/.env", "/Users/user", "see /Users/username/work/app/.env"),
        ("in /Users/user.", "/Users/user/", "in /Users/user."),
        ("home is /Users/user", "/Users/user", "home is ~"),
        ("'/Users/user' and \"/Users/user/x y\"", "/Users/user", "'~' and \"~/x y\""),
        ("backup at /mnt/Users/user/x", "/Users/user", "backup at /mnt/Users/user/x"),
        ("/etc/passwd", "/", "/etc/passwd"),
    ],
)
def test_the_home_directory_is_written_as_a_tilde_only_as_a_whole_path_prefix(
    installer: ModuleType, text: str, home: str, shown: str
) -> None:
    """Review, 2026-10-11: a plain replace turned /Users/username/x into ~name/x for a home of /Users/user."""
    assert installer._home_as_tilde(text, home) == shown


def test_a_warning_row_with_a_remedy_sentence_is_shown_with_its_remedy(
    installer: ModuleType, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Review, 2026-10-11: the installer echoed a WARN row only when it said "fix:", so a row that names its file
    and its remedy the new way was reduced to a count."""
    message = (
        ".cursor/hooks/lib-distill-hint.sh still resolves the interpreter the old way and belongs to cursor-ide. "
        "Remedy: delete .cursor/hooks/lib-distill-hint.sh if you do not use cursor-ide in this project."
    )

    def fake_run(cmd: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        if "doctor" in cmd:
            checks = [{"name": "hook_python", "status": "WARN", "message": message}]
            return subprocess.CompletedProcess(cmd, 0, json.dumps({"checks": checks}), "")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(installer.subprocess, "run", fake_run)
    monkeypatch.setattr(installer, "find_trw_cmd", lambda *_a, **_k: ["trw-mcp"])
    monkeypatch.setattr(installer, "run_dispatch_step", lambda *_a, **_k: None)

    assert installer.run_install_doctor(installer.UI(interactive=False), "python3", Path("/tmp/project")) is True

    out = capsys.readouterr().out
    assert "1 warning (hook_python)" in out
    assert "Remedy: delete .cursor/hooks/lib-distill-hint.sh" in out
