"""Pure-Python indexable file discovery for the local code index: a pruning, bounded walk."""

from __future__ import annotations

import os
import stat
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from trw_mcp.code_index.bounds import MAX_INDEXED_FILE_BYTES, CodeIndexBounds, Deadline, IndexBoundExceeded

DEFAULT_MAX_FILE_BYTES: int = 1_000_000
DEFAULT_EXCLUDE_DIRS: frozenset[str] = frozenset(
    {
        ".git",
        ".hg",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".tox",
        ".trw",
        ".venv",
        "__pycache__",
        "build",
        "coverage",
        "dist",
        "env",
        "node_modules",
        "site-packages",
        "target",
        "venv",
    }
)
DEFAULT_INCLUDE_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".c",
        ".cc",
        ".cpp",
        ".css",
        ".go",
        ".h",
        ".hpp",
        ".html",
        ".java",
        ".js",
        ".json",
        ".jsx",
        ".kt",
        ".md",
        ".py",
        ".rs",
        ".sh",
        ".sql",
        ".toml",
        ".ts",
        ".tsx",
        ".yaml",
        ".yml",
    }
)


#: Repo-relative directories pruned whatever their contents. Nested linked
#: worktrees are caught by their ``.git`` marker too; this names the known
#: parking place so a marker-less copy there is pruned as well.
DEFAULT_EXCLUDE_RELATIVE: frozenset[str] = frozenset({".claude/worktrees"})


@dataclass(frozen=True)
class DiscoveryResult:
    """Result of applying default code-index discovery filters."""

    files: tuple[Path, ...]
    skipped_count: int
    unreadable_dirs: int = 0


def normalize_repo_relative_path(repo_root: Path, path: Path) -> str:
    """Return the POSIX path of a discovered *path* under *repo_root*, lexically: never through a symlink.

    Resolving would follow a symlink swapped in after the walk to another in-root file, one the walk
    excluded (rc8 pre-C12 sol review); :func:`read_indexed_file` refuses any swapped component instead.
    """

    return path.relative_to(repo_root).as_posix()


