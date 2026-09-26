"""Lexical code search and symbol lookup over the chunk store (PRD-CORE-300-FR15).

A query only reads: it opens the published store read-only, asks SQLite for
candidate rows, and keeps the best ``top_k`` in a heap. Candidates, response
size and wall-clock are bounded (:mod:`trw_mcp.code_index.bounds`); crossing
one returns ``index_bound_exceeded`` naming the key. Building the store is the
build's job (the ``trw-mcp code index`` CLI command, ``BUILD_COMMAND`` below),
never a query's.
"""

from __future__ import annotations

import heapq
import re
import sqlite3
from collections import Counter
from collections.abc import Callable, Iterator
from pathlib import Path, PurePosixPath
from typing import Literal, cast

from pydantic import BaseModel, ConfigDict, Field

from trw_mcp.code_index.bounds import CodeIndexBounds, Deadline, IndexBoundExceeded
from trw_mcp.code_index.chunking import CodeChunk
from trw_mcp.code_index.store import (
    RuntimeUnsupported,
    StoreCorrupt,
    StoreInfo,
    StoreMissing,
    open_store,
    row_to_chunk,
    stream_rows,
)

MAX_SNIPPET_LINES: int = 12
MAX_SNIPPET_CHARS: int = 800
#: PRD-CORE-300-FR06 (slice S4): the build step moved from an MCP tool to the
#: ``trw-mcp code index`` CLI command.
BUILD_COMMAND: str = "trw-mcp code index"

_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+")

ErrorCode = Literal[
    "",
    "index_missing",
    "index_corrupt",
    "index_bound_exceeded",
    "invalid_repo",
    "invalid_path",
    "query_empty",
    "unsupported_runtime",
]
#: One member, and deliberately still a Literal: it is the response's record of
#: WHICH search ran, so a future second mode extends it rather than replaces it.
#: ``"semantic"`` was a member until 2.0.0 and named a branch that could not
#: return a result (UF-031); ``dependency_missing`` left ``ErrorCode`` with it,
#: because the only producer of either was the deleted optional-embedder hook.
SearchMode = Literal["lexical"]


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
    #: ``"stale"`` when the store was built at a git HEAD other than the
    #: current one; the answer is still served, with the revision it came from.
    index_state: Literal["", "stale"] = ""
    index_revision: str = ""
    #: The ``code_index_bounds`` key a failed query crossed.
    bound: str = ""


def lexical_search(
    repo_root: Path | str,
    *,
    query: str,
    top_k: int = 10,
    path: str | None = None,
    bounds: CodeIndexBounds | None = None,
) -> CodeSearchResponse:
    """Return ranked lexical matches from the published store."""

    query_terms = _terms(query[: (bounds or CodeIndexBounds()).query_max_chars])  # a longer query is refused unscanned

    def candidates(conn: sqlite3.Connection, scope: tuple[str, tuple[object, ...]]) -> Iterator[sqlite3.Row]:
        term_clauses: list[str] = []
        params: list[object] = []
        for term in query_terms:
            pattern = f"%{_escape_like(term)}%"
            term_clauses.append(
                "(symbol_name LIKE ? ESCAPE '\\' OR signature LIKE ? ESCAPE '\\' OR docstring_summary LIKE ? "
                "ESCAPE '\\' OR text LIKE ? ESCAPE '\\' OR path LIKE ? ESCAPE '\\')"
            )
            params.extend([pattern] * 5)
        where = " OR ".join(term_clauses) or "0"
        if scope[0]:
            where = f"({where}) AND {scope[0]}"
            params.extend(scope[1])
        return stream_rows(conn, where=where, params=tuple(params))

    def score(chunk: CodeChunk) -> tuple[float, str] | None:
        value = _lexical_score(query_terms, chunk)
        return (value, f"lexical token match: {_matched_terms(query_terms, chunk)}") if value > 0 else None

    return _run(repo_root, query, top_k, path, bounds, candidates, score)


