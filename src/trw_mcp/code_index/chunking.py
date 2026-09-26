"""Deterministic source chunk extraction for the local code index."""

from __future__ import annotations

import ast
import functools
import hashlib
import itertools
from pathlib import PurePosixPath
from typing import Literal, cast

from pydantic import BaseModel, ConfigDict, Field, field_validator

MAX_CHUNK_LINES: int = 80
_DEFINITIONS = (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
#: Bump whenever the chunks a file yields change: a store built by an older chunker is still read, never reused.
CHUNK_FORMAT = "2"  # 2: loose module statements become "module" chunks

SymbolKind = Literal["module", "function", "class", "method", "constant", "fallback"]

_LANGUAGE_BY_SUFFIX: dict[str, str] = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".md": "markdown",
    ".yml": "yaml",
    ".yaml": "yaml",
    ".json": "json",
    ".toml": "toml",
    ".sql": "sql",
    ".sh": "shell",
}


class CodeChunk(BaseModel):
    """One bounded, deterministic code-search chunk."""

    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    chunk_id: str = Field(min_length=16)
    path: str = Field(min_length=1)
    file_sha256: str = Field(min_length=64, max_length=64)
    language: str = Field(min_length=1)
    symbol_name: str | None
    symbol_kind: SymbolKind
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)
    text_hash: str = Field(min_length=64, max_length=64)
    signature: str
    docstring_summary: str
    embedding_id: str | None = None
    ast_available: bool
    text: str

    @field_validator("path")
    @classmethod
    def _path_must_be_repo_relative_posix(cls, value: str) -> str:
        posix = PurePosixPath(value)
        if posix.is_absolute() or value.startswith("../") or value == ".." or "/../" in value:
            raise ValueError("path must be a repo-relative POSIX path")
        if "\\" in value:
            raise ValueError("path must use POSIX separators")
        return value

    @field_validator("file_sha256", "text_hash")
    @classmethod
    def _hash_must_be_lower_hex(cls, value: str) -> str:
        if not all(char in "0123456789abcdef" for char in value):
            raise ValueError("hash must be 64 lowercase hexadecimal characters")
        return value


def chunk_source(relative_path: str, text: str, *, file_sha256: str) -> tuple[CodeChunk, ...]:
    """Return deterministic chunks for one file's *text*; the caller read it (``read_indexed_file``)."""

    language = language_for_path(relative_path)
    if language == "python":
        chunks = _python_chunks(relative_path, language, file_sha256, text)
        if chunks:
            return chunks
    return _fallback_chunks(relative_path, language, file_sha256, text)


def language_for_path(path: str) -> str:
    """Return a small stable language label for a repo-relative path."""

    suffix = PurePosixPath(path).suffix.lower()
    return _LANGUAGE_BY_SUFFIX.get(suffix, suffix.removeprefix(".") or "text")


def _python_chunks(path: str, language: str, file_sha256: str, text: str) -> tuple[CodeChunk, ...]:
    lines = text.splitlines()
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return ()

    named = [(node, _top_level_symbol(node)) for node in tree.body]
    if all(symbol is None for _, symbol in named):
        return ()  # nothing named: the whole-file fallback indexes every line
    window = functools.partial(_windowed_chunks, path=path, file_sha256=file_sha256, language=language, lines=lines)
    chunks: list[CodeChunk] = []
    # Statements that name no symbol -- imports, ``if __name__ == "__main__":`` blocks, calls -- are
    # indexed as consecutive "module" runs; otherwise a file with any symbol lost them from search.
    for loose, group in itertools.groupby(named, key=lambda pair: pair[1] is None):
        run = list(group)
        if loose:
            span = (run[0][0].lineno, _node_end_line(run[-1][0]))
            chunks.extend(window(symbol_name=None, symbol_kind="module", span=span, docstring_summary=""))
            continue
        for node, symbol in cast("list[tuple[ast.stmt, tuple[str, SymbolKind]]]", run):  # a named run
            docstring = ast.get_docstring(node, clean=True) if isinstance(node, _DEFINITIONS) else None
            span = (node.lineno, _node_end_line(node))
            chunks.extend(
                window(symbol_name=symbol[0], symbol_kind=symbol[1], span=span, docstring_summary=_summary(docstring))
            )
    return tuple(chunks)


