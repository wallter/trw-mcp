"""Lexical code-search index and ranking over PRD-CORE-171 manifests."""

from __future__ import annotations

import os
import re
from collections import Counter
from pathlib import Path, PurePosixPath
from typing import Literal, cast

from pydantic import BaseModel, ConfigDict, Field

from trw_mcp.code_index.chunking import CodeChunk, chunk_source_file
from trw_mcp.code_index.models import CodeIndexManifest
from trw_mcp.code_index.storage import default_manifest_path, load_manifest

CHUNK_INDEX_SCHEMA_VERSION: Literal["code-chunk-index/v1"] = "code-chunk-index/v1"
CHUNK_INDEX_RELATIVE_PATH: str = ".trw/code-index/chunks.json"
MAX_SNIPPET_LINES: int = 12
MAX_SNIPPET_CHARS: int = 800

_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+")

ErrorCode = Literal["", "missing_index", "invalid_repo", "invalid_path", "query_empty", "dependency_missing"]
SearchMode = Literal["lexical", "semantic"]


class ChunkIndexStats(BaseModel):
    """Chunk lifecycle counters for one manifest reconciliation."""

    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    total_chunks: int = Field(ge=0)
    added_files: int = Field(ge=0)
    unchanged_files: int = Field(ge=0)
    modified_files: int = Field(ge=0)
    deleted_files: int = Field(ge=0)
    failed_files: int = Field(ge=0)


class CodeChunkIndex(BaseModel):
    """Persisted chunk index scoped to one repository root."""

    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    schema_version: Literal["code-chunk-index/v1"]
    repo_root: str
    manifest_sha256: str | None
    chunks: tuple[CodeChunk, ...]
    stats: ChunkIndexStats


class ChunkIndexUpdateResult(BaseModel):
    """Return value for chunk-index reconciliation."""

    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    index: CodeChunkIndex
    index_path: str
    stats: ChunkIndexStats


class LineRange(BaseModel):
    """Public line range in a search hit."""

    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    start: int = Field(ge=1)
    end: int = Field(ge=1)


class SymbolRef(BaseModel):
    """Public symbol metadata in a search hit."""

    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    name: str | None
    kind: str


class CodeSearchHit(BaseModel):
    """Privacy-safe code-search result."""

    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    path: str
    line_range: LineRange
    symbol: SymbolRef
    symbol_name: str | None
    symbol_kind: str
    score: float
    reason: str
    snippet: str


class CodeSearchResponse(BaseModel):
    """Structured success/failure response for code search and symbol lookup."""

    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    status: Literal["ok", "failed"]
    mode: SearchMode
    query: str
    results: tuple[CodeSearchHit, ...]
    error_code: ErrorCode = ""
    error: str = ""
    remediation: str = ""


def default_chunk_index_path(repo_root: Path | str) -> Path:
    """Return the canonical chunk-index path for ``repo_root``."""

    return Path(repo_root) / CHUNK_INDEX_RELATIVE_PATH


def update_chunk_index(repo_root: Path | str) -> ChunkIndexUpdateResult:
    """Reconcile chunks with the current PRD-CORE-171 manifest."""

    root = _validated_repo_root(repo_root)
    manifest_path = default_manifest_path(root)
    manifest = load_manifest(manifest_path)
    if manifest is None:
        raise FileNotFoundError("code-index manifest is missing or invalid; run trw_code_index_update first")

    previous = load_chunk_index(default_chunk_index_path(root))
    previous_by_path = _chunks_by_path(previous.chunks if previous is not None else ())
    manifest_by_path = {row.path: row for row in manifest.files}
    chunks: list[CodeChunk] = []
    added_files = 0
    unchanged_files = 0
    modified_files = 0
    failed_files = 0

    for row in manifest.files:
        prior_chunks = previous_by_path.get(row.path, ())
        if prior_chunks and all(chunk.file_sha256 == row.sha256 for chunk in prior_chunks):
            chunks.extend(prior_chunks)
            unchanged_files += 1
            continue

        file_path = root / row.path
        try:
            new_chunks = chunk_source_file(root, file_path, file_sha256=row.sha256)
        except (OSError, UnicodeDecodeError, ValueError):
            failed_files += 1
            continue
        chunks.extend(new_chunks)
        if prior_chunks:
            modified_files += 1
        else:
            added_files += 1

    deleted_files = len(set(previous_by_path) - set(manifest_by_path))
    stats = ChunkIndexStats(
        total_chunks=len(chunks),
        added_files=added_files,
        unchanged_files=unchanged_files,
        modified_files=modified_files,
        deleted_files=deleted_files,
        failed_files=failed_files,
    )
    index = CodeChunkIndex(
        schema_version=CHUNK_INDEX_SCHEMA_VERSION,
        repo_root=str(root),
        manifest_sha256=_manifest_fingerprint(manifest),
        chunks=tuple(sorted(chunks, key=lambda chunk: (chunk.path, chunk.start_line, chunk.chunk_id))),
        stats=stats,
    )
    index_path = default_chunk_index_path(root)
    save_chunk_index(index_path, index)
    return ChunkIndexUpdateResult(index=index, index_path=str(index_path), stats=stats)


