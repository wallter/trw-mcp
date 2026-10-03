"""PRUNED-DIR-GIT-PROBE-FOLLOWS-SYMLINKS: a ``.git`` marker or a symlink never exempts a MANAGED dir.

``_is_pruned_nested_dir`` treated any dir holding a ``.git`` (file or dir, probed THROUGH symlinks) as a
nested repo, so a marker planted in ``.claude/hooks`` dropped that managed dir from the snapshot and the
symlink guard while update's writers still wrote into it: the user's dirty edit was overwritten with no
rollback copy, and a symlink inside escaped the guard. Runs are out of process (production paths).
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
import yaml
from trw_memory.testing.daemon_reaper import daemon_env_passthrough, reap_daemons_under

from tests._fs_hazards import (
    assert_user_bytes_preserved,
    race_after,
    snapshot_user_bytes,
    swap_to_symlink,
    unreadable,
    unreadable_parent,
)
from tests.test_update_project_worktrees_symlink import _NO_HOOKS, _git, _subtree

pytestmark = [pytest.mark.integration, pytest.mark.slow, pytest.mark.timeout(600)]

_RUNNER = """
import json, sys
from pathlib import Path
from trw_mcp.bootstrap import init_project, update_project
target, mode = Path(sys.argv[1]), sys.argv[2]
if mode == "init":
    print(json.dumps(init_project(target, ide=sys.argv[3] if len(sys.argv) > 3 else "claude-code")))
    sys.exit(0)
if mode == "fail":  # a write failure after every writer ran: forces the rollback path
    import trw_mcp.bootstrap._update_project as up
    def boom(*a, **k):
        raise OSError("forced write failure")
    up._verify_installation = boom