def _open_under(repo_root: Path, relative_path: str) -> int:
    """Open *relative_path* for reading, each directory relative to its parent: no component may be a symlink."""
    *directories, name = PurePosixPath(relative_path).parts
    parent = os.open(repo_root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for directory in directories:
            child = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
            os.close(parent)
            parent = child
        return os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
    finally:
        os.close(parent)


def read_indexed_file(repo_root: Path, relative_path: str, max_bytes: int) -> bytes:
    """Read one discovered file whole, following no symlink below *repo_root*, or raise ``ValueError``/``OSError``.

    The walk sized the file, but the build reopens it later, and by then any component can be a symlink,
    the file can have grown, or it can be a FIFO (rc8 pre-C12 sol review). Every directory is opened
    relative to its parent with ``O_NOFOLLOW``, the opened file must be regular and within *max_bytes*
    by ``fstat``, and at most ``max_bytes + 1`` bytes are read.
    """
    fd = _open_under(repo_root, relative_path)
    with os.fdopen(fd, "rb") as handle:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > max_bytes:
            raise ValueError(f"{relative_path} is not a regular file of at most {max_bytes} bytes")
        data = handle.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise ValueError(f"{relative_path} grew past {max_bytes} bytes while it was read")
    return data


def _skip_as_binary(root: Path, path: Path) -> bool:
    """True for a NUL in the first 4 KiB, or for anything the probe cannot open as a regular file.

    The entry was a regular file when listed, but a FIFO or symlink swapped in since must neither block
    the walk nor be followed (rc8 pre-C12 sol review).
    """
    try:
        fd = _open_under(root, normalize_repo_relative_path(root, path))
    except (OSError, ValueError):
        return True
    with os.fdopen(fd, "rb") as handle:
        return not stat.S_ISREG(os.fstat(fd).st_mode) or b"\x00" in handle.read(4096)


def _holds_git_marker(directory: str) -> bool:
    """A ``.git`` file, directory or malformed entry marks a nested checkout."""

    return os.path.lexists(os.path.join(directory, ".git"))


class _Walk:
    """One bounded, pruning walk below ``root`` (PRD-CORE-300-FR15).

    Excluded directories are dropped when they are listed, so the walk never
    enters them: nested worktrees, clones and submodules (any ``.git``
    marker), the configured names, and ``DEFAULT_EXCLUDE_RELATIVE``. The root
    itself stays eligible even when it is a linked worktree. Symlinks are
    never followed.
    """

    def __init__(
        self,
        root: Path,
        *,
        exclude_dirs: frozenset[str],
        include_extensions: frozenset[str],
        max_file_bytes: int,
        bounds: CodeIndexBounds,
        deadline: Deadline,
    ) -> None:
        self.root = root
        self.exclude_dirs = exclude_dirs
        self.include_extensions = include_extensions
        self.max_file_bytes = min(max_file_bytes, MAX_INDEXED_FILE_BYTES)  # the store's value limit rests on it
        self.bounds = bounds
        self.deadline = deadline
        self.files: list[Path] = []
        self.skipped = 0
        self.unreadable = 0
        self.entries = 0
        self.source_bytes = 0

    def pruned(self, directory: Path) -> bool:
        relative = directory.relative_to(self.root).as_posix()
        return (
            directory.name in self.exclude_dirs
            or relative in DEFAULT_EXCLUDE_RELATIVE
            or directory.is_symlink()
            or _holds_git_marker(str(directory))
        )

    def ancestry_pruned(self, target: Path) -> bool:
        """True when ``target`` or any directory between it and the root is pruned."""

        relative = target.relative_to(self.root)
        current = self.root
        for part in relative.parts:
            current = current / part
            if (current.is_dir() or current.is_symlink()) and self.pruned(current):
                return True
        return False

    def _count_entry(self) -> None:
        self.entries += 1
        if self.entries > self.bounds.build_max_entries:
            raise IndexBoundExceeded("build_max_entries", self.bounds.build_max_entries)

    def consider_file(self, path: Path, size: int) -> None:
        if path.suffix.lower() not in self.include_extensions:
            return
        if size > self.max_file_bytes or _skip_as_binary(self.root, path):
            self.skipped += 1
            return
        self.files.append(path)
        if len(self.files) > self.bounds.build_max_files:
            raise IndexBoundExceeded("build_max_files", self.bounds.build_max_files)
        self.source_bytes += size
        if self.source_bytes > self.bounds.build_max_source_bytes:
            raise IndexBoundExceeded("build_max_source_bytes", self.bounds.build_max_source_bytes)

    def walk(self, start: Path) -> None:
        stack = [start]
        while stack:
            self.deadline.check()
            directory = stack.pop()
            # Both budgets apply while scandir yields, so a huge directory stops at
            # the entry cap or the deadline instead of being materialized first;
            # only the bounded set is sorted. Entries consumed before a mid-listing
            # OSError stay counted: that work was done even though the directory
            # is then skipped.
            entries: list[os.DirEntry[str]] = []
            try:
                with os.scandir(directory) as listing:
                    for entry in listing:
                        self._count_entry()
                        self.deadline.check()
                        entries.append(entry)
            except OSError:
                self.skipped += 1
                self.unreadable += 1
                continue
            entries.sort(key=lambda entry: entry.name)
            for entry in entries:
                self.deadline.check()
                if entry.is_symlink():
                    self.skipped += 1
                    continue
                path = Path(entry.path)
                if entry.is_dir(follow_symlinks=False):
                    if self.pruned(path):
                        self.skipped += 1
                    else:
                        stack.append(path)
                elif entry.is_file(follow_symlinks=False):
                    self.consider_file(path, entry.stat(follow_symlinks=False).st_size)

    def visit_limit(self, limit: Path, *, file_only: bool = False) -> None:
        """One explicit path limit, charged to the same entry cap and deadline as the walk.

        A ``file_only`` limit is never walked, even if a directory replaced it; a later read opens it as a
        regular file or skips it.
        """

        self._count_entry()
        self.deadline.check()
        if limit != self.root and (not os.path.lexists(limit) or self.ancestry_pruned(limit)):
            return
        if limit.is_file():
            self.consider_file(limit, limit.stat().st_size)
        elif limit.is_dir() and not file_only:
            self.walk(limit)


def _resolve_path_limits(repo_root: Path, paths: Iterable[str] | None) -> tuple[Path, ...]:
    """Repo-relative limits, lexically inside the root; symlinks are judged by the walk, not resolved."""

    if paths is None:
        return ()
    resolved_limits: list[Path] = []
    for raw_path in paths:
        posix = PurePosixPath(raw_path.replace("\\", "/").strip())
        if posix.is_absolute() or ".." in posix.parts:
            continue
        resolved_limits.append(repo_root.joinpath(*posix.parts) if posix.parts else repo_root)
    return tuple(resolved_limits)


def discover_indexable_files(
    repo_root: Path | str,
    *,
    paths: Iterable[str] | None = None,
    files: Iterable[str] = (),
    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
    exclude_dirs: frozenset[str] = DEFAULT_EXCLUDE_DIRS,
    include_extensions: frozenset[str] = DEFAULT_INCLUDE_EXTENSIONS,
    bounds: CodeIndexBounds | None = None,
    deadline: Deadline | None = None,
) -> DiscoveryResult:
    """Discover files eligible for indexing with a pruning, bounded walk.

    Raises :class:`IndexBoundExceeded` when the walk crosses a build budget.
    An explicit path limit inside a pruned directory yields nothing. Each of
    *files* is considered as one file under the same filters and budgets and
    never walked, as a directory or the root.
    """

    root = Path(repo_root).resolve()
    budgets = bounds or CodeIndexBounds()
    walk = _Walk(
        root,
        exclude_dirs=exclude_dirs,
        include_extensions=include_extensions,
        max_file_bytes=max_file_bytes,
        bounds=budgets,
        deadline=deadline or Deadline(budgets.build_timeout_seconds, "build_timeout_seconds"),
    )
    limits = _resolve_path_limits(root, paths)
    if paths is None:
        walk.walk(root)
    for limit in limits:
        walk.visit_limit(limit)
    for file_path in files:  # verbatim: the scope inputs' strip would turn a manifest row " a.py" into "a.py"
        posix = PurePosixPath(file_path)
        if posix.parts and not posix.is_absolute() and ".." not in posix.parts:
            walk.visit_limit(root.joinpath(*posix.parts), file_only=True)

    unique = dict.fromkeys(walk.files)
    return DiscoveryResult(
        files=tuple(sorted(unique, key=lambda item: item.relative_to(root).as_posix())),
        skipped_count=walk.skipped,
        unreadable_dirs=walk.unreadable,
    )


__all__ = [
    "DEFAULT_EXCLUDE_DIRS",
    "DEFAULT_EXCLUDE_RELATIVE",
    "DEFAULT_INCLUDE_EXTENSIONS",
    "DEFAULT_MAX_FILE_BYTES",
    "DiscoveryResult",
    "discover_indexable_files",
    "normalize_repo_relative_path",
    "read_indexed_file",
]