def lexical_search(
    repo_root: Path | str,
    *,
    query: str,
    top_k: int = 10,
    path: str | None = None,
) -> CodeSearchResponse:
    """Return ranked lexical matches without optional parser or embedding dependencies."""

    validation = _validate_request(repo_root, query=query, path=path, mode="lexical")
    if isinstance(validation, CodeSearchResponse):
        return validation
    root, safe_path = validation
    try:
        index = update_chunk_index(root).index
    except FileNotFoundError as exc:
        return _failure("lexical", query, "missing_index", str(exc), "Run trw_code_index_update for this repo first.")
    except NotADirectoryError as exc:
        return _failure("lexical", query, "invalid_repo", str(exc), "Pass an existing repository directory.")

    query_terms = _terms(query)
    hits: list[CodeSearchHit] = []
    for chunk in _filter_chunks(index.chunks, safe_path):
        score = _lexical_score(query_terms, chunk)
        if score <= 0:
            continue
        hits.append(_hit(chunk, score=score, reason=f"lexical token match: {_matched_terms(query_terms, chunk)}"))

    ranked = tuple(sorted(hits, key=lambda hit: (-hit.score, hit.path, hit.line_range.start))[: _bounded_top_k(top_k)])
    return CodeSearchResponse(status="ok", mode="lexical", query=query, results=ranked)


def symbol_search(
    repo_root: Path | str,
    *,
    symbol: str,
    top_k: int = 10,
    path: str | None = None,
) -> CodeSearchResponse:
    """Return exact symbol matches before fuzzy symbol matches."""

    validation = _validate_request(repo_root, query=symbol, path=path, mode="lexical")
    if isinstance(validation, CodeSearchResponse):
        return validation
    root, safe_path = validation
    try:
        index = update_chunk_index(root).index
    except FileNotFoundError as exc:
        return _failure("lexical", symbol, "missing_index", str(exc), "Run trw_code_index_update for this repo first.")
    except NotADirectoryError as exc:
        return _failure("lexical", symbol, "invalid_repo", str(exc), "Pass an existing repository directory.")

    needle = symbol.lower()
    hits: list[CodeSearchHit] = []
    for chunk in _filter_chunks(index.chunks, safe_path):
        if chunk.symbol_name is None:
            continue
        candidate = chunk.symbol_name.lower()
        if candidate == needle:
            hits.append(_hit(chunk, score=100.0, reason="exact symbol match"))
        elif needle in candidate:
            hits.append(_hit(chunk, score=50.0 + (len(needle) / len(candidate)), reason="fuzzy symbol match"))

    ranked = tuple(sorted(hits, key=lambda hit: (-hit.score, hit.path, hit.line_range.start))[: _bounded_top_k(top_k)])
    return CodeSearchResponse(status="ok", mode="lexical", query=symbol, results=ranked)


def load_chunk_index(path: Path) -> CodeChunkIndex | None:
    """Load a chunk index, returning ``None`` for missing or invalid state."""

    if not path.exists():
        return None
    try:
        return CodeChunkIndex.model_validate_json(path.read_text(encoding="utf-8"))
    except ValueError:
        return None


