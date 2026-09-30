"""SWAP-VENV-GC: ``trw-mcp env gc`` removes only version venvs no env uses, and keeps everything it cannot prove.

Deletion rules (tests/AGENTS.md "Changes that delete ..."): positive proof of ownership from bytes just read, a
symlink is never followed or removed, an unreadable file means keep, the act re-checks under the lock (race), and
every run ends with a whole-tree assertion that the kept bytes are still there.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests._fs_hazards import assert_user_bytes_preserved, snapshot_user_bytes, unreadable

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(sys.platform == "win32", reason="POSIX flock and symlinks"),
]

ENV = "canary"


def _paths(tmp_path: Path):
    from trw_mcp.shared_server._records import SharedPaths

    root = tmp_path / "runtime" / "shared-mcp"
    root.mkdir(parents=True)
    return SharedPaths(root=root, token=root / "token", envs_dir=tmp_path / "envs")


def _venv(paths, name: str, *, cfg: str | None = "home = /usr/bin\nversion = 3.14\n") -> Path:
    venv = paths.envs_dir / ENV / name
    (venv / "bin").mkdir(parents=True)
    (venv / "lib" / "site.py").parent.mkdir(parents=True)
    (venv / "lib" / "site.py").write_text("# venv payload\n", encoding="utf-8")
    if cfg is not None:
        (venv / "pyvenv.cfg").write_text(cfg, encoding="utf-8")
    return venv


def _decisions(paths, keep: int = 2) -> dict[str, tuple[bool, str]]:
    from trw_mcp.shared_server._gc import plan_gc

    return {d.path.name: (d.remove, d.reason) for d in plan_gc(paths, ENV, keep=keep)}


def test_plan_keeps_newest_versions_their_forks_and_referenced_venvs(tmp_path: Path) -> None:
    from trw_mcp.shared_server._records import set_env_python

    paths = _paths(tmp_path)
    for name in (
        "venv-8.0.0.dev9",
        "venv-8.0.0.dev10",
        "venv-8.0.0.dev10+distill-0.9.1.dev2",
        "venv-8.0.0.dev11",
        "venv-8.0.0.dev12",
        "venv-8.0.0.dev12+distill-0.9.1.dev3",
    ):
        _venv(paths, name)
    (paths.envs_dir / ENV / "memory").mkdir()
    (paths.envs_dir / ENV / "venv-8.0.0.dev9.lock").write_text("", encoding="utf-8")
    set_env_python(paths, "other-env", paths.envs_dir / ENV / "venv-8.0.0.dev9" / "bin" / "python")

    plan = _decisions(paths, keep=2)

    removable = sorted(name for name, (remove, _) in plan.items() if remove)
    assert removable == ["venv-8.0.0.dev10", "venv-8.0.0.dev10+distill-0.9.1.dev2"]  # dev10 < dev11 < dev12
    assert plan["venv-8.0.0.dev12+distill-0.9.1.dev3"] == (False, "kept: among the newest 2 version(s)")
    assert plan["venv-8.0.0.dev9"][1] == "kept: envs.json points an env at it"  # oldest, but another env uses it
    assert "memory" not in plan and "venv-8.0.0.dev9.lock" not in plan  # not version venvs: never judged


def test_the_live_servers_version_is_kept(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from trw_mcp.shared_server import _gc

    paths = _paths(tmp_path)
    for name in ("venv-1.0.0", "venv-2.0.0", "venv-3.0.0", "venv-4.0.0"):
        _venv(paths, name)
    monkeypatch.setattr(_gc, "read_live_record", lambda *_a: SimpleNamespace(version="1.0.0"))

    plan = _decisions(paths, keep=2)

    assert plan["venv-1.0.0"] == (False, f"kept: {ENV}'s live server runs 1.0.0")
    assert plan["venv-2.0.0"][0] is True


def test_an_unparseable_live_version_keeps_every_venv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from trw_mcp.shared_server import _gc

    paths = _paths(tmp_path)
    for name in ("venv-1.0.0", "venv-2.0.0", "venv-3.0.0"):
        _venv(paths, name)
    monkeypatch.setattr(_gc, "read_live_record", lambda *_a: SimpleNamespace(version="not-a-version"))

    assert not any(remove for remove, _ in _decisions(paths, keep=1).values())


@pytest.mark.parametrize(
    ("setup", "reason"),
    [
        ("no-cfg", "kept: pyvenv.cfg unreadable (FileNotFoundError), so not provably a venv"),
        ("no-home", "kept: pyvenv.cfg has no home line, so not provably a venv"),
        ("symlink", "kept: not a real directory (a symlink or a file is never followed or removed)"),
        ("bad-version", "kept: version does not parse"),
    ],
)
def test_anything_not_provably_an_unused_venv_is_kept(tmp_path: Path, setup: str, reason: str) -> None:
    paths = _paths(tmp_path)
    for name in ("venv-3.0.0", "venv-4.0.0"):
        _venv(paths, name)
    outside = tmp_path / "outside-venv"
    if setup == "no-cfg":
        _venv(paths, "venv-1.0.0", cfg=None)
    elif setup == "no-home":
        _venv(paths, "venv-1.0.0", cfg="version = 3.14\n")
    elif setup == "symlink":
        outside.mkdir()
        (outside / "pyvenv.cfg").write_text("home = /usr/bin\n", encoding="utf-8")
        os.symlink(outside, paths.envs_dir / ENV / "venv-1.0.0")
    else:
        _venv(paths, "venv-banana")
    name = "venv-banana" if setup == "bad-version" else "venv-1.0.0"

    assert _decisions(paths, keep=2)[name] == (False, reason)


def test_unreadable_pyvenv_cfg_is_kept(tmp_path: Path) -> None:
    """tests/AGENTS.md rule 3: a read failure is never proof of ownership."""
    paths = _paths(tmp_path)
    for name in ("venv-3.0.0", "venv-4.0.0"):
        _venv(paths, name)
    old = _venv(paths, "venv-1.0.0")

    with unreadable(old / "pyvenv.cfg"):
        decision = _decisions(paths, keep=2)["venv-1.0.0"]

    assert decision == (False, "kept: pyvenv.cfg unreadable (PermissionError), so not provably a venv")


def test_dry_run_removes_nothing_and_apply_removes_only_the_unused(tmp_path: Path) -> None:
    from trw_mcp.shared_server._gc import run_gc

    paths = _paths(tmp_path)
    for name in ("venv-1.0.0", "venv-2.0.0", "venv-3.0.0", "venv-4.0.0"):
        _venv(paths, name)
    _venv(paths, "venv-0.9.0", cfg=None)  # old but not provably a venv: must survive --apply
    env_dir = paths.envs_dir / ENV
    before = snapshot_user_bytes(env_dir)

    dry = run_gc(paths, ENV, keep=2, apply=False)

    assert snapshot_user_bytes(env_dir) == before
    assert sorted(line for line in dry if line.startswith("would remove")) == [
        "would remove venv-1.0.0: unused: no env record references it",
        "would remove venv-2.0.0: unused: no env record references it",
    ]

    applied = run_gc(paths, ENV, keep=2, apply=True)

    assert sorted(line for line in applied if line.startswith("removed")) == [
        "removed venv-1.0.0",
        "removed venv-2.0.0",
    ]
    assert sorted(p.name for p in env_dir.iterdir() if not p.name.endswith(".lock")) == [
        "venv-0.9.0",
        "venv-3.0.0",
        "venv-4.0.0",
    ]
    kept = {
        digest: names
        for digest, names in before.items()
        if not any(n.startswith(("venv-1.", "venv-2.")) for n in names)
    }
    assert_user_bytes_preserved(kept, env_dir)


def test_a_venv_held_by_a_swap_is_kept_as_in_use(tmp_path: Path) -> None:
    import fcntl

    from trw_mcp.shared_server._gc import run_gc

    paths = _paths(tmp_path)
    for name in ("venv-1.0.0", "venv-3.0.0", "venv-4.0.0"):
        _venv(paths, name)
    with (paths.envs_dir / ENV / "venv-1.0.0.lock").open("a") as held:
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        lines = run_gc(paths, ENV, keep=2, apply=True)

    assert any(line.startswith("keep    venv-1.0.0: in use") for line in lines)
    assert (paths.envs_dir / ENV / "venv-1.0.0" / "lib" / "site.py").is_file()


def test_an_env_pointed_at_the_venv_after_the_plan_keeps_it(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """tests/AGENTS.md rule 1: the act re-checks under the lock; a swap landing between plan and act wins."""
    from trw_mcp.shared_server import _gc, _ops
    from trw_mcp.shared_server._records import set_env_python

    paths = _paths(tmp_path)
    for name in ("venv-1.0.0", "venv-3.0.0", "venv-4.0.0"):
        _venv(paths, name)
    real_lock = _ops._venv_lock
    fired: list[bool] = []

    def racing_lock(venv: Path):
        set_env_python(paths, ENV, venv / "bin" / "python")  # the interloper: a swap points ENV at it
        fired.append(True)
        return real_lock(venv)

    monkeypatch.setattr(_ops, "_venv_lock", racing_lock)

    lines = _gc.run_gc(paths, ENV, keep=2, apply=True)

    assert fired
    assert "keep    venv-1.0.0: an env now points at it" in lines
    assert (paths.envs_dir / ENV / "venv-1.0.0" / "lib" / "site.py").is_file()


def test_keep_below_one_is_refused(tmp_path: Path) -> None:
    from trw_mcp.shared_server._gc import plan_gc
    from trw_mcp.shared_server._records import SharedServerError

    with pytest.raises(SharedServerError, match="--keep"):
        plan_gc(_paths(tmp_path), ENV, keep=0)


def test_env_gc_is_wired_into_the_cli() -> None:
    from trw_mcp.shared_server._cli import add_shared_subcommands

    parser = argparse.ArgumentParser()
    add_shared_subcommands(parser.add_subparsers(dest="command"))
    args = parser.parse_args(["env", "gc", "--env", ENV, "--keep", "3", "--apply"])

    assert (args.env_command, args.env, args.keep, args.apply) == ("gc", ENV, 3, True)
    assert parser.parse_args(["env", "gc"]).apply is False  # dry run by default


def test_a_fork_is_kept_while_its_base_venv_lock_is_held(tmp_path: Path) -> None:
    """codex r1 on W4-swap-venv-gc: build_version_venv holds venv-<V>.lock while building venv-<V>+distill-Y."""
    import fcntl

    from trw_mcp.shared_server._gc import run_gc

    paths = _paths(tmp_path)
    for name in ("venv-1.0.0+distill-0.1", "venv-3.0.0", "venv-4.0.0"):
        _venv(paths, name)
    with (paths.envs_dir / ENV / "venv-1.0.0.lock").open("a") as held:
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        lines = run_gc(paths, ENV, keep=2, apply=True)

    assert any(line.startswith("keep    venv-1.0.0+distill-0.1: in use") for line in lines)
    assert (paths.envs_dir / ENV / "venv-1.0.0+distill-0.1" / "lib" / "site.py").is_file()


@pytest.mark.parametrize("replacement", ["file", "symlink", "directory"])
def test_a_venv_replaced_after_its_ownership_proof_is_never_removed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, replacement: str
) -> None:
    """codex r1 P0 on W4-swap-venv-gc: swap the proven venv for user bytes between the proof and the removal."""
    import shutil

    from trw_mcp.shared_server import _gc

    paths = _paths(tmp_path)
    for name in ("venv-1.0.0", "venv-3.0.0", "venv-4.0.0"):
        _venv(paths, name)
    target = paths.envs_dir / ENV / "venv-1.0.0"
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "user.txt").write_text("user bytes\n", encoding="utf-8")
    real_proof = _gc._cfg_proof_at
    fired: list[bool] = []

    def swapping_proof(dir_fd: int) -> str | None:
        verdict = real_proof(dir_fd)
        shutil.rmtree(target)  # the interloper: the proven venv is replaced right after the proof
        if replacement == "file":
            target.write_text("user bytes\n", encoding="utf-8")
        elif replacement == "symlink":
            os.symlink(outside, target)
        else:
            target.mkdir()
            (target / "user.txt").write_text("user bytes\n", encoding="utf-8")
        fired.append(True)
        return verdict

    monkeypatch.setattr(_gc, "_cfg_proof_at", swapping_proof)
    before = snapshot_user_bytes(tmp_path)

    lines = _gc.run_gc(paths, ENV, keep=2, apply=True)

    assert fired
    assert any(line.startswith("emptied venv-1.0.0, but its name now holds something else") for line in lines)
    assert os.path.lexists(target)
    assert (outside / "user.txt").read_text(encoding="utf-8") == "user bytes\n"
    assert_user_bytes_preserved(
        {d: n for d, n in before.items() if any("user" in x or "venv-3" in x or "venv-4" in x for x in n)}, tmp_path
    )


def test_links_inside_a_venv_are_removed_without_touching_their_targets(tmp_path: Path) -> None:
    """codex r2 on W4-swap-venv-gc: removal walks the proven directory by descriptor and never follows a link."""
    from trw_mcp.shared_server._gc import run_gc

    paths = _paths(tmp_path)
    for name in ("venv-3.0.0", "venv-4.0.0"):
        _venv(paths, name)
    old = _venv(paths, "venv-1.0.0")
    outside = tmp_path / "outside"
    (outside / "dir").mkdir(parents=True)
    (outside / "dir" / "keep.txt").write_text("user bytes\n", encoding="utf-8")
    (outside / "file.txt").write_text("user file\n", encoding="utf-8")
    os.symlink(outside / "dir", old / "lib" / "linked-dir")
    os.symlink(outside / "file.txt", old / "linked-file")
    before = snapshot_user_bytes(outside)

    lines = run_gc(paths, ENV, keep=2, apply=True)

    assert "removed venv-1.0.0" in lines
    assert not os.path.lexists(old)
    assert snapshot_user_bytes(outside) == before


def test_trw_mcp_env_gc_runs_end_to_end_through_the_real_cli(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """``trw-mcp env gc`` argv -> ``main()`` -> SUBCOMMAND_HANDLERS["env"] -> ``run_env`` -> ``run_gc``."""
    from trw_mcp.server._cli import main
    from trw_mcp.shared_server import _cli

    paths = _paths(tmp_path)
    for name in ("venv-1.0.0", "venv-2.0.0", "venv-3.0.0"):
        _venv(paths, name)
    env_dir = paths.envs_dir / ENV
    monkeypatch.setattr(_cli, "_paths", lambda: (paths, None, paths.root.parents[2]))
    monkeypatch.chdir(tmp_path)

    def trw_mcp_env_gc(*extra: str) -> str:
        monkeypatch.setattr(sys, "argv", ["trw-mcp", "env", "gc", "--env", ENV, *extra])
        try:
            main()
        except SystemExit as exc:
            assert exc.code in (None, 0), capsys.readouterr().err
        return capsys.readouterr().out

    dry = trw_mcp_env_gc()

    assert "would remove venv-1.0.0: unused: no env record references it" in dry
    assert (env_dir / "venv-1.0.0").is_dir()  # the default is a dry run

    applied = trw_mcp_env_gc("--apply")

    assert "removed venv-1.0.0" in applied
    assert sorted(p.name for p in env_dir.iterdir() if not p.name.endswith(".lock")) == ["venv-2.0.0", "venv-3.0.0"]


def test_env_create_says_exists_for_an_existing_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """INC-126 (e): a second `env create` repeated 'created' for an env that already existed."""
    import argparse

    from trw_mcp.shared_server import _cli, _ops, _records

    def memory_dir(_paths: object, env: str) -> Path:
        return tmp_path / "envs" / env

    def ensure(_paths: object, env: str, *, seed_from: str | None) -> Path:
        memory_dir(_paths, env).mkdir(parents=True, exist_ok=True)
        return memory_dir(_paths, env)

    monkeypatch.setattr(_cli, "_paths", lambda: (object(), None, tmp_path))
    monkeypatch.setattr(_ops, "_memory_dir", memory_dir)
    monkeypatch.setattr(_ops, "ensure_env", ensure)
    monkeypatch.setattr(_records, "serving_env_path", lambda _p, env: tmp_path / f"{env}.serving")
    args = argparse.Namespace(env_command="create", name="canary", seed_from=None)
    _cli.run_env(args)
    _cli.run_env(args)
    out = capsys.readouterr().out.splitlines()
    assert out[0].startswith("created ") and out[1].startswith("exists ")
