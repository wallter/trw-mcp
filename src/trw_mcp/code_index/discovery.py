"""Pure-Python indexable file discovery for the local code index."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

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


@dataclass(frozen=True)
class DiscoveryResult:
    """Result of applying default code-index discovery filters."""

    files: tuple[Path, ...]
    skipped_count: int


def normalize_repo_relative_path(repo_root: Path, path: Path) -> str:
    """Return a stable POSIX path for ``path`` relative to ``repo_root``."""

    return path.resolve().relative_to(repo_root.resolve()).as_posix()


def _is_within_excluded_dir(repo_root: Path, path: Path, exclude_dirs: frozenset[str]) -> bool:
    relative_parts = path.resolve().relative_to(repo_root.resolve()).parts
    return any(part in exclude_dirs for part in relative_parts[:-1])


def _contains_nul_byte(path: Path) -> bool:
    with path.open("rb") as handle:
        return b"\x00" in handle.read(4096)


def _iter_candidate_files(repo_root: Path, path_limits: tuple[Path, ...]) -> Iterable[Path]:
    if not path_limits:
        yield from (path for path in repo_root.rglob("*") if path.is_file())
        return

    for limit in path_limits:
        if limit.is_file():
            yield limit
        elif limit.is_dir():
            yield from (path for path in limit.rglob("*") if path.is_file())


def _resolve_path_limits(repo_root: Path, paths: Iterable[str] | None) -> tuple[Path, ...]:
    if paths is None:
        return ()
    resolved_limits: list[Path] = []
    resolved_repo = repo_root.resolve()
    for raw_path in paths:
        candidate = (repo_root / raw_path).resolve()
        try:
            candidate.relative_to(resolved_repo)
        except ValueError:
            continue
        resolved_limits.append(candidate)
    return tuple(resolved_limits)


def discover_indexable_files(
    repo_root: Path | str,
    *,
    paths: Iterable[str] | None = None,
    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
    exclude_dirs: frozenset[str] = DEFAULT_EXCLUDE_DIRS,
    include_extensions: frozenset[str] = DEFAULT_INCLUDE_EXTENSIONS,
) -> DiscoveryResult:
    """Discover files eligible for SHA-256 indexing using conservative defaults."""

    root = Path(repo_root).resolve()
    path_limits = _resolve_path_limits(root, paths)
    files: list[Path] = []
    skipped_count = 0

    for candidate in _iter_candidate_files(root, path_limits):
        resolved = candidate.resolve()
        try:
            resolved.relative_to(root)
        except ValueError:
            skipped_count += 1
            continue
        if _is_within_excluded_dir(root, resolved, exclude_dirs):
            skipped_count += 1
            continue
        if resolved.suffix.lower() not in include_extensions:
            continue
        stat = resolved.stat()
        if stat.st_size > max_file_bytes:
            skipped_count += 1
            continue
        if _contains_nul_byte(resolved):
            skipped_count += 1
            continue
        files.append(resolved)

    return DiscoveryResult(
        files=tuple(sorted(files, key=lambda item: normalize_repo_relative_path(root, item))),
        skipped_count=skipped_count,
    )


__all__ = [
    "DEFAULT_EXCLUDE_DIRS",
    "DEFAULT_INCLUDE_EXTENSIONS",
    "DEFAULT_MAX_FILE_BYTES",
    "DiscoveryResult",
    "discover_indexable_files",
    "normalize_repo_relative_path",
]
