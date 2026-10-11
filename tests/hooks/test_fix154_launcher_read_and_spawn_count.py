"""PRD-FIX-154 FR06 and NFR03: the launcher read with jq hidden, and the jq path's python3 spawn count.

FR06: ``_trw_mcp_json_interpreter`` in ``lib-intent-guard.sh`` reads ``.mcpServers.trw.command`` from the
project's ``.mcp.json`` with jq and prints the interpreter named by that launcher's shebang. Without jq it
returns 1 and prints nothing, so ``_trw_resolve_and_run_python`` goes on to its PATH ``python3`` tier.
NFR03: with jq on PATH the shared JSON helpers, the hooks FR01-FR03 changed and the degenerate-result hook
start no ``python3``; a PATH shim in front of the real interpreter counts every start. The cursor and copilot
edit hints are not counted here: they start python3 for the hint itself, on either path.

Both tests run the REAL shipped shell with ``sh``.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from tests._layout import HAS_JQ, path_without
from tests.hooks._degenerate_result_harness import _advisories, _payload
from tests.hooks._degenerate_result_harness import _project as _degenerate_project
from tests.hooks._degenerate_result_harness import _run as _run_degenerate
from tests.hooks._sh_census import DATA

pytestmark = pytest.mark.skipif(shutil.which("sh") is None, reason="sh unavailable")

_HOOKS = DATA / "hooks"
_INTENT_LIB = _HOOKS / "lib-intent-guard.sh"
_TRW_LIB = _HOOKS / "lib-trw.sh"
_SHEBANG_BIN = "/usr/bin/env-stand-in"


def _launcher_project(root: Path, *, command: str, shebang: str | None = None) -> Path:
    """A project whose ``.mcp.json`` names *command*; an absolute command is written as an executable script."""
    project = root / "project"
    project.mkdir(parents=True)
    if shebang is not None:
        script = Path(command)
        script.parent.mkdir(parents=True, exist_ok=True)
        script.write_text(f"{shebang}\nexit 0\n", encoding="utf-8")
        script.chmod(script.stat().st_mode | stat.S_IXUSR)
    (project / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"trw": {"command": command, "args": []}}}), encoding="utf-8"
    )
    return project


def _interpreter(library: Path, project: Path, path: str) -> tuple[int, str]:
    """Source *library* in ``sh`` and call ``_trw_mcp_json_interpreter``; return (status, stdout)."""
    result = subprocess.run(
        ["sh", "-c", f'. "{library}" || exit 99; _trw_mcp_json_interpreter; echo "rc=$?" >&2'],
        capture_output=True,
        text=True,
        timeout=30,
        env={"PATH": path, "CLAUDE_PROJECT_DIR": str(project), "HOME": str(project)},
        check=False,
    )
    assert result.returncode == 0, result.stderr
    status = [line for line in result.stderr.splitlines() if line.startswith("rc=")]
    assert status, f"the function did not run: {result.stderr!r}"
    return int(status[-1][3:]), result.stdout


def _resolver_candidates(library: Path, project: Path, path: str) -> str:
    """The interpreters ``_trw_resolve_and_run_python`` tried, in order (``_trw_python_tried``)."""
    probe = project.parent / "probe_module.py"
    probe.write_text("import sys\nsys.stdin.read()\n", encoding="utf-8")
    script = (
        f'. "{library}" || exit 99; _payload="{{}}"; _trw_module=probe_module; _trw_budget=20; '
        f'_trw_python_tried=""; _trw_resolve_and_run_python; echo "tried=$_trw_python_tried" >&2'
    )
    result = subprocess.run(
        ["sh", "-c", script],
        capture_output=True,
        text=True,
        timeout=60,
        env={"PATH": path, "CLAUDE_PROJECT_DIR": str(project), "HOME": str(project), "PYTHONPATH": str(probe.parent)},
        check=False,
    )
    tried = [line for line in result.stderr.splitlines() if line.startswith("tried=")]
    assert tried, result.stderr
    return tried[-1][6:]


@pytest.mark.skipif(not HAS_JQ, reason="the jq half needs jq")
def test_launcher_interpreter_with_jq_returns_the_shebang_binary(tmp_path: Path) -> None:
    launcher = tmp_path / "bin" / "trw-mcp"
    project = _launcher_project(tmp_path, command=str(launcher), shebang=f"#!{_SHEBANG_BIN} --flag")

    status, out = _interpreter(_INTENT_LIB, project, os.environ["PATH"])

    assert (status, out) == (0, _SHEBANG_BIN)


@pytest.mark.skipif(not HAS_JQ, reason="the jq half needs jq")
def test_launcher_interpreter_with_jq_reads_env_style_shebang_name(tmp_path: Path) -> None:
    launcher = tmp_path / "bin" / "trw-mcp"
    project = _launcher_project(tmp_path, command=str(launcher), shebang="#!/usr/bin/env python3.12")

    assert _interpreter(_INTENT_LIB, project, os.environ["PATH"]) == (0, "python3.12")


def test_launcher_interpreter_without_jq_returns_one_and_prints_nothing(tmp_path: Path) -> None:
    """With jq hidden the same launcher yields no interpreter and no error: the caller's PATH tier answers."""
    launcher = tmp_path / "bin" / "trw-mcp"
    project = _launcher_project(tmp_path, command=str(launcher), shebang=f"#!{_SHEBANG_BIN}")
    no_jq = path_without(tmp_path, {"jq"})
    assert shutil.which("jq", path=no_jq) is None, "the harness still resolves jq"

    status, out = _interpreter(_INTENT_LIB, project, no_jq)

    assert (status, out) == (1, "")


