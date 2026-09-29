"""PRD-FIX-155: the interpreter the bundled hooks start.

``.trw/channels/cc03-python.txt`` had four readers and no writer, so every hook
fell back to bare ``python3``, which usually cannot import trw_mcp. These tests
cover the writer (init-project and update-project), the one resolution order the
hooks share, the post-commit hook actually starting what that order picks, and
the doctor row that reports an interpreter the hooks cannot use.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from trw_memory.testing.daemon_reaper import daemon_env_passthrough

from tests._layout import MONOREPO_ROOT, requires_monorepo
from trw_mcp.bootstrap import init_project, update_project
from trw_mcp.bootstrap._hook_interpreter import HOOK_INTERPRETER_REL as HOOK_INTERPRETER_RELPATH
from trw_mcp.server._doctor_hook_python import _FUNCTION_RE, hook_python_row

from ._bootstrap_test_support import fake_git_repo, initialized_repo  # noqa: F401

pytestmark = pytest.mark.usefixtures("no_memory_daemon")

_DATA = Path(__file__).resolve().parent.parent / "src" / "trw_mcp" / "data"
_CLAUDE_LIB = _DATA / "claude_code" / "hooks" / "lib-distill-hint.sh"
_RESOLVER_COPIES = (
    _CLAUDE_LIB,
    _DATA / "hooks" / "cursor" / "lib-distill-hint.sh",
    _DATA / "copilot" / "hooks" / "lib-copilot-distill-hint.sh",
    _DATA / "git_hooks" / "trw-post-commit.sh",
)


def _executable(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)
    return path


def _resolve(project: Path, path_env: str) -> subprocess.CompletedProcess[str]:
    """Run the bundled ``_get_python_path`` exactly as a hook does."""
    return subprocess.run(
        ["/bin/sh", "-c", '. "$0" && _get_python_path "$1"', str(_CLAUDE_LIB), str(project)],
        capture_output=True,
        text=True,
        check=False,
        env={"PATH": path_env},
        timeout=30,
    )


# --- the writer -------------------------------------------------------------


def test_init_project_records_the_running_interpreter(initialized_repo: Path) -> None:
    assert (initialized_repo / HOOK_INTERPRETER_RELPATH).read_text(encoding="utf-8").strip() == sys.executable


def test_init_project_records_it_outside_a_git_repository(tmp_path: Path) -> None:
    init_project(tmp_path, ide="claude-code")
    assert (tmp_path / HOOK_INTERPRETER_RELPATH).read_text(encoding="utf-8").strip() == sys.executable


@pytest.mark.parametrize(("dry_run", "expected"), [(False, sys.executable), (True, "/moved/venv/bin/python")])
def test_update_project_rewrites_a_stale_pointer_only_for_real(
    initialized_repo: Path, dry_run: bool, expected: str
) -> None:
    pointer = initialized_repo / HOOK_INTERPRETER_RELPATH
    pointer.write_text("/moved/venv/bin/python", encoding="utf-8")

    result = update_project(initialized_repo, dry_run=dry_run)

    assert pointer.read_text(encoding="utf-8").strip() == expected  # the writer ends the line with "\n"
    assert "hook_interpreter" in result["would_run" if dry_run else "ran"]


@pytest.mark.parametrize(
    "gitignore",
    [
        _DATA / "gitignore.txt",
        pytest.param(
            (MONOREPO_ROOT or Path()) / ".trw" / ".gitignore", marks=requires_monorepo, id="monorepo-gitignore"
        ),
    ],
)
def test_the_pointer_is_git_ignored(tmp_path: Path, gitignore: Path) -> None:
    """A per-machine absolute path must not reach version control (fresh install + this repo)."""
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / ".trw").mkdir()
    (tmp_path / ".trw" / ".gitignore").write_text(gitignore.read_text(encoding="utf-8"), encoding="utf-8")
    probe = ["git", "check-ignore", "-q"]
    assert subprocess.run([*probe, str(HOOK_INTERPRETER_RELPATH)], cwd=tmp_path, check=False).returncode == 0
    # Non-vacuity: the tracked channel definition beside it stays tracked.
    assert subprocess.run([*probe, ".trw/channels/manifest.yaml"], cwd=tmp_path, check=False).returncode == 1


# --- the one resolution order -------------------------------------------------


def test_every_hook_carries_the_same_resolver() -> None:
    """No client lib is shared with another, so the copies are pinned byte-identical instead."""
    bodies = {path: _FUNCTION_RE.search(path.read_text(encoding="utf-8")) for path in _RESOLVER_COPIES}
    assert all(bodies.values()), [str(p) for p, m in bodies.items() if m is None]
    assert len({m.group(0) for m in bodies.values() if m}) == 1
    for path in _RESOLVER_COPIES:
        assert "_get_python_path 2>" not in path.read_text(encoding="utf-8"), f"{path} calls it without a dir"


def test_the_checked_in_client_mirrors_carry_the_same_resolver() -> None:
    """The live copies a monorepo checkout runs (.claude/.cursor/.github hooks) match the bundled resolver.

    The byte-identity test above only covers the bundled data copies, so a stale checked-in mirror still
    ran the old resolver in every worktree. Skipped outside the monorepo, where the mirrors do not exist.
    """
    repo = Path(__file__).resolve().parents[2]
    mirrors = [
        repo / ".claude" / "hooks" / "lib-distill-hint.sh",
        repo / ".cursor" / "hooks" / "lib-distill-hint.sh",
        repo / ".github" / "hooks" / "lib-copilot-distill-hint.sh",
    ]
    present = [m for m in mirrors if m.is_file()]
    if not present:
        pytest.skip("not a monorepo checkout: no client hook mirrors")
    bundled = _FUNCTION_RE.search(_CLAUDE_LIB.read_text(encoding="utf-8"))
    assert bundled is not None
    for mirror in present:
        found = _FUNCTION_RE.search(mirror.read_text(encoding="utf-8"))
        assert found is not None and found.group(0) == bundled.group(0), str(mirror)


def test_the_checked_in_claude_distill_hook_matches_the_bundled_one() -> None:
    """The monorepo's own .claude/hooks distill hook files are the bundled ones, byte for byte.

    They drifted ~300 lines behind (no Codex apply_patch batching, old debounce), so every Claude Code
    edit in this repo ran a stale hook. Skipped outside the monorepo, where the checked-in copies do not exist.
    """
    repo = Path(__file__).resolve().parents[2]
    bundled_dir = _CLAUDE_LIB.parent
    pairs = [
        (repo / ".claude" / "hooks" / name, bundled_dir / name)
        for name in ("pre-tool-distill-hint.sh", "lib-distill-hint.sh")
    ]
    present = [(live, bundled) for live, bundled in pairs if live.is_file()]
    if not present:
        pytest.skip("not a monorepo checkout: no .claude/hooks copies")
    for live, bundled in present:
        assert live.read_bytes() == bundled.read_bytes(), f"{live} differs from {bundled}"


def _scenario(tmp_path: Path, case: str) -> tuple[Path, str, str]:
    """Build one resolution scenario; return (project, PATH, expected stdout)."""
    project, bin_dir = tmp_path / "project", tmp_path / "bin"
    project.mkdir()
    bin_dir.mkdir()
    fake_py = _executable(tmp_path / "venv" / "bin" / "python3.14", "#!/bin/sh\nexit 0\n")
    venv_py = project / ".venv" / "bin" / "python"
    pointer = project / HOOK_INTERPRETER_RELPATH
    tools = f"{bin_dir}:/usr/bin:/bin"
    if case == "pointer wins":
        pointer.parent.mkdir(parents=True)
        pointer.write_text(str(fake_py), encoding="utf-8")
        _executable(bin_dir / "trw-mcp", "#!/elsewhere/python\n")
        return project, tools, str(fake_py)
    if case == "stale pointer falls to the trw-mcp shebang":
        pointer.parent.mkdir(parents=True)
        pointer.write_text("/moved/venv/bin/python", encoding="utf-8")
        _executable(bin_dir / "trw-mcp", f"#!{fake_py}\nimport trw_mcp\n")
        _executable(venv_py, "#!/bin/sh\n")
        return project, tools, str(fake_py)
    if case == "no pointer: trw-mcp shebang":
        _executable(bin_dir / "trw-mcp", f"#!{fake_py} -E\n")
        return project, tools, str(fake_py)
    if case == "env shebang is not an interpreter: project venv":
        _executable(bin_dir / "trw-mcp", "#!/usr/bin/env python3\n")
        _executable(venv_py, "#!/bin/sh\n")
        return project, tools, str(venv_py)
    if case == "missing shebang interpreter: project venv":
        _executable(bin_dir / "trw-mcp", "#!/gone/bin/python3\n")
        _executable(venv_py, "#!/bin/sh\n")
        return project, tools, str(venv_py)
    if case == "nothing else: python3 on PATH":
        _executable(bin_dir / "python3", "#!/bin/sh\n")
        return project, str(bin_dir), "python3"
    raise AssertionError(case)


@pytest.mark.parametrize(
    "case",
    [
        "pointer wins",
        "stale pointer falls to the trw-mcp shebang",
        "no pointer: trw-mcp shebang",
        "env shebang is not an interpreter: project venv",
        "missing shebang interpreter: project venv",
        "nothing else: python3 on PATH",
    ],
)
def test_resolution_order(tmp_path: Path, case: str) -> None:
    project, path_env, expected = _scenario(tmp_path, case)
    completed = _resolve(project, path_env)
    assert (completed.returncode, completed.stdout) == (0, expected)


def test_no_interpreter_at_all_fails_the_lookup(tmp_path: Path) -> None:
    (tmp_path / "empty").mkdir()
    completed = _resolve(tmp_path, str(tmp_path / "empty"))
    assert (completed.returncode, completed.stdout) == (1, "")


# --- worktree fallback to the main checkout ---------------------


def _git_env(home: Path) -> dict[str, str]:
    """A hermetic git environment: no system/global config, no ambient identity."""
    return {
        "HOME": str(home),
        "PATH": "/usr/bin:/bin",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_AUTHOR_NAME": "Test",
        "GIT_AUTHOR_EMAIL": "test@example.com",
        "GIT_COMMITTER_NAME": "Test",
        "GIT_COMMITTER_EMAIL": "test@example.com",
    }


def _main_repo_with_worktree(tmp_path: Path) -> tuple[Path, Path]:
    """A main checkout with one commit and a linked worktree off it. Returns (main, worktree)."""
    main = tmp_path / "main"
    main.mkdir()
    env = _git_env(tmp_path / "home")
    (tmp_path / "home").mkdir()
    subprocess.run(["git", "init", "-q"], cwd=main, check=True, env=env)
    (main / "README.md").write_text("x", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=main, check=True, env=env)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=main, check=True, env=env)
    worktree = tmp_path / "wt"
    subprocess.run(["git", "worktree", "add", "-q", "-b", "wt-branch", str(worktree)], cwd=main, check=True, env=env)
    return main, worktree


def test_worktree_falls_back_to_the_main_checkouts_pointer(tmp_path: Path) -> None:
    """A linked worktree has no .trw of its own; the resolver reaches the main checkout's pointer."""
    main, worktree = _main_repo_with_worktree(tmp_path)
    fake_py = _executable(tmp_path / "elsewhere" / "python3.14", "#!/bin/sh\nexit 0\n")
    pointer = main / HOOK_INTERPRETER_RELPATH
    pointer.parent.mkdir(parents=True)
    pointer.write_text(str(fake_py), encoding="utf-8")

    completed = _resolve(worktree, "/usr/bin:/bin")

    assert (completed.returncode, completed.stdout) == (0, str(fake_py))


