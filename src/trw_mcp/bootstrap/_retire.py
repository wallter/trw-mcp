"""Retire a TRW-managed file or tree in place: the one decision every update-time retirement uses.

TRW's own bytes need no backup, and neither do bytes git already holds. A file is deleted when it (1) hashes to
what TRW recorded or ships, or (2) is tracked and clean in git (recoverable with ``git restore``), and is
KEPT, with the command that removes it, otherwise (HB-2): an uncommitted edit, an edited untracked file, or
non-TRW bytes in a project that is not in git.
"""

from __future__ import annotations

import contextlib
import errno
import hashlib
import os
import shlex
import stat
import subprocess
from collections.abc import Callable, Collection, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Literal, NamedTuple

from ._safe_remove import delete_proven_unchanged_captures, path_refusal, remove_if_hash, trash_dir
from ._trash import _CHUNK, _HASH_CAP
from ._utils import printable

__all__ = [
    "Retired",
    "Retirement",
    "as_retirement",
    "git_recoverable",
    "git_view_of",
    "record_retirement",
    "retire_file",
    "retire_tree",
]


#: ``(scratch, real)`` while ``update --dry-run`` runs against a scratch copy: git knows nothing of the copy, so
#: the git-clean rule asks about the same file in the real checkout and the preview matches the real run.
_GIT_VIEW: ContextVar[tuple[Path, Path] | None] = ContextVar("trw_retire_git_view", default=None)


@contextmanager
def git_view_of(scratch: Path, real: Path) -> Iterator[None]:
    """Within the block, :func:`git_recoverable` for a path under *scratch* asks git about *real*."""
    token = _GIT_VIEW.set((scratch, real))
    try:
        yield
    finally:
        _GIT_VIEW.reset(token)


class Retired(NamedTuple):
    """``status``: removed (TRW's bytes), git (removed, committed in git), kept (``why``), absent."""

    status: Literal["removed", "git", "kept", "absent"]
    why: str = ""


class Retirement(NamedTuple):
    """What :func:`retire_tree` did, as repo-relative paths; ``kept`` holds ``(path, why)``."""

    removed: list[str]
    git: list[str]
    kept: list[tuple[str, str]]


def as_retirement(rel: str, outcome: Retired) -> Retirement:
    """*outcome* for the one file *rel*, in :func:`record_retirement`'s shape."""
    return Retirement(
        [rel] * (outcome.status == "removed"),
        [rel] * (outcome.status == "git"),
        [(rel, outcome.why)] * (outcome.status == "kept"),
    )


def git_recoverable(path: Path, root: Path, digest: str) -> bool:
    """True when *path* is tracked by git with no uncommitted change, HEAD and the index hold the same blob, and
    that blob as ``git restore`` would write it (smudge filters, eol conversion) hashes to *digest*, the sha256 of
    the bytes on disk. Any git error or mismatch is False."""
    rel = path.relative_to(root).as_posix()
    view = _GIT_VIEW.get()
    if view is not None and root == view[0]:
        root = view[1]
    env = {**os.environ, "GIT_CEILING_DIRECTORIES": str(root.parent), "GIT_OPTIONAL_LOCKS": "0"}
    base = ["git", "--literal-pathspecs", "-C", str(root)]  # a filename is a name, never a glob or magic pathspec
    try:
        tracked = subprocess.run(  # noqa: S603
            [*base, "ls-files", "-z", "--error-unmatch", "--", rel],
            capture_output=True,
            timeout=5,
            env=env,
            check=False,
        )
        if tracked.returncode != 0 or tracked.stdout.split(b"\0")[:-1] != [rel.encode()]:
            return False  # not tracked under exactly this name
        status = subprocess.run(  # noqa: S603
            [*base, "status", "--porcelain", "--", rel], capture_output=True, timeout=5, env=env, check=False
        )
        if status.returncode != 0 or status.stdout.strip():
            return False
        # ``status`` is silent about a skip-worktree / assume-unchanged file whose bytes differ, and compares
        # through the clean filter (a lossy one hides a change), so recoverable means proven: what ``git restore``
        # would write is byte-for-byte the *digest* retire_file took and remove_if_hash re-checks. ``./`` keeps
        # both names relative to *root*, not the repository's top level.
        held = subprocess.run(  # noqa: S603
            [*base, "rev-parse", f"HEAD:./{rel}", f":./{rel}"], capture_output=True, timeout=5, env=env, check=False
        )
        blobs = held.stdout.split()
        if held.returncode != 0 or len(blobs) != 2 or blobs[0] != blobs[1]:
            return False  # HEAD and the index disagree, or git cannot name the blob
        restored = subprocess.run(  # noqa: S603
            [*base, "cat-file", "--filters", f"HEAD:./{rel}"], capture_output=True, timeout=5, env=env, check=False
        )
    except (OSError, subprocess.TimeoutExpired):  # trw-fail-silent-allow: git cannot answer, so the file is kept
        return False
    return restored.returncode == 0 and hashlib.sha256(restored.stdout).hexdigest() == digest


