"""PRD-FIX-156 FR04 (B71-22): no hook goes dark because jq is missing.

The cursor and copilot edit hints and the degenerate-result advisory read their
payload with jq, else python3. These tests run the REAL hooks with jq hidden
and compare against the jq run, then with neither parser, where each hook keeps
its allow / print-nothing / silent exit (and the degenerate-result hook logs
``jq_unavailable=1``, the key every hook uses for "no JSON parser").
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from tests._layout import HAS_JQ, path_without
from tests.hooks._degenerate_result_harness import _advisories, _payload, _project, _run
from tests.hooks._sh_census import DATA

pytestmark = pytest.mark.skipif(shutil.which("sh") is None, reason="sh unavailable")

_EDIT = "src/module.py"
#: hook path -> (debounce dir, payload carrying the edit target the way that client sends it)
_HINT_HOOKS: dict[str, tuple[str, dict[str, object]]] = {
    "hooks/cursor/trw-before-edit-hint.sh": (
        "cur06-debounce",
        {"tool_name": "edit_file", "tool_input": {"file_path": _EDIT}},
    ),
    "copilot/hooks/trw-copilot-distill-hint.sh": (
        "c5-copilot-debounce",
        {"toolName": "edit", "toolArgs": {"filePath": _EDIT}},
    ),
}


def _run_hint(hook: str, project: Path, path: str) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    """Run an edit hint; return the result and the debounce markers it wrote (one per file path read)."""
    (project / ".trw").mkdir(parents=True)
    (project / ".trw" / "config.yaml").write_text("cc03_hook_enabled: true\n", encoding="utf-8")
    debounce_dir, payload = _HINT_HOOKS[hook]
    result = subprocess.run(
        ["sh", str(DATA / hook)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        timeout=30,
        env={"PATH": path, "TRW_PROJECT_DIR": str(project), "HOME": os.environ.get("HOME", "/tmp")},
        check=False,
    )
    markers = sorted(p.name for p in (project / ".trw" / "context" / debounce_dir).glob("*.ts"))
    return result, markers


@pytest.mark.parametrize("hook", sorted(_HINT_HOOKS))
def test_edit_hint_reads_the_file_path_without_jq(tmp_path: Path, hook: str) -> None:
    """Pre-fix, a jq-less host exited before reading the path: no debounce marker, no hint."""
    no_jq = path_without(tmp_path, {"jq"})
    assert shutil.which("python3", path=no_jq) is not None

    result, markers = _run_hint(hook, tmp_path / "no-jq", no_jq)

    assert result.returncode == 0
    assert len(markers) == 1 and markers[0].startswith("src_module.py-"), markers
    if HAS_JQ:
        _with_jq, jq_markers = _run_hint(hook, tmp_path / "jq", os.environ["PATH"])
        assert jq_markers == markers


@pytest.mark.parametrize("hook", sorted(_HINT_HOOKS))
def test_edit_hint_with_no_parser_allows_quietly(tmp_path: Path, hook: str) -> None:
    result, markers = _run_hint(hook, tmp_path / "bare", path_without(tmp_path, {"jq", "python3"}))

    assert result.returncode == 0
    assert markers == []
    assert result.stdout.strip() in {"", '{"permission": "allow"}'}


@pytest.mark.parametrize("hook", sorted(_HINT_HOOKS))
@pytest.mark.parametrize("gate", ["disabled", "enabled"])
def test_parsing_never_runs_the_checkout_chosen_interpreter(tmp_path: Path, hook: str, gate: str) -> None:
    """sol r1 (PRD-FIX-156): `.trw/channels/cc03-python.txt` is checkout-written. The
    payload reader must use PATH's python3, and a disabled hint must run no interpreter."""
    project = tmp_path / "proj"
    (project / ".trw" / "channels").mkdir(parents=True)
    ran = tmp_path / "checkout-program-ran"
    planted = project / "planted.sh"
    planted.write_text(f"#!/bin/sh\ntouch '{ran}'\nexit 1\n", encoding="utf-8")
    planted.chmod(0o755)
    (project / ".trw" / "channels" / "cc03-python.txt").write_text(str(planted), encoding="utf-8")
    (project / ".trw" / "config.yaml").write_text(f"cc03_hook_enabled: {gate == 'enabled'}\n".lower(), encoding="utf-8")
    _debounce, payload = _HINT_HOOKS[hook]
    extension_skipped = json.loads(json.dumps(payload).replace(_EDIT, "notes.md"))
    no_jq = path_without(tmp_path, {"jq"})

    for body in ({}, extension_skipped):
        result = subprocess.run(
            ["sh", str(DATA / hook)],
            input=json.dumps(body),
            capture_output=True,
            text=True,
            timeout=30,
            env={"PATH": no_jq, "TRW_PROJECT_DIR": str(project), "HOME": str(tmp_path)},
            check=False,
        )
        assert result.returncode == 0

    assert not ran.exists(), "parsing the payload ran the checkout-selected interpreter"


@pytest.mark.parametrize(
    "response",
    ["", {"stdout": "", "stderr": ""}, "line one\n[171470 more lines]", "def add(a, b):\n    return a + b\n"],
    ids=["empty", "empty-leaves", "truncated", "healthy"],
)
def test_degenerate_result_without_jq_matches_the_jq_run(tmp_path: Path, response: object) -> None:
    no_jq = _run(_project(tmp_path, "no-jq"), _payload(response), env={"PATH": path_without(tmp_path, {"jq"})})

    assert no_jq.returncode == 0
    expected = 0 if isinstance(response, str) and response.startswith("def ") else 1
    assert len(_advisories(no_jq)) == expected
    if HAS_JQ:
        with_jq = _run(_project(tmp_path, "jq"), _payload(response))
        assert _advisories(with_jq) == _advisories(no_jq)


def test_degenerate_result_with_no_parser_logs_it_and_stays_silent(tmp_path: Path) -> None:
    root = _project(tmp_path, "bare")

    result = _run(root, _payload(""), env={"PATH": path_without(tmp_path, {"jq", "python3"})})

    assert (result.returncode, _advisories(result)) == (0, [])
    log = (root / ".trw" / "context" / "hook-executions.log").read_text(encoding="utf-8")
    assert "jq_unavailable=1" in log


def test_degenerate_result_read_cap_advises_with_no_parser(tmp_path: Path) -> None:
    """The oversized-by-cap branch needs no parser; pre-fix the jq probe exited before it."""
    root = _project(tmp_path, "cap")

    result = _run(root, _payload("y" * (70 * 1024)), env={"PATH": path_without(tmp_path, {"jq", "python3"})})

    assert result.returncode == 0
    assert len(_advisories(result)) == 1


@pytest.mark.parametrize("parsers", ["none", "jq-or-python3"])
def test_session_start_says_once_when_hooks_run_without_a_parser(tmp_path: Path, parsers: str) -> None:
    """E2E-INC-033: with neither jq nor python3 every other hook degrades silently; the session hears it once."""
    project = tmp_path / "project"
    (project / ".trw").mkdir(parents=True)
    path = path_without(tmp_path, {"jq", "python3"}) if parsers == "none" else os.environ["PATH"]
    result = subprocess.run(
        ["sh", str(DATA / "hooks" / "session-start.sh")],
        input=json.dumps({"source": "startup", "session_id": "s-1"}),
        capture_output=True,
        text=True,
        env={"PATH": path, "CLAUDE_PROJECT_DIR": str(project), "HOME": str(tmp_path)},
        timeout=60,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    notices = [line for line in result.stdout.splitlines() if "neither jq nor python3" in line]
    assert len(notices) == (1 if parsers == "none" else 0), result.stdout
