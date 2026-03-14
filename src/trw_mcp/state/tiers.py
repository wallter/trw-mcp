"""Tiered memory storage: Hot (LRU) / Warm (sqlite-vec) / Cold (YAML archive).

Implements PRD-CORE-043 — lifecycle management for learning entries with
automatic tier transitions based on recency and importance scores.
"""

from __future__ import annotations

import contextlib
import json
import math
import re
from collections import OrderedDict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import cast

import structlog

from trw_memory.lifecycle.tiers import TierSweepResult as TierSweepResult

from trw_mcp.models.config import TRWConfig, get_config
from trw_mcp.models.learning import LearningEntry
from trw_mcp.scoring import _days_since_access
from trw_mcp.state.dedup import cosine_similarity
from trw_mcp.state.memory_adapter import list_active_learnings
from trw_mcp.state.persistence import FileStateReader, FileStateWriter

logger = structlog.get_logger()


# ---------------------------------------------------------------------------
# Importance scoring (FR05)
# NOTE: parallel implementation exists in trw_memory.lifecycle.tiers —
# field names differ (impact vs importance, TRWConfig vs MemoryConfig).
# ---------------------------------------------------------------------------


def compute_importance_score(
    entry: dict[str, object],
    query_tokens: list[str],
    query_embedding: list[float] | None = None,
    entry_embedding: list[float] | None = None,
    *,
    config: TRWConfig | None = None,
) -> float:
    """Compute a composite importance score for a learning entry.

    Formula: score = w1*relevance + w2*recency + w3*importance

    Weights are normalized if they don't sum to 1.0.

    Args:
        entry: Learning entry as a dict (from YAML).
        query_tokens: Tokenized query for token-overlap fallback.
        query_embedding: Optional dense query vector for cosine similarity.
        entry_embedding: Optional dense entry vector for cosine similarity.
        config: TRWConfig for weights and decay settings. Uses get_config() if None.

    Returns:
        Composite importance score in [0.0, 1.0].
    """
    cfg = config or get_config()

    w1 = cfg.memory_score_w1
    w2 = cfg.memory_score_w2
    w3 = cfg.memory_score_w3

    # Normalize weights
    total_w = w1 + w2 + w3
    if total_w > 0 and abs(total_w - 1.0) > 1e-9:
        w1 /= total_w
        w2 /= total_w
        w3 /= total_w

    # Relevance: cosine similarity when both embeddings present, else token overlap
    if query_embedding is not None and entry_embedding is not None:
        relevance = max(0.0, cosine_similarity(query_embedding, entry_embedding))
    else:
        # Token overlap ratio fallback
        entry_text = (
            str(entry.get("summary", "")).lower()
            + " "
            + str(entry.get("detail", "")).lower()
        )
        entry_tokens = set(entry_text.split())
        query_set = {t.lower() for t in query_tokens}
        if query_set:
            relevance = len(query_set & entry_tokens) / len(query_set)
        else:
            relevance = 0.0

    # Recency: exponential decay based on days since access
    today = date.today()
    days = _days_since_access(entry, today)
    half_life = cfg.learning_decay_half_life_days
    decay_rate = math.log(2) / half_life if half_life > 0 else 0.0
    recency = math.exp(-decay_rate * days)

    # Importance: the entry's Bayesian-calibrated impact field
    importance = float(str(entry.get("impact", 0.5)))
    importance = max(0.0, min(1.0, importance))

    score = w1 * relevance + w2 * recency + w3 * importance
    return max(0.0, min(1.0, score))


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
        entries_dir = (
            self._trw_dir / cfg.learnings_dir / cfg.entries_dir
        )
        # Derive exact filename from entry.id (same slugify convention as the codebase)
        sanitized = re.sub(r"[^a-zA-Z0-9_\-]", "-", entry_id)
        target = entries_dir / f"{sanitized}.yaml"
        if not target.exists():
            # No file yet — nothing to flush
            return
        try:
            data = self._reader.read_yaml(target)
            data["last_accessed_at"] = date.today().isoformat()
            self._writer.write_yaml(target, data)
        except Exception:  # noqa: BLE001 — best-effort flush, must not raise
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
        from trw_mcp.state.memory_store import MemoryStore

        db_path = self._get_warm_db_path()

        if MemoryStore.available() and embedding is not None:
            store = MemoryStore(db_path)
            try:
                store.upsert(entry_id, embedding, {"source": "warm_tier"})
            finally:
                store.close()
        else:
            # Fallback: write to a JSON sidecar for keyword search
            self._warm_sidecar_upsert(entry_id, entry_data)

        logger.debug("warm_tier_add", entry_id=entry_id, has_embedding=embedding is not None)

    def _warm_sidecar_path(self) -> Path:
        """Path to the warm tier keyword-search sidecar (JSONL)."""
        return self._get_warm_db_path().with_suffix(".jsonl")

    def _warm_sidecar_upsert(
        self, entry_id: str, entry_data: dict[str, object]
    ) -> None:
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
        from trw_mcp.state.memory_store import MemoryStore

        db_path = self._get_warm_db_path()
        if MemoryStore.available():
            store = MemoryStore(db_path)
            try:
                store.delete(entry_id)
            finally:
                store.close()

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
        from trw_mcp.state.memory_store import MemoryStore

        db_path = self._get_warm_db_path()

        if MemoryStore.available() and query_embedding is not None:
            store = MemoryStore(db_path)
            try:
                raw = store.search(query_embedding, top_k=top_k)
            finally:
                store.close()
            return [{"id": entry_id, "score": float(1.0 - dist)} for entry_id, dist in raw]

        # Keyword LIKE fallback via sidecar
        return self._warm_keyword_search(query_tokens, top_k)

    def _warm_keyword_search(
        self, query_tokens: list[str], top_k: int
    ) -> list[dict[str, object]]:
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
            except Exception:  # noqa: BLE001 — skip unreadable files in scan
                continue
            if str(data.get("id", "")) != entry_id:
                continue

            # Found — update last_accessed_at and move to warm
            data["last_accessed_at"] = date.today().isoformat()
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
            except Exception:  # noqa: BLE001 — skip unreadable files in scan
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

    def assign_impact_tiers(self, trw_dir: Path) -> dict[str, int]:
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
        distribution: dict[str, int] = {"critical": 0, "high": 0, "medium": 0, "low": 0}
        t0 = time.monotonic()

        try:
            active_entries = list_active_learnings(trw_dir)
        except Exception:  # noqa: BLE001 — fail-open
            logger.warning("assign_impact_tiers_list_failed", exc_info=True)
            return distribution

        for data in active_entries:
            entry_id = str(data.get("id", ""))
            if not entry_id:
                continue

            impact = max(0.0, min(1.0, float(str(data.get("impact", 0.5)))))
            tier = self._compute_impact_tier(impact)

            # Derive YAML path using same slug convention as rest of codebase
            yaml_path = entries_dir / f"{re.sub(r'[^a-zA-Z0-9_\-]', '-', entry_id)}.yaml"
            if not yaml_path.exists():
                logger.debug("assign_impact_tiers_no_yaml", entry_id=entry_id)
                continue

            try:
                file_data = self._reader.read_yaml(yaml_path)
                file_data["impact_tier"] = tier
                self._writer.write_yaml(yaml_path, file_data)
                distribution[tier] += 1
            except Exception:  # noqa: BLE001 — fail-open per-entry
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
    # Sweep — FR04 / FR06
    # -----------------------------------------------------------------------

    def _sweep_hot_to_warm(
        self, cfg: TRWConfig, today: date,
    ) -> tuple[int, int]:
        """Phase 1: evict stale hot-tier entries into warm tier.

        Identifies entries whose last_accessed_at exceeds
        ``memory_hot_ttl_days``, moves them to the warm tier,
        and flushes last_accessed_at to disk.

        Args:
            cfg: Resolved TRWConfig for threshold values.
            today: Reference date for staleness checks.

        Returns:
            Tuple of (demoted, errors) counts.
        """
        demoted = 0
        errors = 0

        stale_hot_ids: list[str] = []
        for entry_id, entry in list(self._hot.items()):
            days = _days_since_access(entry.model_dump(), today)
            if days > cfg.memory_hot_ttl_days:
                stale_hot_ids.append(entry_id)

        for entry_id in stale_hot_ids:
            try:
                evicted = self._hot.pop(entry_id)
                self.warm_add(entry_id, evicted.model_dump(), None)
                self._flush_last_accessed(entry_id)
                demoted += 1
                logger.debug("sweep_hot_to_warm", entry_id=entry_id)
            except Exception:  # noqa: BLE001 — sweep error counter, any failure increments
                logger.warning("sweep_hot_to_warm_failed", entry_id=entry_id, exc_info=True)
                errors += 1

        return demoted, errors

    def _sweep_warm_to_cold(
        self, cfg: TRWConfig, today: date, entries_dir: Path,
    ) -> tuple[int, int]:
        """Phase 2: demote idle low-importance warm entries to cold archive.

        PRD-FIX-033-FR05: Uses SQLite via ``list_active_learnings`` when
        available, falling back to YAML glob on error.
        Uses ``compute_importance_score`` (Stanford Generative Agents formula)
        instead of raw impact for more nuanced tier transition decisions.

        Args:
            cfg: Resolved TRWConfig for threshold values.
            today: Reference date for staleness checks.
            entries_dir: Path to the warm-tier YAML entries directory.

        Returns:
            Tuple of (demoted, errors) counts.
        """
        demoted = 0
        errors = 0

        _used_sqlite = False
        try:
            from trw_mcp.state.memory_adapter import (
                find_yaml_path_for_entry,
                list_active_learnings,
            )
            active_entries = list_active_learnings(self._trw_dir)
            for data in active_entries:
                entry_id = str(data.get("id", ""))
                if not entry_id:
                    continue
                try:
                    days = _days_since_access(data, today)
                    importance = compute_importance_score(data, [], config=cfg)
                    if days > cfg.memory_cold_threshold_days and importance < 0.22:
                        # Resolve YAML path for cold_archive
                        yaml_file = find_yaml_path_for_entry(self._trw_dir, entry_id)
                        if yaml_file is None:
                            logger.warning(
                                "sweep_warm_to_cold_no_yaml",
                                entry_id=entry_id,
                            )
                            continue
                        self.cold_archive(entry_id, yaml_file)
                        demoted += 1
                        logger.debug(
                            "sweep_warm_to_cold",
                            entry_id=entry_id,
                            days=days,
                            importance_score=importance,
                        )
                except Exception:  # noqa: BLE001 — sweep error counter
                    logger.warning(
                        "sweep_warm_to_cold_failed",
                        entry_id=entry_id,
                        exc_info=True,
                    )
                    errors += 1
            _used_sqlite = True
        except (ImportError, OSError, RuntimeError, ValueError, TypeError):
            logger.warning(
                "sqlite_read_fallback",
                step="sweep_warm_to_cold",
                reason="list_active_learnings failed",
            )

        if not _used_sqlite and entries_dir.exists():
            # YAML fallback path (original implementation)
            for yaml_file in sorted(entries_dir.glob("*.yaml")):
                if yaml_file.name == "index.yaml":
                    continue
                try:
                    data = self._reader.read_yaml(yaml_file)
                    entry_id = str(data.get("id", ""))
                    if not entry_id:
                        continue
                    # Skip non-active entries
                    if str(data.get("status", "active")) != "active":
                        continue
                    days = _days_since_access(data, today)
                    importance = compute_importance_score(data, [], config=cfg)
                    if days > cfg.memory_cold_threshold_days and importance < 0.22:
                        self.cold_archive(entry_id, yaml_file)
                        demoted += 1
                        logger.debug(
                            "sweep_warm_to_cold",
                            entry_id=entry_id,
                            days=days,
                            importance_score=importance,
                        )
                except Exception:  # noqa: BLE001 — sweep error counter
                    logger.warning(
                        "sweep_warm_to_cold_failed",
                        path=str(yaml_file),
                        exc_info=True,
                    )
                    errors += 1

        return demoted, errors

    def _sweep_cold_to_purge(
        self, cfg: TRWConfig, today: date, purge_audit_path: Path,
    ) -> tuple[int, int]:
        """Phase 3: purge expired cold-tier entries past retention.

        Scans the cold archive for entries idle longer than
        ``memory_retention_days`` with importance below 0.1. Writes
        an audit record to ``purge_audit_path`` before deletion.

        Uses ``compute_importance_score`` for purge decisions.

        Args:
            cfg: Resolved TRWConfig for threshold values.
            today: Reference date for staleness checks.
            purge_audit_path: JSONL file for purge audit records.

        Returns:
            Tuple of (purged, errors) counts.
        """
        purged = 0
        errors = 0

        cold_base = self._cold_dir()
        if cold_base.exists():
            for yaml_file in sorted(cold_base.rglob("*.yaml")):
                try:
                    data = self._reader.read_yaml(yaml_file)
                    entry_id = str(data.get("id", ""))
                    days = _days_since_access(data, today)
                    importance = compute_importance_score(data, [], config=cfg)
                    if days > cfg.memory_retention_days and importance < 0.1:
                        # Append to purge audit log before deleting
                        audit_record: dict[str, object] = {
                            "entry_id": entry_id,
                            "purged_at": datetime.now(timezone.utc).isoformat(),
                            "days_idle": days,
                            "importance_score": importance,
                            "impact": float(str(data.get("impact", 0.5))),
                            "summary": str(data.get("summary", "")),
                        }
                        purge_audit_path.parent.mkdir(parents=True, exist_ok=True)
                        with purge_audit_path.open("a", encoding="utf-8") as fh:
                            fh.write(json.dumps(audit_record) + "\n")
                        yaml_file.unlink(missing_ok=True)
                        purged += 1
                        logger.debug(
                            "sweep_cold_purge",
                            entry_id=entry_id,
                            days=days,
                            importance_score=importance,
                        )
                except Exception:  # noqa: BLE001 — sweep error counter
                    logger.warning(
                        "sweep_cold_purge_failed",
                        path=str(yaml_file),
                        exc_info=True,
                    )
                    errors += 1

        return purged, errors

    def sweep(self) -> TierSweepResult:
        """Execute lifecycle sweep across all tiers.

        Performs four transition checks in order:
        1. Hot → Warm: entries whose last_accessed_at exceeds memory_hot_ttl_days.
        2. Warm → Cold: entries idle > memory_cold_threshold_days with impact < 0.5.
        3. Cold → Purge: entries idle > memory_retention_days with impact < 0.3.
        4. Cold → Warm is handled on-demand by cold_promote().

        All thresholds are read from get_config() at call time (FR06).
        Per-entry failures are logged and counted in ``errors``; the sweep
        continues with remaining entries.

        Returns:
            TierSweepResult with counts of promoted, demoted, purged, and errors.
        """
        cfg = get_config()
        today = date.today()

        entries_dir = self._trw_dir / cfg.learnings_dir / cfg.entries_dir
        purge_audit_path = self._trw_dir / "memory" / "purge_audit.jsonl"

        # Phase 1: Hot → Warm
        hot_demoted, hot_errors = self._sweep_hot_to_warm(cfg, today)

        # Phase 2: Warm → Cold
        warm_demoted, warm_errors = self._sweep_warm_to_cold(
            cfg, today, entries_dir,
        )

        # Phase 3: Cold → Purge
        purged, purge_errors = self._sweep_cold_to_purge(
            cfg, today, purge_audit_path,
        )

        demoted = hot_demoted + warm_demoted
        errors = hot_errors + warm_errors + purge_errors
        promoted = 0

        logger.info(
            "tier_sweep_complete",
            promoted=promoted,
            demoted=demoted,
            purged=purged,
            errors=errors,
        )
        return TierSweepResult(
            promoted=promoted,
            demoted=demoted,
            purged=purged,
            errors=errors,
        )
