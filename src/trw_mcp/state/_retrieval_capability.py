"""What recall can use on this install (docs/sprint-mcp7/PLAN.md §3b item 3).

Four components, each ``active``, ``degraded`` (with the one-line fix) or ``off``:

* ``vectors`` -- sqlite-vec loads into the SQLite engine trw-memory selected
  (a base dependency since 6.1.0, but an interpreter built without extension
  loading still cannot use it).
* ``embeddings`` -- sentence-transformers is importable. ``off`` when the
  operator set ``embeddings_enabled: false``: a stated choice, reported, not a fault.
* ``weights`` -- the configured recall model is complete in the local Hugging
  Face cache, so the first embed neither downloads nor fails offline.
* ``bm25`` -- rank-bm25 is importable. Optional: FTS5 keyword search always
  runs, so a missing bm25 is listed but never makes retrieval degraded.

Probes stay cheap -- ``find_spec``, an in-memory extension load and a cache
walk; never a torch import or a model load. The daemon trw-mcp auto-starts runs
this same interpreter (``start_daemon_detached`` launches ``sys.executable``),
so a probe here answers for it too.
"""

from __future__ import annotations

from dataclasses import dataclass
from importlib import import_module
from importlib.util import find_spec
from typing import Literal

__all__ = ["RetrievalComponent", "probe_retrieval", "retrieval_row", "retrieval_summary"]

State = Literal["active", "degraded", "off"]

_EMBEDDINGS_FIX = "pip install 'trw-memory[embeddings]'"
_BM25_FIX = "pip install 'trw-memory[bm25]'"


@dataclass(frozen=True)
class RetrievalComponent:
    """One retrieval component's state, why, and the fix when it is degraded."""

    name: str
    state: State
    detail: str
    fix: str = ""


def _vectors() -> RetrievalComponent:
    if find_spec("sqlite_vec") is None:
        return RetrievalComponent(
            "vectors", "degraded", "sqlite-vec is not installed", "reinstall trw-mcp (sqlite-vec is a base dependency)"
        )
    import sqlite_vec
    import trw_memory.storage._dbapi  # noqa: F401

    # Importing _dbapi selected the engine (it may swap pysqlite3 into
    # ``sys.modules["sqlite3"]``); resolve it only now, so import order cannot
    # hand the probe a different SQLite than the store runs on.
    engine = import_module("sqlite3")
    conn = engine.connect(":memory:")
    try:
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
    except (engine.Error, OSError, AttributeError) as exc:
        return RetrievalComponent(
            "vectors",
            "degraded",
            f"sqlite-vec does not load ({type(exc).__name__})",
            "use a Python whose SQLite allows extension loading (e.g. a uv or python.org build)",
        )
    finally:
        conn.close()
    return RetrievalComponent("vectors", "active", "sqlite-vec loads")


def _weights(model: str) -> RetrievalComponent:
    from trw_memory.embeddings._hf_cache import CacheState, probe_model_cache

    state = probe_model_cache(model).state
    if state is CacheState.COMPLETE:
        return RetrievalComponent("weights", "active", f"{model} cached")
    fix = f"python -c \"from sentence_transformers import SentenceTransformer; SentenceTransformer('{model}')\""
    return RetrievalComponent("weights", "degraded", f"{model} cache {state.value}", fix)


def probe_retrieval(model: str, *, embeddings_enabled: bool) -> tuple[RetrievalComponent, ...]:
    """The four components, in report order."""
    if not embeddings_enabled:
        embeddings = RetrievalComponent("embeddings", "off", "embeddings_enabled=false")
        weights = RetrievalComponent("weights", "off", "embeddings_enabled=false")
    elif find_spec("sentence_transformers") is None:
        embeddings = RetrievalComponent(
            "embeddings", "degraded", "sentence-transformers is not installed", _EMBEDDINGS_FIX
        )
        weights = _weights(model)
    else:
        embeddings = RetrievalComponent("embeddings", "active", "sentence-transformers importable")
        weights = _weights(model)
    if find_spec("rank_bm25") is None:
        bm25 = RetrievalComponent("bm25", "off", f"optional; FTS5 keyword search active ({_BM25_FIX})")
    else:
        bm25 = RetrievalComponent("bm25", "active", "rank-bm25 importable")
    return (_vectors(), embeddings, weights, bm25)


def retrieval_summary(components: tuple[RetrievalComponent, ...]) -> str:
    """``session_start``'s compact field: ``active``, ``keyword-only: ...`` or ``degraded: ...``."""
    degraded = [c for c in components if c.state == "degraded"]
    if degraded:
        return "degraded: " + "; ".join(f"{c.name} ({c.detail}) fix: {c.fix}" for c in degraded)
    if any(c.name == "embeddings" and c.state == "off" for c in components):
        return "keyword-only: embeddings_enabled=false"
    return "active"


def retrieval_row(components: tuple[RetrievalComponent, ...]) -> tuple[str, str]:
    """``(status, message)`` for ``doctor``: WARN when any component is degraded."""
    parts = [f"{c.name} {c.state} ({c.detail})" + (f" fix: {c.fix}" if c.fix else "") for c in components]
    status = "WARN" if any(c.state == "degraded" for c in components) else "PASS"
    return status, "; ".join(parts)
