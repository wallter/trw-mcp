"""Deterministic source chunk extraction for the local code index."""

from __future__ import annotations

import ast
import hashlib
from pathlib import Path, PurePosixPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from trw_mcp.code_index.discovery import normalize_repo_relative_path

MAX_CHUNK_LINES: int = 80

SymbolKind = Literal["module", "function", "class", "method", "fallback"]

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


def chunk_source_file(repo_root: Path | str, file_path: Path | str, *, file_sha256: str) -> tuple[CodeChunk, ...]:
    """Return deterministic chunks for ``file_path`` without returning full-file responses."""

    root = Path(repo_root).resolve()
    path = Path(file_path).resolve()
    relative_path = normalize_repo_relative_path(root, path)
    language = language_for_path(relative_path)
    text = path.read_text(encoding="utf-8")
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

    chunks: list[CodeChunk] = []
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            chunks.append(
                _chunk_from_lines(
                    path=path,
                    file_sha256=file_sha256,
                    language=language,
                    symbol_name=node.name,
                    symbol_kind="class",
                    start_line=node.lineno,
                    end_line=_bounded_end_line(node, len(lines)),
                    lines=lines,
                    signature=_line_at(lines, node.lineno),
                    docstring_summary=_summary(ast.get_docstring(node, clean=True)),
                    ast_available=True,
                )
            )
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            chunks.append(
                _chunk_from_lines(
                    path=path,
                    file_sha256=file_sha256,
                    language=language,
                    symbol_name=node.name,
                    symbol_kind="function",
                    start_line=node.lineno,
                    end_line=_bounded_end_line(node, len(lines)),
                    lines=lines,
                    signature=_line_at(lines, node.lineno),
                    docstring_summary=_summary(ast.get_docstring(node, clean=True)),
                    ast_available=True,
                )
            )
    return tuple(chunks)


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


def _bounded_end_line(node: ast.AST, file_line_count: int) -> int:
    raw_end = getattr(node, "end_lineno", None)
    actual_end = raw_end if isinstance(raw_end, int) else getattr(node, "lineno", 1)
    max_end = getattr(node, "lineno", 1) + MAX_CHUNK_LINES - 1
    return min(actual_end, max_end, file_line_count)


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
    "chunk_source_file",
    "language_for_path",
]