def save_chunk_index(path: Path, index: CodeChunkIndex) -> None:
    """Persist a chunk index via atomic replace."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.tmp")
    temp_path.write_text(f"{index.model_dump_json(indent=2)}\n", encoding="utf-8")
    try:
        os.replace(temp_path, path)
    except OSError:
        if temp_path.exists():
            temp_path.unlink()
        raise


def _validated_repo_root(repo_root: Path | str) -> Path:
    root = Path(repo_root).resolve()
    if not root.is_dir():
        raise NotADirectoryError(f"repo_root is not a directory: {root}")
    return root


def _validate_request(
    repo_root: Path | str,
    *,
    query: str,
    path: str | None,
    mode: SearchMode,
) -> tuple[Path, str | None] | CodeSearchResponse:
    stripped_query = query.strip()
    if not stripped_query:
        return _failure(mode, query, "query_empty", "query must not be empty", "Provide a non-empty query.")
    try:
        root = _validated_repo_root(repo_root)
    except NotADirectoryError as exc:
        return _failure(mode, query, "invalid_repo", str(exc), "Pass an existing repository directory.")
    safe_path = _normalize_path_filter(path)
    if path is not None and safe_path is None:
        return _failure(mode, query, "invalid_path", "path must be repo-relative and must not contain '..'", "")
    return root, safe_path


def _normalize_path_filter(path: str | None) -> str | None:
    if path is None:
        return None
    cleaned = path.replace("\\", "/").strip()
    if cleaned in {"", ".", "./"}:
        return None
    posix = PurePosixPath(cleaned)
    if posix.is_absolute() or ".." in posix.parts:
        return None
    return posix.as_posix().strip("/")


def _filter_chunks(chunks: tuple[CodeChunk, ...], path: str | None) -> tuple[CodeChunk, ...]:
    if path is None:
        return chunks
    return tuple(chunk for chunk in chunks if chunk.path == path or chunk.path.startswith(f"{path}/"))


def _chunks_by_path(chunks: tuple[CodeChunk, ...]) -> dict[str, tuple[CodeChunk, ...]]:
    grouped: dict[str, list[CodeChunk]] = {}
    for chunk in chunks:
        grouped.setdefault(chunk.path, []).append(chunk)
    return {path: tuple(path_chunks) for path, path_chunks in grouped.items()}


def _manifest_fingerprint(manifest: CodeIndexManifest) -> str:
    seed = "\n".join(f"{row.path}:{row.sha256}" for row in manifest.files)
    import hashlib

    return hashlib.sha256(seed.encode("utf-8")).hexdigest()


def _terms(text: str) -> Counter[str]:
    return Counter(token.lower() for token in _TOKEN_RE.findall(text))


def _lexical_score(query_terms: Counter[str], chunk: CodeChunk) -> float:
    haystack_terms = _terms(f"{chunk.symbol_name or ''} {chunk.signature} {chunk.docstring_summary} {chunk.text}")
    score = 0.0
    for term, query_count in query_terms.items():
        count = haystack_terms.get(term, 0)
        if count:
            score += float(min(count, query_count) * 2)
        if chunk.symbol_name is not None and term in chunk.symbol_name.lower():
            score += 3.0
        if term in chunk.path.lower():
            score += 1.0
    return score


def _matched_terms(query_terms: Counter[str], chunk: CodeChunk) -> str:
    haystack = f"{chunk.symbol_name or ''} {chunk.signature} {chunk.docstring_summary} {chunk.text}".lower()
    matches = [term for term in query_terms if term in haystack]
    return ", ".join(matches)


def _hit(chunk: CodeChunk, *, score: float, reason: str) -> CodeSearchHit:
    return CodeSearchHit(
        path=chunk.path,
        line_range=LineRange(start=chunk.start_line, end=chunk.end_line),
        symbol=SymbolRef(name=chunk.symbol_name, kind=chunk.symbol_kind),
        symbol_name=chunk.symbol_name,
        symbol_kind=chunk.symbol_kind,
        score=score,
        reason=reason,
        snippet=_capped_snippet(chunk.text),
    )


def _capped_snippet(text: str) -> str:
    lines = text.splitlines()[:MAX_SNIPPET_LINES]
    snippet = "\n".join(lines)
    if len(snippet) <= MAX_SNIPPET_CHARS:
        return snippet
    return f"{snippet[: MAX_SNIPPET_CHARS - 1]}…"


def _failure(mode: SearchMode, query: str, error_code: ErrorCode, error: str, remediation: str) -> CodeSearchResponse:
    return CodeSearchResponse(
        status="failed",
        mode=mode,
        query=query,
        results=(),
        error_code=error_code,
        error=error,
        remediation=remediation,
    )


def _bounded_top_k(top_k: int) -> int:
    return max(1, min(top_k, 50))


def response_to_dict(response: CodeSearchResponse) -> dict[str, object]:
    """Convert a response to a JSON-compatible plain dict for MCP boundaries."""

    return cast("dict[str, object]", response.model_dump(mode="json"))


__all__ = [
    "CHUNK_INDEX_RELATIVE_PATH",
    "CHUNK_INDEX_SCHEMA_VERSION",
    "MAX_SNIPPET_CHARS",
    "MAX_SNIPPET_LINES",
    "ChunkIndexStats",
    "CodeChunkIndex",
    "CodeSearchHit",
    "CodeSearchResponse",
    "ErrorCode",
    "LineRange",
    "SearchMode",
    "SymbolRef",
    "default_chunk_index_path",
    "lexical_search",
    "load_chunk_index",
    "response_to_dict",
    "save_chunk_index",
    "symbol_search",
    "update_chunk_index",
]
