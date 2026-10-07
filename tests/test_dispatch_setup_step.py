"""PRD-INFRA-210 FR03: the resolved-defaults report that ``trw_dispatch(action='clients')`` and the setup step share."""

from __future__ import annotations

import builtins
import json
import os
import time
from collections.abc import Callable
from pathlib import Path

import pytest

from trw_mcp.dispatch._client_specs import CLIENT_SPECS, SUPPORTED_CLIENTS
from trw_mcp.dispatch._resolve import resolve_dispatch_request
from trw_mcp.dispatch._targets import list_clients, resolved_dispatch_defaults
from trw_mcp.models.config import TRWConfig
from trw_mcp.server._cli_argparse import _build_arg_parser
from trw_mcp.tools._config_cli import run_config, run_dispatch_step

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in tuple(os.environ):
        if key.upper().startswith("TRW_DISPATCH_") or key.upper() in {"TRW_HEADLESS", "TRW_JSON"}:
            monkeypatch.delenv(key)


def _rows(**cfg: object) -> dict[str, dict[str, object]]:
    rows = resolved_dispatch_defaults(TRWConfig(_env_file=None, **cfg).dispatch)  # type: ignore[arg-type]
    return {str(r["client"]): r for r in rows}


def test_one_row_per_supported_client() -> None:
    assert list(_rows()) == list(SUPPORTED_CLIENTS)


def test_codex_row_reads_the_client_spec_not_a_literal() -> None:
    spec = CLIENT_SPECS["codex"]
    row = _rows()["codex"]
    assert (row["model"], row["model_source"]) == (spec.default_model, "default")
    assert (row["effort"], row["effort_source"]) == (spec.default_effort, "default")


def test_per_client_effort_override_shows_with_config_source() -> None:
    row = _rows(dispatch_default_efforts={"codex": "high"})["codex"]
    assert (row["effort"], row["effort_source"]) == ("high", "config")


