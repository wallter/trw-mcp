"""The code-index build: the SHA-256 manifest and the chunk store, inside the build budgets.

This is the only writer of the index (PRD-CORE-300-FR15); queries never build.
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from trw_mcp.code_index.bounds import MAX_INDEXED_FILE_BYTES, CodeIndexBounds, Deadline
from trw_mcp.code_index.discovery import (
    DEFAULT_EXCLUDE_DIRS,
    DEFAULT_INCLUDE_EXTENSIONS,
    DEFAULT_MAX_FILE_BYTES,
    discover_indexable_files,
    normalize_repo_relative_path,
    read_indexed_file,
)
from trw_mcp.code_index.models import (
    CODE_INDEX_SCHEMA_VERSION,
    CodeIndexFileRow,
    CodeIndexManifest,
    CodeIndexStats,
)
from trw_mcp.code_index.storage import (
    MAX_MANIFEST_BYTES,
    default_manifest_path,
    load_manifest,
    manifest_text,
    save_manifest,
)
from trw_mcp.code_index.store import ChunkIndexStats, build_chunk_store, require_supported_runtime
from trw_mcp.tools._sidecar_substrate import resolve_git_sha


@dataclass(frozen=True)
class CodeIndexUpdateResult:
    """Result returned by the updater and MCP tool wrapper."""

    manifest: CodeIndexManifest
    manifest_path: Path
    stats: CodeIndexStats
    chunk_stats: ChunkIndexStats


def _path_is_in_scope(path: str, scopes: tuple[str, ...] | None) -> bool:
    if scopes is None:
        return True
    return any(path == scope or path.startswith(f"{scope}/") for scope in scopes)


def _normalize_scopes(paths: Iterable[str] | None) -> tuple[str, ...] | None:
    if paths is None:
        return None
    normalized: dict[str, None] = {}
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
        if clean:
            normalized.setdefault(clean)
    return tuple(normalized)


def update_code_index(
    repo_root: Path | str,
    *,
    force: bool = False,
    paths: Iterable[str] | None = None,
    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
    exclude_dirs: frozenset[str] = DEFAULT_EXCLUDE_DIRS,
    include_extensions: frozenset[str] = DEFAULT_INCLUDE_EXTENSIONS,
    bounds: CodeIndexBounds | None = None,
) -> CodeIndexUpdateResult:
    """Build the manifest and the chunk store, classifying files by SHA-256 deltas.

    Raises :class:`~trw_mcp.code_index.bounds.IndexBoundExceeded` when the
    build crosses a budget, and ``ValueError`` for a scoped update over an
    unreadable manifest; the published store and manifest are then unchanged.
    The store is always rebuilt from source: a scoped update rescans only its
    paths, but every manifest file is chunked again from its current bytes.
    """

    require_supported_runtime()  # before the walk: a refused build does no work and writes nothing
    budgets = bounds or CodeIndexBounds()
    deadline = Deadline(budgets.build_timeout_seconds, "build_timeout_seconds")
    root = Path(repo_root).resolve()
    manifest_path = default_manifest_path(root)
    previous = load_manifest(manifest_path)
    path_filters = tuple(paths) if paths is not None else None
    scopes = _normalize_scopes(path_filters)  # None for no paths or a whole-tree one such as "."
    if scopes is not None and previous is None and os.path.lexists(manifest_path):
        # A scoped update carries every out-of-scope row from this manifest: an unreadable, oversized or
        # crafted one would publish the scope alone and drop the rest (rc8 pre-C12 sol review).
        raise ValueError("the code index manifest is unreadable: run `trw-mcp code index` without --paths once")
    previous_rows = {row.path: row for row in previous.files} if previous is not None else {}
    discovery = discover_indexable_files(
        root,
        paths=path_filters,
        max_file_bytes=max_file_bytes,
        exclude_dirs=exclude_dirs,
        include_extensions=include_extensions,
        bounds=budgets,
        deadline=deadline,
    )
    scoped_previous = {path: row for path, row in previous_rows.items() if _path_is_in_scope(path, scopes)}
    preserved_rows = [row for path, row in previous_rows.items() if not _path_is_in_scope(path, scopes)]

    now = datetime.now(timezone.utc)
    added = unchanged = modified = raced = 0
    discovery_max_bytes = min(max_file_bytes, MAX_INDEXED_FILE_BYTES)
    discovered_rows: list[CodeIndexFileRow] = []
    for file_path in discovery.files:
        deadline.check()
        try:
            relative_path = normalize_repo_relative_path(root, file_path)
            data = read_indexed_file(root, relative_path, discovery_max_bytes)
        except (OSError, ValueError):  # swapped for a symlink or a non-file, or grown past the cap since the walk
            raced += 1
            continue
        sha256 = hashlib.sha256(data).hexdigest()
        previous_row = None if force else scoped_previous.get(relative_path)
        if previous_row is None:
            added += 1
        elif previous_row.sha256 == sha256:
            unchanged += 1
        else:
            modified += 1
        discovered_rows.append(
            CodeIndexFileRow(path=relative_path, sha256=sha256, size_bytes=len(data), indexed_at=now)
        )

    all_rows = sorted([*preserved_rows, *discovered_rows], key=lambda row: row.path)
    deleted = len(previous_rows.keys() - {row.path for row in all_rows})
    stats = CodeIndexStats(
        total_files=len(all_rows),
        added=added,
        unchanged=unchanged,
        modified=modified,
        deleted=deleted,
        skipped=discovery.skipped_count + raced,
    )
    manifest = CodeIndexManifest(
        schema_version=CODE_INDEX_SCHEMA_VERSION,
        repo_root=str(root),
        git_head=resolve_git_sha(root),
        generated_at=now,
        files=all_rows,
        stats=stats,
    )
    # The next build must be able to read this manifest back, so one too large is refused before anything publishes.
    if len(manifest_text(manifest).encode("utf-8")) > MAX_MANIFEST_BYTES:
        raise ValueError(
            f"the manifest for {len(all_rows)} files exceeds {MAX_MANIFEST_BYTES} bytes: lower build_max_files"
        )
    chunk_stats = build_chunk_store(root, manifest, deadline=deadline)
    save_manifest(manifest_path, manifest)  # build_chunk_store already refused a symlinked index directory
    return CodeIndexUpdateResult(manifest=manifest, manifest_path=manifest_path, stats=stats, chunk_stats=chunk_stats)


__all__ = [
    "CodeIndexUpdateResult",
    "update_code_index",
]