def _bounded_sha256(path: Path) -> tuple[str | None, str]:
    """``(sha256, "")`` of a regular file read through an O_NOFOLLOW|O_NONBLOCK fd, or ``(None, reason)`` for a
    symlink, FIFO, device, directory or a file over the size cap. A FIFO never blocks the sweep."""
    not_regular = (None, "not a regular file")
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError as exc:
        if exc.errno in (errno.ELOOP, errno.EMLINK):  # a symlink at the name: not TRW's regular file
            return not_regular
        raise
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return not_regular
        digest = hashlib.sha256()
        total = 0
        while chunk := os.read(fd, _CHUNK):
            total += len(chunk)
            if total > _HASH_CAP:
                return None, f"larger than the {_HASH_CAP // (1024 * 1024)} MiB cap"
            digest.update(chunk)
        return digest.hexdigest(), ""
    finally:
        os.close(fd)


def retire_file(path: Path, root: Path, proven: Collection[str], *, managed: bool | None = None) -> Retired:
    """Delete *path* in place when its sha256 is in *proven* or, for a TRW-managed file, git holds it clean.

    *managed* says TRW wrote this path (default: *proven* is non-empty, i.e. a record exists). A path TRW never
    recorded is the user's own, so a clean git copy never makes it deletable.

    The hash is taken immediately before the unlink; a save landing inside that window is not recoverable,
    which is why a file that is neither proven nor git-clean is never touched.
    """
    refusal = path_refusal(path, root)
    if refusal:
        return Retired("kept", refusal)
    try:
        digest, why = _bounded_sha256(path)
    except (FileNotFoundError, NotADirectoryError):
        return Retired("absent")
    except OSError as exc:
        return Retired("kept", f"unreadable: {exc}")
    if digest is None:
        return Retired("kept", why)
    if digest in proven:
        status: Literal["removed", "git"] = "removed"
    elif (bool(proven) if managed is None else managed) and git_recoverable(path, root, digest):
        status = "git"
    else:
        return Retired("kept", "not TRW's unchanged bytes, and it differs from git HEAD (or is not tracked)")
    removal = remove_if_hash(path, root, digest)  # rename-capture, re-prove the hash; a save in between is put back
    if removal.status == "absent":
        return Retired("absent")
    if removal.status != "removed":
        return Retired("kept", removal.reason)
    if removal.retained_at is not None:  # the capture is TRW's own proven bytes (or git-held): drop it, no trash
        _, refused = delete_proven_unchanged_captures(root, [removal.retained_at])
        if refused:  # the bytes changed after the capture: they are in trash, not removed
            return Retired(
                "kept", f"kept at {removal.retained_at.relative_to(root).as_posix()} (changed during removal)"
            )
        for leftover in (trash_dir(root), trash_dir(root).parent):
            with contextlib.suppress(OSError):  # trw-fail-silent-allow: not empty means something else lives there
                os.rmdir(leftover)
    return Retired(status)


