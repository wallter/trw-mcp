"""Standalone snapshot of a caller's tree for the isolated-review lane (PRD-CORE-297-FR03).

Belongs to the ``trw_mcp.dispatch`` package. A ``git worktree`` shares the
caller's ``.git``, so a child that moves a ref or edits config in a worktree
changes the real checkout. This snapshot is a COPY of the caller's live tree
(tracked plus untracked, minus ignored) with its own freshly initialised
repository, so the only way back to the caller is an absolute-path write, and
``diff`` fingerprints the caller (git metadata and every non-ignored file's
bytes) so that case is reported, not hidden. A symlink whose target resolves
outside the snapshot is dropped: confinement denies writes, not reads, so such
a link would be a read path into the caller or the real HOME.

Every git command here runs with every inherited ``GIT_*`` variable stripped and
the system/global config off: an exported ``GIT_DIR`` would otherwise redirect
``init``/``add``/``commit`` into whatever repository it names (grok v1.2 P0).
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import tempfile
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

__all__ = ["Snapshot", "standalone_snapshot"]

_IDENTITY = ("-c", "user.name=trw-snapshot", "-c", "user.email=snapshot@trw.invalid", "-c", "commit.gpgsign=false")


def _git(env: dict[str, str], *args: str, cwd: Path | None = None) -> bytes:
    return subprocess.run(  # noqa: S603 - fixed argv, no shell
        ["git", *_IDENTITY, *args],  # noqa: S607 - git resolves from PATH like every repo git call
        cwd=cwd,
        env=env,
        check=True,
        capture_output=True,
    ).stdout


def _clean_env(home: Path) -> dict[str, str]:
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    return env | {"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull, "HOME": str(home)}


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _file_sha(path: Path) -> str:
    return _sha(path.read_bytes()) if path.is_file() else "absent"


def _listed(env: dict[str, str], caller: Path) -> list[str]:
    listed = _git(env, "-C", str(caller), "ls-files", "-co", "--exclude-standard", "-z")
    return sorted({name for name in listed.decode().split("\0") if name})


def _worktree_sha(env: dict[str, str], caller: Path) -> str:
    h = hashlib.sha256()
    for rel in _listed(env, caller):
        path = caller / rel
        body = os.readlink(path).encode() if path.is_symlink() else _file_sha(path).encode()
        h.update(rel.encode() + b"\0" + body + b"\0")
    return h.hexdigest()


def _caller_fingerprint(env: dict[str, str], caller: Path) -> dict[str, str]:
    """Read-only: none of these commands writes the caller's index or refs."""

    def git_path(name: str) -> Path:
        return caller / _git(env, "-C", str(caller), "rev-parse", "--git-path", name).decode().strip()

    return {
        "caller:HEAD": _git(env, "-C", str(caller), "rev-parse", "--verify", "-q", "HEAD").decode(),
        "caller:index": _file_sha(git_path("index")),
        "caller:refs": _sha(_git(env, "-C", str(caller), "for-each-ref", "--format=%(refname) %(objectname)")),
        "caller:config": _file_sha(git_path("config")),
        "caller:worktree": _worktree_sha(env, caller),
    }


@dataclass
class Snapshot:
    """A live snapshot; ``diff`` names everything that differs from the baseline."""

    root: Path
    home: Path
    caller: Path
    _env: dict[str, str]
    _caller_env: dict[str, str]
    dropped_links: list[str] = field(default_factory=list)
    _baseline: dict[str, str] = field(default_factory=dict)

    def _own_state(self) -> dict[str, str]:
        refs = _git(self._env, "for-each-ref", "--format=%(refname) %(objectname)", cwd=self.root)
        head = _git(self._env, "rev-parse", "HEAD", cwd=self.root)
        return {"refs": _sha(refs + head), "config": _file_sha(self.root / ".git" / "config")}

    def _state(self) -> dict[str, str]:
        return self._own_state() | _caller_fingerprint(self._caller_env, self.caller)

    def diff(self) -> list[str]:
        """Changed snapshot paths, plus ``refs``/``config``/``caller:*`` keys that moved."""
        status = _git(self._env, "--no-optional-locks", "status", "--porcelain=v1", "-z", "-uall", "--ignored")
        entries = [entry for entry in status.decode().split("\0") if entry]
        paths: set[str] = set()
        rename_source = False
        for entry in entries:
            if rename_source:  # the entry after an R/C record is its source path
                paths.add(entry)
                rename_source = False
                continue
            paths.add(entry[3:])
            rename_source = entry[0] in "RC"
        moved = {key for key, value in self._state().items() if self._baseline.get(key) != value}
        return sorted(paths | moved)


def _copy_tree(env: dict[str, str], caller: Path, root: Path, strip_paths: Sequence[str]) -> list[str]:
    """Copy the live tree; return the escaping symlinks it dropped."""
    listed = _listed(env, caller)
    for rel in listed:
        src = caller / rel
        if not src.is_symlink() and not src.is_file():
            continue  # tracked-but-deleted, or a submodule gitlink directory
        dst = root / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst, follow_symlinks=False)
    for rel in strip_paths:
        target = root / rel
        if target.is_dir() and not target.is_symlink():
            shutil.rmtree(target)
        elif target.exists() or target.is_symlink():
            target.unlink()
    escaping = [rel for rel in listed if (root / rel).is_symlink() and not (root / rel).resolve().is_relative_to(root)]
    for rel in escaping:
        (root / rel).unlink()
    return escaping


@contextmanager
def standalone_snapshot(caller: Path, strip_paths: Sequence[str] = ()) -> Iterator[Snapshot]:
    """Copy ``caller``'s live tree into a private repository; always removed on exit."""
    caller = caller.resolve()
    parent = Path(tempfile.mkdtemp(prefix="trw-isolated-review-")).resolve()
    try:
        root, home = parent / "tree", parent / "home"
        root.mkdir()
        home.mkdir()
        caller_env = _clean_env(home)
        env = caller_env | {"GIT_DIR": str(root / ".git"), "GIT_WORK_TREE": str(root)}
        dropped = _copy_tree(caller_env, caller, root, strip_paths)
        _git(env, "init", "-q", cwd=root)
        _git(env, "add", "-A", cwd=root)
        _git(env, "commit", "-q", "--allow-empty", "--no-verify", "-m", "isolated-review snapshot", cwd=root)
        snapshot = Snapshot(root, home, caller, env, caller_env, dropped)
        snapshot._baseline = snapshot._state()
        yield snapshot
    finally:
        shutil.rmtree(parent)  # a removal failure propagates: a leaked snapshot is never silent
