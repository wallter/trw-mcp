"""Tiered memory storage: Hot (LRU) / Warm (sqlite-vec) / Cold (YAML archive).

Implements PRD-CORE-043 — lifecycle management for learning entries with
automatic tier transitions based on recency and importance scores.
"""

from __future__ import annotations

import contextlib
import json
import re
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path
from typing import cast

import structlog
from trw_memory.lifecycle.tiers import TierSweepResult as TierSweepResult

from trw_mcp.models.config import TRWConfig, get_config
from trw_mcp.models.learning import LearningEntry
from trw_mcp.models.typed_dicts import TierDistribution

# Re-export compute_importance_score so that existing test patches at
# ``trw_mcp.state.tiers.compute_importance_score`` continue to resolve.
from trw_mcp.state._tier_scoring import (
    compute_importance_score as compute_importance_score,
)

# Import sweep functions — assigned to TierManager at the bottom of this module.
from trw_mcp.state._tier_sweep import (
    _sweep_cold_to_purge as _sweep_cold_to_purge_impl,
)
from trw_mcp.state._tier_sweep import (
    _sweep_hot_to_warm as _sweep_hot_to_warm_impl,
)
from trw_mcp.state._tier_sweep import (
    _sweep_warm_to_cold as _sweep_warm_to_cold_impl,
)
from trw_mcp.state._tier_sweep import (
    sweep as _sweep_impl,
)
from trw_mcp.state.memory_adapter import list_active_learnings
from trw_mcp.state.persistence import FileStateReader, FileStateWriter

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# TierManager
# NOTE: parallel implementation exists in trw_memory.lifecycle.tiers —
# storage backends differ (FileStateReader/Writer vs SQLiteBackend).
# TierSweepResult is imported from trw_memory (canonical definition).
# ---------------------------------------------------------------------------


