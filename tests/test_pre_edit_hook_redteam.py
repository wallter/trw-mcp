"""Red-team regressions for the shipped pre-edit hook: forged checkouts, clobbered names, unbounded starts.

Threat model the hook states in its header: the session's environment and the session repository's
``.trw`` operator state are trusted; NOTHING about where the edited file sits is. Each world here was
first reproduced against the committed hook by an agent that ran it (round 4), then ported.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import pytest

from tests.channels.claude_code._distill_hint_support import CHECKOUT_PYTHONPATH, deploy_distill_hint
from tests.test_sidecar_ancestry import _batch, _git, _repo


@dataclass
class Run:
    context: str
    stdout: str
    stderr: str
    wall: float


def _world(tmp_path: Path) -> tuple[Path, str]:
    """Session repository A: gate on, real interpreter pointer, a sidecar for foo.py, the deployed hook."""
    main, head = _repo(tmp_path)
    (main / ".trw/channels").mkdir()
    (main / ".trw/channels/cc03-python.txt").write_text(sys.executable)
    (main / ".trw/config.yaml").write_text("cc03_hook_enabled: true\n")
    deploy_distill_hint(main)
    _batch(main, head)
    return main, head


def _plant(checkout: Path, marker: Path) -> None:
    """A gate, an interpreter pointer and a trw_mcp package that each leave a mark if they are ever used."""
    (checkout / ".trw/channels").mkdir(parents=True, exist_ok=True)
    (checkout / ".trw/config.yaml").write_text("cc03_hook_enabled: true\n")
    planted = checkout / ".trw/planted-python"
    planted.write_text(f'#!/bin/sh\necho "interpreter $1" >> "{marker}"\n')
    planted.chmod(0o755)
    (checkout / ".trw/channels/cc03-python.txt").write_text(str(planted))
    package = checkout / "pkg/src/trw_mcp"
    package.mkdir(parents=True, exist_ok=True)
    (package / "__init__.py").write_text(f'open({str(marker)!r}, "a").write("module\\n")\n')
    (checkout / "foo.py").write_text("x = 1\n")


def _state(checkout: Path) -> list[str]:
    context = checkout / ".trw/context"
    return sorted(str(p.relative_to(checkout)) for p in context.rglob("*")) if context.exists() else []


def _hook(
    main: Path,
    target: Path | str,
    tmp_path: Path,
    *,
    env: dict[str, str] | None = None,
    path: str = "/usr/bin:/bin",
    cwd: Path | None = None,
    raw: bytes | None = None,
) -> Run:
    payload = {
        "session_id": "s1",
        "hook_event_name": "PreToolUse",
        "tool_use_id": "rt-1",
        "tool_name": "Edit",
        "tool_input": {"file_path": str(target), "old_string": "x", "new_string": "y"},
    }
    started = time.monotonic()
    proc = subprocess.run(
        ["/bin/sh", str(main / ".claude/hooks/pre-tool-distill-hint.sh")],
        cwd=cwd or main,
        input=raw if raw is not None else json.dumps(payload).encode(),
        capture_output=True,
        timeout=60,
        env={
            "PATH": path,
            "HOME": str(tmp_path),
            "CLAUDE_PROJECT_DIR": str(main),
            "PYTHONPATH": CHECKOUT_PYTHONPATH,
            **(env or {}),
        },
    )
    wall = time.monotonic() - started
    assert proc.returncode == 0
    assert proc.stderr == b"", proc.stderr.decode("utf-8", "replace")[:600]  # exit 0 with noise is still noise
    stdout = proc.stdout.decode("utf-8", "replace")
    context = json.loads(stdout)["hookSpecificOutput"]["additionalContext"] if stdout.strip() else ""
    return Run(context, stdout, proc.stderr.decode("utf-8", "replace"), wall)


def _forge(target: Path, main: Path, *, git_as: str) -> None:
    """A directory with no git objects that CLAIMS to be a worktree of *main*: its commondir points at main's .git."""
    branch = _git(main, "rev-parse", "--abbrev-ref", "HEAD")
    if git_as == "file":
        gitdir = target / ".gd"
        gitdir.mkdir(parents=True)
        (target / ".git").write_text("gitdir: .gd\n")
    else:
        gitdir = target / ".git"
        gitdir.mkdir(parents=True)
    (gitdir / "HEAD").write_text(f"ref: refs/heads/{branch}\n")
    (gitdir / "commondir").write_text(os.path.relpath(main / ".git", gitdir) + "\n")


