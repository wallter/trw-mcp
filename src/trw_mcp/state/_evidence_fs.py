"""Descriptor-anchored filesystem reads beneath the evidence binding facade.

Symlink targets are expanded without opening an unchecked pathname. Every
observed link and directory edge is rechecked before an entry is returned.
"""

from __future__ import annotations

import errno
import os
import stat
from collections import deque
from collections.abc import Callable
from pathlib import Path

from trw_mcp.models._evidence_core import ContentEntry, EntryState, EvidenceLimits

_MAX_LINK_HOPS = 40


class StableReadError(RuntimeError):
    """A safely normalized non-positive filesystem observation."""

    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


def _safe_read_supported() -> bool:
    return (
        all(hasattr(os, name) for name in ("O_NOFOLLOW", "O_DIRECTORY", "O_NONBLOCK"))
        and all(function in os.supports_dir_fd for function in (os.open, os.stat, os.readlink))
        and os.stat in os.supports_follow_symlinks
    )


def signature(value: os.stat_result) -> tuple[int, int, int, int, int, int]:
    return (stat.S_IFMT(value.st_mode), value.st_size, value.st_dev, value.st_ino, value.st_mtime_ns, value.st_ctime_ns)


def _identity(value: os.stat_result) -> tuple[int, int, int]:
    return stat.S_IFMT(value.st_mode), value.st_dev, value.st_ino


def _parts(path: str) -> list[str]:
    if (
        not path
        or len(path.encode("utf-8")) > EvidenceLimits.MAX_PATH_BYTES
        or any(part in {"", ".", ".."} for part in path.split("/"))
        or any(character in path for character in ("\\", ":", "\x00"))
    ):
        raise StableReadError("path_invalid")
    return path.split("/")


_AliasSnapshot = tuple[tuple[str, tuple[int, ...], str | None], ...]


def _resolve_absolute_metadata(target: str, hop_budget: int) -> tuple[Path, _AliasSnapshot, int]:
    """Resolve an absolute alias with bounded metadata work, never payload I/O."""
    pending = deque(target.split("/"))
    current = Path("/")
    hops = 0
    steps = 0
    observations: list[tuple[str, tuple[int, ...], str | None]] = []
    while pending:
        steps += 1
        if steps > 2048:
            raise StableReadError("symlink_resolution_limit")
        part = pending.popleft()
        if part in {"", "."}:
            continue
        if part == "..":
            current = current.parent
            continue
        candidate = current / part
        info = os.lstat(candidate)
        if stat.S_ISLNK(info.st_mode):
            hops += 1
            if hops > hop_budget:
                raise StableReadError("symlink_broken_or_cyclic")
            text = os.readlink(candidate)
            observations.append((str(candidate), signature(info), text))
            if len(text.encode("utf-8")) > EvidenceLimits.MAX_PATH_BYTES:
                raise StableReadError("symlink_target_too_large")
            if text.startswith("/"):
                current = Path("/")
            pending.extendleft(reversed(text.split("/")))
        else:
            observations.append((str(candidate), _identity(info), None))
            if pending and not stat.S_ISDIR(info.st_mode):
                raise StableReadError("path_not_directory")
            current = candidate
    return current, tuple(observations), hops