def symbol_search(
    repo_root: Path | str,
    *,
    symbol: str,
    top_k: int = 10,
    path: str | None = None,
    bounds: CodeIndexBounds | None = None,
) -> CodeSearchResponse:
    """Return exact symbol matches before fuzzy symbol matches."""

    needle = symbol[: (bounds or CodeIndexBounds()).query_max_chars].strip().lower()  # a longer one is refused

    def candidates(conn: sqlite3.Connection, scope: tuple[str, tuple[object, ...]]) -> Iterator[sqlite3.Row]:
        where = "symbol_name LIKE ? ESCAPE '\\'"
        params: list[object] = [f"%{_escape_like(needle)}%"]
        if scope[0]:
            where = f"{where} AND {scope[0]}"
            params.extend(scope[1])
        return stream_rows(conn, where=where, params=tuple(params))

    def score(chunk: CodeChunk) -> tuple[float, str] | None:
        if chunk.symbol_name is None:
            return None
        candidate = chunk.symbol_name.lower()
        if candidate == needle:
            return 100.0, "exact symbol match"
        if needle in candidate:
            return 50.0 + (len(needle) / len(candidate)), "fuzzy symbol match"
        return None

    return _run(repo_root, symbol, top_k, path, bounds, candidates, score)


def _run(
    repo_root: Path | str,
    query: str,
    top_k: int,
    path: str | None,
    bounds: CodeIndexBounds | None,
    candidates: Callable[[sqlite3.Connection, tuple[str, tuple[object, ...]]], Iterator[sqlite3.Row]],
    score: Callable[[CodeChunk], tuple[float, str] | None],
) -> CodeSearchResponse:
    budgets = bounds or CodeIndexBounds()
    validation = _validate_request(repo_root, query=query, path=path, budgets=budgets)
    if isinstance(validation, CodeSearchResponse):
        return validation
    root, safe_path = validation
    deadline = Deadline(budgets.query_timeout_seconds, "query_timeout_seconds")
    conn: sqlite3.Connection | None = None
    try:
        conn, info = open_store(root, deadline)
        deadline.check()
        ranked = _top_hits(candidates(conn, _scope(safe_path)), score, top_k, budgets, deadline)
        response = CodeSearchResponse(
            status="ok",
            mode="lexical",
            query=query,
            results=ranked,
            index_state=_state(root, info),
            index_revision=info.revision,
        )
        if len(response.model_dump_json()) > budgets.query_max_response_bytes:
            raise IndexBoundExceeded("query_max_response_bytes", budgets.query_max_response_bytes)
        deadline.check()  # an answer that arrived late is a timeout, however few rows it scanned
        return response
    except StoreMissing as exc:
        return _failure(query, "index_missing", str(exc), f"Run {BUILD_COMMAND} for this repository to build it.")
    except RuntimeUnsupported as exc:
        return _failure(query, "unsupported_runtime", str(exc), "Run trw-mcp on Python 3.11 or newer.")
    except IndexBoundExceeded as exc:
        return _failure(query, "index_bound_exceeded", str(exc), "Narrow the query or its path.", bound=exc.bound)
    except (StoreCorrupt, sqlite3.DatabaseError) as exc:
        if deadline.expired():  # an interrupted statement is the query deadline, not a corrupt store
            bound = IndexBoundExceeded("query_timeout_seconds", budgets.query_timeout_seconds)
            return _failure(query, "index_bound_exceeded", str(bound), "Narrow the query.", bound=bound.bound)
        return _failure(query, "index_corrupt", str(exc), f"Rebuild the store with {BUILD_COMMAND}.")
    finally:
        if conn is not None:
            conn.close()


