"""Replace a user's file only while it still holds the bytes it was judged on (CLAUDE-MD S2 codex r1, r2).

Built from the ``_trash`` primitives (FB-01-KI1-RACE). A compare-then-replace always leaves a window in which a
save is replaced, and so does creating the name and then filling it (codex r2: an in-place save into the new file
was overwritten by the fill, or unlinked by its failure cleanup). So:

1. The new bytes are written completely, fsynced, into a private 0700 folder in ``.trw/trash``, opened by fd. It
   must sit on the file's own filesystem, where a hard-link probe succeeds; otherwise nothing is touched.
2. The file is captured: renamed into a fresh ``.trw/trash`` folder and re-proven there by its content
   (``remove_if_hash``); a mismatch goes back to its name.
3. The staged file is linked at the now-free name (``link`` never replaces), so the name only ever appears holding
   the complete new bytes, and a save that recreated the name meanwhile keeps it.

The displaced file is never unlinked: it stays in its capture folder as the previous version. Only names TRW made in
its own private folder are removed.
"""

from __future__ import annotations

import contextlib
import errno
import hashlib
import os
import secrets
import stat
from collections.abc import Iterator
from pathlib import Path
from typing import Literal, NamedTuple

import structlog

logger = structlog.get_logger(__name__)

_NEW, _PROBE = "new", "probe"
#: Cleanup outcomes that are expected, not failures: already gone, or a directory something else now uses.
_BENIGN_CLEANUP = frozenset({errno.ENOENT, errno.ENOTEMPTY, errno.EEXIST})


class Replaced(NamedTuple):
    """``replaced``: *new* is at the name. ``refused``: it is not; ``reason`` says what is there.

    ``previous`` is the displaced file kept in ``.trw/trash`` (None when nothing was moved, or not known); the
    caller names it once, so ``reason`` never repeats that path. ``failed`` marks a refusal caused by the write
    itself failing (disk full, no hard links, an unusable trash) rather than by the file changing meanwhile, so a
    caller never reports an I/O failure as a concurrent save.
    """

    status: Literal["replaced", "refused"]
    previous: Path | None
    reason: str
    failed: bool = False


class _Unstageable(Exception):
    """The new bytes cannot be published safely here; nothing of the user's was touched."""


def replace_proven(path: Path, root: Path, expected: bytes, new: bytes) -> Replaced:
    """Put *new* at *path* only if *path* still holds exactly *expected*; see the module docstring."""
    from ._trash import remove_if_hash

    if new == expected:  # nothing to change: no capture, so .trw/trash stays empty (lead condition 1)
        return Replaced("replaced", None, "")
    try:
        rel_parts = path.relative_to(root).parts
        mode = stat.S_IMODE(os.lstat(path).st_mode)
        with _staged(root, rel_parts, new, mode) as (pfd, sfd):
            taken = remove_if_hash(path, root, hashlib.sha256(expected).hexdigest(), key="/".join(rel_parts))
            if taken.status == "absent":
                return Replaced("refused", None, "it was removed while TRW was changing it; nothing was written")
            if taken.status == "retained":
                return Replaced(
                    "refused", taken.retained_at, "it changed while TRW was changing it, and a new file took its name"
                )
            if taken.status != "removed":  # a mismatch put back keeps a second name of the file in its capture
                return Replaced("refused", taken.retained_at, f"left as found ({taken.reason})")
            return _publish(pfd, sfd, rel_parts[-1], taken.retained_at)
    except _Unstageable as exc:
        return Replaced("refused", None, f"left as found ({exc})", failed=True)
    except (OSError, ValueError) as exc:
        return Replaced("refused", None, f"left as found, it could not be prepared ({exc})", failed=True)


def create_exclusive(path: Path, root: Path, new: bytes) -> Replaced:
    """Create *path* holding *new* only while nothing is at its name; its mode follows the umask (CLAUDE-MD S1).

    Staged complete and linked like :func:`replace_proven`, so the name never appears half-written and a file that
    appeared at it meanwhile is kept.
    """
    try:
        rel_parts = path.relative_to(root).parts
        with _staged(root, rel_parts, new, None) as (pfd, sfd):
            os.link(_NEW, rel_parts[-1], src_dir_fd=sfd, dst_dir_fd=pfd, follow_symlinks=False)
    except FileExistsError:
        return Replaced("refused", None, "a file appeared at its name while TRW was writing it; that file was kept")
    except _Unstageable as exc:
        return Replaced("refused", None, f"nothing was written ({exc})", failed=True)
    except (OSError, ValueError) as exc:
        return Replaced("refused", None, f"nothing was written ({exc})", failed=True)
    return Replaced("replaced", None, "")


