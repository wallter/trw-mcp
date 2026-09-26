"""The chunk store: one SQLite file that only the build writes (PRD-CORE-300-FR15).

It replaces the single chunks.json that every query rewrote and loaded whole.

- **Build** writes a fresh database beside the published one and publishes it
  with ``os.replace``. Unchanged files are copied from the previous store; a
  failed or interrupted build removes its temporary file and leaves the
  previous store answering. A reader that opened the old file keeps reading
  one whole revision.
- **Query** opens the store read-only (``mode=ro``) and streams rows through a
  cursor, so no query materializes the store. The legacy chunks.json is never
  opened or migrated.
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
import sys
import uuid
from collections.abc import Iterator
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import cache
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from trw_mcp.code_index.bounds import MAX_INDEXED_FILE_BYTES, STORE_LENGTH_LIMIT, Deadline
from trw_mcp.code_index.chunking import CHUNK_FORMAT, CodeChunk, chunk_source
from trw_mcp.code_index.discovery import read_indexed_file
from trw_mcp.code_index.models import CodeIndexManifest
from trw_mcp.code_index.storage import ensure_index_dir

STORE_RELATIVE_PATH: str = ".trw/code-index/chunks.sqlite"
STORE_SCHEMA_VERSION: str = "code-chunk-store/v1"
_FORMAT: dict[str, str] = {"schema_version": STORE_SCHEMA_VERSION, "chunk_format": CHUNK_FORMAT}
CHUNK_COLUMNS: tuple[str, ...] = (
    "chunk_id",
    "path",
    "file_sha256",
    "language",
    "symbol_name",
    "symbol_kind",
    "start_line",
    "end_line",
    "text_hash",
    "signature",
    "docstring_summary",
    "ast_available",
    "text",
)
_SCHEMA = """
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE chunks (
    chunk_id TEXT NOT NULL,
    path TEXT NOT NULL,
    file_sha256 TEXT NOT NULL,
    language TEXT NOT NULL,
    symbol_name TEXT,
    symbol_kind TEXT NOT NULL,
    start_line INTEGER NOT NULL,
    end_line INTEGER NOT NULL,
    text_hash TEXT NOT NULL,
    signature TEXT NOT NULL,
    docstring_summary TEXT NOT NULL,
    ast_available INTEGER NOT NULL,
    text TEXT NOT NULL
);
CREATE INDEX chunks_by_path ON chunks (path);
CREATE INDEX chunks_by_symbol ON chunks (symbol_name COLLATE NOCASE);
"""
# Column names are module constants; every value is a bound parameter.
_INSERT = f"INSERT INTO chunks ({', '.join(CHUNK_COLUMNS)}) VALUES ({', '.join('?' for _ in CHUNK_COLUMNS)})"  # noqa: S608


class StoreMissing(Exception):
    """No published store exists."""


class StoreCorrupt(Exception):
    """A published store exists but cannot be read."""


class RuntimeUnsupported(Exception):
    """This Python cannot cap a store's values (``Connection.setlimit`` is 3.11+), so no store is read or built."""


def require_supported_runtime() -> None:
    if sys.version_info < (3, 11):
        found = f"{sys.version_info[0]}.{sys.version_info[1]}"
        raise RuntimeUnsupported(
            f"code search and the code-index build need Python 3.11+, and this is Python {found}: "
            "only 3.11+ can cap the size of a value in a checkout's store (rc7 C12)"
        )


class ChunkIndexStats(BaseModel):
    """Chunk lifecycle counters for one build."""

    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    total_chunks: int = Field(ge=0)
    indexed_files: int = Field(ge=0)
    failed_files: int = Field(ge=0)


@dataclass(frozen=True)
class StoreInfo:
    """What a reader learns about the revision it opened."""

    revision: str
    git_head: str | None


def default_store_path(repo_root: Path | str) -> Path:
    """Return the canonical chunk-store path for ``repo_root``."""

    return Path(repo_root) / STORE_RELATIVE_PATH


def _row(chunk: CodeChunk) -> tuple[object, ...]:
    values = chunk.model_dump()
    values["ast_available"] = int(chunk.ast_available)
    return tuple(values[column] for column in CHUNK_COLUMNS)


def row_to_chunk(row: sqlite3.Row) -> CodeChunk:
    """Rebuild a :class:`CodeChunk` from one store row."""

    values = {column: row[column] for column in CHUNK_COLUMNS}
    values["ast_available"] = bool(values["ast_available"])
    try:
        return CodeChunk.model_validate(values)
    except ValidationError as exc:  # a crafted row is a corrupt store, not a crashed query
        raise StoreCorrupt(f"invalid chunk row: {exc.error_count()} field error(s)") from exc


def _bounded(conn: sqlite3.Connection, deadline: Deadline) -> None:
    """Bound every statement on *conn* in time and every value and row in size, before its first read.

    The published store is the checkout's to craft (rc5 and rc7 C12), and a value computed inside one VM step
    never reaches the progress handler, so the length limit is what refuses it.
    """
    # Every 1,000 instructions: a filtered scan spends ~700 per row, so 10,000 let a dozen crafted rows' LIKEs run blind.
    conn.set_progress_handler(lambda: int(deadline.expired()), 1_000)
    if sys.version_info >= (3, 11):  # always true here: require_supported_runtime refused older ones before any connect
        conn.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, STORE_LENGTH_LIMIT)


