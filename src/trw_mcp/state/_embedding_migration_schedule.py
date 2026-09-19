"""When and where the automatic embedding migration runs, and what a session says.

Belongs to ``_embedding_migration`` (which owns the bounded run itself). This
module decides, WITHOUT loading the model, whether a store may need migrating;
refuses to start when the model is not cached and downloads are blocked
(``TRW_OFFLINE`` / ``HF_HUB_OFFLINE`` / memory ``local_only``); starts at most one
background run per process (a daemon thread, so ``trw_session_start`` never
waits on it); and renders the one progress line a session reports.
"""

from __future__ import annotations

import os
import sqlite3
import threading
from contextlib import closing
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog

from trw_mcp.state._constants import DEFAULT_NAMESPACE
from trw_mcp.state._embedding_migration import Budget, load_migration_state, run_migration

if TYPE_CHECKING:
    from trw_memory.embeddings.interface import EmbeddingProvider

__all__ = [
    "SESSION_BUDGET",
    "encoder_identity",
    "migrate_now",
    "migration_needed",
    "offline_block",
    "plan_session_migration",
    "schedule_embedding_migration",
    "vector_counts",
    "wait_for_migration",
]

logger = structlog.get_logger(__name__)

#: One session's share of the work. At ~10 ms per encode (bge-small on an Apple
#: GPU; a few times that on a laptop CPU) this re-embeds a few thousand rows,
#: i.e. a typical store in one session and a large one over several.
SESSION_BUDGET = Budget(seconds=45.0, rows=5000)

_THREAD: threading.Thread | None = None
_THREAD_LOCK = threading.Lock()


def encoder_identity() -> dict[str, Any]:
    """``{"model", "revision", "dim"}`` of the configured encoder, no model load."""
    from trw_mcp.models.config import get_config

    cfg = get_config()
    revision = ""
    try:
        from trw_memory.embeddings._declared_space import snapshot_revision
        from trw_memory.embeddings._hf_cache import probe_model_cache

        revision = snapshot_revision(probe_model_cache(cfg.retrieval_embedding_model).snapshot_path)
    except Exception:  # justified: fail-open, an unreadable cache only weakens the no-load staleness check
        logger.debug("embedding_migration_revision_unavailable", exc_info=True)
    return {"model": cfg.retrieval_embedding_model, "revision": revision, "dim": cfg.retrieval_embedding_dim}


def offline_block(model: str) -> str:
    """What blocks loading *model* here, or ``""`` when it can be loaded.

    Only a download can be blocked: an already-loaded embedder or a complete
    local snapshot loads with no network at all.
    """
    from trw_memory.embeddings._hf_cache import CacheState, probe_model_cache

    from trw_mcp.state import _memory_connection as conn
    from trw_mcp.state._memory_offline import embeddings_offline

    if conn.get_initialized_embedder() is not None:
        return ""
    switch = "TRW_OFFLINE/HF_HUB_OFFLINE" if embeddings_offline(dict(os.environ)) else ""
    if not switch:
        try:
            from trw_memory.models.config import MemoryConfig

            switch = "memory local_only=True" if MemoryConfig().local_only else ""
        except Exception:  # justified: fail-open, an unreadable config leaves the loader's own guard in charge
            logger.debug("embedding_migration_local_only_unreadable", exc_info=True)
    if not switch:
        return ""
    try:
        cached = probe_model_cache(model).state is CacheState.COMPLETE
    except Exception:  # justified: fail-closed, an uninspectable cache is not proof the model is present
        logger.debug("embedding_migration_cache_probe_failed", exc_info=True)
        cached = False
    return "" if cached else switch


def vector_counts(trw_dir: Path) -> tuple[int, int] | None:
    """``(stored, space_less)`` vectors in the project store; ``None`` when unreadable.

    A project with no store yet has ``(0, 0)``: nothing to migrate.
    """
    db_path = trw_dir / "memory" / "memory.db"
    if not db_path.is_file():
        return (0, 0)
    try:
        with closing(sqlite3.connect(db_path.as_uri() + "?mode=ro", uri=True)) as connection:
            stored, space_less = connection.execute(
                "SELECT COUNT(*), COALESCE(SUM(provenance_json IS NULL), 0) FROM vec_index WHERE namespace = ?",
                (DEFAULT_NAMESPACE,),
            ).fetchone()
    except sqlite3.Error:
        # Unknown, and every caller treats unknown as "may need migrating".
        logger.info("embedding_migration_count_unavailable", reason="vector_index_unreadable", exc_info=True)
        return None
    return int(stored), int(space_less)