result = update_project(target)
print(json.dumps(result))
sys.exit(1 if result["errors"] else 0)
"""


def _run(target: Path, mode: str, home: Path, ide: str = "claude-code") -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("TRW_")} | daemon_env_passthrough()
    env.update(
        HOME=str(home),
        XDG_DATA_HOME=str(home / ".local" / "share"),
        TRW_EMBEDDINGS_ENABLED="false",
        MEMORY_DAEMON_AUTOSTART="false",
    )
    return subprocess.run(
        [sys.executable, "-c", _RUNNER, str(target), mode, ide],
        capture_output=True,
        text=True,
        env=env,
        cwd=target,
        check=False,
    )


@pytest.fixture(scope="module")
def workspace(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    root = tmp_path_factory.mktemp("git-probe")
    yield root
    reap_daemons_under(root, wait=True, by_process=True)
    shutil.rmtree(root, ignore_errors=True)


_BASES: dict[str, Path] = {}


def _base(workspace: Path, ide: str) -> Path:
    """One committed, twice-updated project per client (cached for the module)."""
    if ide not in _BASES:
        home = workspace / "home"
        home.mkdir(exist_ok=True)
        project = workspace / f"base-{ide}"
        project.mkdir()
        _git(project, "init", "-q")
        assert _run(project, "init", home, ide).returncode == 0
        for _ in range(2):
            assert _run(project, "real", home).returncode == 0
            _git(project, "add", "-A")
        _git(project, *_NO_HOOKS, "commit", "-qm", "base")
        _BASES[ide] = project
    return _BASES[ide]


@pytest.fixture
def base_project(workspace: Path) -> Path:
    return _base(workspace, "claude-code")


def _copy(workspace: Path, base: Path, name: str) -> Path:
    project = workspace / name
    shutil.copytree(base, project, symlinks=True)
    return project


def _outside(workspace: Path, name: str, *, with_git: bool = False) -> Path:
    out = workspace / name
    out.mkdir()
    (out / "sentinel.txt").write_text("outside\n", encoding="utf-8")
    if with_git:
        (out / ".git").mkdir()
        (out / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    return out


def _dirty_hook(project: Path) -> tuple[Path, bytes]:
    hook = project / ".claude" / "hooks" / "session-start.sh"
    hook.write_text(hook.read_text(encoding="utf-8") + "\n# user edit\n", encoding="utf-8")
    return hook, hook.read_bytes()


def _user_bytes(project: Path) -> dict[str, list[str]]:
    return {
        d: [n for n in names if n.endswith("session-start.sh")]
        for d, names in snapshot_user_bytes(project).items()
        if any(n.endswith("session-start.sh") for n in names)
    }


def _plant_marker(hooks: Path, kind: str) -> None:
    if kind == "file":
        (hooks / ".git").write_text("gitdir: /nonexistent\n", encoding="utf-8")
    else:
        (hooks / ".git").mkdir()
        (hooks / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")


def _plant_submodule(path: Path) -> None:
    (path / ".git").write_text("gitdir: ../../../.git/modules/shared\n", encoding="utf-8")


def _refused(proc: subprocess.CompletedProcess[str], *needles: str) -> None:
    out = proc.stderr + proc.stdout
    assert proc.returncode != 0, out[-2000:]
    for needle in needles:
        assert needle in out, f"{needle!r} not in output: {out[-1500:]}"


@pytest.mark.parametrize("kind", ["file", "dir"])
def test_git_marker_in_owned_hooks_dir_refuses_update_and_changes_nothing(
    workspace: Path, base_project: Path, kind: str
) -> None:
    """A marker in a TRW-owned dir may be a user repo: refuse (naming it) instead of writing into it."""
    project = _copy(workspace, base_project, f"marker-{kind}")
    hooks = project / ".claude" / "hooks"
    _plant_marker(hooks, kind)
    hook, edited = _dirty_hook(project)
    before, user_bytes = _subtree(hooks), _user_bytes(project)

    proc = _run(project, "real", workspace / "home")

    _refused(proc, ".claude/hooks", ".git marker")
    assert _subtree(hooks) == before
    assert hook.read_bytes() == edited
    assert_user_bytes_preserved(user_bytes, project)


def test_user_clone_named_like_a_canonical_skill_is_refused_not_written_into(
    workspace: Path, base_project: Path
) -> None:
    project = _copy(workspace, base_project, "skill-clone")
    skill = project / ".claude" / "skills" / "trw-deliver"
    _plant_marker(skill, "dir")
    (skill / "user-notes.txt").write_text("my repo file\n", encoding="utf-8")
    (skill / "SKILL.md").write_text("user rewrite\n", encoding="utf-8")
    before, user_bytes = _subtree(skill), snapshot_user_bytes(skill)

    proc = _run(project, "real", workspace / "home")

    _refused(proc, ".claude/skills/trw-deliver", ".git marker")
    assert _subtree(skill) == before
    assert_user_bytes_preserved(user_bytes, skill)


def test_deeper_submodule_is_pruned_but_any_other_deeper_marker_fails_closed(
    workspace: Path, base_project: Path
) -> None:
    """A: deeper in an owned dir only a clear submodule checkout is pruned; any other marker is refused."""
    project = _copy(workspace, base_project, "subtree")
    agents = project / ".claude" / "agents"
    sub = agents / "team"
    sub.mkdir()
    (sub / "ln").symlink_to(_outside(workspace, "outside-subtree-sub"))

    unmarked = _run(project, "real", workspace / "home")
    _refused(unmarked, "symlink")  # an unmarked subdir is still scanned by the guard
    (sub / "ln").unlink()
    _plant_marker(sub, "dir")
    _refused(_run(project, "real", workspace / "home"), ".claude/agents/team", ".git marker")  # not a submodule
    shutil.rmtree(sub / ".git")
    forged = _outside(workspace, "forged-super") / ".git" / "modules" / "team"
    forged.mkdir(parents=True)
    (sub / ".git").write_text(f"gitdir: {forged}\n", encoding="utf-8")  # another repo's modules: forged
    _refused(_run(project, "real", workspace / "home"), ".claude/agents/team", ".git marker")
    (sub / ".git").unlink()
    _plant_submodule(sub)
    (sub / "ln").symlink_to(_outside(workspace, "outside-subtree-sub2"))  # pruned: never followed, left as is
    (sub / "member.md").write_text("team agent\n", encoding="utf-8")
    before, user_bytes = _subtree(sub), snapshot_user_bytes(sub)

    ok = _run(project, "real", workspace / "home")

    assert ok.returncode == 0, ok.stderr[-2000:] + ok.stdout[-1000:]
    assert _subtree(sub) == before
    assert_user_bytes_preserved(user_bytes, sub)
    (agents / ".git").write_text("gitdir: /nonexistent\n", encoding="utf-8")
    _refused(_run(project, "real", workspace / "home"), ".claude/agents", ".git marker")  # directly in it: refused


def test_team_rules_submodule_in_cursor_rules_is_pruned_and_trw_rules_still_written(workspace: Path) -> None:
    project = _copy(workspace, _base(workspace, "cursor-ide"), "cursor-submodule")
    rules = project / ".cursor" / "rules"
    shared = rules / "shared"
    shared.mkdir()
    _plant_submodule(shared)
    (shared / "team.mdc").write_text("team rule\n", encoding="utf-8")
    (shared / "ln").symlink_to(_outside(workspace, "outside-cursor-shared"))
    manifest = yaml.safe_load((project / ".trw" / "managed-artifacts.yaml").read_text(encoding="utf-8"))
    rel = next(k for k in manifest["content_hashes"] if k.startswith(".cursor/rules/"))
    stale = project / rel
    stale.write_bytes(stale.read_bytes() + b"\n# stale trw version\n")
    manifest["content_hashes"][rel] = hashlib.sha256(stale.read_bytes()).hexdigest()
    (project / ".trw" / "managed-artifacts.yaml").write_text(yaml.safe_dump(manifest), encoding="utf-8")
    stale_bytes, before = stale.read_bytes(), _subtree(shared)

    proc = _run(project, "real", workspace / "home")

    assert proc.returncode == 0, proc.stderr[-2000:] + proc.stdout[-1000:]
    assert _subtree(shared) == before
    assert stale.read_bytes() != stale_bytes, "TRW's own rule file was not refreshed"


def _stale_copilot_instruction(project: Path) -> tuple[Path, bytes]:
    """A TRW-recorded, older-than-bundled instruction file: update refreshes it."""
    rel = ".github/instructions/trw-ceremony.instructions.md"
    path = project / rel
    path.write_bytes(path.read_bytes() + b"\n<!-- stale trw version -->\n")
    manifest = project / ".trw" / "managed-artifacts.yaml"
    data = yaml.safe_load(manifest.read_text(encoding="utf-8"))
    data["content_hashes"][rel] = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest.write_text(yaml.safe_dump(data), encoding="utf-8")
    return path, path.read_bytes()


@pytest.mark.parametrize("kind", ["file", "dir"])
def test_failed_update_rolls_back_writes_into_github_instructions_dir_with_git_marker(
    workspace: Path, kind: str
) -> None:
    """F1: ``.github/instructions`` is only the PARENT of registered file surfaces, yet writers write into it."""
    project = _copy(workspace, _base(workspace, "copilot"), f"copilot-rollback-{kind}")
    instructions = project / ".github" / "instructions"
    _plant_marker(instructions, kind)
    stale, stale_bytes = _stale_copilot_instruction(project)
    user_bytes = {  # bytes outside .trw/, whose sync-cache the rollback drops on purpose
        d: kept
        for d, names in snapshot_user_bytes(project).items()
        if (kept := [n for n in names if not n.startswith(".trw/")])
    }

    proc = _run(project, "fail", workspace / "home")

    assert proc.returncode != 0
    assert stale.read_bytes() == stale_bytes, "a write into the marked managed dir survived the rollback"
    assert (instructions / ".git").exists()
    assert_user_bytes_preserved(user_bytes, project)


@pytest.mark.parametrize(
    ("ide", "managed"),
    [("copilot", ".github/hooks"), ("copilot", ".github/instructions"), ("antigravity-cli", ".agents/rules")],
)
@pytest.mark.parametrize("plant", ["symlink-inside", "git-symlink"])
def test_marker_in_writer_parent_dir_does_not_hide_symlinks(
    workspace: Path, ide: str, managed: str, plant: str
) -> None:
    """F1 (a)/(c) for copilot and antigravity: the symlink guard still scans the dir."""
    project = _copy(workspace, _base(workspace, ide), f"parent-{ide}-{managed.replace('/', '_')}-{plant}")
    outside = _outside(workspace, f"outside-parent-{ide}-{managed.replace('/', '_')}-{plant}", with_git=True)
    directory = project / managed
    if plant == "symlink-inside":
        _plant_marker(directory, "file")
    else:
        (directory / ".git").symlink_to(outside)
    (directory / "planted").symlink_to(outside)
    before, user_bytes = _subtree(outside), snapshot_user_bytes(project)

    proc = _run(project, "real", workspace / "home")

    _refused(proc, "symlink")
    assert _subtree(outside) == before
    assert_user_bytes_preserved(user_bytes, project)


def test_managed_set_covers_every_directory_a_writer_installs_into(workspace: Path) -> None:
    """PARITY: init every client, then every dir holding an installed file under the transaction roots is managed."""
    from trw_mcp.bootstrap import _update_transaction as ut

    project = workspace / "parity"
    project.mkdir()
    _git(project, "init", "-q")
    assert _run(project, "init", workspace / "home", "all").returncode == 0
    from trw_mcp.bootstrap import SUPPORTED_IDES

    assert {"antigravity-cli", "copilot", "codex", "grok", "opencode"} <= set(SUPPORTED_IDES)
    for expected in (".github/hooks", ".agents/rules", ".claude/hooks", ".cursor/rules"):
        assert (project / expected).is_dir(), f"init did not create {expected}: the parity walk would be vacuous"
    gaps: list[str] = []
    for rel in ut._TRANSACTION_DIRS:
        top = project / rel
        if not top.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(top, followlinks=False):
            dirnames[:] = [d for d in dirnames if d not in ut._PRUNED_NESTED_DIR_NAMES]
            base = Path(dirpath)
            if filenames and ut._managed_kind(base, project) is None:
                gaps.append(base.relative_to(project).as_posix())
    assert not gaps, f"dirs receiving installed files but not in the managed set: {gaps}"


@pytest.mark.parametrize("target_has_git", [True, False], ids=["git-checkout", "plain-dir"])
def test_user_skill_symlink_is_left_untouched_and_does_not_abort(
    workspace: Path, base_project: Path, target_has_git: bool
) -> None:
    """F4: a non-TRW-named symlinked skill (a user's own checkout) is never followed, written or refused."""
    tag = "git" if target_has_git else "plain"
    project = _copy(workspace, base_project, f"user-skill-link-{tag}")
    outside = _outside(workspace, f"outside-user-skill-{tag}", with_git=target_has_git)
    link = project / ".claude" / "skills" / "myskill"
    link.symlink_to(outside)
    hook, edited = _dirty_hook(project)
    before, user_bytes = _subtree(outside), _user_bytes(project)

    proc = _run(project, "real", workspace / "home")

    assert proc.returncode == 0, proc.stderr[-3000:] + proc.stdout[-1000:]
    assert _subtree(outside) == before
    assert link.is_symlink() and os.readlink(link) == str(outside)
    assert hook.read_bytes() == edited
    assert_user_bytes_preserved(user_bytes, project)


def test_user_skill_symlink_survives_a_rolled_back_update(workspace: Path, base_project: Path) -> None:
    project = _copy(workspace, base_project, "user-skill-link-rollback")
    outside = _outside(workspace, "outside-user-skill-rollback", with_git=True)
    link = project / ".claude" / "skills" / "myskill"
    link.symlink_to(outside)
    before = _subtree(outside)

    proc = _run(project, "fail", workspace / "home")

    assert proc.returncode != 0
    assert link.is_symlink() and os.readlink(link) == str(outside), "rollback dropped the user's skill link"
    assert _subtree(outside) == before


def test_canonical_named_skill_symlink_is_refused(workspace: Path, base_project: Path) -> None:
    project = _copy(workspace, base_project, "canonical-skill-link")
    outside = _outside(workspace, "outside-canonical-skill", with_git=True)
    skill = project / ".claude" / "skills" / "trw-deliver"
    shutil.rmtree(skill)
    skill.symlink_to(outside)
    before = _subtree(outside)

    proc = _run(project, "real", workspace / "home")

    _refused(proc, "symlink")
    assert _subtree(outside) == before
    assert skill.is_symlink()


def test_case_variant_managed_dir_name_is_still_managed(workspace: Path, base_project: Path) -> None:
    """F5: on a case-insensitive filesystem ``.claude/Hooks`` IS the hooks dir."""
    project = _copy(workspace, base_project, "case-variant")
    hooks = project / ".claude" / "hooks"
    os.rename(hooks, project / ".claude" / "Hooks")
    if not hooks.exists():
        pytest.skip("case-sensitive filesystem: Hooks is a different directory")  # skip-category: platform
    _plant_marker(project / ".claude" / "Hooks", "file")

    proc = _run(project, "real", workspace / "home")

    _refused(proc, ".git marker")


def test_git_marker_probe_surfaces_unreadable_dir_instead_of_reading_no_repo(tmp_path: Path) -> None:
    """tests/AGENTS.md rule 4 (and 3, unreadable parent): only FileNotFoundError means absent."""
    from trw_mcp.bootstrap._update_transaction import _has_git_marker, _is_pruned_nested_dir

    nested = tmp_path / "vendor" / "repo"
    (nested / ".git").mkdir(parents=True)
    with unreadable(nested):
        with pytest.raises(PermissionError):
            _is_pruned_nested_dir(nested, tmp_path)
        assert _is_pruned_nested_dir(nested, tmp_path, denied_is_marker=True) is True  # rollback: preserve
    with unreadable_parent(nested / ".git"):
        with pytest.raises(PermissionError):
            _has_git_marker(nested)
    assert _is_pruned_nested_dir(nested, tmp_path) is True


def test_rollback_removal_never_raises_on_an_unreadable_nested_dir_and_preserves_it(tmp_path: Path) -> None:
    """B: an EACCES probe mid-rollback preserves the dir instead of raising after partial removal."""
    from trw_mcp.bootstrap._update_transaction import _remove_transaction_path

    managed = tmp_path / "vendor" / "managed"
    managed.mkdir(parents=True)
    (managed / "trw-owned.txt").write_text("trw\n", encoding="utf-8")
    locked = managed / "locked"
    locked.mkdir()
    (locked / "user.txt").write_text("user bytes\n", encoding="utf-8")
    user_bytes = snapshot_user_bytes(locked)

    with unreadable(locked):
        _remove_transaction_path(managed, tmp_path)

    assert not (managed / "trw-owned.txt").exists()
    assert (locked / "user.txt").read_text(encoding="utf-8") == "user bytes\n"
    assert_user_bytes_preserved(user_bytes, tmp_path)


def test_git_marker_probe_survives_a_swap_to_symlink_between_checks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A dir swapped for a symlink to a repo just before the probe is never followed."""
    from trw_mcp.bootstrap._update_transaction import _is_pruned_nested_dir

    repo = tmp_path / "elsewhere"
    (repo / ".git").mkdir(parents=True)
    nested = tmp_path / "vendor" / "plain"
    nested.mkdir(parents=True)
    probe = race_after(
        monkeypatch, target=nested, op="open", when="before", interloper=lambda: swap_to_symlink(nested, repo)
    )

    assert _is_pruned_nested_dir(nested, tmp_path) is False
    assert probe.fired
    assert (repo / ".git").is_dir()


def test_symlinked_skills_dir_whose_target_has_git_is_refused(workspace: Path, base_project: Path) -> None:
    project = _copy(workspace, base_project, "skills-link")
    outside = _outside(workspace, "outside-skills-link", with_git=True)
    skills = project / ".claude" / "skills"
    shutil.rmtree(skills)
    skills.symlink_to(outside)
    hook, edited = _dirty_hook(project)
    before, user_bytes = _subtree(outside), _user_bytes(project)

    proc = _run(project, "real", workspace / "home")

    assert proc.returncode != 0
    assert "symlink" in proc.stderr + proc.stdout
    assert _subtree(outside) == before
    assert skills.is_symlink()
    assert hook.read_bytes() == edited
    assert_user_bytes_preserved(user_bytes, project)


def test_git_symlink_in_managed_dir_neither_prunes_it_nor_touches_target(workspace: Path, base_project: Path) -> None:
    project = _copy(workspace, base_project, "git-link")
    outside = _outside(workspace, "outside-git-link", with_git=True)
    hooks = project / ".claude" / "hooks"
    (hooks / ".git").symlink_to(outside)
    (hooks / "planted").symlink_to(outside)  # proves the dir is still scanned
    hook, edited = _dirty_hook(project)
    before, user_bytes = _subtree(outside), _user_bytes(project)

    proc = _run(project, "real", workspace / "home")

    assert proc.returncode != 0
    assert "symlink" in proc.stderr + proc.stdout
    assert _subtree(outside) == before
    assert hook.read_bytes() == edited
    assert_user_bytes_preserved(user_bytes, project)


def test_real_nested_git_worktree_under_claude_worktrees_is_still_pruned(workspace: Path, base_project: Path) -> None:
    project = _copy(workspace, base_project, "nested-still-pruned")
    nested = project / ".claude" / "worktrees" / "x"
    _git(project, "worktree", "add", "-q", "-b", "nested-git-probe", str(nested))
    (nested / "ln").symlink_to(_outside(workspace, "outside-nested-pruned"))
    hook, edited = _dirty_hook(project)
    before, user_bytes = _subtree(nested), _user_bytes(project)

    proc = _run(project, "real", workspace / "home")

    assert proc.returncode == 0, proc.stderr[-3000:] + proc.stdout[-1000:]
    assert _subtree(nested) == before
    assert hook.read_bytes() == edited
    assert_user_bytes_preserved(user_bytes, project)


def test_unmanaged_dir_with_git_marker_under_managed_root_is_still_pruned(workspace: Path, base_project: Path) -> None:
    project = _copy(workspace, base_project, "unmanaged-repo")
    repo = project / ".claude" / "my-tool"
    repo.mkdir()
    (repo / ".git").write_text("gitdir: /nonexistent\n", encoding="utf-8")
    (repo / "ln").symlink_to(_outside(workspace, "outside-unmanaged"))
    hook, edited = _dirty_hook(project)
    before, user_bytes = _subtree(repo), _user_bytes(project)

    proc = _run(project, "real", workspace / "home")

    assert proc.returncode == 0, proc.stderr[-3000:] + proc.stdout[-1000:]
    assert _subtree(repo) == before
    assert hook.read_bytes() == edited
    assert_user_bytes_preserved(user_bytes, project)


@pytest.mark.timeout(10)
def test_only_a_clear_submodule_of_this_project_counts_as_a_submodule(tmp_path: Path) -> None:
    """Fail closed: the gitdir must resolve under THIS project's own .git/modules; a forged traversal to
    another .git/modules, a worktree pointer, a .git dir or link, garbage or undecodable bytes is not."""
    from trw_mcp.bootstrap._managed_dirs import _is_submodule_checkout

    root = tmp_path / "project"
    (root / ".git" / "modules" / "sub").mkdir(parents=True)
    other = tmp_path / "other"
    (other / ".git" / "modules" / "y").mkdir(parents=True)
    cases = {
        "submodule": "gitdir: ../.git/modules/sub\n",
        "forged-traversal": "gitdir: ../../other/.git/modules/y\n",
        "forged-absolute": f"gitdir: {other / '.git' / 'modules' / 'y'}\n",
        "modules-itself": "gitdir: ../.git/modules\n",
        "worktree": "gitdir: ../.git/worktrees/wt\n",
        "garbage": "not a gitdir line\n",
    }
    for name, text in cases.items():
        (root / name).mkdir()
        (root / name / ".git").write_text(text, encoding="utf-8")
    (root / "repo" / ".git").mkdir(parents=True)
    (root / "linked").mkdir()
    (root / "linked" / ".git").symlink_to(root / "submodule" / ".git")
    (root / "binary").mkdir()
    (root / "binary" / ".git").write_bytes(b"gitdir: \xff\xfe/.git/modules/x\n")
    (root / "padded").mkdir()
    (root / "padded" / ".git").write_text("gitdir: " + "a/" * 3000 + "../.git/modules/sub\n", encoding="utf-8")

    fifo_cases = ("fifo",) if hasattr(os, "mkfifo") else ()  # no FIFOs on Windows
    for name in fifo_cases:
        (root / name).mkdir()
        os.mkfifo(root / name / ".git")  # must answer promptly, never block on open
    (root / "nul").mkdir()
    (root / "nul" / ".git").write_bytes(b"gitdir: ../.git/modules/s\x00ub\n")

    assert _is_submodule_checkout(root / "submodule", root) is True
    for name in (
        *(n for n in cases if n != "submodule"),
        "repo",
        "linked",
        "binary",
        "padded",
        *fifo_cases,
        "nul",
        "missing",
    ):
        assert _is_submodule_checkout(root / name, root) is False, name

    worktree_root = tmp_path / "wt-project"  # the project's own .git is a file: no modules dir of its own
    (worktree_root / "sub").mkdir(parents=True)
    (worktree_root / ".git").write_text(f"gitdir: {root / '.git'}\n", encoding="utf-8")
    (worktree_root / "sub" / ".git").write_text(f"gitdir: {root / '.git' / 'modules' / 'sub'}\n", encoding="utf-8")
    assert _is_submodule_checkout(worktree_root / "sub", worktree_root) is False


@pytest.mark.parametrize("nested", [".claude/agents/locked", ".claude/team-notes/locked"], ids=["owned", "unmanaged"])
def test_rollback_restore_preserves_an_unreadable_nested_dir_in_a_managed_root(tmp_path: Path, nested: str) -> None:
    """Production rollback (``_update_project._rollback``) over a managed root holding an unreadable
    user dir: TRW's own files are restored from the snapshot, the unreadable dir is kept, nothing raises."""
    from trw_mcp.bootstrap._update_project import _rollback

    target, snapshot = tmp_path / "project", tmp_path / "snapshot"
    for root in (target, snapshot):
        (root / ".claude" / "agents").mkdir(parents=True)
        (root / ".claude" / "agents" / "trw-lead.md").write_text("snapshotted\n", encoding="utf-8")
    (target / ".claude" / "agents" / "trw-lead.md").write_text("half-written by the failed update\n", encoding="utf-8")
    locked = target / nested
    locked.mkdir(parents=True)
    (locked / "user.txt").write_text("user bytes\n", encoding="utf-8")

    with unreadable(locked):
        _rollback(target, snapshot, {"warnings": [], "errors": []})

    assert (target / ".claude" / "agents" / "trw-lead.md").read_text(encoding="utf-8") == "snapshotted\n"
    assert (locked / "user.txt").read_text(encoding="utf-8") == "user bytes\n"
