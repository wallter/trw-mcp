"""Trash-backed removal: ``remove_if_hash`` and the uninstall move into the macOS system Trash.

Belongs to the ``_safe_remove.py`` facade, which re-exports every public name here, so callers keep a
single import point. Split out to keep both modules under the 350 effective-LOC gate.
"""

from __future__ import annotations

import contextlib
import errno
import hashlib
import json
import os
import re
import secrets
import stat
import sys
import time
from collections.abc import Callable
from pathlib import Path, PurePosixPath
from typing import Literal, NamedTuple

# ---------------------------------------------------------------------------------------------------
# remove_if_hash: capture into trash, verify, keep or link back. remove_if_hash never deletes a byte:
# its only mutating syscalls are mkdir, one O_EXCL meta.json write, the single capture ``os.rename``
# and (on mismatch) a no-replace ``os.link``. Bytes that leave a user-visible name stay in
# ``.trw/trash`` until a human empties it; an explicit uninstall moves its own matched captures on into
# the macOS system Trash by rename (``move_captures_to_os_trash``), still without unlinking anything.
# ---------------------------------------------------------------------------------------------------

_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_HASH_CAP = 16 * 1024 * 1024
_CHUNK = 1 << 20
_DIR_FLAGS_NAMES = ("O_DIRECTORY", "O_NOFOLLOW", "O_NONBLOCK")


class Removal(NamedTuple):
    """Outcome of :func:`remove_if_hash`.

    ``removed``: the recorded bytes were moved into trash (``retained_at``); ``absent``: nothing at the
    name; ``kept``: the user's bytes are at ``published`` (or never moved); ``retained``: the bytes could
    not be put back and are only in trash at ``retained_at``.
    """

    key: str | None
    path: Path
    status: Literal["removed", "absent", "kept", "retained"]
    published: Path | None
    retained_at: Path | None
    reason: str


#: The top-level ``.trw/`` entry that holds captured user bytes; uninstall never lists it as a
#: non-TRW entry and never removes it recursively.
TRASH_DIR_NAME = "trash"


def trash_dir(root: Path) -> Path:
    """The trash directory for *root*: ``<root>/.trw/trash``."""
    return root / ".trw" / TRASH_DIR_NAME


def _capable() -> str | None:
    """Why the fd-anchored capture cannot run on this platform, or ``None``."""
    if not all(hasattr(os, n) for n in _DIR_FLAGS_NAMES):
        return "platform lacks O_DIRECTORY/O_NOFOLLOW/O_NONBLOCK"
    if not {os.open, os.mkdir, os.stat, os.rename} <= os.supports_dir_fd:
        return "platform lacks dir_fd support for open/mkdir/stat/rename"
    if os.stat not in os.supports_follow_symlinks:
        return "platform lacks no-follow support for stat"
    if os.link not in os.supports_dir_fd or os.link not in os.supports_follow_symlinks:
        return "platform lacks dir_fd/no-follow support for link"
    return None


#: Decided once at import so a caller (or a test) that wraps ``os.rename``/``os.link`` cannot flip it.
_UNSUPPORTED = _capable()


def _open_dir(name: str, dir_fd: int) -> int:
    return os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=dir_fd)


def _walk(root_fd: int, parts: tuple[str, ...], *, create: bool) -> int:
    """Open each of *parts* below *root_fd* with O_NOFOLLOW; optionally mkdir (0700) a missing one."""
    current = os.dup(root_fd)
    try:
        for part in parts:
            try:
                child = _open_dir(part, current)
            except FileNotFoundError:
                if not create:
                    raise
                try:
                    os.mkdir(part, 0o700, dir_fd=current)
                except FileExistsError:  # trw-fail-silent-allow: a concurrent creator made it; we open it next
                    pass
                child = _open_dir(part, current)
            os.close(current)
            current = child
    except BaseException:
        os.close(current)
        raise
    return current