@pytest.mark.parametrize("where", ["outside", "nested"])
@pytest.mark.parametrize("git_as", ["file", "directory"])
def test_a_forged_worktree_claim_is_not_believed(tmp_path: Path, where: str, git_as: str) -> None:
    """F1/F3: the common directory a target reports about itself proves nothing; only the session
    repository's own worktree registry does."""
    main, _ = _world(tmp_path)
    target = tmp_path / "evil" if where == "outside" else main / "vendor/evil"
    _forge(target, main, git_as=git_as)
    marker = tmp_path / "planted-ran"
    _plant(target, marker)
    # Non-vacuity: git itself is fooled, which is why the hook must not take its word.
    reported = _git(target, "rev-parse", "--path-format=absolute", "--git-common-dir")
    assert Path(reported).resolve() == (main / ".git").resolve()

    run = _hook(main, target / "foo.py", tmp_path)

    assert run.stdout == ""
    assert not marker.exists(), f"planted code ran: {marker.read_text()}"
    assert _state(target) == [] and _state(main) == []


@pytest.mark.parametrize("kind", ["ceiling", "core_worktree", "broken_gitfile"])
def test_a_nested_repository_git_cannot_see_is_still_not_the_sessions(tmp_path: Path, kind: str) -> None:
    """F8: the nearest ``.git`` entry above the file decides whose it is, before git is asked anything."""
    main, _ = _world(tmp_path)
    nested = main / "nested"
    (nested / "sub").mkdir(parents=True)
    env: dict[str, str] = {}
    if kind == "broken_gitfile":
        (nested / ".git").write_text("gitdir: /nonexistent/x\n")
    else:
        _git(nested, "init", "-q")
        if kind == "core_worktree":
            _git(nested, "config", "core.worktree", str(main))
        else:
            env["GIT_CEILING_DIRECTORIES"] = str(nested)
    marker = tmp_path / "planted-ran"
    _plant(nested, marker)
    (nested / "sub/y.py").write_text("y = 1\n")

    run = _hook(main, nested / "sub/y.py", tmp_path, env=env)

    assert run.stdout == ""
    assert not marker.exists()
    assert _state(nested) == [] and _state(main) == []


def test_the_edited_worktrees_own_interpreter_pointer_is_never_run(tmp_path: Path) -> None:
    """F2: a real linked worktree whose .trw names another interpreter. The hint is computed AND emitted
    with the session's interpreter; the snapshot helper used to overwrite the hook's `_py` on the way."""
    main, _ = _world(tmp_path)
    linked = tmp_path / "linked"
    _git(main, "worktree", "add", "-q", "--detach", str(linked))
    marker = tmp_path / "planted-ran"
    _plant(linked, marker)
    (linked / ".trw/entitlements.yaml").write_bytes((main / ".trw/entitlements.yaml").read_bytes())
    (linked / "foo.py").write_text("x = 1\n")  # as committed: the sidecar entry still describes it
    _git(linked, "checkout", "-q", "--", "foo.py")

    run = _hook(main, linked / "foo.py", tmp_path)
    time.sleep(1.0)  # the background snapshot start is where the planted pointer used to be picked up

    assert "RISK: 0.42" in run.context, "the T2 hint must reach the model"
    assert not marker.exists(), f"the edited worktree's pointer ran: {marker.read_text()}"


@pytest.mark.parametrize("pointer", ["./relpy", ".trw/planted-python", "relpy"])
def test_a_relative_interpreter_pointer_is_not_resolved_against_the_cwd(tmp_path: Path, pointer: str) -> None:
    """F6: only an absolute path to an executable regular file is a pointer; anything else falls through."""
    main, _ = _world(tmp_path)
    marker = tmp_path / "planted-ran"
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    for root in (main, elsewhere):
        _plant(root, marker) if root is elsewhere else None
        script = root / "relpy"
        script.write_text(f'#!/bin/sh\necho relpy >> "{marker}"\n')
        script.chmod(0o755)
    (main / ".trw/channels/cc03-python.txt").write_text(pointer)

    for cwd in (main, elsewhere):
        _hook(main, main / "foo.py", tmp_path, cwd=cwd)

    assert not marker.exists(), f"a relative pointer was executed: {marker.read_text()}"


