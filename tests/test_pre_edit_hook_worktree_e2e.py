"""The shipped pre-edit hook, run as Claude Code runs it, against a real linked worktree.

What the worktree fix is for, end to end: a session rooted in the MAIN checkout edits a file in a
linked worktree, and the hook answers from the one sidecar cache every worktree shares -- at the
sidecar's own commit and at a descendant of it -- names the real state once per session when no
sidecar is usable, and never starts a map build or writes outside the checkout it resolved.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tests.channels.claude_code._distill_hint_support import CHECKOUT_PYTHONPATH, deploy_distill_hint
from tests.test_sidecar_ancestry import _batch, _commit, _git, _repo

#: git subcommands the pre-edit path may run: every one only reads.
_READ_ONLY_GIT = {"rev-parse", "rev-list", "diff", "status", "ls-files", "cat-file"}


def _linked(tmp_path: Path) -> tuple[Path, Path, str]:
    """A main checkout holding the hook, the interpreter pointer and the gate; plus a linked worktree."""
    main, head = _repo(tmp_path)
    (main / ".trw/channels").mkdir()
    (main / ".trw/channels/cc03-python.txt").write_text(sys.executable)
    (main / ".trw/config.yaml").write_text("cc03_hook_enabled: true\n")
    wt = tmp_path / "linked"
    _git(main, "worktree", "add", "-q", "--detach", str(wt))
    (wt / ".trw").mkdir()
    for name in ("entitlements.yaml", "config.yaml"):
        (wt / ".trw" / name).write_bytes((main / ".trw" / name).read_bytes())
    deploy_distill_hint(main)
    return main, wt, head


def _shims(tmp_path: Path) -> tuple[Path, Path]:
    """PATH shims that record every builder start and every git argv the hook path runs."""
    shim = tmp_path / "shims"
    shim.mkdir(exist_ok=True)
    log = tmp_path / "spawned.log"
    for name in ("trw-distill", "nice"):
        (shim / name).write_text(f'#!/bin/sh\necho "{name} $*" >> "{log}"\n')
        (shim / name).chmod(0o755)
    (shim / "git").write_text(f'#!/bin/sh\necho "git $*" >> "{log}"\nexec /usr/bin/git "$@"\n')
    (shim / "git").chmod(0o755)
    return shim, log


def _edit(
    main: Path,
    target: Path,
    tmp_path: Path,
    *,
    tool_use_id: str,
    session: str = "s1",
    session_root: Path | None = None,
    path: str | None = None,
) -> tuple[str, subprocess.CompletedProcess[str]]:
    """One PreToolUse call from a session rooted in *main*; returns (additionalContext, process)."""
    shim, _ = _shims(tmp_path)
    proc = subprocess.run(
        ["sh", str(main / ".claude/hooks/pre-tool-distill-hint.sh")],
        cwd=session_root or main,
        capture_output=True,
        text=True,
        timeout=30,
        input=json.dumps(
            {
                "session_id": session,
                "hook_event_name": "PreToolUse",
                "tool_use_id": tool_use_id,
                "tool_name": "Edit",
                "tool_input": {"file_path": str(target), "old_string": "x", "new_string": "y"},
            }
        ),
        env={
            "PATH": path or f"{shim}{os.pathsep}/usr/bin:/bin",
            "HOME": str(tmp_path),
            "CLAUDE_PROJECT_DIR": str(session_root or main),
            "PYTHONPATH": CHECKOUT_PYTHONPATH,
            # Content assertions must not depend on how loaded the test host is; the deadline has its own tests.
            "TRW_CC03_ALARM_S": "20",
            "TRW_CC03_BOUND_S": "25",
        },
    )
    assert proc.returncode == 0, proc.stderr
    context = json.loads(proc.stdout)["hookSpecificOutput"]["additionalContext"] if proc.stdout.strip() else ""
    return context, proc


def _spawned(tmp_path: Path) -> list[str]:
    log = tmp_path / "spawned.log"
    return log.read_text().splitlines() if log.exists() else []


@pytest.mark.parametrize("descendant", [False, True])
def test_main_rooted_session_gets_the_worktree_hint_from_the_shared_cache(tmp_path: Path, descendant: bool) -> None:
    main, wt, head = _linked(tmp_path)
    _batch(main, head)  # the only sidecar: in the MAIN checkout's cache, for main's HEAD
    if descendant:
        _commit(wt, "other.py", "y = 1\n")

    context, _ = _edit(main, wt / "foo.py", tmp_path, tool_use_id="e2e-1")

    assert "[TRW Distill Hint — T2]" in context
    assert "RISK: 0.42" in context
    assert ("1 commit behind HEAD" in context) is descendant, context
    assert "Sidecar" not in context, "a served sidecar needs no remedy line"
    record = json.loads((wt / ".trw/context/cc03-hints/e2e-1.json").read_text())
    assert record["tier"] == "T2"
    assert record["distill_status"] == ("hint_available_stale" if descendant else "hint_available")
    assert record["sidecar_commits_behind"] == (1 if descendant else None)  # an exact-HEAD sidecar carries no as-of
    # State lands in the checkout that owns the file, never in the session's.
    assert not (main / ".trw/context").exists()


def test_no_usable_sidecar_names_the_state_once_per_session(tmp_path: Path) -> None:
    main, wt, _ = _linked(tmp_path)
    (wt / "second.py").write_text("z = 1\n")

    first, _ = _edit(main, wt / "foo.py", tmp_path, tool_use_id="e2e-a")
    second, _ = _edit(main, wt / "second.py", tmp_path, tool_use_id="e2e-b")
    other_session, _ = _edit(main, wt / "third.py", tmp_path, tool_use_id="e2e-c", session="s2")

    cache = (main / ".trw/distill/map-cache").resolve()
    assert first.startswith("[TRW] Sidecar missing; run: trw-distill self-improve refresh-sidecars")
    assert f"--repo {wt.resolve()}" in first and f"--cache-dir {cache}" in first
    assert "Sidecar" not in second, "printed once per session"
    assert "[TRW] Sidecar missing" in other_session, "a new session is told again"
    assert json.loads((wt / ".trw/context/cc03-hints/e2e-a.json").read_text())["distill_status"] == "sidecar_missing"


def test_the_pre_edit_path_starts_no_build_and_only_reads_git(tmp_path: Path) -> None:
    """A served hint, a stale one and a missing sidecar: none starts trw-distill, none runs a git write."""
    main, wt, head = _linked(tmp_path)
    _edit(main, wt / "foo.py", tmp_path, tool_use_id="e2e-missing")  # no sidecar at all
    _batch(main, head)
    _commit(wt, "other.py", "y = 1\n")
    (wt / "new.py").write_text("n = 1\n")
    context, _ = _edit(main, wt / "new.py", tmp_path, tool_use_id="e2e-uncovered", session="s2")
    for marker in (wt / ".trw/context/cc03-debounce").iterdir():
        marker.unlink()  # foo.py was hinted a moment ago; let the stale lookup run instead of the 180s debounce
    stale, _ = _edit(main, wt / "foo.py", tmp_path, tool_use_id="e2e-stale", session="s3")
    assert "RISK: 0.42" not in context and "does not cover this file" in context
    assert "RISK: 0.42" in stale and "1 commit behind HEAD" in stale

    spawned = _spawned(tmp_path)
    assert spawned, "non-vacuity: the shims saw the hook's git calls"
    assert [line for line in spawned if line.startswith(("trw-distill", "nice"))] == []
    verbs = set()
    for line in spawned:
        argv = line.split()[1:]
        while argv and argv[0] in {"-C", "-c"}:
            argv = argv[2:]
        verbs.add(next(arg for arg in argv if not arg.startswith("-")))
    assert verbs <= _READ_ONLY_GIT, verbs
    assert not list((main / ".trw/distill/map-cache").glob("**/rebuild-requested.json"))
    assert not (main / ".trw/distill/map-cache/refresh-requests").exists()


def test_a_file_in_an_unrelated_repository_gets_nothing_and_no_trw_directory(tmp_path: Path) -> None:
    """Resolving the checkout from the file must not start writing .trw state into some other repository."""
    main, _, _ = _linked(tmp_path)
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    _git(foreign, "init", "-q")
    (foreign / "x.py").write_text("x = 1\n")

    context, proc = _edit(main, foreign / "x.py", tmp_path, tool_use_id="e2e-foreign")

    assert context == "" and proc.stdout == ""
    assert not (foreign / ".trw").exists()
    assert not (main / ".trw/context").exists()


def _plant(checkout: Path, marker: Path) -> None:
    """Everything a hostile checkout could offer the hook: a gate, an interpreter pointer, a trw_mcp package."""
    (checkout / ".trw/channels").mkdir(parents=True, exist_ok=True)
    (checkout / ".trw/config.yaml").write_text("cc03_hook_enabled: true\n")
    planted = checkout / ".trw/planted-python"
    planted.write_text(f'#!/bin/sh\necho interpreter >> "{marker}"\n')
    planted.chmod(0o755)
    (checkout / ".trw/channels/cc03-python.txt").write_text(str(planted))
    package = checkout / "pkg/src/trw_mcp"
    package.mkdir(parents=True, exist_ok=True)
    (package / "__init__.py").write_text(f'open({str(marker)!r}, "a").write("module\\n")\n')
    (checkout / "x.py").write_text("x = 1\n")


def _state_files(checkout: Path) -> list[str]:
    context = checkout / ".trw/context"
    return sorted(str(p.relative_to(checkout)) for p in context.rglob("*")) if context.exists() else []


@pytest.mark.parametrize("session_gate", ["true", "false"])
@pytest.mark.parametrize("kind", ["unrelated_worktree", "unrelated_plain", "submodule", "outside_any_repository"])
def test_a_file_outside_the_sessions_repository_reads_runs_and_writes_nothing(
    tmp_path: Path, kind: str, session_gate: str
) -> None:
    """Project A's session edits a file elsewhere: B's gate, interpreter, source and .trw are all left alone."""
    main, _, _ = _linked(tmp_path)
    (main / ".trw/config.yaml").write_text(f"cc03_hook_enabled: {session_gate}\n")
    marker = tmp_path / "planted-ran"
    if kind == "outside_any_repository":
        target = tmp_path / "loose"
        target.mkdir()
        _plant(target, marker)
    elif kind == "submodule":
        origin = tmp_path / "origin"
        origin.mkdir()
        _git(origin, "init", "-q")
        _git(origin, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "init")
        _git(main, "-c", "protocol.file.allow=always", "submodule", "add", "-q", str(origin), "vendor")
        target = main / "vendor"
        assert (target / ".git").is_file(), "non-vacuity: a submodule's .git is a file, the PYTHONPATH vector"
        _plant(target, marker)
    else:
        target = tmp_path / "other"
        target.mkdir()
        _git(target, "init", "-q")
        _git(target, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "init")
        if kind == "unrelated_worktree":  # .git is a file here too, so its */src would be offered as PYTHONPATH
            _git(target, "worktree", "add", "-q", "--detach", str(tmp_path / "other-wt"))
            target = tmp_path / "other-wt"
        _plant(target, marker)

    context, proc = _edit(main, target / "x.py", tmp_path, tool_use_id="e2e-boundary")

    assert context == "" and proc.stdout == ""
    assert not marker.exists(), f"the planted interpreter or module ran: {marker.read_text()}"
    assert _state_files(target) == []
    assert _state_files(main) == []


@pytest.mark.parametrize("main_gate", ["true", "false"])
def test_a_worktree_without_trw_takes_gate_and_interpreter_from_the_main_checkout(
    tmp_path: Path, main_gate: str
) -> None:
    """A linked worktree whose .trw is untracked and absent is the session's own repository, governed by main."""
    main, head = _repo(tmp_path)
    (main / ".trw/channels").mkdir()
    (main / ".trw/channels/cc03-python.txt").write_text(sys.executable)
    (main / ".trw/config.yaml").write_text(f"cc03_hook_enabled: {main_gate}\n")
    wt = tmp_path / "bare-linked"
    _git(main, "worktree", "add", "-q", "--detach", str(wt))
    assert not (wt / ".trw").exists()
    deploy_distill_hint(main)
    _batch(main, head)

    for session_root in (main, wt):  # rooted in main, then rooted in the worktree itself
        tool_use_id = f"e2e-bare-{session_root.name}"
        (wt / f"{session_root.name}.py").write_text("x = 1\n")
        _edit(main, wt / f"{session_root.name}.py", tmp_path, tool_use_id=tool_use_id, session_root=session_root)
        record = wt / ".trw/context/cc03-hints" / f"{tool_use_id}.json"
        assert record.is_file() is (main_gate == "true")
    if main_gate == "false":
        assert not (wt / ".trw").exists(), "main switched the hook off: nothing may be created in its worktree"