def test_worktree_falls_back_to_the_main_checkouts_venv(tmp_path: Path) -> None:
    """No pointer anywhere: the worktree still finds the MAIN checkout's .venv, not its own (absent) one."""
    main, worktree = _main_repo_with_worktree(tmp_path)
    main_venv_py = _executable(main / ".venv" / "bin" / "python", "#!/bin/sh\nexit 0\n")

    completed = _resolve(worktree, "/usr/bin:/bin")

    assert (completed.returncode, completed.stdout) == (0, str(main_venv_py))


def test_non_worktree_git_repo_behaviour_is_unchanged(tmp_path: Path) -> None:
    """A plain (non-linked) checkout resolves exactly as before: the added step is a same-directory no-op."""
    env = _git_env(tmp_path / "home")
    (tmp_path / "home").mkdir()
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True, env=env)
    (repo / "README.md").write_text("x", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=repo, check=True, env=env)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True, env=env)

    completed = _resolve(repo, "/usr/bin:/bin")

    # No pointer, no trw-mcp on PATH, no .venv: the same terminal fallback as
    # before this fix (bare python3 on PATH) -- the added worktree step is a
    # same-directory no-op here, not a new resolution path.
    assert (completed.returncode, completed.stdout) == (0, "python3")


def test_post_commit_starts_the_trw_mcp_shebang_interpreter_without_a_pointer(tmp_path: Path) -> None:
    """The live hook, not the function in isolation: no cc03 file, a fake trw-mcp on PATH."""
    marker = tmp_path / "started"
    recorder = _executable(tmp_path / "venv" / "bin" / "python3", f'#!/bin/sh\ntouch "{marker}"\n')
    _executable(tmp_path / "bin" / "trw-mcp", f"#!{recorder}\n")
    hook = _DATA / "git_hooks" / "trw-post-commit.sh"

    completed = subprocess.run(
        ["/bin/sh", str(hook)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
        env={
            **daemon_env_passthrough(),
            "PATH": f"{tmp_path / 'bin'}:/usr/bin:/bin",
            "TRW_PROJECT_DIR": str(tmp_path),
            "TRW_POST_COMMIT_SYNC": "1",
        },
        timeout=60,
    )

    assert completed.returncode == 0
    assert marker.exists(), "the post-commit hook did not start the interpreter behind trw-mcp"


# --- the doctor row -----------------------------------------------------------


def test_doctor_passes_when_the_hooks_start_an_interpreter_that_imports_trw_mcp(tmp_path: Path) -> None:
    (tmp_path / HOOK_INTERPRETER_RELPATH).parent.mkdir(parents=True)
    (tmp_path / HOOK_INTERPRETER_RELPATH).write_text(sys.executable, encoding="utf-8")

    status, message = hook_python_row(tmp_path)

    assert (status, message) == ("PASS", f"the hooks start {sys.executable}, which imports trw_mcp.")


def test_the_path_fallback_python3_in_the_running_environment_counts_as_it(monkeypatch: pytest.MonkeyPatch) -> None:
    """Review dist-int-merge-2 r2 KI: the resolver's bare ``python3`` fallback, located on PATH in the
    running interpreter's own directory, is that environment (compared, never executed)."""
    from trw_mcp.server._doctor_hook_python import _same_environment

    running_dir = Path(sys.executable).parent
    if not (running_dir / "python3").exists():
        pytest.skip("the running environment has no python3 entry point")
    monkeypatch.setenv("PATH", f"{running_dir}:/bin:/usr/bin")

    assert _same_environment("python3")


def test_sibling_python_named_script_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """PRD-CORE-336-FR05 (KI dist-doctor-env): a sibling ``python*``-named executable in the running
    environment's own directory is NOT the running interpreter and must not PASS."""
    from trw_mcp.server._doctor_hook_python import _same_environment

    # A stand-in environment directory, so the test never writes into the real (shared) venv.
    env_bin = tmp_path / "bin"
    env_bin.mkdir()
    running = env_bin / "python3"
    running.symlink_to(sys.executable)
    evil = env_bin / "python-evil"
    evil.symlink_to(sys.executable)  # never executed: the comparison must reject it by name
    monkeypatch.setattr(sys, "executable", str(running))

    assert _same_environment(str(running))
    assert not _same_environment(str(evil))
    assert not _same_environment(str(env_bin / "python3\n"))


def test_the_running_environments_python_entry_points_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    """PRD-CORE-336-FR05: the running venv's own ``python``/``python3`` entry points still PASS."""
    from trw_mcp.server._doctor_hook_python import _same_environment

    running_dir = Path(sys.executable).parent
    found = False
    for name in ("python", "python3"):
        if (running_dir / name).exists():
            found = True
            assert _same_environment(str(running_dir / name))
    if not found:
        pytest.skip("the running environment has no python/python3 entry point beside sys.executable")


def test_doctor_fails_a_symlink_elsewhere_to_the_running_interpreter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A python3 symlink in another directory starts the BASE interpreter (pyvenv.cfg is found next to
    the invoked path, not the symlink target), which cannot import trw_mcp: it must not PASS."""
    (tmp_path / ".trw").mkdir()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "python3").symlink_to(sys.executable)
    monkeypatch.setenv("PATH", f"{bin_dir}:/bin:/usr/bin")

    status, _message = hook_python_row(tmp_path)

    assert status == "FAIL"


def test_doctor_never_runs_the_interpreter_a_checkout_names_and_fails_with_the_fix(tmp_path: Path) -> None:
    """Review dist-int-merge-2 r1 (P0): the pointer file is checkout-controlled, so doctor must not execute it."""
    marker = tmp_path / "executed"
    planted = _executable(tmp_path / "planted" / "python3", f'#!/bin/sh\ntouch "{marker}"\nexit 0\n')
    (tmp_path / HOOK_INTERPRETER_RELPATH).parent.mkdir(parents=True)
    (tmp_path / HOOK_INTERPRETER_RELPATH).write_text(str(planted), encoding="utf-8")

    status, message = hook_python_row(tmp_path)

    assert not marker.exists(), "doctor executed a program the checkout chose"
    assert status == "FAIL"
    assert f"the hooks start {planted}, not the interpreter running trw-mcp" in message
    assert "trw-mcp update-project" in message


_PRE_FIX_RESOLVER = '_get_python_path() {\n    printf "python3"\n}\n'


@pytest.mark.parametrize("deployed", [".claude/hooks/lib-distill-hint.sh", ".trw/hooks/trw-post-commit.sh"])
@pytest.mark.parametrize("current", [True, False])
def test_doctor_fails_a_deployed_hook_that_still_resolves_the_old_way(
    tmp_path: Path, deployed: str, current: bool
) -> None:
    """A PASS on the bundled lib says nothing about an installed hook that predates the fix."""
    marker = tmp_path / "sourced"
    body = _CLAUDE_LIB.read_text(encoding="utf-8") if current else _PRE_FIX_RESOLVER
    _executable(tmp_path / deployed, f'touch "{marker}"\n{body}')
    (tmp_path / HOOK_INTERPRETER_RELPATH).parent.mkdir(parents=True)
    (tmp_path / HOOK_INTERPRETER_RELPATH).write_text(sys.executable, encoding="utf-8")

    status, message = hook_python_row(tmp_path)

    if current:
        assert status == "PASS"
    else:
        assert (status, message.split(" still")[0]) == ("FAIL", deployed)
    assert not marker.exists(), "the doctor executed a hook file the project ships"


def test_doctor_skips_a_directory_without_trw(tmp_path: Path) -> None:
    assert hook_python_row(tmp_path)[0] == "SKIP"


# --- a linked worktree runs its OWN trw_mcp -----------------------------------
# The shared .venv's editable install imports the MAIN checkout's source, so a hint
# hook started from a worktree ran main's trw_mcp, not the code under test.


def _worktree_with_trw_source(tmp_path: Path) -> tuple[Path, Path]:
    main, worktree = _main_repo_with_worktree(tmp_path)
    for root, who in ((main, "main"), (worktree, "worktree")):
        pkg = root / "trw-mcp" / "src" / "trw_mcp_probe_pkg"
        pkg.mkdir(parents=True)
        (pkg / "__init__.py").write_text(f"WHO = {who!r}\n", encoding="utf-8")
    return main, worktree


def _worktree_pythonpath(project: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["/bin/sh", "-c", '. "$0" && _worktree_pythonpath "$1"', str(_CLAUDE_LIB), str(project)],
        capture_output=True,
        text=True,
        check=False,
        env={"PATH": "/usr/bin:/bin"},
        timeout=30,
    )


def test_worktree_hint_imports_worktree_trw_mcp(tmp_path: Path) -> None:
    """In a linked worktree the hint interpreter is pointed at the worktree's own source trees."""
    main, worktree = _worktree_with_trw_source(tmp_path)

    completed = _worktree_pythonpath(worktree)

    assert completed.returncode == 0
    assert completed.stdout == str(worktree / "trw-mcp" / "src")
    srcless = worktree / "srcless-pkg"
    srcless.mkdir()
    (srcless / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    (worktree / "docs").mkdir()  # neither src nor pyproject: never added
    assert _worktree_pythonpath(worktree).stdout == f"{srcless}:{worktree / 'trw-mcp' / 'src'}"
    completed = _worktree_pythonpath(worktree)
    imported = subprocess.run(
        [sys.executable, "-c", "import trw_mcp_probe_pkg as p; print(p.WHO)"],
        capture_output=True,
        text=True,
        check=True,
        env={"PATH": "/usr/bin:/bin", "PYTHONPATH": completed.stdout},
        timeout=30,
    )
    assert imported.stdout.strip() == "worktree"


def test_main_checkout_and_non_monorepo_worktree_add_no_pythonpath(tmp_path: Path) -> None:
    """Only a linked worktree that carries its own trw-mcp/src is redirected; everything else is untouched."""
    main, worktree = _worktree_with_trw_source(tmp_path)
    assert _worktree_pythonpath(main).stdout == ""
    bare = tmp_path / "bare"
    bare.mkdir()
    assert _worktree_pythonpath(bare).stdout == ""
    plain_worktree = tmp_path / "plain-wt"
    subprocess.run(
        ["git", "worktree", "add", "-q", "-b", "plain-branch", str(plain_worktree)],
        cwd=main,
        check=True,
        env=_git_env(tmp_path / "home"),
    )
    assert not (plain_worktree / "trw-mcp").exists()  # uncommitted in main, so this worktree has no source tree
    assert _worktree_pythonpath(plain_worktree).stdout == ""


def test_the_distill_hint_hooks_hand_the_worktree_path_to_their_interpreter() -> None:
    """The wiring: both hook scripts export what _worktree_pythonpath prints as PYTHONPATH."""
    for hook in (
        _DATA / "claude_code" / "hooks" / "pre-tool-distill-hint.sh",
        _DATA / "hooks" / "cursor" / "trw-before-edit-hint.sh",
    ):
        text = hook.read_text(encoding="utf-8")
        assert '_worktree_pythonpath "' in text, str(hook)
        assert 'PYTHONPATH="$_wt_pythonpath' in text, str(hook)
