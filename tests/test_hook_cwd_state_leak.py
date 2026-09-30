"""HOOK-CWD-STATE-LEAK: the Claude Code pre-edit hint hook resolves the project root, not the shell's CWD.

The hook fell back to ``${TRW_PROJECT_DIR:-$(pwd)}``. Claude Code sets ``CLAUDE_PROJECT_DIR``, not
``TRW_PROJECT_DIR``, and Codex sets neither, so an edit made while the shell sat in a subdirectory wrote
``.trw/context/cc03-*`` and telemetry into THAT directory (observed 2026-09-30: ``trw-distill/trw_distill/.trw/``)
and read the gate from a ``.trw/config.yaml`` that is not the project's. The hook now resolves the root once with
``lib-trw.sh``'s ``get_repo_root`` (``CLAUDE_PROJECT_DIR``, then the git top level, then ``pwd``).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(sys.platform == "win32", reason="POSIX sh hook"),
]

_DATA = Path(__file__).resolve().parents[1] / "src" / "trw_mcp" / "data"
_MONOREPO = Path(__file__).resolve().parents[2]
_HOOK_FILES = (
    _DATA / "claude_code" / "hooks" / "pre-tool-distill-hint.sh",
    _DATA / "claude_code" / "hooks" / "lib-distill-hint.sh",
    _DATA / "hooks" / "lib-trw.sh",
)


def _project(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    (root / ".trw").mkdir(parents=True)
    (root / ".trw" / "config.yaml").write_text("cc03_hook_enabled: true\n", encoding="utf-8")
    hooks = root / ".claude" / "hooks"
    hooks.mkdir(parents=True)
    for source in _HOOK_FILES:
        shutil.copy2(source, hooks / source.name)
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    (root / "src" / "pkg").mkdir(parents=True)
    (root / "src" / "pkg" / "mod.py").write_text("X = 1\n", encoding="utf-8")
    return root


def _run_hook_from(cwd: Path, root: Path, env_extra: dict[str, str]) -> None:
    env = {k: v for k, v in os.environ.items() if k not in ("CLAUDE_PROJECT_DIR", "TRW_PROJECT_DIR")}
    # The hint's Python program writes channel events only when trw-distill is importable. Put the monorepo's
    # copy on the path so this test exercises that writer in every runner, not only where distill is installed
    # (a runner without it passed while the writer still wrote under the shell's CWD).
    distill = [str(_MONOREPO / d) for d in ("trw-distill", "trw-distill/src") if (_MONOREPO / d).is_dir()]
    env["PYTHONPATH"] = os.pathsep.join([*distill, *filter(None, [env.get("PYTHONPATH", "")])])
    env.update(env_extra)
    payload = {
        "tool_name": "Edit",
        "tool_use_id": "toolu_test",
        "tool_input": {"file_path": str(root / "src" / "pkg" / "mod.py")},
    }
    subprocess.run(
        ["sh", str(root / ".claude" / "hooks" / "pre-tool-distill-hint.sh")],
        input=json.dumps(payload),
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


@pytest.mark.parametrize(
    "env_extra", [{}, {"CLAUDE_PROJECT_DIR": "__root__"}], ids=["codex-no-project-env", "claude-code"]
)
def test_hook_run_from_a_subdirectory_writes_state_under_the_project_root(
    tmp_path: Path, env_extra: dict[str, str]
) -> None:
    root = _project(tmp_path)
    subdir = root / "src" / "pkg"
    env = {k: (str(root) if v == "__root__" else v) for k, v in env_extra.items()}

    _run_hook_from(subdir, root, env)

    assert not (subdir / ".trw").exists(), sorted(str(p.relative_to(subdir)) for p in (subdir / ".trw").rglob("*"))
    # Non-vacuity: with the gate read from the ROOT's config (on), the hook creates its hints dir -- under the root.
    assert (root / ".trw" / "context" / "cc03-hints").is_dir()
    if (_MONOREPO / "trw-distill").is_dir():  # ... and the Python hint path ran: its channel event is at the root
        assert (root / ".trw" / "telemetry" / "channel-events.jsonl").is_file()