def test_installed_follows_path_without_launching(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import subprocess

    def boom(*a: object, **k: object) -> None:
        raise AssertionError("a client process was started")

    monkeypatch.setattr(subprocess, "run", boom)
    monkeypatch.setattr(subprocess, "Popen", boom)
    monkeypatch.setattr("shutil.which", lambda b: "/bin/x" if b in CLIENT_SPECS["codex"].binary_names else None)
    rows = _rows()
    assert [c for c, r in rows.items() if r["installed"]] == ["codex"]


@pytest.mark.parametrize(
    "cfg", [{}, {"dispatch_default_efforts": {"codex": "medium"}, "dispatch_default_effort": "high"}]
)
def test_every_row_equals_the_resolver(cfg: dict[str, object]) -> None:
    dispatch_cfg = TRWConfig(_env_file=None, **cfg).dispatch  # type: ignore[arg-type]
    for row in resolved_dispatch_defaults(dispatch_cfg):
        req = resolve_dispatch_request(
            client=str(row["client"]),
            prompt="x",
            role=None,
            model=None,
            cwd=Path("."),
            timeout_s=None,
            isolate=True,
            use_pty=False,
            dispatch_cfg=dispatch_cfg,
        )
        assert (row["model"], row["model_source"], row["effort"], row["effort_source"]) == (
            req.model,
            req.model_source,
            req.effort,
            req.effort_source,
        )


def test_list_clients_gains_effort_and_sources() -> None:
    out = list_clients({"codex": "gpt-x"})
    codex = next(c for c in out["clients"] if c["client"] == "codex")  # type: ignore[union-attr]
    assert codex["default_model"] == "gpt-x"
    assert codex["model_source"] == "config"
    assert codex["effort_source"] == "default"
    assert codex["default_effort"] == CLIENT_SPECS["codex"].default_effort


# -- PRD-INFRA-210 FR07/FR09/FR10/NFR02/NFR03: the ``config dispatch`` installer step ----------------------------------

_STUB_CLIENTS = ("codex", "claude", "agy", "opencode")


class _Tty:
    """A scripted /dev/tty: ``answer(question)`` returns the typed line for each question written."""

    def __init__(self, log: list[str], answer: Callable[[str], str]) -> None:
        self._log, self._answer, self._next = log, answer, ""

    def __enter__(self) -> _Tty:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def write(self, text: str) -> int:
        self._log.append(text)
        self._next = self._answer(text)
        return len(text)

    def flush(self) -> None:
        return None

    def readline(self) -> str:
        return self._next + "\n"


@pytest.fixture
def setup_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    """Temp HOME and project, a scripted tty, and N dispatch binaries 'on PATH' (shutil.which patched)."""
    home, project = tmp_path / "home", tmp_path / "project"
    (home / ".trw").mkdir(parents=True)
    (project / ".trw").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("TRW_PROJECT_ROOT", str(project))
    state: dict[str, object] = {"home": home, "project": project, "asked": [], "answer": lambda q: "", "tty": True}
    installed: list[str] = list(_STUB_CLIENTS)
    state["installed"] = installed

    def which(binary: str) -> str | None:
        return "/bin/x" if any(binary in CLIENT_SPECS[c].binary_names for c in installed) else None

    monkeypatch.setattr("shutil.which", which)
    real_open = builtins.open

    def fake_open(file: object, *args: object, **kwargs: object) -> object:
        if file == "/dev/tty":
            if not state["tty"]:
                raise OSError("no tty")
            return _Tty(state["asked"], lambda q: state["answer"](q))  # type: ignore[arg-type, call-arg]
        return real_open(file, *args, **kwargs)  # type: ignore[call-overload]

    monkeypatch.setattr(builtins, "open", fake_open)
    return state


def _step(
    state: dict[str, object], capsys: pytest.CaptureFixture[str], *argv: str
) -> tuple[int, dict[str, object], str]:
    """The interactive-capable step (no ``--json``): parse *argv* as the CLI would, run the step, return its result."""
    args = _build_arg_parser().parse_args(["config", "dispatch", str(state["project"]), *argv])
    result = run_dispatch_step(
        args.target,
        offer=args.offer,
        enable=args.enable,
        disable=args.disable,
        model_pins=args.dispatch_model,
        effort_pins=args.dispatch_effort,
    )
    return 0, result, capsys.readouterr().err


def _cli_json(
    state: dict[str, object], capsys: pytest.CaptureFixture[str], *argv: str
) -> tuple[int, dict[str, object], str]:
    """The real CLI with ``--json``: (exit code, the JSON document, stderr)."""
    args = _build_arg_parser().parse_args(["config", "dispatch", str(state["project"]), "--json", *argv])
    code = 0
    try:
        run_config(args)
    except SystemExit as exc:
        code = int(exc.code or 0)
    cap = capsys.readouterr()
    return code, (json.loads(cap.out) if cap.out.strip().startswith("{") else {}), cap.err


def _machine(state: dict[str, object]) -> Path:
    return Path(str(state["home"])) / ".trw" / "config.yaml"


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def test_default_path_writes_nothing_and_never_prompts(
    setup_env: dict[str, object], capsys: pytest.CaptureFixture[str]
) -> None:
    code, out, _err = _step(setup_env, capsys)
    assert code == 0 and (out["enabled"], out["decided_by"]) == (False, "default")
    assert not _machine(setup_env).exists() and setup_env["asked"] == []
    assert out["line"].startswith("Dispatch: off (enable: trw-mcp config set dispatch_tools_exposed true")  # type: ignore[union-attr]


@pytest.mark.parametrize(
    ("argv", "answer", "installed", "tty", "prompted", "written", "decided"),
    [
        (["--enable"], "", 0, True, False, "dispatch_tools_exposed: true\n", "flag"),
        (["--disable"], "", 3, True, False, "dispatch_tools_exposed: false\n", "flag"),
        (["--offer"], "y", 2, True, True, "dispatch_tools_exposed: true\n", "prompt"),
        (["--offer"], "n", 3, True, True, "", "default"),
        (["--offer"], "", 3, True, True, "", "default"),
        (["--offer"], "y", 1, True, False, "", "default"),
        (["--offer"], "y", 0, True, False, "", "default"),
        ([], "y", 3, True, False, "", "default"),
        (["--offer"], "y", 3, False, False, "", "default"),
    ],
)
def test_enable_decision_matrix(
    setup_env: dict[str, object],
    capsys: pytest.CaptureFixture[str],
    argv: list[str],
    answer: str,
    installed: int,
    tty: bool,
    prompted: bool,
    written: str,
    decided: str,
) -> None:
    setup_env["installed"][:] = list(_STUB_CLIENTS[:installed])  # type: ignore[index]
    setup_env["tty"] = tty
    setup_env["answer"] = lambda q: answer if "Enable trw_dispatch" in q else ""
    code, out, _err = _step(setup_env, capsys, *argv)
    assert code == 0
    asked = [q for q in setup_env["asked"] if "Enable trw_dispatch" in q]  # type: ignore[union-attr]
    assert bool(asked) is prompted
    assert _text(_machine(setup_env)) == written
    assert out["decided_by"] == decided


@pytest.mark.parametrize("flag", [[], ["--offer"], ["--offer", "--enable"]])
@pytest.mark.parametrize("var", ["TRW_HEADLESS", "TRW_JSON"])
def test_headless_never_opens_the_terminal(
    setup_env: dict[str, object],
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    var: str,
    flag: list[str],
) -> None:
    monkeypatch.setenv(var, "1")
    code, out, _err = _step(setup_env, capsys, *flag)
    assert code == 0 and setup_env["asked"] == []
    assert out["enabled"] is ("--enable" in flag)


def test_prior_answer_in_any_layer_is_reused_without_a_prompt(
    setup_env: dict[str, object], capsys: pytest.CaptureFixture[str]
) -> None:
    (Path(str(setup_env["project"])) / ".trw" / "config.yaml").write_text(
        "dispatch_tools_exposed: false\n", encoding="utf-8"
    )
    code, out, _err = _step(setup_env, capsys, "--offer")
    assert code == 0 and setup_env["asked"] == []
    assert (out["enabled"], out["decided_by"]) == (False, "prior")
    assert not _machine(setup_env).exists()


def test_prior_enabled_rerun_asks_no_pin_question(
    setup_env: dict[str, object], capsys: pytest.CaptureFixture[str]
) -> None:
    _machine(setup_env).write_text("dispatch_tools_exposed: true\n", encoding="utf-8")
    code, out, _err = _step(setup_env, capsys, "--offer")
    assert code == 0 and setup_env["asked"] == [] and out["decided_by"] == "prior"


def test_enter_keeps_every_pin_and_writes_nothing_more(
    setup_env: dict[str, object], capsys: pytest.CaptureFixture[str]
) -> None:
    code, out, _err = _step(setup_env, capsys, "--offer", "--enable")
    assert code == 0
    assert any("(Enter keeps)" in q for q in setup_env["asked"])  # type: ignore[union-attr]
    assert _text(_machine(setup_env)) == "dispatch_tools_exposed: true\n"
    assert out["writes"] == [{"key": "dispatch_tools_exposed", "value": True, "outcome": "written"}]


def test_a_typed_effort_is_pinned_and_siblings_kept(
    setup_env: dict[str, object], capsys: pytest.CaptureFixture[str]
) -> None:
    _machine(setup_env).write_text("dispatch_default_efforts:\n  claude: high\n", encoding="utf-8")
    asked_for = {"codex": "medium"}

    def answer(question: str) -> str:
        return asked_for["codex"] if "effort [" in question and "codex" in question else ""

    setup_env["answer"] = answer
    code, out, _err = _step(setup_env, capsys, "--offer", "--enable")
    assert code == 0
    text = _text(_machine(setup_env))
    assert "codex: medium" in text and "claude: high" in text
    codex = next(r for r in out["clients"] if r["client"] == "codex")  # type: ignore[union-attr]
    assert (codex["effort"], codex["effort_source"]) == ("medium", "config")


def test_an_invalid_effort_is_reasked_once_then_kept(
    setup_env: dict[str, object], capsys: pytest.CaptureFixture[str]
) -> None:
    setup_env["answer"] = lambda q: "extreme" if "effort [" in q and "codex" in q else ""
    code, _out, _err = _step(setup_env, capsys, "--offer", "--enable")
    assert code == 0
    assert "dispatch_default_efforts" not in _text(_machine(setup_env))
    assert sum("effort [" in q and "codex" in q for q in setup_env["asked"]) == 2  # type: ignore[union-attr]


def test_a_client_without_an_effort_carrier_is_never_asked_for_effort(
    setup_env: dict[str, object], capsys: pytest.CaptureFixture[str]
) -> None:
    _step(setup_env, capsys, "--offer", "--enable")
    asked = [q for q in setup_env["asked"] if "(Enter keeps)" in q]  # type: ignore[union-attr]
    assert any("opencode model" in q for q in asked)
    assert not any("effort" in q and "opencode" in q for q in asked)


def test_typed_model_is_pinned_as_a_literal(setup_env: dict[str, object], capsys: pytest.CaptureFixture[str]) -> None:
    setup_env["answer"] = lambda q: "gpt-x.1" if "codex model" in q else ""
    _step(setup_env, capsys, "--offer", "--enable")
    assert "codex: gpt-x.1" in _text(_machine(setup_env))


def test_headless_env_and_flag_pins_apply_without_a_terminal(
    setup_env: dict[str, object], capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TRW_HEADLESS", "1")
    monkeypatch.setenv("TRW_DISPATCH_EFFORTS", "codex=high,claude=medium")
    monkeypatch.setenv("TRW_DISPATCH_MODELS", "codex=gpt-env")
    code, out, _err = _step(setup_env, capsys, "--enable", "--dispatch-model", "codex=gpt-flag")
    assert code == 0 and setup_env["asked"] == []
    text = _text(_machine(setup_env))
    assert "codex: high" in text and "claude: medium" in text
    assert "codex: gpt-flag" in text and "gpt-env" not in text  # an explicit flag wins over the env pin
    assert "Dispatch: on" in out["line"]  # type: ignore[operator]


@pytest.mark.parametrize(
    "pins",
    [
        ["--dispatch-effort", "nosuchclient=high"],
        ["--dispatch-effort", "codex"],
        ["--dispatch-effort", "codex=extreme"],
        ["--dispatch-effort", "opencode=high"],
        ["--dispatch-effort", "agy=xhigh"],
        ["--dispatch-model", "codex=has space"],
    ],
)
def test_bad_pins_warn_and_write_nothing_but_exit_zero(
    setup_env: dict[str, object], capsys: pytest.CaptureFixture[str], pins: list[str]
) -> None:
    code, out, _err = _step(setup_env, capsys, "--enable", *pins)
    assert code == 0
    assert _text(_machine(setup_env)) == "dispatch_tools_exposed: true\n"
    assert len(out["warnings"]) == 1  # type: ignore[arg-type]


def test_pins_are_ignored_when_dispatch_stays_off(
    setup_env: dict[str, object], capsys: pytest.CaptureFixture[str]
) -> None:
    code, out, _err = _step(setup_env, capsys, "--dispatch-effort", "codex=high")
    assert code == 0 and not _machine(setup_env).exists() and out["enabled"] is False


def test_the_result_reports_what_the_launch_will_apply(
    setup_env: dict[str, object], capsys: pytest.CaptureFixture[str]
) -> None:
    _step(setup_env, capsys, "--enable", "--dispatch-effort", "agy=high")
    _machine(setup_env).write_text("dispatch_tools_exposed: true\ndispatch_default_effort: xhigh\n", encoding="utf-8")
    _code, out, _err = _step(setup_env, capsys)
    rows = {r["client"]: r for r in out["clients"]}  # type: ignore[union-attr]
    assert (rows["claude"]["effort"], rows["claude"]["applied_effort"]) == ("xhigh", "xhigh")
    assert (rows["agy"]["effort"], rows["agy"]["applied_effort"]) == ("xhigh", "high")  # clamped at launch
    assert (rows["opencode"]["effort"], rows["opencode"]["applied_effort"]) == ("xhigh", None)  # no carrier


def test_env_shadow_of_the_enable_is_warned(
    setup_env: dict[str, object], capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TRW_DISPATCH_TOOLS_EXPOSED", "false")
    code, out, _err = _step(setup_env, capsys, "--enable")
    assert code == 0 and out["enabled"] is False
    assert any("TRW_DISPATCH_TOOLS_EXPOSED" in w for w in out["warnings"])  # type: ignore[union-attr]


def test_human_output_is_exactly_one_dispatch_line(
    setup_env: dict[str, object], capsys: pytest.CaptureFixture[str]
) -> None:
    args = _build_arg_parser().parse_args(["config", "dispatch", str(setup_env["project"]), "--enable"])
    with pytest.raises(SystemExit):
        run_config(args)
    lines = capsys.readouterr().out.strip().splitlines()
    assert len(lines) == 1 and lines[0].startswith("Dispatch: on, default client codex (model ")
    spec = CLIENT_SPECS["codex"]
    assert f"model {spec.default_model} [default]" in lines[0] and f"effort {spec.default_effort} [default]" in lines[0]
    assert lines[0].endswith("pin: trw-mcp config set dispatch_default_efforts.<client> <level> --scope machine")


def test_no_default_client_line() -> None:
    from trw_mcp.dispatch._setup import render_line

    assert render_line({"enabled": True, "default_client": None, "clients": []}) == (
        "Dispatch: on, no default client (pass --client)"
    )


def test_the_step_starts_no_client_process_and_is_fast(
    setup_env: dict[str, object], capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    import subprocess

    def boom(*a: object, **k: object) -> None:
        raise AssertionError("a process was started")

    monkeypatch.setattr(subprocess, "run", boom)
    monkeypatch.setattr(subprocess, "Popen", boom)
    started = time.perf_counter()
    code, _out, _err = _step(setup_env, capsys, "--offer", "--enable")
    assert code == 0 and time.perf_counter() - started < 1.0


def test_enable_and_disable_together_is_a_usage_error(setup_env: dict[str, object]) -> None:
    with pytest.raises(SystemExit) as exc:
        _build_arg_parser().parse_args(["config", "dispatch", "--enable", "--disable"])
    assert exc.value.code == 2


def test_reviewer_role_cannot_run_the_step(setup_env: dict[str, object], monkeypatch: pytest.MonkeyPatch) -> None:
    from trw_mcp.server._cli_replacements import enforce_state_changing_guard

    monkeypatch.setenv("TRW_SURFACE_ROLE", "reviewer")
    args = _build_arg_parser().parse_args(["config", "dispatch", str(setup_env["project"]), "--enable"])
    with pytest.raises(SystemExit) as exc:
        enforce_state_changing_guard("config", args)
    assert exc.value.code != 0 and not _machine(setup_env).exists()


@pytest.mark.parametrize("argv", [["--offer"], ["--offer", "--enable"]])
def test_json_mode_never_prompts_even_with_offer_on_a_terminal(
    setup_env: dict[str, object], capsys: pytest.CaptureFixture[str], argv: list[str]
) -> None:
    setup_env["answer"] = lambda q: "y" if "Enable trw_dispatch" in q else "medium"
    code, out, _err = _cli_json(setup_env, capsys, *argv)
    assert code == 0 and setup_env["asked"] == []
    assert out["enabled"] is ("--enable" in argv)
    assert "dispatch_default_efforts" not in _text(_machine(setup_env))


_SECRET = "sk-ant-api03-" + "A1b2C3d4E5f6G7h8I9j0" * 2


@pytest.mark.parametrize("flag", ["--dispatch-effort", "--dispatch-model"])
def test_a_credential_in_a_pin_is_redacted_from_stderr_and_json(
    setup_env: dict[str, object], capsys: pytest.CaptureFixture[str], flag: str
) -> None:
    code, out, err = _cli_json(setup_env, capsys, "--enable", flag, f"{_SECRET} x")
    assert code == 0
    assert _SECRET not in err and _SECRET not in json.dumps(out)
    assert out["warnings"]  # the pin was skipped and said so


def test_list_clients_reports_the_clamped_applied_effort() -> None:
    """agy takes low|medium|high: a configured ``max`` resolves as max but a launch carries high."""
    cfg = TRWConfig(_env_file=None, dispatch_default_efforts={"agy": "max"}).dispatch
    clients = {c["client"]: c for c in list_clients(dispatch_cfg=cfg)["clients"]}  # type: ignore[union-attr]
    agy = clients["agy"]
    assert (agy["default_effort"], agy["applied_effort"], agy["effort_source"]) == ("max", "high", "config")
    assert "applied_effort" not in clients["opencode"] or clients["opencode"]["applied_effort"] is None