def _toolbin(tmp_path: Path, **scripts: str) -> str:
    """A PATH directory with the system tools and the given replacements."""
    tools = tmp_path / "toolbin"
    tools.mkdir()
    for directory in ("/bin", "/usr/bin"):
        for name in os.listdir(directory):
            if name not in scripts and not (tools / name).exists():
                (tools / name).symlink_to(Path(directory) / name)
    for name, text in scripts.items():
        (tools / name).write_text(text)
        (tools / name).chmod(0o755)
    return str(tools)


@pytest.mark.parametrize("target_kind", ["session_checkout", "linked_worktree", "unrelated_repository"])
def test_a_slow_git_cannot_hold_the_hook(tmp_path: Path, target_kind: str) -> None:
    """F4: every shell git call draws on one budget; a git that sleeps five seconds costs about one."""
    main, _ = _world(tmp_path)
    linked = tmp_path / "linked"
    _git(main, "worktree", "add", "-q", "--detach", str(linked))
    other = tmp_path / "other"
    other.mkdir()
    _git(other, "init", "-q")
    (other / "foo.py").write_text("x = 1\n")
    target = {"session_checkout": main, "linked_worktree": linked, "unrelated_repository": other}[target_kind]
    slow = _toolbin(tmp_path, git='#!/bin/sh\nsleep 5\nexec /usr/bin/git "$@"\n')

    run = _hook(main, target / "foo.py", tmp_path, path=slow)

    assert run.wall < 4.0, f"{run.wall:.1f}s"
    assert run.stderr == ""


@pytest.mark.parametrize("slow_start", ["emit", "restamp", "every_start_with_slow_git"])
def test_every_interpreter_start_is_bounded(tmp_path: Path, slow_start: str) -> None:
    """F5: the JSON emit and the timeout re-stamp were started bare. Worst case: git budget, then the
    hint program's bound, then one bounded tail start -- under four seconds against a five second registration."""
    main, _ = _world(tmp_path)
    wrapper = tmp_path / "wrapped-python"
    if slow_start == "emit":  # the hint program runs normally; only the `-I -S` emit start hangs
        body = f'case "$1" in -I) sleep 8;; esac\nexec "{sys.executable}" "$@"\n'
    elif slow_start == "restamp":  # the hint program hangs past its bound, then the re-stamp start hangs too
        body = "sleep 8\n"
    else:
        body = "sleep 8\n"
    wrapper.write_text("#!/bin/sh\n" + body)
    wrapper.chmod(0o755)
    (main / ".trw/channels/cc03-python.txt").write_text(str(wrapper))
    (main / ".trw/config.yaml").unlink() if slow_start == "every_start_with_slow_git" else None
    path = "/usr/bin:/bin"
    if slow_start == "every_start_with_slow_git":  # no config: the gate lookup asks git, which eats most of its budget
        (main / ".trw/config.yaml").write_text("# no explicit gate\n")
        path = _toolbin(tmp_path, git='#!/bin/sh\nsleep 0.6\nexec /usr/bin/git "$@"\n')

    run = _hook(main, main / "foo.py", tmp_path, path=path)

    assert run.wall < 4.0, f"{run.wall:.1f}s"
    assert run.stdout == ""


@pytest.mark.parametrize("shape", ["leading_dash", "leading_dash_dir", "very_long", "garbage_stdin"])
def test_awkward_input_leaves_stderr_empty(tmp_path: Path, shape: str) -> None:
    """F7: exit 0 with usage text or `Broken pipe` on stderr is still noise in the client's log."""
    main, _ = _world(tmp_path)
    raw = None
    target: Path | str = main / "foo.py"
    if shape == "leading_dash":
        target = "-rf.py"
    elif shape == "leading_dash_dir":
        target = "--help/x.py"
    elif shape == "very_long":
        target = str(main) + "/" + ("a" * 200 + "/") * 40 + "z.py"
    else:
        raw = b"\x00\xff{" * 350_000

    run = _hook(main, target, tmp_path, raw=raw)

    assert run.stderr == ""


_POINTING_FORGERIES = ["gitfile_to_common", "symlink_to_common", "gitfile_to_worktree_admin"]