@cache
def _expected_schema() -> frozenset[tuple[str, str, str, str | None]]:
    with closing(sqlite3.connect(":memory:")) as conn:
        conn.executescript(_SCHEMA)
        return frozenset(conn.execute("SELECT type, name, tbl_name, sql FROM sqlite_master"))


def _require_schema(conn: sqlite3.Connection, schema: str) -> None:
    """Refuse *schema* unless its objects are exactly the ones the build creates, statement for statement.

    A view, a trigger, a generated column or an expression index would run the checkout's SQL on every read.
    """
    found = frozenset(conn.execute(f"SELECT type, name, tbl_name, sql FROM {schema}.sqlite_master LIMIT 16"))  # noqa: S608 - main or prev
    if found != _expected_schema():
        raise sqlite3.DatabaseError("the store's schema is not the one the build creates")


def _read_meta(conn: sqlite3.Connection, schema: str) -> dict[str, str]:
    """*schema*'s meta rows; call only after :func:`_require_schema` proved ``meta`` a real table."""
    return dict(conn.execute(f"SELECT key, value FROM {schema}.meta LIMIT 64").fetchall())  # noqa: S608 - main or prev


def build_chunk_store(repo_root: Path, manifest: CodeIndexManifest, *, deadline: Deadline) -> ChunkIndexStats:
    """Build the store for ``manifest`` from source and publish it atomically.

    Every file is chunked again from the bytes the manifest hashed: the published store is the checkout's to
    craft, so nothing is read back from it (rc11 F2b; reuse saved about half of a full build).
    """

    require_supported_runtime()
    final = ensure_index_dir(repo_root) / Path(STORE_RELATIVE_PATH).name
    temp = final.with_name(f".{final.name}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp")
    conn = sqlite3.connect(temp.as_uri(), uri=True)
    _bounded(conn, deadline)
    try:
        conn.executescript(_SCHEMA)
        indexed = failed = 0
        for row in manifest.files:
            deadline.check()
            try:
                data = read_indexed_file(repo_root, row.path, MAX_INDEXED_FILE_BYTES)
                if hashlib.sha256(data).hexdigest() != row.sha256:  # chunk exactly the bytes the manifest hashed
                    raise ValueError(f"{row.path} changed after it was hashed")
                chunks = chunk_source(row.path, data.decode("utf-8"), file_sha256=row.sha256)
            except (OSError, UnicodeDecodeError, ValueError):
                failed += 1
                continue
            conn.executemany(_INSERT, (_row(chunk) for chunk in chunks))
            indexed += 1
        total = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        revision = f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}"
        conn.executemany(
            "INSERT INTO meta (key, value) VALUES (?, ?)",
            [*_FORMAT.items(), ("revision", revision), ("git_head", manifest.git_head or "")],
        )
        conn.commit()
        conn.close()
        os.replace(temp, final)
    except BaseException as exc:
        conn.close()
        temp.unlink(missing_ok=True)
        if isinstance(exc, sqlite3.OperationalError):
            deadline.check()  # a statement the deadline interrupted fails by the bound's name
        raise
    return ChunkIndexStats(total_chunks=total, indexed_files=indexed, failed_files=failed)


def open_store(repo_root: Path, deadline: Deadline) -> tuple[sqlite3.Connection, StoreInfo]:
    """Open the published store read-only, every read under *deadline*; never creates or writes anything."""

    require_supported_runtime()
    path = default_store_path(repo_root)
    if not path.is_file():
        raise StoreMissing(f"no code-index store at {STORE_RELATIVE_PATH}")
    try:
        conn = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)
    except sqlite3.Error as exc:
        raise StoreCorrupt(str(exc)) from exc
    try:
        _bounded(conn, deadline)  # before the first read (rc5 C12)
        _require_schema(conn, "main")
        meta = _read_meta(conn, "main")
        # A TEXT column still stores a BLOB: the values the response carries must be short strings.
        stamps = (meta.get("revision"), meta.get("git_head", ""))
        if (
            meta.get("schema_version") != STORE_SCHEMA_VERSION
            or not stamps[0]
            or not all(isinstance(stamp, str) and len(stamp) <= 128 for stamp in stamps)
        ):
            raise sqlite3.DatabaseError(f"unrecognized store metadata (schema {meta.get('schema_version')!r:.80})")
        conn.row_factory = sqlite3.Row
    except sqlite3.DatabaseError as exc:
        conn.close()
        raise StoreCorrupt(str(exc)) from exc
    return conn, StoreInfo(revision=meta["revision"], git_head=meta.get("git_head") or None)


def stream_rows(
    conn: sqlite3.Connection,
    *,
    where: str = "",
    params: tuple[object, ...] = (),
) -> Iterator[sqlite3.Row]:
    """Yield matching rows one at a time from a cursor."""

    # ``where`` is built by this package from fixed clauses; values arrive as ``params``.
    sql = f"SELECT {', '.join(CHUNK_COLUMNS)} FROM chunks{f' WHERE {where}' if where else ''}"  # noqa: S608
    yield from conn.execute(sql, params)


__all__ = [
    "CHUNK_COLUMNS",
    "STORE_RELATIVE_PATH",
    "STORE_SCHEMA_VERSION",
    "ChunkIndexStats",
    "RuntimeUnsupported",
    "StoreCorrupt",
    "StoreInfo",
    "StoreMissing",
    "build_chunk_store",
    "default_store_path",
    "open_store",
    "require_supported_runtime",
    "row_to_chunk",
    "stream_rows",
]
