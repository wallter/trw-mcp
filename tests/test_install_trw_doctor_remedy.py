"""The installer's post-install doctor failure names the real problem and its real remedy (E2E-INC-131 b).

After an upgrade left the OLD memory daemon serving, ``trw-mcp doctor`` FAILed ``memory_backend`` with a
``daemon_version_mismatch`` message, and the installer answered "Fix: run 'git init && trw-mcp init-project .'" for a
repository that was already initialised. The fix text now follows what the failing check said.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

_TEMPLATE = Path(__file__).resolve().parent.parent / "scripts" / "install-trw.template.py"


def _installer() -> ModuleType:
    spec = importlib.util.spec_from_file_location("install_trw_doctor_remedy", _TEMPLATE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Ui:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def step_warn(self, text: str) -> None:
        self.lines.append(text)

    def step_ok(self, text: str) -> None:
        self.lines.append(text)

    def info(self, text: str = "") -> None:
        self.lines.append(text)


def _run_doctor(monkeypatch: pytest.MonkeyPatch, checks: list[dict[str, Any]]) -> tuple[bool, list[str]]:
    installer = _installer()
    monkeypatch.setattr(installer, "find_trw_cmd", lambda *_a, **_k: ["trw-mcp"])
    monkeypatch.setattr(
        installer.subprocess,
        "run",
        lambda *_a, **_k: subprocess.CompletedProcess([], 1, stdout=json.dumps({"checks": checks}), stderr=""),
    )
    ui = _Ui()
    return installer.run_install_doctor(ui, "python3", Path("/tmp/project")), ui.lines


_MISMATCH = (
    "daemon_version_mismatch: the trw-memory daemon (pid 22973) serves 4.0.1, but this client is 5.1.2; "
    "their tool signatures differ."
)


def test_a_daemon_version_mismatch_is_not_answered_with_git_init(monkeypatch: pytest.MonkeyPatch) -> None:
    ok, lines = _run_doctor(monkeypatch, [{"name": "memory_backend", "status": "FAIL", "message": _MISMATCH}])

    text = "\n".join(lines)
    assert ok is False
    assert "git init" not in text
    assert "memory_backend" in text and "daemon_version_mismatch" in text  # what failed, in the doctor's own words
    assert "serves 4.0.1" in text and "stop" in text.lower()  # and the daemon remedy


def test_any_other_failure_shows_its_message_and_points_at_doctor(monkeypatch: pytest.MonkeyPatch) -> None:
    ok, lines = _run_doctor(
        monkeypatch, [{"name": "framework_integrity", "status": "FAIL", "message": "FRAMEWORK.md digest mismatch"}]
    )

    text = "\n".join(lines)
    assert ok is False
    assert "FRAMEWORK.md digest mismatch" in text and "trw-mcp doctor" in text
    assert "git init" not in text  # the checkout is initialised; that advice was only ever right for one failure


def test_a_missing_framework_still_gets_the_init_remedy(monkeypatch: pytest.MonkeyPatch) -> None:
    ok, lines = _run_doctor(
        monkeypatch,
        [
            {
                "name": "framework_integrity",
                "status": "FAIL",
                "message": "runtime frameworks not installed yet; run 'trw-mcp init-project .'",
            }
        ],
    )

    assert ok is False and any("init-project" in line for line in lines)


def test_a_clean_doctor_is_a_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    ok, lines = _run_doctor(monkeypatch, [{"name": "memory_backend", "status": "PASS", "message": "ok"}])

    assert ok is True and any("health check passed" in line for line in lines)


def _banner(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], **kwargs: Any) -> str:
    installer = _installer()

    class _Plain(_Ui):
        quiet = False
        interactive = False

    installer.show_success_banner(_Plain(), "offline", [], **kwargs)
    return capsys.readouterr().out


def test_the_banner_does_not_say_ready_over_a_doctor_failure(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    out = _banner(monkeypatch, capsys, health_ok=False)
    assert "ready" not in out.lower()
    assert "health check failed" in out.lower() and "trw-mcp doctor" in out


def test_the_banner_still_says_ready_when_healthy(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    assert "ready" in _banner(monkeypatch, capsys, health_ok=True).lower()
    assert "ready" in _banner(monkeypatch, capsys).lower()  # callers that never ran the doctor are unchanged


def test_the_quiet_line_also_withholds_ready_on_a_failure(capsys: pytest.CaptureFixture[str]) -> None:
    installer = _installer()

    class _Quiet(_Ui):
        quiet = True
        interactive = False

    installer.show_success_banner(_Quiet(), "offline", [], health_ok=False)
    out = capsys.readouterr().out
    assert "health check failed" in out.lower() and "installed." not in out
