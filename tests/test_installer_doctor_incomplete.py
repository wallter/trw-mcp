"""The installer's doctor verdict is tri-state: a doctor that never finished is INCOMPLETE, never FAILED.

Feedback #155: a 60s timeout printed "health check FAILED" over a healthy install. #161: the timeout note names
the machine load, and the doctor runs once, with no warm-up.
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
    spec = importlib.util.spec_from_file_location("install_trw_doctor_incomplete", _TEMPLATE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Ui:
    interactive = False
    quiet = False

    def __init__(self) -> None:
        self.lines: list[str] = []

    def step_warn(self, text: str) -> None:
        self.lines.append(text)

    def step_ok(self, text: str) -> None:
        self.lines.append(text)

    def info(self, text: str = "") -> None:
        self.lines.append(text)


def _run(monkeypatch: pytest.MonkeyPatch, doctor: Any) -> tuple[object, list[str], list[list[str]]]:
    installer = _installer()
    monkeypatch.setattr(installer, "find_trw_cmd", lambda *_a, **_k: ["trw-mcp"])
    calls: list[list[str]] = []

    def fake_run(cmd: list[str], *_a: Any, **_k: Any) -> subprocess.CompletedProcess[str]:
        calls.append(list(cmd))
        if "doctor" in cmd:
            return doctor(len([c for c in calls if "doctor" in c]))
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(installer.subprocess, "run", fake_run)
    ui = _Ui()
    return installer.run_install_doctor(ui, "python3", Path("/tmp/project")), ui.lines, calls


def _timeout(_n: int) -> subprocess.CompletedProcess[str]:
    raise subprocess.TimeoutExpired(["trw-mcp", "doctor"], 60)


def _ok_doc(_n: int) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess([], 0, stdout=json.dumps({"checks": []}), stderr="")


def test_timeout_twice_is_incomplete_with_load_note(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("os.getloadavg", lambda: (9.5, 4.0, 2.0))
    verdict, lines, calls = _run(monkeypatch, _timeout)
    text = "\n".join(lines)
    assert verdict is None
    assert sum("doctor" in c for c in calls) == 1
    assert "FAIL" not in text
    assert "did not complete" in text
    assert "load average 9.5/4.0/2.0" in text


def test_unparseable_output_is_incomplete(monkeypatch: pytest.MonkeyPatch) -> None:
    verdict, lines, _ = _run(monkeypatch, lambda _n: subprocess.CompletedProcess([], 1, stdout="not json", stderr=""))
    assert verdict is None
    assert not any("FAILED" in line for line in lines)


def test_real_fail_row_is_still_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = json.dumps({"checks": [{"name": "x", "status": "FAIL", "message": "broken"}]})
    verdict, _lines, _ = _run(monkeypatch, lambda _n: subprocess.CompletedProcess([], 1, stdout=payload, stderr=""))
    assert verdict is False


@pytest.mark.parametrize(
    ("health_ok", "expect", "forbid"),
    [(None, "did not complete", "FAILED"), (False, "FAILED", "did not complete"), (True, "is ready", "FAILED")],
)
@pytest.mark.parametrize("quiet", [True, False])
def test_banner_wording_follows_the_tri_state(
    capsys: pytest.CaptureFixture[str], health_ok: bool | None, expect: str, forbid: str, quiet: bool
) -> None:
    installer = _installer()
    ui = installer.UI(interactive=False, quiet=quiet)
    installer.show_success_banner(ui, "connected", [], health_ok=health_ok)
    out = capsys.readouterr().out
    if quiet and health_ok is True:
        expect = "installed."
    assert expect in out
    assert forbid not in out