def retire_tree(artifact: Path, root: Path, allowed: Callable[[Path], set[str]]) -> Retirement:
    """:func:`retire_file` for every regular file under *artifact* (a file or a directory), then ``rmdir`` the
    emptied directories deepest first, so one kept file (or one created after the listing) keeps its directory."""
    out = Retirement([], [], [])

    def shown(p: Path) -> str:
        try:
            return printable(p.relative_to(root).as_posix())
        except ValueError:
            return printable(str(p))

    try:
        is_tree = artifact.is_dir() and not artifact.is_symlink()
        entries = sorted(artifact.rglob("*")) if is_tree else [artifact]
    except OSError as exc:  # an inspection failure keeps the artifact and reports it; never abort the update
        out.kept.append((shown(artifact), f"could not inspect: {exc}"))
        return out
    for entry in entries:
        if is_tree and entry.is_dir() and not entry.is_symlink():
            continue
        outcome = retire_file(entry, root, allowed(entry))
        if outcome.status == "removed":
            out.removed.append(shown(entry))
        elif outcome.status == "git":
            out.git.append(shown(entry))
        elif outcome.status == "kept":
            out.kept.append((shown(entry), outcome.why))
    if is_tree:
        try:
            dirs = [d for d in artifact.rglob("*") if d.is_dir() and not d.is_symlink()]
        except OSError:  # trw-fail-silent-allow: cannot list, so rmdir only the top; a non-empty one stays
            dirs = []
        for directory in [*sorted(dirs, key=lambda d: len(d.parts), reverse=True), artifact]:
            try:
                os.rmdir(directory)
            except OSError:  # trw-fail-silent-allow: not empty means something was kept or added; it stays
                pass
        if os.path.lexists(artifact) and not out.kept:
            out.kept.append((shown(artifact), "not empty after removal"))
    return out


def describe_removal(result: dict[str, list[str]], rel: str, text: str, dry_text: str = "") -> None:
    """Warn that *rel* was removed, and record THAT warning (path and exact text) so a rollback can retract only it.

    A dry run only proposes the removal, so it shows *dry_text* when given. ``retired_described`` marks the path as
    already described, and ``retirement_notes`` holds ``<path><TAB><exact warning>``: a genuine recovery warning for
    the same path (where the user's edit went) is a different string and is never touched by the rollback.
    """
    message = dry_text if dry_text and "dry_run" in result else text
    result.setdefault("warnings", []).append(message)
    result.setdefault("retired_described", []).append(rel)
    result.setdefault("retirement_notes", []).append(f"{rel}\t{message}")


def record_retirement(result: dict[str, list[str]], outcome: Retirement) -> None:
    """Report *outcome*: every gone file in ``retired`` (the uncommitted-changes guard must not restore it), a
    warning naming each git-recoverable removal with its restore command and each kept file with its removal."""
    gone = [*outcome.removed, *outcome.git]
    notes = [
        *(
            f"{p}: {why}"
            if why.startswith("kept at ")
            else f"{p} ({why}): kept; to remove it yourself run: rm {shlex.quote(p)}"
            for p, why in outcome.kept
        ),
    ]
    for p in outcome.git:  # the restore warning, retractable by a rollback and a proposal in a dry run
        describe_removal(
            result,
            p,
            f"{p}: removed; your version differs from TRW's but is committed in git (restore: git restore -- {shlex.quote(p)})",
            f"{p}: would remove; your version differs from TRW's but is committed in git"
            f" (it would be recoverable with: git restore -- {shlex.quote(p)})",
        )
    if outcome.kept:  # likewise for a kept retired file: its warning carries the removal command
        result.setdefault("retired_kept", []).extend(p for p, _why in outcome.kept)
    if gone:
        result.setdefault("retired", []).extend(gone)
    if notes:
        result.setdefault("warnings", []).extend(notes)
