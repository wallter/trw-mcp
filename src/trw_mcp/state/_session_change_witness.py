"""Per-session change witness outside ``.trw/`` (EVIDENCE-DELETION-POLICY (b); PRD-FIX-140-FR04 fallback).

Every change-evidence surface an unpinned session has lives under ``.trw/`` and can be deleted. This witness
does not: at ``trw_session_start`` it remembers, IN MEMORY and keyed by ``(session id, project root)``, the
checkout's git ``HEAD`` and a digest of every path outside ``.trw/`` that was already dirty or untracked. Later,
:func:`changed_since_snapshot` counts only the paths whose content differs from that moment -- a pre-existing
untracked file that nobody touched is not a change, and neither is another session's earlier commit.

The snapshot is per session and never persisted, so a server restart loses it: that is ``no_snapshot`` and
the caller treats it as uncomputable (fail closed with the reason), never 0. A project that is not a git
checkout is ``no_git`` and the caller keeps its previous behaviour.
"""

from __future__ import annotations

import hashlib
import os
import stat
import subprocess
import threading
from concurrent.futures import Future
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import structlog

logger = structlog.get_logger(__name__)

WitnessStatus = Literal["counted", "no_snapshot", "no_git", "unreadable"]
_GIT_TIMEOUT_S = 20.0
_EXCLUDED = ".trw/"
_ABSENT = "<absent>"
NO_SNAPSHOT_REASON = (
    "no change snapshot for this session in this server process (server restarted or trw_session_start not "
    "called): run trw_build_check, or record an acceptable-failure"
)


@dataclass(frozen=True)
class WitnessCount:
    """``count`` and ``paths`` are set only for ``counted``; every other status carries the named ``reason``.

    ``paths`` are repo-relative, so a caller can UNION them with hook-recorded paths (W5: a Codex Bash edit is
    invisible to every hook, so one recorded apply_patch edit must not hide the others).
    """

    status: WitnessStatus
    count: int | None
    reason: str
    paths: frozenset[str] = frozenset()


@dataclass(frozen=True)
class _Snapshot:
    head: str | None  # None: an unborn branch (no commit yet)
    digests: dict[str, str]  # path -> digest of each path dirty or untracked at the snapshot


_LOCK = threading.Lock()
_SNAPSHOTS: dict[tuple[str, str], Future[_Snapshot | None]] = {}


def _key(session_id: str, project_root: Path) -> tuple[str, str]:
    return session_id, str(project_root.resolve())


def _git(root: Path, *args: str) -> bytes:
    # No inherited GIT_* variable may point this read at another repository or index.
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    done = subprocess.run(  # noqa: S603 -- fixed git argv; args are literals from this module, root is a Path
        ["git", "-C", str(root), *args],  # noqa: S607 -- git is on PATH; partial path is intentional
        capture_output=True, timeout=_GIT_TIMEOUT_S, check=False, env=env,
    )  # fmt: skip
    if done.returncode != 0:
        raise OSError(f"git {args[0]} exited {done.returncode}")
    return done.stdout


def _is_git(root: Path) -> bool:
    try:
        return _git(root, "rev-parse", "--is-inside-work-tree").strip() == b"true"
    except (OSError, subprocess.SubprocessError):  # trw-fail-silent-allow: no git binary or not a checkout
        return False


def _dirty_paths(root: Path) -> set[str]:
    out = _git(root, "status", "--porcelain=v1", "-z", "--untracked-files=all")
    paths: set[str] = set()
    fields = out.split(b"\0")
    index = 0
    while index < len(fields):
        entry = fields[index]
        index += 1
        if len(entry) < 4:
            continue
        paths.add(entry[3:].decode("utf-8", "surrogateescape"))
        if entry[:1] in (b"R", b"C"):  # a rename/copy is followed by its source path
            if index < len(fields):
                paths.add(fields[index].decode("utf-8", "surrogateescape"))
            index += 1
    return {path for path in paths if not path.startswith(_EXCLUDED)}


def _digest(root: Path, path: str) -> str:
    target = root / path
    try:
        mode = os.lstat(target).st_mode
        if stat.S_ISLNK(mode):
            return "link:" + str(target.readlink())
        if not stat.S_ISREG(mode):  # a directory, FIFO or device is never opened (a FIFO would block the read)
            return f"<mode {stat.S_IFMT(mode):o}>"
        return hashlib.sha256(target.read_bytes()).hexdigest()
    except FileNotFoundError:  # trw-fail-silent-allow: deletion is itself a state the digest records
        return _ABSENT


def _head(root: Path) -> str | None:
    try:
        return _git(root, "rev-parse", "--verify", "-q", "HEAD").decode().strip() or None
    except OSError:  # trw-fail-silent-allow: an unborn branch has no HEAD commit yet
        return None


def _take(root: Path) -> _Snapshot | None:
    if not _is_git(root):
        return None
    return _Snapshot(_head(root), {path: _digest(root, path) for path in _dirty_paths(root)})


def record_snapshot(session_id: str, project_root: Path) -> None:
    """Take this session's snapshot now (idempotent per key: the FIRST session_start is the baseline).

    Synchronous on purpose: a baseline taken in the background could absorb an edit made while it ran,
    and that edit would then never count.
    """
    key = _key(session_id, project_root)
    with _LOCK:
        if key in _SNAPSHOTS:
            return
        future: Future[_Snapshot | None] = Future()
        _SNAPSHOTS[key] = future
    try:
        future.set_result(_take(project_root))
    except Exception as exc:  # justified: delivered to the reader as "unreadable", never a silent zero
        future.set_exception(exc)


def changed_since_snapshot(session_id: str, project_root: Path) -> WitnessCount:
    """Paths outside ``.trw/`` whose content differs from this session's snapshot."""
    with _LOCK:
        future = _SNAPSHOTS.get(_key(session_id, project_root))
    if future is None:
        if not _is_git(project_root):  # no snapshot could ever have been taken here: the previous answer stands
            return WitnessCount("no_git", None, "the project is not a git checkout")
        # Never suggest re-running trw_session_start: a snapshot taken after the edits would hide them.
        return WitnessCount("no_snapshot", None, NO_SNAPSHOT_REASON)
    try:
        snapshot = future.result(timeout=0)
        if snapshot is None:
            return WitnessCount("no_git", None, "the project is not a git checkout")
        candidates = set(snapshot.digests) | _dirty_paths(project_root)
        if snapshot.head is not None:
            diff = _git(project_root, "diff", "--name-only", "-z", "--no-renames", snapshot.head, "--")
        else:  # an unborn baseline: everything tracked now was either untracked then (digested) or is new
            diff = _git(project_root, "ls-files", "-z")
        candidates |= {p.decode("utf-8", "surrogateescape") for p in diff.split(b"\0") if p}
        changed = {
            path for path in candidates
            if not path.startswith(_EXCLUDED)
            and (path not in snapshot.digests or _digest(project_root, path) != snapshot.digests[path])
        }  # fmt: skip
    except Exception:  # justified: fail-CLOSED, a witness that cannot be computed is uncomputable, never 0
        logger.warning("session_change_witness_unreadable", outcome="fail_closed", exc_info=True)
        return WitnessCount("unreadable", None, "the checkout could not be compared with this session's snapshot")
    return WitnessCount("counted", len(changed), f"{len(changed)} path(s) changed since trw_session_start",
                        frozenset(changed))  # fmt: skip


def _reset_for_tests() -> None:
    with _LOCK:
        _SNAPSHOTS.clear()


__all__ = ["NO_SNAPSHOT_REASON", "WitnessCount", "changed_since_snapshot", "record_snapshot"]
