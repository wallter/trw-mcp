"""Shared CLI drivers for the ``trw-mcp handoff`` tests: a scratch git repo, the real parser, and a filler.

Every helper drives the real parser and handler table (no handler is called directly).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
from pathlib import Path
from typing import Any

import pytest

from tests._path_isolation import set_current_root
from trw_mcp.handoff import load
from trw_mcp.handoff._validate import PLACEHOLDER
from trw_mcp.server._cli_argparse import _build_arg_parser
from trw_mcp.server._subcommands import SUBCOMMAND_HANDLERS

_IDENTITY = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}


def _git(repo: Path, *args: str) -> str:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")} | _IDENTITY
    return subprocess.run(["git", *args], cwd=repo, env=env, check=True, capture_output=True, text=True).stdout


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = (tmp_path / "repo").resolve()
    root.mkdir()
    _git(root, "init", "-q")
    (root / "a.txt").write_text("alpha\n", encoding="utf-8")
    _git(root, "add", "a.txt")  # .trw/ is NOT ignored: the handoff dir must be excluded by the tool itself
    _git(root, "commit", "-qm", "init")
    monkeypatch.chdir(root)
    set_current_root(root)  # the suite's path isolation owns resolve_project_root(); point it at the repo
    monkeypatch.setattr("trw_mcp.state._paths.find_active_run", lambda **_: None)
    return root


def _run(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, str, str]:
    args: argparse.Namespace = _build_arg_parser().parse_args(["handoff", *argv])
    with pytest.raises(SystemExit) as exc:
        SUBCOMMAND_HANDLERS["handoff"](args)
    out, err = capsys.readouterr()
    return int(exc.value.code or 0), out, err


def _new(capsys: pytest.CaptureFixture[str], *argv: str) -> Path:
    code, out, err = _run(capsys, "new", *argv)
    assert code == 0, err
    return Path(out.strip())


_CHOICES = {"label": "observed", "severity": "medium", "at": "2026-01-01T00:00:00Z"}


def _fill(value: Any, key: str = "") -> Any:
    """Replace every sentinel the way an agent would: real text, and a real choice for each enum.

    A drafted claim carries both shapes; like an agent, keep ``evidence`` for ``verified`` and ``basis`` otherwise.
    """
    if isinstance(value, dict):
        filled = {k: _fill(v, k) for k, v in value.items()}
        if key == "claims" and {"basis", "evidence"} <= filled.keys():
            filled.pop("basis" if filled.get("label") == "verified" else "evidence")
        return filled
    if isinstance(value, list):
        return [_fill(v, key) for v in value]
    if isinstance(value, str) and PLACEHOLDER in value:
        return _CHOICES.get(key, f"Concrete {key or 'value'} for the test.")
    return value


def _filled_sealed(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[Path, str]:
    if "--subject" not in argv:
        argv = ("--subject", "swap-refusal", *argv)
    path = _new(capsys, *argv)
    path.write_text(json.dumps(_fill(load(path)), indent=2), encoding="utf-8")
    code, out, err = _run(capsys, "seal", str(path))
    assert code == 0, out + err
    return path, out.strip()


def _check(capsys: pytest.CaptureFixture[str], path: Path, *extra: str) -> tuple[int, dict[str, Any]]:
    code, out, _ = _run(capsys, "check", str(path), *extra)
    return code, json.loads(out)


def _raw(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