@pytest.mark.parametrize("where", ["outside", "nested"])
@pytest.mark.parametrize("kind", _POINTING_FORGERIES)
def test_pointing_at_the_sessions_git_directory_is_not_membership(tmp_path: Path, kind: str, where: str) -> None:
    """Round 5 F1: a directory need not write into A's registry to pass for A's, it only has to POINT at
    it. What cannot be pointed at is the back-link the repository wrote: the main checkout's own real
    ``.git`` directory, or ``worktrees/<n>/gitdir`` naming the registered worktree's ``.git`` file."""
    main, _ = _world(tmp_path)
    linked = tmp_path / "linked"
    _git(main, "worktree", "add", "-q", "--detach", str(linked))
    target = tmp_path / "evil" if where == "outside" else main / "vendor/evil"
    target.mkdir(parents=True)
    points_at = main / ".git/worktrees/linked" if kind == "gitfile_to_worktree_admin" else main / ".git"
    reference = os.path.relpath(points_at, target) if where == "nested" else str(points_at)
    if kind == "symlink_to_common":
        (target / ".git").symlink_to(reference)
    else:
        (target / ".git").write_text(f"gitdir: {reference}\n")
    marker = tmp_path / "planted-ran"
    _plant(target, marker)
    # Non-vacuity: git reports this directory as a checkout of the session's repository.
    assert Path(_git(target, "rev-parse", "--show-toplevel")).resolve() == target.resolve()
    assert (
        Path(_git(target, "rev-parse", "--path-format=absolute", "--git-common-dir")).resolve()
        == (main / ".git").resolve()
    )

    run = _hook(main, target / "foo.py", tmp_path)

    assert run.stdout == ""
    assert not marker.exists()
    assert _state(target) == [] and _state(main) == [] and _state(linked) == []


@pytest.mark.parametrize("session", ["main", "linked"])
@pytest.mark.parametrize("edited", ["main", "linked"])
def test_real_checkouts_of_the_sessions_repository_still_get_the_hint(
    tmp_path: Path, session: str, edited: str
) -> None:
    """The four legitimate pairings: either checkout's session editing either checkout's file."""
    main, _ = _world(tmp_path)
    linked = tmp_path / "linked"
    _git(main, "worktree", "add", "-q", "--detach", str(linked))
    (linked / ".trw").mkdir()
    for name in ("entitlements.yaml", "config.yaml"):
        (linked / ".trw" / name).write_bytes((main / ".trw" / name).read_bytes())
    roots = {"main": main, "linked": linked}

    run = _hook(main, roots[edited] / "foo.py", tmp_path, env={"CLAUDE_PROJECT_DIR": str(roots[session])})

    assert "RISK: 0.42" in run.context
    assert _state(roots[edited]) != []
    assert _state(roots["linked" if edited == "main" else "main"]) == []


@pytest.mark.parametrize("copied_to", ["the_registered_path", "another_path"])
def test_a_copied_worktree_gitfile_matches_only_at_the_registered_path(tmp_path: Path, copied_to: str) -> None:
    """A registered worktree's directory replaced by an unrelated one carrying the same ``.git`` file IS that
    worktree to git and to this hook (same path, same back-link): the repository registered that path. The
    same file anywhere else is a forgery."""
    main, _ = _world(tmp_path)
    linked = tmp_path / "linked"
    _git(main, "worktree", "add", "-q", "--detach", str(linked))
    gitfile = (linked / ".git").read_text()
    import shutil

    shutil.rmtree(linked)
    target = linked if copied_to == "the_registered_path" else tmp_path / "elsewhere"
    target.mkdir()
    (target / ".git").write_text(gitfile)
    marker = tmp_path / "planted-ran"
    _plant(target, marker)

    run = _hook(main, target / "foo.py", tmp_path)

    assert not marker.exists(), "even at the registered path, nothing the directory carries is executed"
    if copied_to == "another_path":
        assert run.stdout == "" and _state(target) == []
    else:
        assert (target / ".trw/context/cc03-hints/rt-1.json").is_file()


@pytest.mark.parametrize("target_kind", ["linked_worktree", "unrelated_repository"])
def test_a_git_that_ignores_term_cannot_hold_the_hook(tmp_path: Path, target_kind: str) -> None:
    """Round 5 F2: the probes are read-only, so the budget kills outright."""
    main, _ = _world(tmp_path)
    linked = tmp_path / "linked"
    _git(main, "worktree", "add", "-q", "--detach", str(linked))
    other = tmp_path / "other"
    other.mkdir()
    _git(other, "init", "-q")
    (other / "foo.py").write_text("x = 1\n")
    stubborn = _toolbin(tmp_path, git='#!/bin/sh\ntrap "" TERM\nsleep 6\nexec /usr/bin/git "$@"\n')

    run = _hook(main, (linked if target_kind == "linked_worktree" else other) / "foo.py", tmp_path, path=stubborn)

    assert run.wall < 2.0, f"{run.wall:.1f}s"
    assert run.stdout == ""


