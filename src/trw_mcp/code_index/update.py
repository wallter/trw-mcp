"""SHA-256 incremental update orchestration for the local code index."""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from trw_mcp.code_index.discovery import (
    DEFAULT_EXCLUDE_DIRS,
    DEFAULT_INCLUDE_EXTENSIONS,
    DEFAULT_MAX_FILE_BYTES,
    discover_indexable_files,
    normalize_repo_relative_path,
)
from trw_mcp.code_index.models import (
    CODE_INDEX_SCHEMA_VERSION,
    CodeIndexFileRow,
    CodeIndexManifest,
    CodeIndexStats,
)
from trw_mcp.code_index.storage import default_manifest_path, load_manifest, save_manifest
from trw_mcp.tools._sidecar_substrate import resolve_git_sha


@dataclass(frozen=True)
class CodeIndexUpdateResult:
    """Result returned by the updater and MCP tool wrapper."""

    manifest: CodeIndexManifest
    manifest_path: Path
    stats: CodeIndexStats


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _path_is_in_scope(path: str, scopes: tuple[str, ...] | None) -> bool:
    if scopes is None:
        return True
    return any(path == scope or path.startswith(f"{scope}/") for scope in scopes)


def _normalize_scopes(paths: Iterable[str] | None) -> tuple[str, ...] | None:
    if paths is None:
        return None
    normalized: list[str] = []
    for raw_path in paths:
        raw_clean = raw_path.replace("\\", "/").strip()
        if raw_clean in {"", ".", "./"}:
            return None
        posix_path = PurePosixPath(raw_clean)
        if posix_path.is_absolute() or ".." in posix_path.parts:
            continue
        clean = posix_path.as_posix().strip("/")
        if clean in {"", "."}:
            return None
        if clean and clean not in normalized:
            normalized.append(clean)
    return tuple(normalized)


def update_code_index(
    repo_root: Path | str,
    *,
    force: bool = False,
    paths: Iterable[str] | None = None,
    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
    exclude_dirs: frozenset[str] = DEFAULT_EXCLUDE_DIRS,
    include_extensions: frozenset[str] = DEFAULT_INCLUDE_EXTENSIONS,
) -> CodeIndexUpdateResult:
    """Update the local manifest and classify files by SHA-256 deltas."""

    root = Path(repo_root).resolve()
    manifest_path = default_manifest_path(root)
    previous = load_manifest(manifest_path)
    path_filters = tuple(paths) if paths is not None else None
    scopes = _normalize_scopes(path_filters)
    discovery = discover_indexable_files(
        root,
        paths=path_filters,
        max_file_bytes=max_file_bytes,
        exclude_dirs=exclude_dirs,
        include_extensions=include_extensions,
    )
    previous_rows = {row.path: row for row in previous.files} if previous is not None else {}
    scoped_previous = {
        path: row
        for path, row in previous_rows.items()
        if _path_is_in_scope(path, scopes)
    }
    preserved_rows = [
        row
        for path, row in previous_rows.items()
        if not _path_is_in_scope(path, scopes)
    ]

    now = datetime.now(timezone.utc)
    added = 0
    unchanged = 0
    modified = 0
    discovered_rows: list[CodeIndexFileRow] = []
    for file_path in discovery.files:
        relative_path = normalize_repo_relative_path(root, file_path)
        sha256 = _sha256_file(file_path)
        size_bytes = file_path.stat().st_size
        previous_row = None if force else scoped_previous.get(relative_path)
        if previous_row is None:
            added += 1
        elif previous_row.sha256 == sha256:
            unchanged += 1
        else:
            modified += 1
        discovered_rows.append(
            CodeIndexFileRow(
                path=relative_path,
                sha256=sha256,
                size_bytes=size_bytes,
                indexed_at=now,
            )
        )

    discovered_paths = {row.path for row in discovered_rows}
    deleted = len(set(scoped_previous) - discovered_paths)

    all_rows = sorted(
        [*preserved_rows, *discovered_rows],
        key=lambda row: row.path,
    )
    stats = CodeIndexStats(
        total_files=len(all_rows),
        added=added,
        unchanged=unchanged,
        modified=modified,
        deleted=deleted,
        skipped=discovery.skipped_count,
    )
    manifest = CodeIndexManifest(
        schema_version=CODE_INDEX_SCHEMA_VERSION,
        repo_root=str(root),
        git_head=resolve_git_sha(root),
        generated_at=now,
        files=all_rows,
        stats=stats,
    )
    save_manifest(manifest_path, manifest)
    return CodeIndexUpdateResult(manifest=manifest, manifest_path=manifest_path, stats=stats)


__all__ = [
    "CodeIndexUpdateResult",
    "update_code_index",
]