def _sha256_stable(fd: int, expected: str, cfd: int) -> bool:
    """True only if the bytes read through *fd* hash to *expected*, the inode did not change meanwhile,
    and the name ``data`` in *cfd* still refers to that same inode."""
    before = os.fstat(fd)
    if not stat.S_ISREG(before.st_mode) or before.st_size > _HASH_CAP:
        return False
    digest = hashlib.sha256()
    total = 0
    while True:
        chunk = os.read(fd, _CHUNK)
        if not chunk:
            break
        total += len(chunk)
        if total > _HASH_CAP:
            return False
        digest.update(chunk)
    after = os.fstat(fd)
    same = (before.st_size, before.st_mtime_ns, before.st_ctime_ns) == (
        after.st_size,
        after.st_mtime_ns,
        after.st_ctime_ns,
    )
    named = os.stat("data", dir_fd=cfd, follow_symlinks=False)
    still_named = (named.st_dev, named.st_ino) == (after.st_dev, after.st_ino)
    return same and still_named and total == after.st_size and digest.hexdigest() == expected.lower()


def _write_meta(cfd: int, rel: str, key: str | None, captured_at: str, sha256: str) -> None:
    fd = os.open("meta.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=cfd)
    try:
        # ``sha256`` is the hash TRW recorded for these bytes; an uninstall off macOS re-proves it before deleting.
        payload = {"v": 1, "path": rel, "key": key, "captured_at": captured_at, "sha256": sha256}
        os.write(fd, json.dumps(payload).encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)


def _drop_empty_capture(tfd: int, folder: str, cfd: int) -> None:
    """Remove a capture folder that never received data: only its own meta file, then the folder itself."""
    with contextlib.suppress(OSError):
        os.unlink("meta.json", dir_fd=cfd)
    with contextlib.suppress(OSError):
        os.rmdir(folder, dir_fd=tfd)


def _make_capture_dir(tfd: int, stamp: str) -> tuple[str, int]:
    """Create ``<stamp>-<hex32>`` (0700) below the trash fd, retrying on a name collision."""
    while True:
        folder = f"{stamp}-{secrets.token_hex(16)}"
        try:
            os.mkdir(folder, 0o700, dir_fd=tfd)
        except FileExistsError:  # trw-fail-silent-allow: name collision; retry with a new token
            continue
        return folder, _open_dir(folder, tfd)


def remove_if_hash(path: Path, root: Path, expected_sha256: str, *, key: str | None = None) -> Removal:
    """Move *path* into ``.trw/trash`` if and only if its bytes hash to *expected_sha256*.

    The file is first captured (renamed) into a fresh trash folder, then verified there. A match is
    reported ``removed`` and the bytes stay in trash; anything else (mismatch, changed while hashing,
    any error) links the captured inode back to its name with a no-replace ``link``. Nothing is ever
    unlinked, so a concurrent writer's bytes always keep at least one name.
    """

    def done(
        status: Literal["removed", "absent", "kept", "retained"],
        reason: str,
        published: Path | None = None,
        retained_at: Path | None = None,
    ) -> Removal:
        return Removal(key, path, status, published, retained_at, reason)

    if not isinstance(expected_sha256, str) or _SHA256_RE.fullmatch(expected_sha256) is None:
        raise ValueError("expected_sha256 must be 64 lowercase hex characters")
    if _UNSUPPORTED:
        return done("kept", _UNSUPPORTED)
    try:
        rel_parts = path.relative_to(root).parts
    except ValueError:
        return done("kept", "path is not under root")
    if not rel_parts or ".." in rel_parts:
        return done("kept", "path is the root or contains '..'")
    fds: list[int] = []
    try:
        return _capture(path, root, expected_sha256, rel_parts, key, fds, done)
    except OSError as exc:
        return done("kept", f"could not prepare capture ({type(exc).__name__}): {exc}")
    finally:
        for fd in fds:
            os.close(fd)


def _capture(
    path: Path,
    root: Path,
    expected_sha256: str,
    rel_parts: tuple[str, ...],
    key: str | None,
    fds: list[int],
    done: Callable[..., Removal],
) -> Removal:
    """Anchor, capture, verify. Every fd opened is appended to *fds* for the caller to close."""
    rel = str(PurePosixPath(*rel_parts))
    root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    fds.append(root_fd)
    try:
        pfd = _walk(root_fd, rel_parts[:-1], create=False)
    except FileNotFoundError:
        return done("absent", "parent directory does not exist")
    fds.append(pfd)
    name = rel_parts[-1]
    try:
        mode = os.stat(name, dir_fd=pfd, follow_symlinks=False).st_mode
    except FileNotFoundError:
        return done("absent", "nothing at the path")
    if not stat.S_ISREG(mode):
        return done("kept", "not a regular file")
    # Created lazily: absent / kept / non-regular outcomes above leave no trash behind.
    tfd = _walk(root_fd, (".trw", "trash"), create=True)
    fds.append(tfd)
    now = time.gmtime()
    captured_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", now)
    folder, cfd = _make_capture_dir(tfd, time.strftime("%Y%m%dT%H%M%SZ", now))
    fds.append(cfd)
    data_path = trash_dir(root) / folder / "data"
    try:
        _write_meta(cfd, rel, key, captured_at, expected_sha256.lower())
        os.rename(name, "data", src_dir_fd=pfd, dst_dir_fd=cfd)
    except OSError as exc:
        _drop_empty_capture(tfd, folder, cfd)  # nothing was captured: give back the space it took (full disk)
        if isinstance(exc, FileNotFoundError):
            return done("absent", "vanished before capture")
        if exc.errno == errno.EXDEV:
            return done("kept", "trash on another device")
        raise
    try:
        return _verify_and_publish(path, root, root_fd, rel_parts, expected_sha256, pfd, cfd, data_path, done)
    except BaseException:
        # An interrupt or bug after the capture must still try to give the user's name back.
        try:
            os.link("data", name, src_dir_fd=cfd, dst_dir_fd=pfd, follow_symlinks=False)
        except OSError:  # trw-fail-silent-allow: best effort restore while another exception propagates
            pass
        raise


def _data_present(cfd: int) -> bool:
    try:
        os.stat("data", dir_fd=cfd, follow_symlinks=False)
    except OSError:  # trw-fail-silent-allow: cannot see the capture, so its location is reported unknown
        return False
    return True


def _verify_and_publish(
    path: Path,
    root: Path,
    root_fd: int,
    rel_parts: tuple[str, ...],
    expected_sha256: str,
    pfd: int,
    cfd: int,
    data_path: Path,
    done: Callable[..., Removal],
) -> Removal:
    """Verify the captured ``data``; keep it in trash on a match, else link it back to its name."""
    name = rel_parts[-1]
    is_dir = False
    matched = False
    try:
        is_dir = stat.S_ISDIR(os.stat("data", dir_fd=cfd, follow_symlinks=False).st_mode)
    except OSError:  # trw-fail-silent-allow: unknown kind is handled as a mismatch below
        pass
    try:
        dfd = os.open("data", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=cfd)
    except OSError:  # trw-fail-silent-allow: unreadable capture means mismatch; the file is linked back
        dfd = -1
    if dfd >= 0:
        try:
            matched = _sha256_stable(dfd, expected_sha256, cfd)
        except OSError:  # trw-fail-silent-allow: any read error means mismatch; the file is linked back
            matched = False
        finally:
            os.close(dfd)
    present = _data_present(cfd)
    where = data_path if present else None
    unknown = "" if present else "; capture location unknown"
    if matched:
        return done("removed", "recorded bytes moved to trash" + unknown, retained_at=where)
    try:
        os.link("data", name, src_dir_fd=cfd, dst_dir_fd=pfd, follow_symlinks=False)
    except OSError as exc:
        reason = f"could not put the file back ({type(exc).__name__}): {exc}"
        if is_dir:
            reason = f"a directory replaced the file during removal; it is kept at {where}"
        return done("retained", reason + unknown, retained_at=where)
    reason = "bytes differ from the recorded hash; put back" + unknown
    published: Path | None = path
    if not _same_folder(root_fd, rel_parts, pfd):
        published = None
        reason += "; the folder moved during removal"
    return done("kept", reason, published=published, retained_at=where)


def _same_folder(root_fd: int, rel_parts: tuple[str, ...], pfd: int) -> bool:
    """True if re-resolving the parent from the root still reaches the pinned directory."""
    try:
        again = _walk(root_fd, rel_parts[:-1], create=False)
    except OSError:  # trw-fail-silent-allow: cannot re-resolve, so the location is reported uncertain
        return False
    try:
        a, b = os.fstat(again), os.fstat(pfd)
    finally:
        os.close(again)
    return (a.st_dev, a.st_ino) == (b.st_dev, b.st_ino)


_CAPTURE_NAME_RE = re.compile(r"\d{8}T\d{6}Z-[0-9a-f]{32}")


def move_captures_to_os_trash(root: Path, data_paths: list[Path]) -> tuple[Path | None, list[tuple[Path, str]]]:
    """Move whole capture folders from ``.trw/trash`` into the macOS system Trash, by rename only.

    For an explicit uninstall (lead ruling 2026-09-29, option e): nothing is unlinked, so the captured inode
    and any late write through a held fd survive in ``~/.Trash/trw-<project>-<utc>-<hex8>/``, which the user
    empties like any Trash. Each *data_path* must be ``<root>/.trw/trash/<stamp>-<hex32>/data``. Returns the
    destination folder (``None`` when nothing moved) and ``(data_path, why)`` for every capture left in
    ``.trw/trash`` (another device, no system Trash, not macOS, not a TRW capture).
    """
    kept: list[tuple[Path, str]] = []
    if not data_paths:
        return None, kept
    if sys.platform != "darwin":
        return None, [(p, "the system Trash is only used on macOS") for p in data_paths]
    if _UNSUPPORTED:
        return None, [(p, _UNSUPPORTED) for p in data_paths]
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    dest_name = f"trw-{root.name}-{stamp}-{secrets.token_hex(4)}"
    dest = Path.home() / ".Trash" / dest_name
    fds: list[int] = []
    moved = 0
    try:
        try:
            home_fd = os.open(Path.home(), os.O_RDONLY | os.O_DIRECTORY)
            fds.append(home_fd)
            os_trash_fd = _open_dir(".Trash", home_fd)
            fds.append(os_trash_fd)
            os.mkdir(dest_name, 0o700, dir_fd=os_trash_fd)
            dest_fd = _open_dir(dest_name, os_trash_fd)
            fds.append(dest_fd)
            root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
            fds.append(root_fd)
            tfd = _walk(root_fd, (".trw", TRASH_DIR_NAME), create=False)
            fds.append(tfd)
        except OSError as exc:
            return None, [(p, f"no usable system Trash ({type(exc).__name__}): {exc}") for p in data_paths]
        for data_path in data_paths:
            folder = data_path.parent.name
            if (
                data_path.name != "data"
                or data_path.parent.parent != trash_dir(root)
                or not _CAPTURE_NAME_RE.fullmatch(folder)
            ):
                kept.append((data_path, "not a TRW capture folder"))
                continue
            try:
                # The capture folder names are unique (stamp + 128-bit token) and dest is freshly made, so
                # this rename cannot replace anything.
                os.rename(folder, folder, src_dir_fd=tfd, dst_dir_fd=dest_fd)
            except OSError as exc:
                why = "the system Trash is on another device" if exc.errno == errno.EXDEV else str(exc)
                kept.append((data_path, why))
                continue
            moved += 1
        if not moved:
            try:
                os.rmdir(dest_name, dir_fd=os_trash_fd)  # our own folder, still empty
            except OSError:  # trw-fail-silent-allow: an empty leftover folder in the system Trash is harmless
                pass
    finally:
        for fd in reversed(fds):
            os.close(fd)
    return (dest if moved else None), kept
