"""Build and query budgets for the local code index (PRD-CORE-300-FR15).

On 2026-09-24 one index update here walked 289,760 files, most under nested
worktrees, wrote an 8.4 GB chunks.json, and a query that loaded it whole took
the MCP server to 62 GB. Every walk, build and query now runs inside these
budgets and stops with :class:`IndexBoundExceeded` naming the key it crossed.

Resident memory is deliberately NOT a budget here: the product bounds what it
visits, reads, scans and returns, and the test suite measures the peak RSS
those bounds produce (``tests/test_code_index_bounds.py``).
"""

from __future__ import annotations

import time

from pydantic import BaseModel, ConfigDict, Field

BOUNDS_CONFIG_KEY: str = "code_index_bounds"
#: The largest file the walk admits, whatever ``code_index_max_file_bytes`` says: a chunk's text is a slice of one file.
MAX_INDEXED_FILE_BYTES: int = 1024 * 1024  # just over the 1,000,000-byte config default
#: SQLITE_LIMIT_LENGTH on every store connection: the most bytes one value or one row may hold. A row carries a
#: chunk's text plus a signature and docstring summary drawn from that same chunk, and fixed-width hashes, so four
#: files' worth never refuses a real row. A crafted store computes values inside one VM step (hex(zeroblob(N)) in a
#: view), where the deadline's progress handler cannot act; this limit fails that value as too big (rc7 C12). It also
#: bounds one row's LIKE scan, which runs inside a single VM step: 64 terms over a 4 MiB row is ~75 ms.
STORE_LENGTH_LIMIT: int = 4 * MAX_INDEXED_FILE_BYTES


class CodeIndexBounds(BaseModel):
    """Separate build and query budgets; each crossing names its key."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    build_max_entries: int = Field(default=500_000, ge=1, description="Directory entries visited per build.")
    build_max_files: int = Field(default=50_000, ge=1, description="Files indexed per build.")
    build_max_source_bytes: int = Field(default=512 * 1024 * 1024, ge=1, description="Source bytes read per build.")
    build_timeout_seconds: float = Field(default=300.0, gt=0, description="Build wall-clock.")
    query_max_chars: int = Field(default=1_000, ge=1, description="Query text length, as trw-memory's FTS leg.")
    query_max_terms: int = Field(
        default=64, ge=1, description="Distinct query terms; each adds a LIKE per scanned row, as trw-memory's FTS leg."
    )
    query_max_rows: int = Field(default=200_000, ge=1, description="Store rows scanned per query.")
    query_max_response_bytes: int = Field(default=256 * 1024, ge=1, description="Serialized response size.")
    query_timeout_seconds: float = Field(
        default=20.0, gt=0, description="Query wall-clock; kept under the client tool timeout."
    )


class IndexBoundExceeded(Exception):
    """A build or query crossed one of its budgets."""

    def __init__(self, field: str, limit: float, detail: str = "") -> None:
        self.bound = f"{BOUNDS_CONFIG_KEY}.{field}"
        self.limit = limit
        message = f"{self.bound} ({limit}) exceeded"
        super().__init__(f"{message}: {detail}" if detail else message)


class Deadline:
    """A monotonic wall-clock budget checked at loop boundaries."""

    def __init__(self, seconds: float, field: str) -> None:
        self._seconds = seconds
        self._field = field
        self._expires = time.monotonic() + seconds

    def expired(self) -> bool:
        return time.monotonic() >= self._expires

    def check(self) -> None:
        if self.expired():
            raise IndexBoundExceeded(self._field, self._seconds, "wall-clock limit reached")


__all__ = [
    "BOUNDS_CONFIG_KEY",
    "MAX_INDEXED_FILE_BYTES",
    "STORE_LENGTH_LIMIT",
    "CodeIndexBounds",
    "Deadline",
    "IndexBoundExceeded",
]