class TierManager:
    """Hot/Warm/Cold tier manager for learning entry lifecycle.

    Hot tier: in-memory LRU cache (OrderedDict, O(1) ops).
    Warm tier: sqlite-vec backed persistent index (MemoryStore).
    Cold tier: YAML archive partitioned by {YYYY}/{MM}/.

    Usage::

        mgr = TierManager(trw_dir=Path(".trw"))
        entry = mgr.hot_get("some-id")
        mgr.hot_put("some-id", learning_entry)
        result = mgr.sweep()
    """

    def __init__(
        self,
        trw_dir: Path,
        reader: FileStateReader | None = None,
        writer: FileStateWriter | None = None,
        config: TRWConfig | None = None,
    ) -> None:
        """Initialise TierManager.

        Args:
            trw_dir: Path to the .trw directory (project root / .trw).
            reader: FileStateReader for YAML reads. Defaults to new instance.
            writer: FileStateWriter for atomic YAML writes. Defaults to new instance.
            config: TRWConfig for capacity/TTL settings. Reads get_config() at sweep time.
        """
        self._trw_dir = trw_dir
        self._reader = reader or FileStateReader()
        self._writer = writer or FileStateWriter()
        self._config = config  # None = read at call time (FR06)

        # Hot tier: OrderedDict used as LRU cache
        # LRU invariant: MRU at the end (rightmost), LRU at the front (leftmost)
        self._hot: OrderedDict[str, LearningEntry] = OrderedDict()

    # -----------------------------------------------------------------------
    # Hot Tier — FR01
    # -----------------------------------------------------------------------

    def hot_get(self, entry_id: str) -> LearningEntry | None:
        """Return a cached entry, moving it to MRU position on hit.

        Args:
            entry_id: Learning entry identifier.

        Returns:
            LearningEntry if in cache, None otherwise.
        """
        if entry_id not in self._hot:
            return None
        # Move to MRU position (end)
        self._hot.move_to_end(entry_id)
        return self._hot[entry_id]

    def hot_put(self, entry_id: str, entry: LearningEntry) -> None:
        """Add or refresh an entry in the hot cache.

        Evicts the LRU entry when capacity is exceeded. On eviction,
        writes the evicted entry's last_accessed_at to disk via FileStateWriter.

        Args:
            entry_id: Learning entry identifier.
            entry: LearningEntry to cache.
        """
        cfg = self._config or get_config()

        if entry_id in self._hot:
            self._hot.move_to_end(entry_id)
            self._hot[entry_id] = entry
            return

        self._hot[entry_id] = entry
        self._hot.move_to_end(entry_id)

        # Evict LRU if over capacity
        if len(self._hot) > cfg.memory_hot_max_entries:
            evicted_id, _ = self._hot.popitem(last=False)
            self._flush_last_accessed(evicted_id)
            logger.debug(
                "hot_tier_evict",
                evicted_id=evicted_id,
                capacity=cfg.memory_hot_max_entries,
            )

    def _flush_last_accessed(self, entry_id: str) -> None:
        """Persist last_accessed_at for an evicted hot-tier entry.

        Writes the `last_accessed_at` field into the entry's YAML file
        in the learnings/entries/ directory (best-effort; errors are logged
        but never re-raised).

        Args:
            entry_id: ID of the evicted entry.
        """
        cfg = self._config or get_config()
        entries_dir = self._trw_dir / cfg.learnings_dir / cfg.entries_dir
        # Derive exact filename from entry.id (same slugify convention as the codebase)
        sanitized = re.sub(r"[^a-zA-Z0-9_\-]", "-", entry_id)
        target = entries_dir / f"{sanitized}.yaml"
        if not target.exists():
            # No file yet — nothing to flush
            return
        try:
            data = self._reader.read_yaml(target)
            data["last_accessed_at"] = datetime.now(tz=timezone.utc).date().isoformat()
            self._writer.write_yaml(target, data)
        except Exception:  # justified: fail-open, hot-tier flush is best-effort and must not block access tracking
            logger.warning(
                "hot_tier_flush_failed",
                entry_id=entry_id,
                path=str(target),
                exc_info=True,
            )

    def hot_clear(self) -> None:
        """Evict all entries from the hot cache (for testing / shutdown)."""
        self._hot.clear()

    @property
    def hot_size(self) -> int:
        """Number of entries currently in the hot cache."""
        return len(self._hot)

    # -----------------------------------------------------------------------
    # Warm Tier — FR02
    # -----------------------------------------------------------------------

    def _get_warm_db_path(self) -> Path:
        """Resolve path to warm.db (sibling to vectors.db in .trw/memory/)."""
        mem_dir = self._trw_dir / "memory"
        mem_dir.mkdir(parents=True, exist_ok=True)
        return mem_dir / "warm.db"

    def warm_add(
        self,
        entry_id: str,
        entry_data: dict[str, object],
        embedding: list[float] | None,
    ) -> None:
        """Insert or replace an entry in the warm sqlite-vec store.

        When embedding is None or sqlite-vec is unavailable, stores entry
        metadata in a fallback JSON sidecar for LIKE-based search.

        Args:
            entry_id: Learning entry identifier.
            entry_data: Dict of entry fields (from YAML).
            embedding: Optional dense embedding vector.
        """
        from trw_mcp.state.memory_store import MemoryStore, get_memory_store

        db_path = self._get_warm_db_path()

        if MemoryStore.available() and embedding is not None:
            store = get_memory_store(db_path)
            store.upsert(entry_id, embedding, {"source": "warm_tier"})
        else:
            # Fallback: write to a JSON sidecar for keyword search
            self._warm_sidecar_upsert(entry_id, entry_data)

        logger.debug("warm_tier_add", entry_id=entry_id, has_embedding=embedding is not None)

    def _warm_sidecar_path(self) -> Path:
        """Path to the warm tier keyword-search sidecar (JSONL)."""
        return self._get_warm_db_path().with_suffix(".jsonl")

    def _warm_sidecar_upsert(self, entry_id: str, entry_data: dict[str, object]) -> None:
        """Write entry metadata to the warm sidecar JSONL for keyword search."""
        sidecar = self._warm_sidecar_path()
        records: list[dict[str, object]] = []
        if sidecar.exists():
            for line in sidecar.read_text(encoding="utf-8").splitlines():
                line_s = line.strip()
                if not line_s:
                    continue
                try:
                    rec = json.loads(line_s)
                    if str(rec.get("id", "")) != entry_id:
                        records.append(rec)
                except json.JSONDecodeError:
                    continue
        record: dict[str, object] = {
            "id": entry_id,
            "summary": str(entry_data.get("summary", "")),
            "tags": entry_data.get("tags", []),
        }
        records.append(record)
        sidecar.parent.mkdir(parents=True, exist_ok=True)
        sidecar.write_text(
            "\n".join(json.dumps(r) for r in records) + "\n",
            encoding="utf-8",
        )

    def warm_remove(self, entry_id: str) -> None:
        """Delete an entry from the warm sqlite-vec store and sidecar.

        Args:
            entry_id: Learning entry identifier to remove.
        """
        from trw_mcp.state.memory_store import MemoryStore, get_memory_store

        db_path = self._get_warm_db_path()
        if MemoryStore.available():
            store = get_memory_store(db_path)
            store.delete(entry_id)

        # Also purge from sidecar
        sidecar = self._warm_sidecar_path()
        if sidecar.exists():
            lines = []
            for line in sidecar.read_text(encoding="utf-8").splitlines():
                line_s = line.strip()
                if not line_s:
                    continue
                try:
                    rec = json.loads(line_s)
                    if str(rec.get("id", "")) != entry_id:
                        lines.append(line_s)
                except json.JSONDecodeError:
                    continue
            sidecar.write_text("\n".join(lines) + "\n" if lines else "", encoding="utf-8")

        logger.debug("warm_tier_remove", entry_id=entry_id)

    def warm_search(
        self,
        query_tokens: list[str],
        query_embedding: list[float] | None,
        top_k: int = 25,
    ) -> list[dict[str, object]]:
        """Search the warm tier for relevant entries.

        Performs dense vector search when embedding is available; falls back
        to SQL LIKE keyword search over the sidecar when embedding is None
        or sqlite-vec is unavailable.

        Args:
            query_tokens: Tokenized query for keyword fallback.
            query_embedding: Optional dense query vector.
            top_k: Maximum results to return.

        Returns:
            List of dicts with at minimum ``{"id": ..., "score": ...}``.
        """
        from trw_mcp.state.memory_store import MemoryStore, get_memory_store

        db_path = self._get_warm_db_path()

        if MemoryStore.available() and query_embedding is not None:
            store = get_memory_store(db_path)
            raw = store.search(query_embedding, top_k=top_k)
            return [{"id": entry_id, "score": float(1.0 - dist)} for entry_id, dist in raw]

        # Keyword LIKE fallback via sidecar
        return self._warm_keyword_search(query_tokens, top_k)

    def _warm_keyword_search(self, query_tokens: list[str], top_k: int) -> list[dict[str, object]]:
        """Search the warm sidecar JSONL for keyword matches.

        Args:
            query_tokens: Tokens to match against summary and tags.
            top_k: Maximum results.

        Returns:
            List of result dicts with ``id`` and ``score`` (match fraction).
        """
        sidecar = self._warm_sidecar_path()
        if not sidecar.exists() or not query_tokens:
            return []

        results: list[dict[str, object]] = []
        lower_tokens = {t.lower() for t in query_tokens}
        for line in sidecar.read_text(encoding="utf-8").splitlines():
            line_s = line.strip()
            if not line_s:
                continue
            try:
                rec = json.loads(line_s)
            except json.JSONDecodeError:
                continue
            text = str(rec.get("summary", "")).lower()
            tags = [str(t).lower() for t in cast("list[object]", rec.get("tags") or [])]
            text += " " + " ".join(tags)
            matched = sum(1 for tok in lower_tokens if tok in text)
            if matched > 0:
                score = matched / len(lower_tokens)
                results.append({"id": str(rec.get("id", "")), "score": score})

        results.sort(key=lambda r: float(str(r.get("score", 0))), reverse=True)
        return results[:top_k]

    # -----------------------------------------------------------------------
    # Cold Tier — FR03
    # -----------------------------------------------------------------------

    def _cold_dir(self) -> Path:
        """Base cold archive directory (.trw/memory/cold/)."""
        return self._trw_dir / "memory" / "cold"

    def _cold_partition(self, ts: datetime | None = None) -> Path:
        """Return cold partition directory for a given datetime.

        Args:
            ts: Datetime to use for partitioning. Defaults to now (UTC).

        Returns:
            Path like .trw/memory/cold/2026/02/
        """
        if ts is None:
            ts = datetime.now(timezone.utc)
        return self._cold_dir() / str(ts.year) / f"{ts.month:02d}"

    def cold_archive(self, entry_id: str, entry_path: Path) -> None:
        """Move a warm-tier YAML entry to the cold archive partition.

        Writes the entry to .trw/memory/cold/{YYYY}/{MM}/{filename} atomically,
        then removes the original file. Removes the entry from the warm tier
        sqlite-vec store.

        Args:
            entry_id: Learning entry identifier.
            entry_path: Absolute path to the source YAML file (in entries/).
        """
        partition = self._cold_partition()
        partition.mkdir(parents=True, exist_ok=True)
        dest = partition / entry_path.name

        try:
            data = self._reader.read_yaml(entry_path)
            self._writer.write_yaml(dest, data)
            # Remove from warm vec store (best-effort)
            with contextlib.suppress(OSError, RuntimeError, ValueError):
                self.warm_remove(entry_id)
            # Delete original
            entry_path.unlink(missing_ok=True)
            logger.debug("cold_archive", entry_id=entry_id, dest=str(dest))
        except (OSError, RuntimeError, ValueError, TypeError):
            logger.warning(
                "cold_archive_failed",
                entry_id=entry_id,
                src=str(entry_path),
                dest=str(dest),
                exc_info=True,
            )
            raise

    def cold_promote(self, entry_id: str) -> dict[str, object] | None:
        """Move a cold-tier entry back to warm tier on access.

        Locates the YAML in the cold archive by scanning for a file
        containing the entry_id, copies it to the warm tier, updates
        last_accessed_at, and removes it from the cold archive.

        Args:
            entry_id: Learning entry identifier to promote.

        Returns:
            Entry data dict if found and promoted, None otherwise.
        """
        cold_base = self._cold_dir()
        if not cold_base.exists():
            return None

        for yaml_file in cold_base.rglob("*.yaml"):
            try:
                data = self._reader.read_yaml(yaml_file)
            except Exception:  # justified: scan-resilience, one corrupt YAML must not abort cold-tier lookup
                logger.warning("cold_tier_file_unreadable", path=str(yaml_file), exc_info=True)
                continue
            if str(data.get("id", "")) != entry_id:
                continue

            # Found — update last_accessed_at and move to warm
            data["last_accessed_at"] = datetime.now(tz=timezone.utc).date().isoformat()
            # Write back updated data before warm_add
            try:
                self._writer.write_yaml(yaml_file, data)
                self.warm_add(entry_id, data, None)
                yaml_file.unlink(missing_ok=True)
                logger.debug("cold_promote", entry_id=entry_id, src=str(yaml_file))
                return data
            except (OSError, RuntimeError, ValueError, TypeError):
                logger.warning(
                    "cold_promote_failed",
                    entry_id=entry_id,
                    path=str(yaml_file),
                    exc_info=True,
                )
                return None

        return None

    def cold_search(self, query_tokens: list[str]) -> list[dict[str, object]]:
        """Linear scan of the cold archive for keyword matches.

        Searches summary and tags across all cold YAML files.

        Args:
            query_tokens: Tokens to match (case-insensitive).

        Returns:
            List of matching entry dicts (includes all YAML fields).
        """
        cold_base = self._cold_dir()
        if not cold_base.exists() or not query_tokens:
            return []

        lower_tokens = {t.lower() for t in query_tokens}
        results: list[dict[str, object]] = []

        for yaml_file in sorted(cold_base.rglob("*.yaml")):
            try:
                data = self._reader.read_yaml(yaml_file)
            except Exception:  # justified: scan-resilience, one corrupt YAML must not abort cold-tier search
                logger.warning("cold_tier_file_unreadable", path=str(yaml_file), exc_info=True)
                continue

            text = str(data.get("summary", "")).lower()
            tags = [str(t).lower() for t in cast("list[object]", data.get("tags") or [])]
            text += " " + " ".join(tags)

            if any(tok in text for tok in lower_tokens):
                results.append(data)

        return results

    # -----------------------------------------------------------------------
    # PRD-FIX-052-FR01: Impact Tier Label Assignment
    # -----------------------------------------------------------------------

    @staticmethod
    def _compute_impact_tier(impact: float) -> str:
        """Return the impact tier label for a given impact score.

        Thresholds per PRD-FIX-052-FR01:
          critical >= 0.9
          high     >= 0.7
          medium   >= 0.4
          low      < 0.4

        Args:
            impact: Entry impact score in [0.0, 1.0].

        Returns:
            One of {"critical", "high", "medium", "low"}.
        """
        if impact >= 0.9:
            return "critical"
        if impact >= 0.7:
            return "high"
        if impact >= 0.4:
            return "medium"
        return "low"

    def assign_impact_tiers(self, trw_dir: Path) -> TierDistribution:
        """Assign impact_tier labels to all active learning entries.

        Iterates over active entries via list_active_learnings(), computes
        the tier from the impact score, and writes the impact_tier field
        back to the YAML file atomically. Skips entries with no YAML file.

        Per-entry failures are logged but do not abort the pass (fail-open).

        Args:
            trw_dir: Path to the .trw directory.

        Returns:
            Dict with per-tier counts: {"critical": N, "high": N, "medium": N, "low": N}.
        """
        import time

        cfg = self._config or get_config()
        entries_dir = trw_dir / cfg.learnings_dir / cfg.entries_dir
        distribution: TierDistribution = {"critical": 0, "high": 0, "medium": 0, "low": 0}
        t0 = time.monotonic()

        try:
            active_entries = list_active_learnings(trw_dir)
        except Exception:  # justified: boundary, listing active entries may fail on corrupt backend state
            logger.warning("assign_impact_tiers_list_failed", exc_info=True)
            return distribution

        for data in active_entries:
            entry_id = str(data.get("id", ""))
            if not entry_id:
                continue

            impact = max(0.0, min(1.0, float(str(data.get("impact", 0.5)))))
            tier = self._compute_impact_tier(impact)

            # Derive YAML path using same slug convention as rest of codebase
            slug = re.sub(r"[^a-zA-Z0-9_\-]", "-", entry_id)
            yaml_path = entries_dir / f"{slug}.yaml"
            if not yaml_path.exists():
                logger.debug("assign_impact_tiers_no_yaml", entry_id=entry_id)
                continue

            try:
                file_data = self._reader.read_yaml(yaml_path)
                file_data["impact_tier"] = tier
                self._writer.write_yaml(yaml_path, file_data)
                cast("dict[str, int]", distribution)[tier] += 1
            except (
                Exception
            ):  # justified: scan-resilience, one failed tier write must not abort the full assignment sweep
                logger.warning(
                    "assign_impact_tiers_write_failed",
                    entry_id=entry_id,
                    path=str(yaml_path),
                    exc_info=True,
                )

        duration_ms = round((time.monotonic() - t0) * 1000, 2)
        logger.info("tier_assignment_complete", distribution=distribution, duration_ms=duration_ms)
        return distribution

    # -----------------------------------------------------------------------
    # Sweep — FR04 / FR06  (implementations in _tier_sweep.py)
    # -----------------------------------------------------------------------

    _sweep_hot_to_warm = _sweep_hot_to_warm_impl
    _sweep_warm_to_cold = _sweep_warm_to_cold_impl
    _sweep_cold_to_purge = _sweep_cold_to_purge_impl
    sweep = _sweep_impl