def _top_hits(
    rows: Iterator[sqlite3.Row],
    score: Callable[[CodeChunk], tuple[float, str] | None],
    top_k: int,
    budgets: CodeIndexBounds,
    deadline: Deadline,
) -> tuple[CodeSearchHit, ...]:
    """Keep the best ``top_k`` in a heap; count candidates against the row budget."""

    limit = _bounded_top_k(top_k)
    heap: list[tuple[tuple[float, str, int], int, CodeSearchHit]] = []
    for scanned, row in enumerate(rows, start=1):
        if scanned > budgets.query_max_rows:
            raise IndexBoundExceeded("query_max_rows", budgets.query_max_rows)
        deadline.check()  # every row: one row of a crafted store can take the whole budget (rc7 C12)
        chunk = row_to_chunk(row)
        scored = score(chunk)
        if scored is None:
            continue
        value, reason = scored
        # Min-heap on the inverse of the result order: the root is the worst kept hit.
        key = (value, _Reverse(chunk.path), -chunk.start_line)
        entry = (key, scanned, _hit(chunk, score=value, reason=reason))
        if len(heap) < limit:
            heapq.heappush(heap, entry)
        elif key > heap[0][0]:
            heapq.heapreplace(heap, entry)
    ordered = sorted(heap, key=lambda item: (-item[2].score, item[2].path, item[2].line_range.start))
    return tuple(item[2] for item in ordered)


class _Reverse(str):
    """A string that sorts in reverse, so a min-heap drops the later path first."""

    __slots__ = ()

    def __lt__(self, other: str) -> bool:
        return str.__gt__(self, other)

    def __gt__(self, other: str) -> bool:
        return str.__lt__(self, other)


def _state(root: Path, info: StoreInfo) -> Literal["", "stale"]:
    from trw_mcp.tools._sidecar_substrate import resolve_git_sha

    return "stale" if info.git_head != resolve_git_sha(root) else ""


def _scope(path: str | None) -> tuple[str, tuple[object, ...]]:
    if path is None:
        return "", ()
    return "(path = ? OR path LIKE ? ESCAPE '\\')", (path, f"{_escape_like(path)}/%")


def _escape_like(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


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
    budgets: CodeIndexBounds,
) -> tuple[Path, str | None] | CodeSearchResponse:
    # Refused before any copy, tokenizing or store open, echoing no more than the cap: a scan costs rows x terms,
    # one LIKE per term (rc7 sweep).
    if len(query) > budgets.query_max_chars:
        return _refused(query[: budgets.query_max_chars], "query_max_chars", budgets.query_max_chars, len(query))
    stripped_query = query.strip()
    if not stripped_query:
        return _failure(query, "query_empty", "query must not be empty", "Provide a non-empty query.")
    terms = len(_terms(stripped_query))
    if terms > budgets.query_max_terms:
        return _refused(query, "query_max_terms", budgets.query_max_terms, terms)
    try:
        root = _validated_repo_root(repo_root)
    except NotADirectoryError as exc:
        return _failure(query, "invalid_repo", str(exc), "Pass an existing repository directory.")
    safe_path = _normalize_path_filter(path)
    if path is not None and safe_path is None:
        return _failure(query, "invalid_path", "path must be repo-relative and must not contain '..'", "")
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


def _refused(query: str, field: str, limit: int, size: int) -> CodeSearchResponse:
    bound = IndexBoundExceeded(field, limit, f"the query has {size}")
    return _failure(query, "index_bound_exceeded", str(bound), "Shorten the query.", bound=bound.bound)


def _failure(query: str, error_code: ErrorCode, error: str, remediation: str, *, bound: str = "") -> CodeSearchResponse:
    return CodeSearchResponse(
        status="failed",
        mode="lexical",
        query=query,
        results=(),
        error_code=error_code,
        error=error,
        remediation=remediation,
        bound=bound,
    )


def _bounded_top_k(top_k: int) -> int:
    return max(1, min(top_k, 50))


def response_to_dict(response: CodeSearchResponse) -> dict[str, object]:
    """Convert a response to a JSON-compatible plain dict for MCP boundaries."""

    return cast("dict[str, object]", response.model_dump(mode="json"))


__all__ = [
    "BUILD_COMMAND",
    "MAX_SNIPPET_CHARS",
    "MAX_SNIPPET_LINES",
    "CodeSearchHit",
    "CodeSearchResponse",
    "ErrorCode",
    "LineRange",
    "SearchMode",
    "SymbolRef",
    "lexical_search",
    "response_to_dict",
    "symbol_search",
]