@pytest.mark.skipif(not HAS_JQ, reason="the jq half needs jq")
def test_resolver_candidates_gain_the_launcher_only_when_jq_is_present(tmp_path: Path) -> None:
    """The jq run tries the launcher's interpreter; the jq-hidden run falls back to PATH python3 and still ends."""
    # The launcher's interpreter is a stand-in with a path of its own: were it ``sys.executable``, a host whose
    # PATH ``python3`` is that same file would show it in the jq-hidden run too, for the wrong reason.
    stand_in = tmp_path / "launcher-interpreter" / "python-stand-in"
    stand_in.parent.mkdir()
    stand_in.write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n', encoding="utf-8")
    stand_in.chmod(0o755)
    launcher = tmp_path / "bin" / "trw-mcp"
    project = _launcher_project(tmp_path, command=str(launcher), shebang=f"#!{stand_in}")
    assert shutil.which("python3") is not None

    with_jq = _resolver_candidates(_INTENT_LIB, project, os.environ["PATH"]).split()
    without_jq = _resolver_candidates(_INTENT_LIB, project, path_without(tmp_path, {"jq"})).split()

    assert str(stand_in) in with_jq
    assert str(stand_in) not in without_jq
    assert without_jq and Path(without_jq[-1]).name.startswith("python")


def test_launcher_interpreter_without_jq_pin_fails_on_a_copy_that_answers_anyway(tmp_path: Path) -> None:
    """Non-vacuity: a library copy that names an interpreter when jq is hidden breaks the pinned contract."""
    text = _INTENT_LIB.read_text(encoding="utf-8")
    anchor = '  [ -f "$_tmi_json" ] || return 1\n'
    assert text.count(anchor) == 1
    lying = tmp_path / "lib-lying.sh"
    lying.write_text(
        text.replace(anchor, anchor + "  command -v jq >/dev/null 2>&1 || { printf x; return 0; }\n"), encoding="utf-8"
    )
    launcher = tmp_path / "bin" / "trw-mcp"
    project = _launcher_project(tmp_path, command=str(launcher), shebang=f"#!{_SHEBANG_BIN}")

    no_jq = path_without(tmp_path, {"jq"})
    assert _interpreter(lying, project, no_jq) == (0, "x")
    assert _interpreter(_INTENT_LIB, project, no_jq) == (1, "")


# ---------------------------------------------------------------- NFR03 spawn count


def _counting_python3(tmp_path: Path) -> tuple[str, Path]:
    """A directory holding a ``python3`` that logs its arguments and runs the real interpreter; (PATH, log)."""
    real = shutil.which("python3")
    assert real is not None
    log = tmp_path / "python3-spawns.log"
    shim_dir = tmp_path / "shim"
    shim_dir.mkdir()
    shim = shim_dir / "python3"
    shim.write_text(f'#!/bin/sh\nprintf "%s\\n" "$*" >> "{log}"\nexec "{real}" "$@"\n', encoding="utf-8")
    shim.chmod(0o755)
    return os.pathsep.join([str(shim_dir), os.environ["PATH"]]), log


def _spawn_lines(log: Path) -> list[str]:
    return log.read_text(encoding="utf-8").splitlines() if log.exists() else []


def _project(root: Path) -> Path:
    project = root / "proj"
    (project / ".trw" / "context").mkdir(parents=True)
    (project / ".trw" / "logs").mkdir(parents=True)
    return project


def _hook_env(project: Path, path: str) -> dict[str, str]:
    return {
        "PATH": path,
        "HOME": str(project),
        "CLAUDE_PROJECT_DIR": str(project),
        "TRW_PROJECT_DIR": str(project),
    }


_HELPER_PROGRAM = """
. "{lib}" || exit 99
printf '{{"a":{{"b":"x"}},"c":"y"}}' | _json_get .a.b >/dev/null
printf '{{"a":{{"b":"x"}},"c":"y"}}' | _json_get --strings --default dflt .zz .c >/dev/null
_json_object --str k 'va"l' --int n 3 >/dev/null
echo ran >&2
"""


