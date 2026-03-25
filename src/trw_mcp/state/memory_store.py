"""Local vector store backed by sqlite-vec for semantic search.

Provides persistent vector storage using sqlite-vec (pip-installable SQLite
extension). Graceful degradation: when sqlite-vec is not available, all
operations return empty results and available() returns False.
"""

from __future__ import annotations

import sqlite3
import struct
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar

if TYPE_CHECKING:
    from trw_mcp.state.persistence import FileStateReader

import structlog

from trw_mcp.state._helpers import iter_yaml_entry_files

try:
    import sqlite_vec

    _SQLITE_VEC_AVAILABLE = True
except ImportError:
    _SQLITE_VEC_AVAILABLE = False

logger = structlog.get_logger(__name__)


class MemoryStore:
    """SQLite-vec backed vector store for learning entry embeddings.

    When sqlite-vec is not installed, all operations are no-ops and
    available() returns False — the retrieval engine falls back to BM25-only.
    """

    _DEFAULT_DIM: ClassVar[int] = 384

    def __init__(self, db_path: Path, dim: int = 384) -> None:
        """Open or create the vector store database.

        Args:
            db_path: Path to the SQLite database file. Parent directories
                are created automatically.
            dim: Embedding dimension. Defaults to 384 (all-MiniLM-L6-v2).
        """
        self._db_path = db_path
        self._dim = dim
        self._conn: sqlite3.Connection | None = None

        if not _SQLITE_VEC_AVAILABLE:
            logger.debug("memory_store_unavailable", reason="sqlite_vec_not_installed")
            return

        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path))
        self._conn.enable_load_extension(True)
        sqlite_vec.load(self._conn)
        self._conn.enable_load_extension(False)
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        """Create tables if they do not exist."""
        if self._conn is None:
            raise RuntimeError("_ensure_schema called before connection was established")
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS entries(rowid INTEGER PRIMARY KEY AUTOINCREMENT, entry_id TEXT UNIQUE NOT NULL)"
        )
        self._conn.execute(f"CREATE VIRTUAL TABLE IF NOT EXISTS vec_entries USING vec0(embedding float[{self._dim}])")
        self._conn.commit()

    @classmethod
    def available(cls) -> bool:
        """Return True if sqlite-vec is importable and functional."""
        return _SQLITE_VEC_AVAILABLE

    def upsert(
        self,
        entry_id: str,
        embedding: list[float],
        metadata: dict[str, str],
    ) -> None:
        """Insert or update a vector entry.

        Args:
            entry_id: Unique identifier for the learning entry.
            embedding: Dense embedding vector (must match dim).
            metadata: Arbitrary string metadata (currently unused in DB,
                but kept for API compatibility and future indexing).
        """
        if self._conn is None:
            return

        emb_bytes = struct.pack(f"{self._dim}f", *embedding)

        # Upsert into entries table to get/create rowid
        self._conn.execute("INSERT OR IGNORE INTO entries(entry_id) VALUES(?)", (entry_id,))
        row = self._conn.execute("SELECT rowid FROM entries WHERE entry_id=?", (entry_id,)).fetchone()
        rowid: int = row[0]

        # Delete old vector if exists, then insert fresh
        self._conn.execute("DELETE FROM vec_entries WHERE rowid=?", (rowid,))
        self._conn.execute(
            "INSERT INTO vec_entries(rowid, embedding) VALUES(?, ?)",
            (rowid, emb_bytes),
        )
        self._conn.commit()
        logger.debug("memory_store_upsert_ok", entry_id=entry_id, dim=len(embedding))

    def search(
        self,
        query_embedding: list[float],
        top_k: int = 20,
    ) -> list[tuple[str, float]]:
        """Find the top-k nearest entries by cosine distance.

        Args:
            query_embedding: Query vector (must match dim).
            top_k: Number of results to return.

        Returns:
            List of (entry_id, distance) pairs sorted by distance ascending.
            Empty list when unavailable or no entries exist.
        """
        if self._conn is None:
            return []

        query_bytes = struct.pack(f"{self._dim}f", *query_embedding)
        try:
            rows = self._conn.execute(
                """
                SELECT e.entry_id, v.distance
                FROM vec_entries v
                JOIN entries e ON v.rowid = e.rowid
                WHERE v.embedding MATCH ? AND k = ?
                ORDER BY v.distance
                """,
                (query_bytes, top_k),
            ).fetchall()
            return [(str(row[0]), float(row[1])) for row in rows]
        except sqlite3.Error:
            logger.debug("memory_store_search_error", exc_info=True)
            return []

    def delete(self, entry_id: str) -> None:
        """Remove an entry by ID.

        Args:
            entry_id: The entry to remove. No-op if not found.
        """
        if self._conn is None:
            return

        row = self._conn.execute("SELECT rowid FROM entries WHERE entry_id=?", (entry_id,)).fetchone()
        if row is None:
            return
        rowid: int = row[0]
        self._conn.execute("DELETE FROM vec_entries WHERE rowid=?", (rowid,))
        self._conn.execute("DELETE FROM entries WHERE rowid=?", (rowid,))
        self._conn.commit()

    def count(self) -> int:
        """Return the number of stored vectors.

        Returns:
            Zero when unavailable or database is empty.
        """
        if self._conn is None:
            return 0
        row = self._conn.execute("SELECT COUNT(*) FROM entries").fetchone()
        return int(row[0]) if row else 0

    def migrate(
        self,
        entries_dir: Path,
        reader: FileStateReader,
    ) -> dict[str, int]:
        """Batch-embed existing YAML learnings into the vector store.

        Idempotent: uses INSERT OR REPLACE so re-running is safe.

        Args:
            entries_dir: Path to the learnings/entries/ directory.
            reader: File state reader for loading YAML files.

        Returns:
            Dict with 'migrated', 'skipped', and 'total' counts.
        """
        from trw_mcp.state.memory_adapter import embed_text_batch as embed_batch
        from trw_mcp.state.memory_adapter import embedding_available

        if self._conn is None or not embedding_available():
            return {"migrated": 0, "skipped": 0, "total": 0}

        entries: list[tuple[str, str]] = []
        for yaml_file in iter_yaml_entry_files(entries_dir):
            try:
                data = reader.read_yaml(yaml_file)
                if str(data.get("status", "active")) != "active":
                    continue
                entry_id = str(data.get("id", ""))
                summary = str(data.get("summary", ""))
                detail = str(data.get("detail", ""))
                if entry_id and (summary or detail):
                    entries.append((entry_id, summary + " " + detail))
            except (OSError, ValueError):
                continue

        if not entries:
            return {"migrated": 0, "skipped": 0, "total": 0}

        texts = [text for _, text in entries]
        embeddings = embed_batch(texts)

        migrated = 0
        skipped = 0
        for (entry_id, _), embedding in zip(entries, embeddings, strict=False):
            if embedding is not None:
                self.upsert(entry_id, embedding, {"source": "migration"})
                migrated += 1
            else:
                skipped += 1

        logger.info(
            "memory_store_migrate_ok",
            migrated=migrated,
            skipped=skipped,
            total=len(entries),
            db_path=str(self._db_path),
        )
        return {"migrated": migrated, "skipped": skipped, "total": len(entries)}

    def close(self) -> None:
        """Close the database connection."""
        if self._conn is not None:
            self._conn.close()
            self._conn = None


# ---------------------------------------------------------------------------
# Module-level singleton for connection reuse (PRD-FIX-046)
# ---------------------------------------------------------------------------

_store: MemoryStore | None = None
_store_path: Path | None = None


def get_memory_store(db_path: Path) -> MemoryStore:
    """Return a shared MemoryStore instance, creating it lazily.

    Re-creates the store if ``db_path`` differs from the cached instance.
    """
    global _store, _store_path
    if _store is not None and _store_path == db_path:
        return _store
    if _store is not None:
        _store.close()
    _store = MemoryStore(db_path)
    _store_path = db_path
    return _store


def reset_memory_store() -> None:
    """Close and discard the shared MemoryStore singleton."""
    global _store, _store_path
    if _store is not None:
        _store.close()
    _store = None
    _store_path = None
