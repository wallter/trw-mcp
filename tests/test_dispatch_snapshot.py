"""PRD-CORE-297-FR03: a standalone snapshot of the caller's tree, with its own .git.

Real git in ``tmp_path``. The caller-side git environment is hostile on purpose:
an exported ``GIT_DIR`` must not redirect a single snapshot git command into the
caller's repository (grok v1.2 P0).
"""

from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path

import pytest

_ENV = {"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull, "HOME": os.devnull}


def _git(repo: Path, *args: str) -> str:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")} | _ENV
    ident = ("-c", "user.name=t", "-c", "user.email=t@t")
    return subprocess.run(["git", *ident, *args], cwd=repo, env=env, check=True, capture_output=True, text=True).stdout


def _repo(path: Path) -> Path:
    path.mkdir()
    _git(path, "init", "-q")
    (path / ".gitignore").write_text("ignored.log\n", encoding="utf-8")
    (path / "tracked.py").write_text("v1\n", encoding="utf-8")
    (path / "gone.py").write_text("x\n", encoding="utf-8")
    (path / ".grok").mkdir()
    (path / ".grok" / "config.toml").write_text("[mcp]\n", encoding="utf-8")
    _git(path, "add", "-A")
    _git(path, "commit", "-q", "-m", "init")
    return path


def _digest(root: Path) -> str:
    h = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        h.update(str(path.relative_to(root)).encode() + b"\0" + path.read_bytes())
    return h.hexdigest()


@pytest.fixture
def caller(tmp_path: Path) -> Path:
    repo = _repo(tmp_path / "caller")
    (repo / "tracked.py").write_text("v2 uncommitted\n", encoding="utf-8")
    (repo / "new.py").write_text("untracked\n", encoding="utf-8")
    (repo / "ignored.log").write_text("secret\n", encoding="utf-8")
    (repo / "gone.py").unlink()
    return repo


def test_the_snapshot_holds_the_callers_live_tree_minus_ignored_and_stripped(caller: Path) -> None:
    from trw_mcp.dispatch._snapshot import standalone_snapshot

    with standalone_snapshot(caller, strip_paths=(".grok/config.toml",)) as snap:
        assert (snap.root / "tracked.py").read_text(encoding="utf-8") == "v2 uncommitted\n"
        assert (snap.root / "new.py").is_file()
        assert not (snap.root / "ignored.log").exists()
        assert not (snap.root / "gone.py").exists()
        assert not (snap.root / ".grok" / "config.toml").exists()
        assert snap.diff() == [], "the stripping and the copy are the baseline, not contamination"


@pytest.mark.parametrize("hostile_git_dir", ["caller", "other"])
def test_snapshot_shares_no_git_dir_with_caller(
    caller: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, hostile_git_dir: str
) -> None:
    from trw_mcp.dispatch._snapshot import standalone_snapshot

    other = _repo(tmp_path / "other")
    target = caller if hostile_git_dir == "caller" else other
    monkeypatch.setenv("GIT_DIR", str(target / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(target))
    monkeypatch.setenv("GIT_INDEX_FILE", str(target / ".git" / "index"))
    before = {repo: (_digest(repo / ".git"), _digest(repo)) for repo in (caller, other)}

    with standalone_snapshot(caller) as snap:
        assert (snap.root / ".git").is_dir()
        assert snap.root not in (caller, other)
        (snap.root / "tracked.py").write_text("child write\n", encoding="utf-8")
        assert snap.diff() == ["tracked.py"]

    assert {repo: (_digest(repo / ".git"), _digest(repo)) for repo in (caller, other)} == before
    assert not snap.root.exists() and not snap.home.exists()


def test_diff_names_created_deleted_ref_and_config_changes(caller: Path) -> None:
    from trw_mcp.dispatch._snapshot import standalone_snapshot

    with standalone_snapshot(caller) as snap:
        (snap.root / "created.txt").write_text("x", encoding="utf-8")
        (snap.root / "new.py").unlink()
        _git(snap.root, "branch", "sneaky")
        _git(snap.root, "config", "core.hooksPath", "/tmp/evil")
        assert snap.diff() == ["config", "created.txt", "new.py", "refs"]


def test_a_caller_head_move_during_the_run_is_contamination(caller: Path) -> None:
    from trw_mcp.dispatch._snapshot import standalone_snapshot

    with standalone_snapshot(caller) as snap:
        _git(caller, "add", "-A")
        _git(caller, "commit", "-q", "-m", "moved")
        assert {"caller:HEAD", "caller:index"} <= set(snap.diff())


def test_teardown_runs_when_the_body_raises(caller: Path) -> None:
    from trw_mcp.dispatch._snapshot import standalone_snapshot

    with pytest.raises(RuntimeError), standalone_snapshot(caller) as snap:
        raise RuntimeError("child crashed")
    assert not snap.root.exists()


def test_a_write_to_a_caller_working_tree_file_is_contamination(caller: Path) -> None:
    """The caller's git metadata can stay still while its files change (codex core297c P1)."""
    from trw_mcp.dispatch._snapshot import standalone_snapshot

    with standalone_snapshot(caller) as snap:
        (caller / "tracked.py").write_text("v2 uncommitted, then rewritten by the child\n", encoding="utf-8")
        assert snap.diff() == ["caller:worktree"]


def test_links_that_escape_the_snapshot_are_not_copied(caller: Path, tmp_path: Path) -> None:
    """Confinement allows reads, so a link out of the snapshot is a read path to the caller or HOME."""
    from trw_mcp.dispatch._snapshot import standalone_snapshot

    secret = tmp_path / "secret.txt"
    secret.write_text("token\n", encoding="utf-8")
    (caller / "abs-out").symlink_to(secret)
    (caller / "rel-out").symlink_to(Path("..") / "secret.txt")
    (caller / "to-caller").symlink_to(caller / "tracked.py")
    (caller / "chain").symlink_to("rel-out")
    (caller / "inside").symlink_to("tracked.py")

    with standalone_snapshot(caller) as snap:
        assert (snap.root / "inside").read_text(encoding="utf-8") == "v2 uncommitted\n"
        for name in ("abs-out", "rel-out", "to-caller", "chain"):
            assert not (snap.root / name).is_symlink(), name
        assert snap.dropped_links == ["abs-out", "chain", "rel-out", "to-caller"]
        assert snap.diff() == []