def migration_needed(trw_dir: Path, identity: dict[str, Any]) -> bool:
    """Whether a run could have work, decided without loading the model.

    A store with no vectors needs nothing (every new vector records its space).
    A finished migration for the same model and dimension -- and snapshot
    revision, when *identity* carries one -- with no new space-less vectors
    needs nothing either. (A vector written in another space by a differently
    configured process is only seen by a run; that is the one case this cheap
    check cannot rule out.)
    """
    counts = vector_counts(trw_dir)
    if counts is not None and counts[0] == 0:
        return False
    state = load_migration_state(trw_dir)
    if state is None or not state.done or (state.model, state.dim) != (identity["model"], identity["dim"]):
        return True
    if "revision" in identity and state.revision != identity["revision"]:
        return True
    return counts is None or counts[1] > state.residual_unqualified


def migrate_now(trw_dir: Path, provider: EmbeddingProvider, *, budget: Budget) -> dict[str, object]:
    """One bounded foreground run (``update-project``)."""
    return run_migration(trw_dir, provider, identity=encoder_identity(), budget=budget)


def _run_background(trw_dir: Path) -> None:
    """The background half: the revision check, the offline gate, then the run.

    The snapshot-revision probe imports ``huggingface_hub`` (~35 ms cold), so
    it runs here rather than on the session-start path; a store already
    migrated for this exact encoder exits before any model load.
    """
    from trw_mcp.state import _memory_connection as conn

    try:
        identity = encoder_identity()
        if not migration_needed(trw_dir, identity):
            logger.debug("embedding_migration_not_needed", model=identity["model"])
            return
        if reason := offline_block(str(identity["model"])):
            logger.info("embedding_migration_skipped", reason="model_not_cached_offline", blocked_by=reason)
            return
        provider = conn.get_embedder()
        if provider is None:
            logger.info("embedding_migration_skipped", reason="embedder_unavailable")
            return
        report = run_migration(trw_dir, provider, identity=identity, budget=SESSION_BUDGET)
        logger.info("embedding_migration_background_done", **report)
    except Exception:  # justified: fail-open, a background migration must never crash the MCP server
        logger.warning("embedding_migration_failed", trw_dir=str(trw_dir), exc_info=True)


def schedule_embedding_migration(trw_dir: Path) -> bool:
    """Start one background run unless this process already has one going."""
    global _THREAD
    with _THREAD_LOCK:
        if _THREAD is not None and _THREAD.is_alive():
            return False
        _THREAD = threading.Thread(target=_run_background, args=(trw_dir,), name="trw-embed-migration", daemon=True)
        _THREAD.start()
    return True


def wait_for_migration(timeout: float | None = None) -> None:
    """Join this process's background run, if any (tests and shutdown)."""
    with _THREAD_LOCK:
        thread = _THREAD
    if thread is not None:
        thread.join(timeout)


def _line(status: str, message: str) -> dict[str, object]:
    return {"status": status, "message": message}


def plan_session_migration(trw_dir: Path) -> dict[str, object] | None:
    """Schedule the migration when it may have work; return the session's line.

    ``None`` means nothing worth reporting: no work, or a run that has not yet
    measured anything and nothing known to be stale. Never blocks -- every
    check here is a small file read or one COUNT on the vector index. A store
    that looks migrated still gets the background thread, which re-checks the
    snapshot revision off this path and exits without loading the model.
    """
    from trw_mcp.models.config import get_config

    counts = vector_counts(trw_dir)
    if counts is not None and counts[0] == 0:
        return None  # no stored vectors: nothing to migrate, and no model to load
    cfg = get_config()
    model, dim = cfg.retrieval_embedding_model, cfg.retrieval_embedding_dim
    state = load_migration_state(trw_dir)
    ours = state if state is not None and (state.model, state.dim) == (model, dim) else None
    if not migration_needed(trw_dir, {"model": model, "dim": dim}):
        if ours is not None and ours.status == "stalled":
            from trw_mcp.state._embedding_space import REPAIR_COMMAND

            return _line(
                "stalled",
                f"{ours.residual:,} stored vectors could not be re-embedded to {model} and stay out of "
                f"dense recall; see the embedding_migration logs, or retry with `{REPAIR_COMMAND}`",
            )
        schedule_embedding_migration(trw_dir)
        return None
    unqualified = (counts or (0, 0))[1]
    pending = ours.total - ours.repaired if ours is not None and ours.status == "in_progress" else unqualified
    if reason := offline_block(model):
        logger.info("embedding_migration_skipped", reason="model_not_cached_offline", blocked_by=reason)
        if pending <= 0:
            return None
        return _line(
            "skipped_offline",
            f"{pending:,} stored vectors need re-embedding to {model}, but the model is not in the local "
            f"cache and downloads are blocked by {reason}; dense recall stays partial until it is "
            f"(python -m sentence_transformers download {model}, then start a session)",
        )
    schedule_embedding_migration(trw_dir)
    if pending <= 0:
        return None
    repaired, total = (ours.repaired, ours.total) if ours is not None and ours.status == "in_progress" else (0, pending)
    return _line(
        "in_progress" if repaired else "scheduled",
        f"re-embedding {min(repaired, total):,}/{total:,} stored vectors to {model} in the background; "
        "dense recall and similarity links are partial until done",
    )