@pytest.mark.parametrize("parser", ["jq", "python3"])
def test_a_hung_payload_parser_cannot_hold_the_hook(tmp_path: Path, parser: str) -> None:
    """Round 5 F3: the payload is parsed in ONE bounded start, not six unbounded ones."""
    main, _ = _world(tmp_path)
    hang = "#!/bin/sh\nsleep 20\n"
    tools = _toolbin(tmp_path, **({"jq": hang} if parser == "jq" else {"python3": hang}))
    if parser == "python3":
        (Path(tools) / "jq").unlink()

    run = _hook(main, main / "foo.py", tmp_path, path=tools)

    assert run.wall < 2.0, f"{run.wall:.1f}s"
    assert run.stdout == ""


def test_the_payload_is_parsed_in_one_start(tmp_path: Path) -> None:
    main, _ = _world(tmp_path)
    log = tmp_path / "jq.log"
    # The wrapper is started once before the hook, because the first exec of a newly written script costs
    # 0.15-0.4 s on macOS (6 ms afterwards) and the parser's whole bound is 0.5 s. Under the warm-up variable
    # it exits before it records anything, so the log still counts only the starts the hook made.
    guard = '[ -z "${TRW_TEST_STUB_WARM_UP:-}" ] || exit 0'
    tools = _toolbin(tmp_path, jq=f'#!/bin/sh\n{guard}\necho x >> "{log}"\nexec /usr/bin/jq "$@"\n')
    subprocess.run([str(Path(tools) / "jq")], env={"TRW_TEST_STUB_WARM_UP": "1"}, check=True, timeout=30)
    assert not log.exists(), "the warm-up start was counted"

    run = _hook(main, main / "foo.py", tmp_path, path=tools)

    assert "RISK: 0.42" in run.context
    assert log.read_text().splitlines() == ["x"]


def test_worst_case_slow_parser_slow_git_and_hung_interpreter_together(tmp_path: Path) -> None:
    """Each stage slow but inside its own budget, then an interpreter that never answers: the sum the header
    states (parser 0.5 + git 0.7 + hint program 2.5 + tail 0.8 = 4.5s, plus kill grace and process starts)."""
    main, _ = _world(tmp_path)
    (main / ".trw/config.yaml").unlink()  # the gate lookup then needs one git call
    hung = tmp_path / "hung-python"
    hung.write_text("#!/bin/sh\nsleep 30\n")
    hung.chmod(0o755)
    (main / ".trw/channels/cc03-python.txt").write_text(str(hung))
    tools = _toolbin(
        tmp_path,
        jq='#!/bin/sh\nsleep 0.15\nexec /usr/bin/jq "$@"\n',
        git='#!/bin/sh\nsleep 0.15\nexec /usr/bin/git "$@"\n',
    )
    # "Inside its own budget" has to be true of the stage, not of the fixture: the first exec of a newly
    # written script costs 0.15-0.4 s on macOS (6 ms afterwards), which on top of the 0.15 s sleep put the
    # parser past its 0.5 s bound and ended the hook at 0.5 s. Each wrapper is started once before the hook.
    for wrapper in ("jq", "git"):
        subprocess.run([str(Path(tools) / wrapper), "--version"], check=True, capture_output=True, timeout=30)

    run = _hook(main, main / "foo.py", tmp_path, path=tools)

    assert 3.0 < run.wall < 4.9, f"{run.wall:.1f}s"
    assert run.stdout == ""


@pytest.mark.slow
def test_ordinary_cpu_load_never_loses_the_hint(tmp_path: Path) -> None:
    """A loaded machine (a test run, a build) is exactly when an agent is editing: twenty runs for a file in
    the session's own checkout, with every core busy, and the hint is required every time."""
    main, _ = _world(tmp_path)
    for index in range(20):
        (main / f"m{index}.py").write_text("x = 1\n")
    load = [
        subprocess.Popen(["yes"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(os.cpu_count() or 4)
    ]
    try:
        delivered = []
        for _ in range(20):
            for marker in (main / ".trw/context/cc03-debounce").glob("*"):
                marker.unlink()
            for seen in (main / ".trw/context/cc03-hint-seen").glob("*"):
                seen.unlink()  # the session dedup would otherwise suppress the identical second hint
            delivered.append("RISK: 0.42" in _hook(main, main / "foo.py", tmp_path).context)
    finally:
        for process in load:
            process.kill()
            process.wait()
    assert delivered.count(True) == 20, f"{delivered.count(True)}/20 hints under load"
