"""The retired-key warning is shown once per installer run (coordinator item 14).

trw-mcp prints a retired/unrecognised config-key warning on stderr unless ``TRW_RETIRED_KEY_WARNING=off``. The
installer already shows it once, from the CAPTURED ``update-project`` output, so every UNCAPTURED trw-mcp subprocess
(whose stderr goes straight to the terminal) runs with the variable off, and the captured ones must not, or the
warning would disappear entirely.
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

_TEMPLATE = Path(__file__).resolve().parent.parent / "scripts" / "install-trw.template.py"
_VAR = "TRW_RETIRED_KEY_WARNING"


def _installer() -> ModuleType:
    spec = importlib.util.spec_from_file_location("install_trw_retired_env", _TEMPLATE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Ui:
    interactive = False
    quiet = False

    def step_warn(self, text: str) -> None:
        return None

    def step_ok(self, text: str) -> None:
        return None

    def info(self, text: str = "") -> None:
        return None


def _runs(monkeypatch: pytest.MonkeyPatch, installer: ModuleType) -> list[tuple[list[str], dict[str, str] | None]]:
    seen: list[tuple[list[str], dict[str, str] | None]] = []

    def fake_run(cmd: list[str], *_a: Any, **kw: Any) -> subprocess.CompletedProcess[str]:
        seen.append((list(cmd), kw.get("env")))
        body = json.dumps({"checks": [], "line": "", "warnings": []}) if "doctor" in cmd or "dispatch" in cmd else ""
        return subprocess.CompletedProcess(cmd, 0, stdout=body, stderr="")

    monkeypatch.setattr(installer.subprocess, "run", fake_run)
    return seen


def _env_for(seen: list[tuple[list[str], dict[str, str] | None]], verb: str) -> dict[str, str] | None:
    (env,) = [e for cmd, e in seen if verb in cmd]
    return env


def test_assess_install_check_runs_with_the_warning_off(monkeypatch: pytest.MonkeyPatch) -> None:
    installer = _installer()
    monkeypatch.setattr(installer, "find_trw_cmd", lambda *_a, **_k: ["trw-mcp"])
    monkeypatch.delenv(_VAR, raising=False)
    seen = _runs(monkeypatch, installer)

    installer.run_install_doctor(_Ui(), "python3", Path("/tmp/project"))

    env = _env_for(seen, "assess")
    assert env is not None and env[_VAR] == "off"
    assert env["PATH"] == __import__("os").environ["PATH"]  # the rest of the environment is passed through unchanged


def test_the_captured_commands_keep_the_warning(monkeypatch: pytest.MonkeyPatch) -> None:
    """The doctor and the dispatch step are captured and parsed; their env is not touched."""
    installer = _installer()
    monkeypatch.setattr(installer, "find_trw_cmd", lambda *_a, **_k: ["trw-mcp"])
    monkeypatch.delenv(_VAR, raising=False)
    seen = _runs(monkeypatch, installer)

    installer.run_install_doctor(_Ui(), "python3", Path("/tmp/project"))

    for verb in ("doctor", "dispatch"):
        env = _env_for(seen, verb)
        assert env is None or _VAR not in env


def test_update_project_is_not_given_the_variable(monkeypatch: pytest.MonkeyPatch) -> None:
    """update-project's output is captured by run_with_progress; the warning must survive in it."""
    installer = _installer()
    popen_env: list[Any] = []

    class _Proc:
        stdout = iter(())
        returncode = 0

        def wait(self, timeout: float | None = None) -> int:
            return 0

    def fake_popen(cmd: list[str], **kw: Any) -> _Proc:
        popen_env.append(kw.get("env"))
        return _Proc()

    monkeypatch.delenv(_VAR, raising=False)
    monkeypatch.setattr(installer.subprocess, "Popen", fake_popen)
    ui = installer.UI(interactive=False)
    installer.run_with_progress(ui, "Updating", [sys.executable, "update-project", "."])

    assert popen_env and all(e is None or _VAR not in e for e in popen_env)


def test_trust_codex_hooks_runs_with_the_warning_off(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from tests._install_trw_main_support import drive_main, make_project

    installer = _installer()
    project = make_project(tmp_path)
    (project / ".codex").mkdir()
    (project / ".codex" / "hooks.json").write_text("{}", encoding="utf-8")
    monkeypatch.delenv(_VAR, raising=False)
    seen = _runs(monkeypatch, installer)
    monkeypatch.setattr(installer, "find_trw_cmd", lambda *_a, **_k: ["trw-mcp"])

    drive_main(installer, monkeypatch, project, extra_argv=("--trust-codex-hooks",))

    env = _env_for(seen, "trust-codex-hooks")
    assert env is not None and env[_VAR] == "off"


def test_an_inherited_on_is_turned_off_for_an_uncaptured_run(monkeypatch: pytest.MonkeyPatch) -> None:
    installer = _installer()
    monkeypatch.setenv(_VAR, "on")
    assert installer.uncaptured_env()[_VAR] == "off"  # an uncaptured run always shows it once, not twice