@pytest.mark.skipif(not HAS_JQ, reason="the NFR03 jq path needs jq")
def test_json_helpers_with_jq_start_no_python3(tmp_path: Path) -> None:
    path, log = _counting_python3(tmp_path)
    project = _project(tmp_path)

    result = subprocess.run(
        ["sh", "-c", _HELPER_PROGRAM.format(lib=_TRW_LIB)],
        capture_output=True,
        text=True,
        timeout=30,
        env=_hook_env(project, path),
        check=False,
    )

    assert result.returncode == 0 and "ran" in result.stderr, result.stderr
    assert _spawn_lines(log) == []


_HOOK_PAYLOADS: dict[str, dict[str, object]] = {
    "pre-compact.sh": {"trigger": "manual"},
    "subagent-start.sh": {"agent_type": "worker", "session_id": "s-1"},
    "subagent-stop.sh": {"agent_type": "worker"},
    "instructions-loaded.sh": {"file_path": "AGENTS.md", "load_reason": "session_start"},
}


def _run_hook(name: str, project: Path, path: str, hooks: Path = _HOOKS) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["sh", str(hooks / name)],
        input=json.dumps(_HOOK_PAYLOADS[name]),
        capture_output=True,
        text=True,
        timeout=60,
        env=_hook_env(project, path),
        check=False,
    )


def _hook_wrote_its_event(name: str, project: Path) -> bool:
    """Proof the hook got past ``. lib-trw.sh`` and reached its JSON work: it wrote a line carrying its payload."""
    markers = {
        "pre-compact.sh": ("manual", project / ".trw" / "context"),
        "subagent-start.sh": ("subagent_start", project / ".trw"),
        "subagent-stop.sh": ("subagent_stop", project / ".trw"),
        "instructions-loaded.sh": ("instructions_loaded", project / ".trw"),
    }
    needle, root = markers[name]
    return any(needle in p.read_text(encoding="utf-8", errors="replace") for p in root.rglob("*") if p.is_file())


@pytest.mark.skipif(not HAS_JQ, reason="the NFR03 jq path needs jq")
@pytest.mark.parametrize("name", sorted(_HOOK_PAYLOADS))
def test_changed_hook_with_jq_starts_no_python3_for_json_work(tmp_path: Path, name: str) -> None:
    path, log = _counting_python3(tmp_path)
    project = _project(tmp_path)

    result = _run_hook(name, project, path)

    assert result.returncode == 0, result.stderr
    assert _hook_wrote_its_event(name, project), "the hook never reached its JSON work, so a zero count proves nothing"
    assert _spawn_lines(log) == []


@pytest.mark.skipif(not HAS_JQ, reason="the NFR03 jq path needs jq")
def test_degenerate_result_hook_with_jq_starts_no_python3(tmp_path: Path) -> None:
    """The hook PRD-FIX-156-FR04 gave a python3 reader for jq-less hosts still reads with jq alone when it can."""
    path, log = _counting_python3(tmp_path)

    result = _run_degenerate(
        _degenerate_project(tmp_path, "jq"), _payload("line one\n[171470 more lines]"), env={"PATH": path}
    )

    assert result.returncode == 0, result.stderr
    assert len(_advisories(result)) == 1, "the hook never reached its extraction, so a zero count proves nothing"
    assert _spawn_lines(log) == []


@pytest.mark.skipif(not HAS_JQ, reason="the NFR03 jq path needs jq")
def test_a_hook_whose_writer_starts_python3_is_counted(tmp_path: Path) -> None:
    """Non-vacuity of the hook count: the same hook, run from a copy of the bundle whose ``_json_object``
    starts python3 before writing, is seen by the shim although jq is on PATH."""
    hooks = tmp_path / "hooks-copy"
    shutil.copytree(_HOOKS, hooks)
    library = hooks / "lib-trw.sh"
    text = library.read_text(encoding="utf-8")
    anchor = "_json_object() {\n"
    assert text.count(anchor) == 1
    library.write_text(text.replace(anchor, anchor + "    python3 -c pass\n"), encoding="utf-8")
    path, log = _counting_python3(tmp_path)
    project = _project(tmp_path)

    result = _run_hook("subagent-start.sh", project, path, hooks)

    assert result.returncode == 0, result.stderr
    assert _hook_wrote_its_event("subagent-start.sh", project)
    assert _spawn_lines(log) != []


@pytest.mark.skipif(not HAS_JQ, reason="the NFR03 jq path needs jq")
def test_spawn_shim_can_fail_when_the_helper_takes_the_python3_branch(tmp_path: Path) -> None:
    """Non-vacuity: with jq hidden the same helper program starts python3, and the shim sees it."""
    path, log = _counting_python3(tmp_path)
    hidden = path_without(tmp_path, {"jq"}, path)
    project = _project(tmp_path)

    result = subprocess.run(
        ["sh", "-c", _HELPER_PROGRAM.format(lib=_TRW_LIB)],
        capture_output=True,
        text=True,
        timeout=30,
        env=_hook_env(project, hidden),
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert len(_spawn_lines(log)) >= 1
