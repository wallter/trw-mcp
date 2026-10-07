"""The full installer runs the dispatch setup step after ``assess install-check`` (PRD-INFRA-210 FR08, FR09, FR10).

The step is ``trw-mcp config dispatch``: ``--offer`` only when interactive, ``--enable``/``--disable`` from
``--with-dispatch``/``--no-dispatch``/``TRW_WITH_DISPATCH``, repeatable ``--dispatch-model``/``--dispatch-effort``
pins forwarded, and the step's ``line`` shown beside ``Extras:``. A failing step warns and never fails the install.
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
_LINE = "Dispatch: on (codex default; 2 clients installed)"


def _installer() -> ModuleType:
    spec = importlib.util.spec_from_file_location("install_trw_dispatch_step", _TEMPLATE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Ui:
    def __init__(self, *, interactive: bool = False) -> None:
        self.interactive = interactive
        self.quiet = False
        self.lines: list[str] = []

    def step_warn(self, text: str) -> None:
        self.lines.append(text)

    def step_ok(self, text: str) -> None:
        self.lines.append(text)

    def info(self, text: str = "") -> None:
        self.lines.append(text)


def _drive(
    monkeypatch: pytest.MonkeyPatch,
    *,
    interactive: bool = False,
    dispatch_args: tuple[str, ...] = (),
    step: Any = None,
) -> tuple[ModuleType, _Ui, list[list[str]], bool | None]:
    """Run ``run_install_doctor`` with every subprocess stubbed; return the argv of each call."""
    installer = _installer()
    monkeypatch.setattr(installer, "find_trw_cmd", lambda *_a, **_k: ["trw-mcp"])
    calls: list[list[str]] = []

    def fake_run(cmd: list[str], *_a: Any, **_k: Any) -> subprocess.CompletedProcess[str]:
        calls.append(list(cmd))
        if cmd[1:3] == ["config", "dispatch"]:
            if step is not None:
                return step(cmd)  # type: ignore[no-any-return]
            body = (
                _LINE + "\n"
                if "--json" not in cmd
                else json.dumps({"enabled": True, "decided_by": "default", "line": _LINE, "warnings": []})
            )
            return subprocess.CompletedProcess(cmd, 0, stdout=body, stderr="")
        if "doctor" in cmd:
            return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps({"checks": []}), stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(installer.subprocess, "run", fake_run)
    ui = _Ui(interactive=interactive)
    verdict = installer.run_install_doctor(ui, "python3", Path("/tmp/project"), dispatch_args=dispatch_args)
    return installer, ui, calls, verdict


def _step(calls: list[list[str]]) -> list[str]:
    (cmd,) = [c for c in calls if c[1:3] == ["config", "dispatch"]]
    return cmd


def test_the_step_runs_after_assess_and_before_the_doctor(monkeypatch: pytest.MonkeyPatch) -> None:
    _installer_mod, _ui, calls, _ = _drive(monkeypatch)
    order = [c[1] for c in calls]
    assert order.index("assess") < order.index("config") < order.index("doctor")


def test_headless_with_the_env_flag_enables_without_a_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRW_WITH_DISPATCH", "1")
    installer = _installer()
    args = installer.build_dispatch_args(with_dispatch=False, no_dispatch=False, models=[], efforts=[])
    _m, _ui, calls, _ = _drive(monkeypatch, dispatch_args=tuple(args))

    cmd = _step(calls)
    assert cmd[:4] == ["trw-mcp", "config", "dispatch", "/tmp/project"]
    assert "--enable" in cmd and "--disable" not in cmd
    assert "--offer" not in cmd and "--json" in cmd


def test_headless_with_no_flag_passes_neither_enable_nor_disable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TRW_WITH_DISPATCH", raising=False)
    installer = _installer()
    args = installer.build_dispatch_args(with_dispatch=False, no_dispatch=False, models=[], efforts=[])
    _m, _ui, calls, _ = _drive(monkeypatch, dispatch_args=tuple(args))

    cmd = _step(calls)
    assert not {"--enable", "--disable", "--offer"} & set(cmd)


@pytest.mark.parametrize(
    ("env", "with_flag", "no_flag", "expected"),
    [
        ("1", False, False, ["--enable"]),
        ("TRUE", False, False, ["--enable"]),
        ("yes", False, False, ["--enable"]),
        ("0", False, False, ["--disable"]),
        ("False", False, False, ["--disable"]),
        ("NO", False, False, ["--disable"]),
        ("maybe", False, False, []),
        ("", False, False, []),
        ("0", True, False, ["--enable"]),  # an explicit flag beats the env var
        ("1", False, True, ["--disable"]),
        (None, True, False, ["--enable"]),
        (None, False, True, ["--disable"]),
    ],
)
def test_enable_and_disable_resolution(
    monkeypatch: pytest.MonkeyPatch, env: str | None, with_flag: bool, no_flag: bool, expected: list[str]
) -> None:
    if env is None:
        monkeypatch.delenv("TRW_WITH_DISPATCH", raising=False)
    else:
        monkeypatch.setenv("TRW_WITH_DISPATCH", env)
    args = _installer().build_dispatch_args(with_dispatch=with_flag, no_dispatch=no_flag, models=[], efforts=[])
    assert args == expected


def test_an_interactive_install_offers_and_does_not_use_json(monkeypatch: pytest.MonkeyPatch) -> None:
    _m, _ui, calls, _ = _drive(monkeypatch, interactive=True)

    cmd = _step(calls)
    assert "--offer" in cmd
    assert "--json" not in cmd  # --json is a machine mode: it would never ask


def test_model_and_effort_pins_are_forwarded_in_order(monkeypatch: pytest.MonkeyPatch) -> None:
    installer = _installer()
    args = installer.build_dispatch_args(
        with_dispatch=False,
        no_dispatch=False,
        models=["codex=gpt-x", "claude=opus-y"],
        efforts=["codex=high"],
    )
    _m, _ui, calls, _ = _drive(monkeypatch, dispatch_args=tuple(args))

    cmd = _step(calls)
    pairs = [(cmd[i], cmd[i + 1]) for i in range(len(cmd) - 1) if cmd[i].startswith("--dispatch-")]
    assert pairs == [
        ("--dispatch-model", "codex=gpt-x"),
        ("--dispatch-model", "claude=opus-y"),
        ("--dispatch-effort", "codex=high"),
    ]


def test_the_env_pins_reach_the_step_through_the_inherited_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """TRW_DISPATCH_MODELS / TRW_DISPATCH_EFFORTS are read by the step itself: the installer must not strip them."""
    monkeypatch.setenv("TRW_DISPATCH_MODELS", "codex=gpt-x")
    monkeypatch.setenv("TRW_DISPATCH_EFFORTS", "codex=high")
    seen: dict[str, str] = {}

    def step(cmd: list[str]) -> subprocess.CompletedProcess[str]:
        import os

        seen.update({k: os.environ.get(k, "") for k in ("TRW_DISPATCH_MODELS", "TRW_DISPATCH_EFFORTS")})
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps({"line": _LINE, "warnings": []}), stderr="")

    _drive(monkeypatch, step=step)

    assert seen == {"TRW_DISPATCH_MODELS": "codex=gpt-x", "TRW_DISPATCH_EFFORTS": "codex=high"}


@pytest.mark.parametrize("failure", ["exit2", "oserror", "garbage"])
def test_a_failing_step_warns_once_and_the_install_still_finishes(
    monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    def step(cmd: list[str]) -> subprocess.CompletedProcess[str]:
        if failure == "oserror":
            raise OSError("no such file")
        if failure == "garbage":
            return subprocess.CompletedProcess(cmd, 0, stdout="not json", stderr="")
        return subprocess.CompletedProcess(cmd, 2, stdout="", stderr="usage error")

    _m, ui, _calls, verdict = _drive(monkeypatch, step=step)

    assert verdict is True  # the doctor still ran and the install is healthy
    warns = [line for line in ui.lines if "dispatch" in line.lower()]
    assert len(warns) == 1
    assert "trw-mcp config dispatch" in warns[0]


def test_step_warnings_from_the_json_are_shown(monkeypatch: pytest.MonkeyPatch) -> None:
    def step(cmd: list[str]) -> subprocess.CompletedProcess[str]:
        payload = {"line": _LINE, "warnings": ["unknown client 'nope' in --dispatch-model; skipped"]}
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(payload), stderr="")

    _m, ui, _calls, _ = _drive(monkeypatch, step=step)

    assert any("unknown client 'nope'" in line for line in ui.lines)


def test_the_line_is_recorded_for_the_banner_beside_extras(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    installer, _ui, _calls, _ = _drive(monkeypatch)
    ui = installer.UI(interactive=True)

    installer.show_success_banner(ui, "connected", ["Extras-item"], dispatch_line=installer.dispatch_line())

    out = capsys.readouterr().out.splitlines()
    extras = next(i for i, line in enumerate(out) if "Extras:" in line)
    assert _LINE in out[extras + 1]


def test_no_line_means_no_dispatch_row(capsys: pytest.CaptureFixture[str]) -> None:
    installer = _installer()
    installer.show_success_banner(installer.UI(interactive=True), "connected", ["x"], dispatch_line="")
    assert "Dispatch:" not in capsys.readouterr().out


def test_the_cli_exposes_the_flags() -> None:
    text = _TEMPLATE.read_text(encoding="utf-8")
    for flag in ("--with-dispatch", "--no-dispatch", "--dispatch-model", "--dispatch-effort", "TRW_WITH_DISPATCH"):
        assert flag in text


# ── main() wiring ────────────────────────────────────────────────────────


def _main_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, extra: tuple[str, ...] = (), env: dict[str, str] | None = None
):  # type: ignore[no-untyped-def]
    from tests._install_trw_main_support import drive_main, make_project

    installer = _installer()
    monkeypatch.setattr(installer, "_DISPATCH_LINE", _LINE)  # what a real run's step would have recorded
    run = drive_main(installer, monkeypatch, make_project(tmp_path), extra_argv=extra, env=env)
    return run


def test_main_forwards_the_flags_to_the_doctor_step(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    run = _main_run(
        monkeypatch,
        tmp_path,
        ("--with-dispatch", "--dispatch-model", "codex=gpt-x", "--dispatch-effort", "codex=high"),
    )

    ((_args, kwargs),) = run.calls["doctor"]
    assert kwargs["dispatch_args"] == (
        "--enable",
        "--dispatch-model",
        "codex=gpt-x",
        "--dispatch-effort",
        "codex=high",
    )


def test_main_reads_the_env_flag_when_no_flag_is_given(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    run = _main_run(monkeypatch, tmp_path, env={"TRW_WITH_DISPATCH": "no"})
    ((_args, kwargs),) = run.calls["doctor"]
    assert kwargs["dispatch_args"] == ("--disable",)


def test_main_shows_the_dispatch_line_in_the_banner(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    run = _main_run(monkeypatch, tmp_path)
    ((_args, kwargs),) = run.calls["banner"]
    assert kwargs["dispatch_line"] == _LINE


# ── Final audit P2: every step warning is relayed, sanitised, with no secret ───────────────────────

_SECRET = "trw_dk_supersecretvalue0123456789abcdef"
_BOGUS = "config dispatch: warning: --dispatch-effort: 'bogus' is not an effort level for codex; skipped"


def _stderr_step(*warnings: str) -> Any:
    def step(cmd: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(cmd, 0, stdout=_LINE + "\n", stderr="\n".join(warnings) + "\n")

    return step


def test_an_interactive_invalid_pin_warning_is_shown_and_the_install_goes_on(monkeypatch: pytest.MonkeyPatch) -> None:
    _m, ui, calls, verdict = _drive(
        monkeypatch,
        interactive=True,
        dispatch_args=("--enable", "--dispatch-effort", "codex=bogus"),
        step=_stderr_step(_BOGUS),
    )

    assert any("'bogus' is not an effort level" in line for line in ui.lines)
    assert verdict is True  # the doctor still ran: the install finished
    assert any("doctor" in c for c in calls)


def test_a_step_warning_never_shows_a_secret_or_a_control_character(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRW_API_KEY", "trw_testkey")
    leaky = f"config dispatch: warning: key {_SECRET} and trw_testkey and \x1b[2J\x07bell"
    _m, ui, _calls, _ = _drive(monkeypatch, interactive=True, step=_stderr_step(leaky))

    text = "\n".join(ui.lines)
    assert "key " in text  # the warning is still shown
    assert _SECRET not in text and "trw_testkey" not in text
    assert "\x1b" not in text and "\x07" not in text


def test_json_mode_warnings_are_sanitised_too(monkeypatch: pytest.MonkeyPatch) -> None:
    def step(cmd: list[str]) -> subprocess.CompletedProcess[str]:
        payload = {"line": _LINE, "warnings": [f"pin skipped {_SECRET}\x1b[31m"]}
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(payload), stderr="")

    _m, ui, _calls, _ = _drive(monkeypatch, step=step)

    text = "\n".join(ui.lines)
    assert "pin skipped" in text and _SECRET not in text and "\x1b" not in text