def test_a_newline_in_the_file_path_is_refused_not_split(tmp_path: Path) -> None:
    main, wt, head = _linked(tmp_path)
    _batch(main, head)
    context, _ = _edit(main, Path(f"{wt}/nope.py\n{wt}/foo.py"), tmp_path, tool_use_id="e2e-newline")
    assert context == ""
    assert _state_files(wt) == []


def test_a_missing_mktemp_does_not_disable_the_hint(tmp_path: Path) -> None:
    main, wt, head = _linked(tmp_path)
    _batch(main, head)
    tools = tmp_path / "no-mktemp-bin"
    tools.mkdir()
    # grep is here on purpose: the hook reads `cc03_hook_enabled` with it. Without it the explicit
    # switch is unreadable and the hook falls back to "is the optional package importable", which
    # made this test pass only on machines that have that package installed.
    required = (
        "sh",
        "cat",
        "sed",
        "awk",
        "grep",
        "head",
        "dirname",
        "basename",
        "tr",
        "cksum",
        "cut",
        "date",
        "env",
        "rm",
    )
    for name in required:
        (tools / name).symlink_to(shutil.which(name) or f"/usr/bin/{name}")
    for name in ("mkdir", "mv", "printf", "readlink", "wc", "sleep", "kill", "find", "chmod", "jq", "python3", "git"):
        found = shutil.which(name)
        if found:
            (tools / name).symlink_to(found)
    context, _ = _edit(main, wt / "foo.py", tmp_path, tool_use_id="e2e-no-mktemp", path=str(tools))
    assert "RISK: 0.42" in context
