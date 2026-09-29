"""The pre-edit hint hooks do nothing for a file outside the project directory.

Observed 2026-09-29: 74% of recorded hook runs (147 of 198) were edits to scratch files outside any
repo. Each started an interpreter for ~1 s (24% of them timed out) to hint a file no sidecar can
know, and the rows inflated ``fallback_share``. The hooks now skip such a target before any
interpreter starts and write no hint record, so the doctor window counts only real attempts.
Containment is by resolved physical path, so a symlink cannot smuggle an outside file in (or an
inside one out).
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from tests._layout import MONOREPO_ROOT, requires_monorepo

_DATA = Path(__file__).resolve().parents[1] / "src" / "trw_mcp" / "data"
_LIB = _DATA / "claude_code" / "hooks" / "lib-distill-hint.sh"
_CLAUDE_HOOK = _DATA / "claude_code" / "hooks" / "pre-tool-distill-hint.sh"
_LIB_TRW = _DATA / "hooks" / "lib-trw.sh"
_CURSOR_LIB = _DATA / "hooks" / "cursor" / "lib-distill-hint.sh"


def _inside(project: Path, target: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["/bin/sh", "-c", '. "$0" && _path_inside_repo "$1" "$2"', str(_LIB), target, str(project)],
        capture_output=True,
        text=True,
        check=False,
        env={"PATH": "/usr/bin:/bin"},
        timeout=30,
    )


def test_containment_by_resolved_path(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    (project / "src").mkdir(parents=True)
    (project / "src" / "a.py").write_text("x", encoding="utf-8")
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (outside / "b.py").write_text("x", encoding="utf-8")

    assert _inside(project, str(project / "src" / "a.py")).returncode == 0
    assert _inside(project, str(project / "src" / "brand_new.py")).returncode == 0  # not yet created
    assert _inside(project, str(project / "src" / "new_dir" / "deep" / "c.py")).returncode == 0
    assert _inside(project, str(outside / "b.py")).returncode == 1
    assert _inside(project, "/tmp/scratch.sh").returncode == 1
    # a relative path is relative to the project, so `..` is what can leave it
    assert _inside(project, "src/a.py").returncode == 0
    assert _inside(project, "../elsewhere/b.py").returncode == 1
    # a sibling directory sharing the project's name prefix is NOT inside it
    sibling = tmp_path / "proj-other"
    sibling.mkdir()
    assert _inside(project, str(sibling / "x.py")).returncode == 1


def test_symlinks_cannot_bypass_containment(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (outside / "b.py").write_text("x", encoding="utf-8")
    (project / "escape").symlink_to(outside)  # dir symlink out of the project
    (project / "link.py").symlink_to(outside / "b.py")  # file symlink out of the project
    (project / "real.py").write_text("x", encoding="utf-8")
    (outside / "back.py").symlink_to(project / "real.py")  # outside path that resolves inside

    assert _inside(project, str(project / "escape" / "b.py")).returncode == 1
    assert _inside(project, str(project / "link.py")).returncode == 1
    assert _inside(project, str(outside / "back.py")).returncode == 0
    alias = tmp_path / "alias"
    alias.symlink_to(project)  # the project reached through a symlinked prefix
    assert _inside(project, str(alias / "real.py")).returncode == 0


def _hook_project(tmp_path: Path) -> tuple[Path, Path]:
    """A project whose hook interpreter is a stub that records having been started."""
    project = tmp_path / "proj"
    hooks = project / ".claude" / "hooks"
    hooks.mkdir(parents=True)
    for src in (_CLAUDE_HOOK, _LIB, _LIB_TRW):
        (hooks / src.name).write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    marker = tmp_path / "interpreter-started"
    stub = tmp_path / "stub" / "python3.14"
    stub.parent.mkdir()
    stub.write_text(f"#!/bin/sh\necho started >> '{marker}'\nexit 0\n", encoding="utf-8")
    stub.chmod(0o755)
    (project / ".trw" / "channels").mkdir(parents=True)
    (project / ".trw" / "channels" / "cc03-python.txt").write_text(str(stub), encoding="utf-8")
    (project / ".trw" / "config.yaml").write_text("cc03_hook_enabled: true\n", encoding="utf-8")
    (project / "src").mkdir()
    return project, marker


def _run_hook(project: Path, target: Path) -> None:
    payload = json.dumps({"tool_name": "Edit", "tool_use_id": "toolu_x", "tool_input": {"file_path": str(target)}})
    subprocess.run(
        ["/bin/sh", str(project / ".claude" / "hooks" / "pre-tool-distill-hint.sh")],
        input=payload,
        capture_output=True,
        text=True,
        check=False,
        env={"PATH": "/usr/bin:/bin", "HOME": str(project.parent), "TRW_PROJECT_DIR": str(project)},
        cwd=project,
        timeout=60,
    )


def test_outside_target_starts_no_interpreter_and_leaves_no_record(tmp_path: Path) -> None:
    project, marker = _hook_project(tmp_path)
    scratch = tmp_path / "scratch" / "q3.sh"
    scratch.parent.mkdir()
    scratch.write_text("echo hi\n", encoding="utf-8")

    _run_hook(project, scratch)

    assert not marker.exists(), "an out-of-repo edit must not start the hint interpreter"
    hints = project / ".trw" / "context" / "cc03-hints"
    assert not hints.exists() or not any(hints.iterdir()), "and must write no hint record"
    assert not (project / ".trw" / "context" / "cc03-debounce").exists()


def test_inside_target_is_unchanged(tmp_path: Path) -> None:
    project, marker = _hook_project(tmp_path)
    target = project / "src" / "mod.py"
    target.write_text("x = 1\n", encoding="utf-8")

    _run_hook(project, target)

    assert marker.exists(), "an in-repo edit still reaches the hint interpreter"


def test_both_hooks_call_the_containment_check_and_both_libs_define_it() -> None:
    for hook in (_CLAUDE_HOOK, _DATA / "hooks" / "cursor" / "trw-before-edit-hint.sh"):
        assert "_path_inside_repo" in hook.read_text(encoding="utf-8"), str(hook)
    for lib in (_LIB, _CURSOR_LIB):
        assert "_path_inside_repo()" in lib.read_text(encoding="utf-8"), str(lib)


@requires_monorepo
def test_checked_in_mirrors_carry_the_check() -> None:
    for mirror in (
        (MONOREPO_ROOT or Path()) / ".claude" / "hooks" / "lib-distill-hint.sh",
        (MONOREPO_ROOT or Path()) / ".claude" / "hooks" / "pre-tool-distill-hint.sh",
    ):
        assert "_path_inside_repo" in mirror.read_text(encoding="utf-8"), str(mirror)