@contextlib.contextmanager
def _staged(root: Path, rel_parts: tuple[str, ...], new: bytes, mode: int | None) -> Iterator[tuple[int, int]]:
    """Yield (parent fd, staging fd) with *new* complete at ``new`` in a private folder; clean only our names.

    ``.trw`` and ``.trw/trash`` are created when missing and removed again afterwards while still empty, so a
    write into a project that had neither leaves no TRW directory behind (S1 impact gate).
    """
    from . import _trash
    from ._trash import _open_dir, _walk

    if _trash._UNSUPPORTED:  # native Windows has no O_DIRECTORY/dir_fd: refuse before touching anything (S1-r2 KI2)
        raise _Unstageable(_trash._UNSUPPORTED)
    fds: list[int] = []
    tfd, sfd, folder, root_fd = -1, -1, "", -1
    fresh: tuple[bool, bool] = (False, False)
    try:
        root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
        fds.append(root_fd)
        pfd = _walk(root_fd, rel_parts[:-1], create=False)
        fds.append(pfd)
        fresh = (not _has(root_fd, ".trw"), not _has(root_fd, ".trw/trash"))
        tfd = _walk(root_fd, (".trw", "trash"), create=True)
        fds.append(tfd)
        folder = f"publish-{secrets.token_hex(16)}"
        os.mkdir(folder, 0o700, dir_fd=tfd)
        sfd = _open_dir(folder, tfd)
        fds.append(sfd)
        _write_new(sfd, new, mode)
        if os.fstat(sfd).st_dev != os.fstat(pfd).st_dev:
            raise _Unstageable("TRW's .trw/trash is on another filesystem, so the file cannot be swapped in one step")
        try:
            os.link(_NEW, _PROBE, src_dir_fd=sfd, dst_dir_fd=sfd, follow_symlinks=False)
        except OSError as exc:
            raise _Unstageable(
                f"this filesystem has no hard links, so the file cannot be swapped in one step ({exc})"
            ) from exc
        os.unlink(_PROBE, dir_fd=sfd)
        yield pfd, sfd
    finally:
        for name in (_NEW, _PROBE) if sfd >= 0 else ():  # our own names in our own private folder, nothing else
            with _logged_cleanup(name):
                os.unlink(name, dir_fd=sfd)
        if folder:
            with _logged_cleanup(folder):
                os.rmdir(folder, dir_fd=tfd)
        if root_fd >= 0 and any(fresh):
            _drop_fresh_dirs(root_fd, fresh)
        for fd in reversed(fds):
            os.close(fd)


@contextlib.contextmanager
def _logged_cleanup(what: str) -> Iterator[None]:
    """Cleaning up TRW's own names never fails the write, but a failure other than "already gone" or "not empty"
    is logged, so an empty ``.trw/trash`` left behind is never silent (CLAUDE-MD S1 red team)."""
    try:
        yield
    except OSError as exc:
        if exc.errno not in _BENIGN_CLEANUP:
            logger.warning("proven_replace_cleanup_failed", name=what, error=str(exc))


def _has(root_fd: int, rel: str) -> bool:
    try:
        os.stat(rel, dir_fd=root_fd, follow_symlinks=False)
    except FileNotFoundError:  # trw-fail-silent-allow: absent is the answer asked for, not a swallowed error
        return False
    except OSError:  # trw-fail-silent-allow: present but not inspectable; never treated as made by this call
        return True
    return True


def _drop_fresh_dirs(root_fd: int, fresh: tuple[bool, bool]) -> None:
    """Remove the ``.trw`` / ``.trw/trash`` this call created, each only while EMPTY (``rmdir`` never recurses)."""
    from ._trash import _open_dir

    made_trw, made_trash = fresh
    with _logged_cleanup(".trw/trash"):  # a non-empty or vanished dir is simply kept; anything else is logged
        trw_fd = _open_dir(".trw", root_fd)
        try:
            if made_trash:
                os.rmdir("trash", dir_fd=trw_fd)
        finally:
            os.close(trw_fd)
        if made_trw:
            os.rmdir(".trw", dir_fd=root_fd)


def _write_new(sfd: int, new: bytes, mode: int | None) -> None:
    """*mode* is the original's bits, set through the fd; ``None`` (a new file) lets the kernel apply the umask to
    0666, so the process-wide umask is never toggled to be read (codex r2 KI)."""
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC
    out = os.open(_NEW, flags, 0o666 if mode is None else 0o600, dir_fd=sfd)
    try:
        view = memoryview(new)
        while view:
            view = view[os.write(out, view) :]
        if mode is not None:
            os.fchmod(out, mode)
        os.fsync(out)
    finally:
        os.close(out)


def _publish(pfd: int, sfd: int, name: str, previous: Path | None) -> Replaced:
    """Link the staged file at the free *name*; on failure give the name back to *previous*, never replacing."""
    try:
        os.link(_NEW, name, src_dir_fd=sfd, dst_dir_fd=pfd, follow_symlinks=False)
    except FileExistsError:
        return Replaced("refused", previous, "a save recreated it while TRW was changing it; that save was kept")
    except BaseException as exc:
        back = _put_back(previous, pfd, name)
        if isinstance(exc, OSError):
            return Replaced("refused", previous, f"it could not be written ({exc.strerror}); {back}", failed=True)
        raise
    return Replaced("replaced", previous, "")


def _put_back(previous: Path | None, pfd: int, name: str) -> str:
    """Give the name back to the displaced file with a no-replace link (links work here: the probe proved it)."""
    if previous is None:
        return "its previous version is in .trw/trash"
    try:
        os.link(previous, name, dst_dir_fd=pfd, follow_symlinks=False)
    except FileExistsError:  # trw-fail-silent-allow: not silent; a save took the name and stays, reported
        return "a save took its name and was kept"
    except OSError:  # trw-fail-silent-allow: not silent; the caller names where the previous version is
        return "its previous version could not be put back"
    return "its previous version was put back"