def _top_level_symbol(node: ast.stmt) -> tuple[str, SymbolKind] | None:
    """Name and kind of a module-level class, function or assignment (PRD-CORE-300-FR15)."""

    if isinstance(node, ast.ClassDef):
        return node.name, "class"
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return node.name, "function"
    if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
        return node.target.id, "constant"
    if isinstance(node, ast.Assign):
        names = [target.id for target in node.targets if isinstance(target, ast.Name)]
        if names:
            return names[0], "constant"
    return None


def _windowed_chunks(
    *,
    path: str,
    file_sha256: str,
    language: str,
    symbol_name: str | None,
    symbol_kind: SymbolKind,
    span: tuple[int, int],
    lines: list[str],
    docstring_summary: str,
) -> list[CodeChunk]:
    """Split a definition into consecutive windows of at most ``MAX_CHUNK_LINES`` lines.

    A long definition used to be cut at 80 lines, so its tail was never
    searchable. Every window keeps the definition's name and signature.
    """

    start, end = span[0], min(span[1], len(lines))
    signature = _line_at(lines, start)
    return [
        _chunk_from_lines(
            path=path,
            file_sha256=file_sha256,
            language=language,
            symbol_name=symbol_name,
            symbol_kind=symbol_kind,
            start_line=window_start,
            end_line=min(window_start + MAX_CHUNK_LINES - 1, end),
            lines=lines,
            signature=signature,
            docstring_summary=docstring_summary,
            ast_available=True,
        )
        for window_start in range(start, end + 1, MAX_CHUNK_LINES)
    ]


def _fallback_chunks(path: str, language: str, file_sha256: str, text: str) -> tuple[CodeChunk, ...]:
    lines = text.splitlines()
    if not lines:
        lines = [""]
    chunks: list[CodeChunk] = []
    for start_index in range(0, len(lines), MAX_CHUNK_LINES):
        start_line = start_index + 1
        end_line = min(start_index + MAX_CHUNK_LINES, len(lines))
        signature = f"{PurePosixPath(path).name}:{start_line}-{end_line}"
        chunks.append(
            _chunk_from_lines(
                path=path,
                file_sha256=file_sha256,
                language=language,
                symbol_name=None,
                symbol_kind="fallback",
                start_line=start_line,
                end_line=end_line,
                lines=lines,
                signature=signature,
                docstring_summary="",
                ast_available=False,
            )
        )
    return tuple(chunks)


def _node_end_line(node: ast.stmt) -> int:
    return node.end_lineno if node.end_lineno is not None else node.lineno


def _chunk_from_lines(
    *,
    path: str,
    file_sha256: str,
    language: str,
    symbol_name: str | None,
    symbol_kind: SymbolKind,
    start_line: int,
    end_line: int,
    lines: list[str],
    signature: str,
    docstring_summary: str,
    ast_available: bool,
) -> CodeChunk:
    bounded_text = "\n".join(lines[start_line - 1 : end_line])
    text_hash = _sha256_text(bounded_text)
    chunk_id = _stable_chunk_id(path, symbol_kind, symbol_name, start_line, end_line, text_hash)
    return CodeChunk(
        chunk_id=chunk_id,
        path=path,
        file_sha256=file_sha256,
        language=language,
        symbol_name=symbol_name,
        symbol_kind=symbol_kind,
        start_line=start_line,
        end_line=end_line,
        text_hash=text_hash,
        signature=signature.strip(),
        docstring_summary=docstring_summary,
        embedding_id=None,
        ast_available=ast_available,
        text=bounded_text,
    )


def _stable_chunk_id(
    path: str,
    symbol_kind: SymbolKind,
    symbol_name: str | None,
    start_line: int,
    end_line: int,
    text_hash: str,
) -> str:
    seed = f"{path}\0{symbol_kind}\0{symbol_name or ''}\0{start_line}\0{end_line}\0{text_hash}"
    return _sha256_text(seed)


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _line_at(lines: list[str], lineno: int) -> str:
    if 1 <= lineno <= len(lines):
        return lines[lineno - 1].strip()
    return ""


def _summary(docstring: str | None) -> str:
    if docstring is None:
        return ""
    first_line = docstring.strip().splitlines()[0].strip()
    return first_line[:200]


__all__ = [
    "MAX_CHUNK_LINES",
    "CodeChunk",
    "SymbolKind",
    "chunk_source",
    "language_for_path",
]