class _Traversal:
    def __init__(self, root: Path, lexical_root: Path) -> None:
        self.root = root
        self.lexical_root = lexical_root
        self.directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        self.root_fd = os.open(root, self.directory_flags)
        self.descriptors = [self.root_fd]
        self.stack = [self.root_fd]
        self.edges: list[tuple[int, str, int]] = []
        self.links: list[tuple[int, str, os.stat_result, str]] = []
        self.aliases: list[tuple[str, Path, _AliasSnapshot, int]] = []
        self.alias_hops = 0

    def close(self) -> None:
        for fd in reversed(self.descriptors):
            os.close(fd)

    def verify(self) -> None:
        for expression, resolved, observations, budget in self.aliases:
            current_path, current_observations, _ = _resolve_absolute_metadata(expression, budget)
            if current_path != resolved or current_observations != observations:
                raise StableReadError("unstable_read")
        for parent, name, original, target in self.links:
            current = os.stat(name, dir_fd=parent, follow_symlinks=False)
            if signature(original) != signature(current) or os.readlink(name, dir_fd=parent) != target:
                raise StableReadError("unstable_read")
        for parent, name, child in reversed(self.edges):
            if _identity(os.stat(name, dir_fd=parent, follow_symlinks=False)) != _identity(os.fstat(child)):
                raise StableReadError("unstable_read")
        if _identity(os.stat(self.lexical_root)) != _identity(os.fstat(self.root_fd)):
            raise StableReadError("unstable_read")
        if _identity(os.stat(self.root, follow_symlinks=False)) != _identity(os.fstat(self.root_fd)):
            raise StableReadError("unstable_read")

    def directory(self, parent: int, name: str, initial: os.stat_result) -> None:
        child = os.open(name, self.directory_flags, dir_fd=parent)
        self.descriptors.append(child)
        if _identity(initial) != _identity(os.fstat(child)):
            raise StableReadError("unstable_read")
        self.edges.append((parent, name, child))
        self.stack.append(child)

    def expand_link(self, parent: int, name: str, initial: os.stat_result) -> list[str]:
        if len(self.links) + self.alias_hops >= _MAX_LINK_HOPS:
            raise StableReadError("symlink_broken_or_cyclic")
        target = os.readlink(name, dir_fd=parent)
        if len(target.encode("utf-8")) > EvidenceLimits.MAX_PATH_BYTES:
            raise StableReadError("symlink_target_too_large")
        self.links.append((parent, name, initial, target))
        if target.startswith("/"):
            for prefix in (self.root, self.lexical_root):
                if Path(target).is_relative_to(prefix):
                    target = Path(target).relative_to(prefix).as_posix()
                    break
            else:
                try:
                    budget = _MAX_LINK_HOPS - len(self.links) - self.alias_hops
                    resolved, observations, hops = _resolve_absolute_metadata(target, budget)
                    relative = resolved.relative_to(self.root)
                except FileNotFoundError:
                    raise StableReadError("symlink_broken_or_cyclic") from None
                except ValueError:
                    raise StableReadError("symlink_escapes_root") from None
                self.aliases.append((target, resolved, observations, budget))
                self.alias_hops += hops
                target = relative.as_posix()
            self.stack = [self.root_fd]
        return target.split("/")

    def file(
        self,
        parent: int,
        name: str,
        initial: os.stat_result,
        digest_reader: Callable[[int, int], str],
        remaining_bytes: int | None,
        charge_read: Callable[[int], None] | None,
    ) -> tuple[str, int]:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        self.descriptors.append(fd)
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or signature(initial) != signature(before):
            raise StableReadError("unstable_read")
        if before.st_size > EvidenceLimits.MAX_BOUND_FILE_BYTES:
            raise StableReadError("bound_file_too_large")
        if remaining_bytes is not None and before.st_size > remaining_bytes:
            raise StableReadError("bound_total_bytes_exceeded")
        if charge_read is not None:
            charge_read(before.st_size)
        digest = digest_reader(fd, before.st_size)
        after = os.fstat(fd)
        named = os.stat(name, dir_fd=parent, follow_symlinks=False)
        if signature(before) != signature(after) or signature(after) != signature(named):
            raise StableReadError("unstable_read")
        return digest, before.st_size

    def entry(
        self,
        path: str,
        parts: list[str],
        digest_reader: Callable[[int, int], str],
        remaining_bytes: int | None,
        charge_read: Callable[[int], None] | None,
    ) -> ContentEntry:
        pending = deque((part, False) for part in parts)
        leaf_target: str | None = None
        while pending:
            name, required = pending.popleft()
            if name in {"", "."}:
                continue
            if name == "..":
                if len(self.stack) == 1:
                    raise StableReadError("symlink_escapes_root")
                self.stack.pop()
                continue
            parent = self.stack[-1]
            try:
                initial = os.stat(name, dir_fd=parent, follow_symlinks=False)
            except FileNotFoundError:
                if required:
                    raise StableReadError("symlink_broken_or_cyclic") from None
                self.verify()
                try:
                    os.stat(name, dir_fd=parent, follow_symlinks=False)
                except FileNotFoundError:
                    return ContentEntry(path=path, state=EntryState.DELETED)
                raise StableReadError("unstable_read") from None
            if stat.S_ISLNK(initial.st_mode):
                targets = self.expand_link(parent, name, initial)
                if not pending and leaf_target is None:
                    leaf_target = self.links[-1][3]
                pending.extendleft(reversed([(part, True) for part in targets]))
                continue
            if pending:
                if not stat.S_ISDIR(initial.st_mode):
                    raise StableReadError("path_not_directory")
                self.directory(parent, name, initial)
                continue
            if leaf_target is not None:
                if not (stat.S_ISREG(initial.st_mode) or stat.S_ISDIR(initial.st_mode)):
                    raise StableReadError("path_type_unsupported")
                # Target existence/type is observed, but its bytes are NOT bound.
                if signature(initial) != signature(os.stat(name, dir_fd=parent, follow_symlinks=False)):
                    raise StableReadError("unstable_read")
                self.verify()
                return ContentEntry(path=path, state=EntryState.SYMLINK, link_target=leaf_target)
            if not stat.S_ISREG(initial.st_mode):
                raise StableReadError("path_type_unsupported")
            digest, size = self.file(parent, name, initial, digest_reader, remaining_bytes, charge_read)
            self.verify()
            return ContentEntry(path=path, state=EntryState.FILE, byte_digest=digest, byte_size=size)
        if leaf_target is not None:
            self.verify()
            return ContentEntry(path=path, state=EntryState.SYMLINK, link_target=leaf_target)
        raise StableReadError("path_invalid")


def read_entry(
    project_root: Path,
    path: str,
    *,
    digest_reader: Callable[[int, int], str],
    remaining_bytes: int | None = None,
    charge_read: Callable[[int], None] | None = None,
) -> ContentEntry:
    """One bounded anchored observation; caller owns the retry policy."""
    traversal: _Traversal | None = None
    try:
        parts = _parts(path)
        if not _safe_read_supported():
            raise StableReadError("safe_read_unavailable")
        traversal = _Traversal(project_root.resolve(strict=True), project_root.absolute())
        return traversal.entry(path, parts, digest_reader, remaining_bytes, charge_read)
    except FileNotFoundError:
        # Absence is handled only at an unobserved entry, never after an open.
        raise StableReadError("unstable_read") from None
    except NotImplementedError:
        raise StableReadError("safe_read_unavailable") from None
    except OSError as exc:
        reason = "path_unsafe" if exc.errno in {errno.ELOOP, errno.ENOTDIR} else "read_error"
        raise StableReadError(reason) from None
    except (ValueError, RuntimeError, UnicodeError) as exc:
        if isinstance(exc, StableReadError):
            raise
        raise StableReadError("path_invalid") from None
    finally:
        if traversal is not None:
            traversal.close()
