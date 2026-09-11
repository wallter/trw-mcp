"""Tests for _pipeline_health — PRD-FIX-COMPOUNDING-6.

Covers the five compounding-pipeline probes and the step_pipeline_health
aggregator. All paths are fail-open (never raise). Tests follow the
pattern established in test_sync_health.py.

These tests use tmp_path for filesystem fixtures, so they are integration
tier (default when not in _UNIT_FILES).
"""

from __future__ import annotations

import json
import sqlite3
import time
from contextlib import AbstractContextManager, nullcontext
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from tests._structlog_capture import captured_structlog  # noqa: F401

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_trw_dir(tmp_path: Path) -> Path:
    """Create a minimal .trw directory structure."""
    trw_dir = tmp_path / ".trw"
    trw_dir.mkdir(parents=True, exist_ok=True)
    (trw_dir / "memory").mkdir(exist_ok=True)
    (trw_dir / "meta").mkdir(exist_ok=True)
    return trw_dir


def _write_sync_state(trw_dir: Path, state: dict[str, object]) -> None:
    (trw_dir / "sync-state.json").write_text(json.dumps(state))


def _iso_ago(hours: float) -> str:
    return (datetime.now(tz=timezone.utc) - timedelta(hours=hours)).isoformat()


def _days_ago(days: float) -> float:
    """Return a Unix mtime N days ago."""
    return time.time() - (days * 86400)


def _make_memory_db(
    trw_dir: Path,
    *,
    corpus: int = 0,
    vec: int = 0,
    max_recall: int = 0,
    edges: int = 0,
    shared_tags: bool = False,
) -> Path:
    """Create a minimal memory.db with the required tables.

    ``memories`` carries ``namespace``/``updated_at`` and ``memory_tags`` exists
    because ``probe_graph_edges`` reads both: PRD-CORE-245 FR07 moved tag
    co-occurrence out of ``memory_graph_edges`` and into that index, so the
    probe's health verdict is no longer answerable from the edge table alone.
    ``shared_tags`` gives every entry the same two tags, i.e. a corpus whose
    only relations are derived ones.
    """
    db_path = trw_dir / "memory" / "memory.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "CREATE TABLE IF NOT EXISTS memories ("
        "id TEXT PRIMARY KEY, recall_count INTEGER DEFAULT 0, "
        "namespace TEXT DEFAULT 'default', updated_at TEXT DEFAULT '')"
    )
    conn.execute("CREATE TABLE IF NOT EXISTS vec_memories (id TEXT PRIMARY KEY)")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS memory_graph_edges (id INTEGER PRIMARY KEY, source_id TEXT, target_id TEXT)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS memory_tags "
        "(namespace TEXT NOT NULL, tag TEXT NOT NULL, entry_id TEXT NOT NULL, PRIMARY KEY (namespace, tag, entry_id))"
    )
    # Insert corpus entries
    for i in range(corpus):
        recall = max_recall if i == 0 and max_recall > 0 else 0
        conn.execute(
            "INSERT INTO memories (id, recall_count, namespace, updated_at) VALUES (?, ?, 'default', ?)",
            (f"m{i}", recall, f"2026-09-03T00:00:{i:02d}+00:00"),
        )
        if shared_tags:
            for tag in ("alpha", "beta"):
                conn.execute(
                    "INSERT INTO memory_tags (namespace, tag, entry_id) VALUES ('default', ?, ?)",
                    (tag, f"m{i}"),
                )
    # Insert vec entries
    for i in range(vec):
        conn.execute("INSERT INTO vec_memories (id) VALUES (?)", (f"m{i}",))
    # Insert edges
    for i in range(edges):
        conn.execute(
            "INSERT INTO memory_graph_edges (source_id, target_id) VALUES (?, ?)",
            (f"m{i}", f"m{i + 1}"),
        )
    conn.commit()
    conn.close()
    return db_path


def _make_bandit_file(trw_dir: Path) -> Path:
    """Create .trw/meta/bandit_state.json with a recent mtime."""
    p = trw_dir / "meta" / "bandit_state.json"
    p.write_text(json.dumps({"state": "ok"}))
    return p


# ---------------------------------------------------------------------------
# probe_sync_push
# ---------------------------------------------------------------------------


def test_probe_sync_push_degraded_consecutive(tmp_path: Path) -> None:
    """consecutive_failures >= 10 => degraded=True, advisory mentions sync_push."""
    from trw_mcp.tools._pipeline_health import probe_sync_push

    trw_dir = _make_trw_dir(tmp_path)
    _write_sync_state(trw_dir, {"consecutive_failures": 11, "last_push_at": _iso_ago(0.5)})

    result = probe_sync_push(trw_dir)

    assert result["degraded"] is True
    assert "sync_push" in str(result.get("advisory", "")).lower() or result["advisory"] != ""
    assert result.get("consecutive_failures") == 11


def test_probe_sync_push_degraded_stale(tmp_path: Path) -> None:
    """last_push_at older than 6h => degraded even if failures low."""
    from trw_mcp.tools._pipeline_health import probe_sync_push

    trw_dir = _make_trw_dir(tmp_path)
    _write_sync_state(trw_dir, {"consecutive_failures": 2, "last_push_at": _iso_ago(48)})

    result = probe_sync_push(trw_dir)

    assert result["degraded"] is True
    assert result["advisory"] != ""


def test_probe_sync_push_healthy(tmp_path: Path) -> None:
    """Low failures + recent push => not degraded."""
    from trw_mcp.tools._pipeline_health import probe_sync_push

    trw_dir = _make_trw_dir(tmp_path)
    _write_sync_state(trw_dir, {"consecutive_failures": 0, "last_push_at": _iso_ago(0.5)})

    result = probe_sync_push(trw_dir)

    assert result["degraded"] is False
    assert result["advisory"] == ""


def test_probe_sync_push_missing_state_file(tmp_path: Path) -> None:
    """No sync-state.json => fail-open, degraded=False.

    DEF-07 attribution: a missing state file must report ``measured: False``
    with a named reason, not the SAME ``measured: True`` shape a genuinely
    healthy push reports. Reverting the fix (restoring the early
    ``return safe_default``) turns the ``measured``/``advisory`` assertions
    red while ``degraded`` stays green either way (unchanged behaviour).
    """
    from trw_mcp.tools._pipeline_health import probe_sync_push

    trw_dir = _make_trw_dir(tmp_path)
    # Don't create sync-state.json

    result = probe_sync_push(trw_dir)

    assert result["degraded"] is False
    assert result["measured"] is False
    assert "state_file_missing" in result["advisory"]


def test_probe_sync_push_corrupt_json(tmp_path: Path) -> None:
    """Malformed sync-state.json => fail-open, degraded=False."""
    from trw_mcp.tools._pipeline_health import probe_sync_push

    trw_dir = _make_trw_dir(tmp_path)
    (trw_dir / "sync-state.json").write_text("NOT JSON {{{")

    result = probe_sync_push(trw_dir)

    assert result["degraded"] is False


# ---------------------------------------------------------------------------
# probe_graph_edges
# ---------------------------------------------------------------------------


def test_probe_graph_edges_degraded(tmp_path: Path) -> None:
    """0 edges, corpus >= 100 => degraded=True."""
    from trw_mcp.tools._pipeline_health import probe_graph_edges

    trw_dir = _make_trw_dir(tmp_path)
    _make_memory_db(trw_dir, corpus=150, edges=0)

    result = probe_graph_edges(trw_dir)

    assert result["degraded"] is True
    assert result["edge_count"] == 0
    assert result["corpus_count"] == 150
    assert result["advisory"] != ""


def test_probe_graph_edges_empty_corpus_suppressed(tmp_path: Path) -> None:
    """0 edges, corpus < 100 => degraded=False (suppress for small corpora)."""
    from trw_mcp.tools._pipeline_health import probe_graph_edges

    trw_dir = _make_trw_dir(tmp_path)
    _make_memory_db(trw_dir, corpus=10, edges=0)

    result = probe_graph_edges(trw_dir)

    assert result["degraded"] is False


def test_probe_graph_edges_tag_only_corpus_is_not_degraded(tmp_path: Path) -> None:
    """A corpus related only by shared tags is healthy despite 0 edges (CORE-245 FR07).

    Tag co-occurrence materialises no edge row any more. A probe that read the
    edge table alone called this corpus — the ordinary shape for a store with no
    embeddings — permanently degraded, and its advisory pointed at a backfill
    that had nothing to build.
    """
    from trw_mcp.tools._pipeline_health import probe_graph_edges

    trw_dir = _make_trw_dir(tmp_path)
    _make_memory_db(trw_dir, corpus=150, edges=0, shared_tags=True)

    result = probe_graph_edges(trw_dir)

    assert result["degraded"] is False
    assert result["edge_count"] == 0
    assert result["advisory"] == ""


def test_probe_graph_edges_pre_schema5_store_still_reports_degraded(tmp_path: Path) -> None:
    """A store with no memory_tags index has no derived half to consult.

    The derived lookup degrades to "no relation" rather than raising, so the
    materialised answer stands alone exactly as it did before PRD-CORE-245 FR07.
    Letting it raise instead would take the whole probe through its fail-open
    wrapper and report an empty graph as healthy.

    ``memories`` carries ``namespace``/``updated_at`` because a real pre-schema-5
    store does: schema 5 made ``namespace`` NOT NULL and re-keyed the table, but
    the column predates it (``_schema_v5.py`` reads
    ``COALESCE(namespace, 'default')`` off the OLD table to take its census).
    The earlier two-column fixture was not a store any migration path produces,
    and it made a missing-column error look like the missing-``memory_tags``
    case this test is actually about.
    """
    from trw_mcp.tools._pipeline_health import probe_graph_edges

    trw_dir = _make_trw_dir(tmp_path)
    db_path = trw_dir / "memory" / "memory.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "CREATE TABLE memories (id TEXT PRIMARY KEY, namespace TEXT DEFAULT 'default', "
        "updated_at TEXT, recall_count INTEGER DEFAULT 0)"
    )
    conn.execute("CREATE TABLE memory_graph_edges (id INTEGER PRIMARY KEY, source_id TEXT, target_id TEXT)")
    for i in range(150):
        conn.execute("INSERT INTO memories (id, updated_at) VALUES (?, ?)", (f"m{i}", "2026-01-01"))
    conn.commit()
    conn.close()

    result = probe_graph_edges(trw_dir)

    assert result["degraded"] is True
    assert result["measured"] is True, "the store WAS read; only its derived half was absent"
    assert result["corpus_count"] == 150


def test_probe_graph_edges_healthy(tmp_path: Path) -> None:
    """50 edges, 150 entries => not degraded."""
    from trw_mcp.tools._pipeline_health import probe_graph_edges

    trw_dir = _make_trw_dir(tmp_path)
    _make_memory_db(trw_dir, corpus=150, edges=50)

    result = probe_graph_edges(trw_dir)

    assert result["degraded"] is False
    assert result["edge_count"] == 50


def test_probe_graph_edges_no_db(tmp_path: Path) -> None:
    """Missing memory.db => fail-open, degraded=False."""
    from trw_mcp.tools._pipeline_health import probe_graph_edges

    trw_dir = _make_trw_dir(tmp_path)
    # No DB file

    result = probe_graph_edges(trw_dir)

    assert result["degraded"] is False


# ---------------------------------------------------------------------------
# probe_embedding_coverage
# ---------------------------------------------------------------------------


def test_probe_embedding_coverage_degraded(tmp_path: Path) -> None:
    """vec_count/total < 0.10 => degraded=True."""
    from trw_mcp.tools._pipeline_health import probe_embedding_coverage

    trw_dir = _make_trw_dir(tmp_path)
    # 3.6% coverage: 27 vec out of 750 total (simulating the real-world case)
    _make_memory_db(trw_dir, corpus=750, vec=27)

    # The fixture uses ordinary SQL tables: exercise real counting, not optional
    # extension loading. The unavailable-extension boundary has its own test.
    with patch("trw_mcp.tools._pipeline_health._load_sqlite_vec", return_value=None):
        result = probe_embedding_coverage(trw_dir)

    assert result["measured"] is True
    assert result["degraded"] is True
    assert result.get("coverage_ratio", 1.0) < 0.10
    assert result["advisory"] != ""


def test_probe_embedding_coverage_healthy(tmp_path: Path) -> None:
    """95% coverage => not degraded."""
    from trw_mcp.tools._pipeline_health import probe_embedding_coverage

    trw_dir = _make_trw_dir(tmp_path)
    _make_memory_db(trw_dir, corpus=100, vec=95)

    with patch("trw_mcp.tools._pipeline_health._load_sqlite_vec", return_value=None):
        result = probe_embedding_coverage(trw_dir)

    assert result["measured"] is True
    assert result["coverage_ratio"] == 0.95
    assert result["degraded"] is False


def test_probe_embedding_coverage_no_db(tmp_path: Path) -> None:
    """Missing memory.db => fail-open, degraded=False."""
    from trw_mcp.tools._pipeline_health import probe_embedding_coverage

    trw_dir = _make_trw_dir(tmp_path)

    result = probe_embedding_coverage(trw_dir)

    assert result["degraded"] is False


def test_probe_embedding_coverage_sqlite_vec_unavailable(tmp_path: Path) -> None:
    """When sqlite_vec fails to load => fail-open, advisory='sqlite_vec_unavailable'."""
    from trw_mcp.tools._pipeline_health import probe_embedding_coverage

    trw_dir = _make_trw_dir(tmp_path)
    _make_memory_db(trw_dir, corpus=100, vec=50)

    # Patch sqlite_vec to simulate unavailability
    with patch("trw_mcp.tools._pipeline_health._load_sqlite_vec", side_effect=Exception("not installed")):
        result = probe_embedding_coverage(trw_dir)

    assert result["degraded"] is False
    assert "sqlite_vec" in result.get("advisory", "")
    assert result["measured"] is False
    assert result["coverage_ratio"] is None


# ---------------------------------------------------------------------------
# probe_recall_feedback
# ---------------------------------------------------------------------------


def test_probe_recall_feedback_all_zero(tmp_path: Path) -> None:
    """MAX(recall_count)=0, corpus >= 100 => degraded=True."""
    from trw_mcp.tools._pipeline_health import probe_recall_feedback

    trw_dir = _make_trw_dir(tmp_path)
    _make_memory_db(trw_dir, corpus=150, max_recall=0)

    result = probe_recall_feedback(trw_dir)

    assert result["degraded"] is True
    assert result.get("max_recall_count") == 0
    assert result["advisory"] != ""


def test_probe_recall_feedback_small_corpus_suppressed(tmp_path: Path) -> None:
    """MAX(recall_count)=0, corpus < 100 => degraded=False (suppressed)."""
    from trw_mcp.tools._pipeline_health import probe_recall_feedback

    trw_dir = _make_trw_dir(tmp_path)
    _make_memory_db(trw_dir, corpus=10, max_recall=0)

    result = probe_recall_feedback(trw_dir)

    assert result["degraded"] is False


def test_probe_recall_feedback_healthy(tmp_path: Path) -> None:
    """MAX(recall_count)=42 => not degraded."""
    from trw_mcp.tools._pipeline_health import probe_recall_feedback

    trw_dir = _make_trw_dir(tmp_path)
    _make_memory_db(trw_dir, corpus=150, max_recall=42)

    result = probe_recall_feedback(trw_dir)

    assert result["degraded"] is False
    assert result.get("max_recall_count") == 42


def test_probe_recall_feedback_no_db(tmp_path: Path) -> None:
    """Missing memory.db => fail-open, degraded=False."""
    from trw_mcp.tools._pipeline_health import probe_recall_feedback

    trw_dir = _make_trw_dir(tmp_path)

    result = probe_recall_feedback(trw_dir)

    assert result["degraded"] is False


# ---------------------------------------------------------------------------
# probe_bandit_state
# ---------------------------------------------------------------------------


def test_probe_bandit_stale(tmp_path: Path) -> None:
    """bandit_state.json mtime=51 days ago => degraded=True."""
    from trw_mcp.tools._pipeline_health import probe_bandit_state

    trw_dir = _make_trw_dir(tmp_path)
    p = _make_bandit_file(trw_dir)

    # Patch os.path.getmtime to return 51 days ago
    stale_mtime = _days_ago(51)
    with patch("trw_mcp.tools._pipeline_health.os.path.getmtime", return_value=stale_mtime):
        result = probe_bandit_state(trw_dir)

    assert result["degraded"] is True
    assert result.get("age_days", 0) > 7
    assert result["advisory"] != ""


def test_probe_bandit_healthy(tmp_path: Path) -> None:
    """bandit_state.json mtime=2 days ago => not degraded."""
    from trw_mcp.tools._pipeline_health import probe_bandit_state

    trw_dir = _make_trw_dir(tmp_path)
    _make_bandit_file(trw_dir)

    recent_mtime = _days_ago(2)
    with patch("trw_mcp.tools._pipeline_health.os.path.getmtime", return_value=recent_mtime):
        result = probe_bandit_state(trw_dir)

    assert result["degraded"] is False
    assert result["advisory"] == ""


def test_probe_bandit_missing_file(tmp_path: Path) -> None:
    """No bandit_state.json (fresh install) => fail-open, degraded=False.

    DEF-08 attribution: the pre-fix ``safe_default`` reported ``age_days: 0.0``
    for a file that has never existed — a FABRICATED "just refreshed"
    timestamp, not a genuine zero-count measurement (unlike ``edge_count: 0``
    on an absent memory.db). Reverting the fix turns the ``measured``
    assertion red because ``age_days`` would come back ``0.0`` under
    ``measured: True`` again, exactly the "healthy 0.0-day age it never
    observed" shape the probe-disabled branch two lines below already avoids.
    """
    from trw_mcp.tools._pipeline_health import probe_bandit_state

    trw_dir = _make_trw_dir(tmp_path)
    # Don't create the bandit file

    result = probe_bandit_state(trw_dir)

    assert result["degraded"] is False
    assert result["measured"] is False
    assert result["age_days"] is None
    assert "state_missing" in result["advisory"]


def test_probe_bandit_disabled_by_config_not_degraded(tmp_path: Path) -> None:
    """PRD-FIX-105-FR02: probe disabled via config => never degraded even if stale.

    bandit_state.json is written by the backend, not the MCP runtime, so where
    no local writer exists the operator disables the probe to stop cry-wolf.
    """
    from trw_mcp.models.config import TRWConfig
    from trw_mcp.tools._pipeline_health import probe_bandit_state

    trw_dir = _make_trw_dir(tmp_path)
    _make_bandit_file(trw_dir)

    cfg = TRWConfig(pipeline_health_bandit_probe_enabled=False)  # type: ignore[call-arg]
    stale_mtime = _days_ago(99)
    with (
        patch("trw_mcp.models.config.get_config", return_value=cfg),
        patch("trw_mcp.tools._pipeline_health.os.path.getmtime", return_value=stale_mtime),
    ):
        result = probe_bandit_state(trw_dir)

    assert result["degraded"] is False
    # PRD-CORE-263-FR03: a disabled probe read nothing, so it reports
    # not-measured rather than a healthy 0.0-day age it never observed.
    assert result["measured"] is False
    assert result["advisory"] == "bandit_state not measured: probe_disabled"


def test_probe_bandit_custom_stale_threshold(tmp_path: Path) -> None:
    """PRD-FIX-105-FR02: configurable threshold widens the SLA so a 10-day-old
    file is healthy when the operator sets a 30-day window."""
    from trw_mcp.models.config import TRWConfig
    from trw_mcp.tools._pipeline_health import probe_bandit_state

    trw_dir = _make_trw_dir(tmp_path)
    _make_bandit_file(trw_dir)

    cfg = TRWConfig(pipeline_health_bandit_stale_days=30.0)  # type: ignore[call-arg]
    mtime_10d = _days_ago(10)
    with (
        patch("trw_mcp.models.config.get_config", return_value=cfg),
        patch("trw_mcp.tools._pipeline_health.os.path.getmtime", return_value=mtime_10d),
    ):
        result = probe_bandit_state(trw_dir)

    assert result["degraded"] is False

    # Same file under the default 7-day SLA IS degraded — proves the knob is wired.
    cfg_default = TRWConfig()
    with (
        patch("trw_mcp.models.config.get_config", return_value=cfg_default),
        patch("trw_mcp.tools._pipeline_health.os.path.getmtime", return_value=mtime_10d),
    ):
        result_default = probe_bandit_state(trw_dir)

    assert result_default["degraded"] is True


# ---------------------------------------------------------------------------
# step_pipeline_health (aggregator)
# ---------------------------------------------------------------------------


def test_step_pipeline_health_all_healthy(tmp_path: Path) -> None:
    """All five probes healthy => degraded=False, advisory empty."""
    from trw_mcp.tools._pipeline_health import step_pipeline_health

    trw_dir = _make_trw_dir(tmp_path)
    _write_sync_state(trw_dir, {"consecutive_failures": 0, "last_push_at": _iso_ago(0.5)})
    _make_memory_db(trw_dir, corpus=50, vec=48, max_recall=10, edges=20)
    _make_bandit_file(trw_dir)

    recent_mtime = _days_ago(1)
    with patch("trw_mcp.tools._pipeline_health.os.path.getmtime", return_value=recent_mtime):
        result = step_pipeline_health(trw_dir)

    assert result["degraded"] is False
    assert result["advisory"] == "" or result["advisory"] is None or result["advisory"] == "None"
    # All five signal keys must be present
    assert "sync_push" in result
    assert "graph_edges" in result
    assert "embedding_coverage" in result
    assert "recall_feedback" in result
    assert "bandit_state" in result


def test_step_pipeline_health_all_degraded(tmp_path: Path) -> None:
    """All 5 probes degraded => degraded=True, advisory non-empty listing all signals."""
    from trw_mcp.tools._pipeline_health import step_pipeline_health

    trw_dir = _make_trw_dir(tmp_path)
    _write_sync_state(trw_dir, {"consecutive_failures": 15, "last_push_at": _iso_ago(48)})
    _make_memory_db(trw_dir, corpus=150, vec=5, max_recall=0, edges=0)
    # bandit stale 51 days
    _make_bandit_file(trw_dir)

    stale_mtime = _days_ago(51)
    with patch("trw_mcp.tools._pipeline_health.os.path.getmtime", return_value=stale_mtime):
        result = step_pipeline_health(trw_dir)

    assert result["degraded"] is True
    advisory = str(result.get("advisory", ""))
    assert advisory != ""
    # Advisory should mention sync_push
    assert "sync_push" in advisory or "sync" in advisory


def test_step_pipeline_health_partial_degraded(tmp_path: Path) -> None:
    """Only sync_push degraded => degraded=True, advisory mentions sync_push."""
    from trw_mcp.tools._pipeline_health import step_pipeline_health

    trw_dir = _make_trw_dir(tmp_path)
    _write_sync_state(trw_dir, {"consecutive_failures": 11, "last_push_at": _iso_ago(0.5)})
    # Healthy: small corpus so graph/recall don't trigger; recent bandit
    _make_memory_db(trw_dir, corpus=50, vec=48, max_recall=10, edges=20)
    _make_bandit_file(trw_dir)

    recent_mtime = _days_ago(1)
    with patch("trw_mcp.tools._pipeline_health.os.path.getmtime", return_value=recent_mtime):
        result = step_pipeline_health(trw_dir)

    assert result["degraded"] is True
    advisory = str(result.get("advisory", ""))
    assert "sync_push" in advisory or "sync" in advisory


def test_step_pipeline_health_one_probe_raises(tmp_path: Path) -> None:
    """One probe raises => others still run, aggregator returns result."""
    from trw_mcp.tools._pipeline_health import step_pipeline_health

    trw_dir = _make_trw_dir(tmp_path)
    _write_sync_state(trw_dir, {"consecutive_failures": 0, "last_push_at": _iso_ago(0.5)})
    _make_memory_db(trw_dir, corpus=50, max_recall=10, edges=20)
    _make_bandit_file(trw_dir)

    # Make probe_graph_edges raise
    with patch(
        "trw_mcp.tools._pipeline_health.probe_graph_edges",
        side_effect=RuntimeError("DB exploded"),
    ):
        recent_mtime = _days_ago(1)
        with patch("trw_mcp.tools._pipeline_health.os.path.getmtime", return_value=recent_mtime):
            result = step_pipeline_health(trw_dir)

    # Aggregator must not raise and must still have all keys
    assert "sync_push" in result
    assert "graph_edges" in result
    assert "embedding_coverage" in result
    assert "recall_feedback" in result
    assert "bandit_state" in result
    # The errored probe should produce a safe default
    graph_result = result["graph_edges"]
    assert isinstance(graph_result, dict)
    assert graph_result.get("degraded") is False


def test_step_pipeline_health_all_probes_raise(tmp_path: Path) -> None:
    """Every probe raises => aggregator still returns, degraded=False."""
    from trw_mcp.tools._pipeline_health import step_pipeline_health

    trw_dir = _make_trw_dir(tmp_path)

    with (
        patch("trw_mcp.tools._pipeline_health.probe_sync_push", side_effect=RuntimeError("boom")),
        patch("trw_mcp.tools._pipeline_health.probe_graph_edges", side_effect=RuntimeError("boom")),
        patch(
            "trw_mcp.tools._pipeline_health.probe_embedding_coverage",
            side_effect=RuntimeError("boom"),
        ),
        patch("trw_mcp.tools._pipeline_health.probe_recall_feedback", side_effect=RuntimeError("boom")),
        patch("trw_mcp.tools._pipeline_health.probe_bandit_state", side_effect=RuntimeError("boom")),
    ):
        result = step_pipeline_health(trw_dir)

    assert "degraded" in result
    # All probes failed-open => not degraded overall
    assert result["degraded"] is False
    # All signal keys present
    for key in ("sync_push", "graph_edges", "embedding_coverage", "recall_feedback", "bandit_state"):
        assert key in result
        assert result[key].get("degraded") is False


def test_step_pipeline_health_advisory_ends_with_tool_hint(tmp_path: Path) -> None:
    """Advisory when degraded must end with hint to call trw_pipeline_health()."""
    from trw_mcp.tools._pipeline_health import step_pipeline_health

    trw_dir = _make_trw_dir(tmp_path)
    _write_sync_state(trw_dir, {"consecutive_failures": 15, "last_push_at": _iso_ago(48)})
    _make_memory_db(trw_dir, corpus=10)

    result = step_pipeline_health(trw_dir)

    if result["degraded"]:
        advisory = str(result.get("advisory", ""))
        assert "trw_pipeline_health" in advisory


def test_pipeline_health_probe_no_write(tmp_path: Path) -> None:
    """No probe writes to memory.db — verify file mtime unchanged."""
    from trw_mcp.tools._pipeline_health import step_pipeline_health

    trw_dir = _make_trw_dir(tmp_path)
    _write_sync_state(trw_dir, {"consecutive_failures": 0, "last_push_at": _iso_ago(0.5)})
    db_path = _make_memory_db(trw_dir, corpus=10)
    _make_bandit_file(trw_dir)

    import os

    mtime_before = os.path.getmtime(str(db_path))
    time.sleep(0.02)  # ensure clock advances

    recent_mtime = _days_ago(1)
    with patch("trw_mcp.tools._pipeline_health.os.path.getmtime", return_value=recent_mtime):
        step_pipeline_health(trw_dir)

    mtime_after = os.path.getmtime(str(db_path))
    # DB file must not have been modified
    assert mtime_after == pytest.approx(mtime_before, abs=0.01)


# ---------------------------------------------------------------------------
# trw_pipeline_health MCP tool publication seam
#
# Two distinct surfaces, kept explicit so a missing publication is caught:
#
#   * Registration inventory — every tool wired into the server, read via the
#     private ``mcp._list_tools()``. This is the surface that fails if the
#     ``register_pipeline_health_tools(mcp)`` call is dropped from
#     ``server/_tools.py::_register_tools``. It bypasses the security
#     middleware advertisement filter, so it reflects registration, not scope.
#   * Public advertisement — ``mcp.list_tools()``, the surface MCP clients see
#     after the security middleware's ``filter_advertised_tools`` runs.
#     ``trw_pipeline_health`` is an operator/diagnostic tool intentionally kept
#     OUT of the default agent-facing allowlist, so it is registered but not
#     publicly advertised. Asserting against the public surface here would be a
#     false negative — use the registration inventory instead.
# ---------------------------------------------------------------------------


async def test_trw_pipeline_health_tool_registered() -> None:
    """trw_pipeline_health is wired into the *production* server registry.

    Asserts against the real ``trw_mcp.server._app.mcp`` instance that
    ``_register_tools()`` populates at import — not a conftest test factory.
    Mirrors ``test_server_startup.test_mcp_registers_ceremony_feedback_tools``:
    a registrar registered only in conftest would be dead/phantom in prod, so
    this MUST fail (not silently pass) if the production wiring is dropped.
    """
    from trw_mcp.server._app import mcp

    # ``_list_tools`` is the registration inventory — it bypasses the security
    # middleware advertisement filter, so this verifies *registration*, not the
    # narrower public allowlist (``trw_pipeline_health`` is deliberately not
    # in the default public advertisement; see the seam note above).
    tool_names = {t.name for t in await mcp._list_tools()}
    assert "trw_pipeline_health" in tool_names, (
        "trw_pipeline_health is not wired into the production server registry — "
        "check register_pipeline_health_tools(mcp) in "
        "server/_tools.py::_register_tools()"
    )


def test_trw_pipeline_health_registrar_publishes_tool() -> None:
    """The pipeline_health registrar publishes the tool through the test factory.

    Independent of production wiring, this proves ``register_pipeline_health_tools``
    itself advertises ``trw_pipeline_health`` on a server. Catches a broken or
    renamed registrar before it reaches the production path above.
    """
    from tests.conftest import get_tools_sync, make_test_server

    tools = get_tools_sync(make_test_server("pipeline_health"))
    assert "trw_pipeline_health" in tools, (
        f"register_pipeline_health_tools did not advertise trw_pipeline_health; got: {sorted(tools)}"
    )


def test_trw_pipeline_health_tool_crash_reports_measured_false(tmp_path: Path) -> None:
    """PRD-CORE-263 DEF-06 attribution.

    Before this fix the tool's catch-all crash handler returned
    ``degraded: False`` with no ``measured`` key anywhere in the payload — top
    level or per-signal — rendering identically to a healthy aggregate for any
    caller that reads ``degraded`` alone. Reverting the fix (dropping the
    ``measured: False`` entries) turns this red.
    """
    from tests.conftest import extract_tool_fn, make_test_server

    fn = extract_tool_fn(make_test_server("pipeline_health"), "trw_pipeline_health")

    with patch(
        "trw_mcp.state._paths.resolve_trw_dir",
        side_effect=RuntimeError("resolve exploded"),
    ):
        result = fn()

    assert result["degraded"] is False
    assert result["measured"] is False
    for key in ("sync_push", "graph_edges", "embedding_coverage", "recall_feedback", "bandit_state"):
        assert result[key]["measured"] is False, key


def test_trw_pipeline_health_tool_returns_dict(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """trw_pipeline_health tool returns a dict with all five signal keys."""
    from trw_mcp.tools._pipeline_health import step_pipeline_health

    trw_dir = _make_trw_dir(tmp_path)
    _write_sync_state(trw_dir, {"consecutive_failures": 0, "last_push_at": _iso_ago(0.5)})
    _make_memory_db(trw_dir, corpus=10)
    _make_bandit_file(trw_dir)

    recent_mtime = _days_ago(1)
    with patch("trw_mcp.tools._pipeline_health.os.path.getmtime", return_value=recent_mtime):
        result = step_pipeline_health(trw_dir)

    assert isinstance(result, dict)
    assert "degraded" in result
    assert "advisory" in result
    for key in ("sync_push", "graph_edges", "embedding_coverage", "recall_feedback", "bandit_state"):
        assert key in result
        assert isinstance(result[key], dict)


# ---------------------------------------------------------------------------
# PRD-CORE-263-FR03 — a crashed probe is distinguishable from a healthy one
# ---------------------------------------------------------------------------

_ALL_PROBES = (
    "sync_push",
    "graph_edges",
    "embedding_coverage",
    "recall_feedback",
    "bandit_state",
)


def _healthy_trw_dir(tmp_path: Path) -> Path:
    """A .trw dir on which all five probes measure a healthy result."""
    trw_dir = _make_trw_dir(tmp_path)
    (trw_dir / "sync-state.json").write_text(
        json.dumps({"consecutive_failures": 0, "last_push_at": datetime.now(timezone.utc).isoformat()}),
        encoding="utf-8",
    )
    _make_bandit_file(trw_dir)
    return trw_dir


@pytest.mark.parametrize("probe_name", _ALL_PROBES)
def test_probe_exception_is_distinguishable_from_a_healthy_measurement(
    tmp_path: Path,
    probe_name: str,
) -> None:
    """PRD-CORE-263-FR03 — parametrised over ALL five probes.

    At HEAD four of the five collapsed an exception into ``degraded: False`` with
    an empty advisory, which the aggregator then stripped, so the crash entry and
    the healthy entry were byte-identical dicts. Attribution: reverting FR03
    turns this red on sync_push, embedding_coverage, recall_feedback and
    bandit_state (graph_edges already did it correctly and is the precedent the
    fix generalises).
    """
    from trw_mcp.tools import _pipeline_health as ph

    trw_dir = _healthy_trw_dir(tmp_path)
    healthy = ph.step_pipeline_health(trw_dir)[probe_name]

    with patch.object(ph, f"probe_{probe_name}", side_effect=RuntimeError("probe exploded")):
        crashed_aggregate = ph.step_pipeline_health(trw_dir)
    crashed = crashed_aggregate[probe_name]

    assert crashed != healthy, "a crashed probe must not render as a healthy one"
    assert crashed["measured"] is False
    assert crashed.get("advisory"), "the reason must survive the aggregator's empty-advisory strip"
    assert "RuntimeError" in crashed["advisory"]
    # Neither degraded nor healthy: it is listed as unmeasured instead.
    assert crashed["degraded"] is False
    assert crashed_aggregate["unmeasured"] == [probe_name]
    assert crashed_aggregate["degraded"] is False


def test_healthy_aggregate_is_unchanged_apart_from_the_measured_flags(tmp_path: Path) -> None:
    """PRD-CORE-263-FR03 / NFR04 — the healthy payload is pinned."""
    from trw_mcp.tools._pipeline_health import step_pipeline_health

    result = step_pipeline_health(_healthy_trw_dir(tmp_path))

    assert result["degraded"] is False
    assert result["advisory"] == ""
    # Omitted when every probe measured, so the pre-263 key set is preserved.
    assert "unmeasured" not in result
    for name in _ALL_PROBES:
        entry = result[name]
        assert entry["measured"] is True, name
        # The healthy-case compaction still strips the empty advisory.
        assert "advisory" not in entry, name


def test_probe_failure_emits_exactly_one_structured_event(
    tmp_path: Path,
    captured_structlog: list[dict[str, object]],
) -> None:
    """PRD-CORE-263 DEF-10 / NFR03 attribution.

    A single probe exception must be logged from exactly ONE call site. Before
    this fix a crashed probe's own ``pipeline_probe_<name>_failed`` handler
    logged the failure, and the aggregator's final rollup ALSO emitted
    ``pipeline_health_unmeasured`` for the identical occurrence — two events
    naming the same condition. Reverting the fix (restoring the aggregate log
    call) turns this red because ``pipeline_health_unmeasured`` would appear
    again alongside the per-probe event.
    """
    from trw_mcp.tools import _pipeline_health as ph

    trw_dir = _healthy_trw_dir(tmp_path)
    with patch.object(ph, "probe_sync_push", side_effect=RuntimeError("probe exploded")):
        result = ph.step_pipeline_health(trw_dir)

    assert result["unmeasured"] == ["sync_push"]
    events = [entry["event"] for entry in captured_structlog]
    assert events.count("pipeline_probe_failed") == 1
    assert "pipeline_health_unmeasured" not in events


def test_unmeasured_probe_keeps_its_advisory_through_the_compaction(tmp_path: Path) -> None:
    """PRD-CORE-263-FR03 — the strip is what erased the crash reason."""
    from trw_mcp.tools import _pipeline_health as ph

    trw_dir = _healthy_trw_dir(tmp_path)
    # An entry that is unmeasured AND (hypothetically) advisory-less must still
    # keep the key, because "not measured" is the fact the caller needs.
    with patch.object(ph, "probe_sync_push", return_value={"degraded": False, "measured": False, "advisory": ""}):
        entry = ph.step_pipeline_health(trw_dir)["sync_push"]
    assert "advisory" in entry


def _inject_inside_probe(ph: Any, probe_name: str, trw_dir: Path) -> AbstractContextManager[object]:
    """Make *probe_name* fail from INSIDE, so its own handler is what runs.

    The sibling test patches the probe function, which only exercises the
    aggregator's belt-and-braces handler. That handler was never the defect:
    every probe caught its own exceptions, so nothing could raise past them and
    the aggregator's reason string was unreachable (PRD-CORE-263 §1, "carried
    with a correction"). The four swallows FR03 names are the probes' OWN
    handlers, and only an injection below the probe boundary reaches them.

    The injections are the failures the PRD names, not synthetic ones: a locked
    database for the three that open a connection, a state file that is not
    valid UTF-8 for the one that reads text, and an unreadable config for the
    one that resolves its threshold before reading anything.
    """
    if probe_name == "sync_push":
        (trw_dir / "sync-state.json").write_bytes(b'{"consecutive_failures": 0, "last_push_at": "\xff\xfe"}')
        return nullcontext()
    if probe_name == "bandit_state":
        return patch.object(ph, "_bandit_probe_config", side_effect=RuntimeError("config unreadable"))
    return patch.object(ph.sqlite3, "connect", side_effect=sqlite3.OperationalError("database is locked"))


@pytest.mark.parametrize("probe_name", _ALL_PROBES)
def test_a_probes_own_handler_reports_not_measured(tmp_path: Path, probe_name: str, monkeypatch) -> None:
    """PRD-CORE-263-FR03 — the injection lands below the probe boundary.

    Attribution: restoring any one probe's ``return safe_default`` crash branch
    turns this red for that probe, which the sibling
    ``test_probe_exception_is_distinguishable_from_a_healthy_measurement`` does
    not, because patching the probe function never reaches the handler being
    reverted.
    """
    from trw_mcp.tools import _pipeline_health as ph

    if probe_name == "embedding_coverage":
        # Reach the connection failure below this probe, independent of whether
        # the optional extension happens to be installed on the test host.
        monkeypatch.setattr(ph, "_load_sqlite_vec", lambda _conn: None)
    trw_dir = _healthy_trw_dir(tmp_path)
    # A real store, so the three connection-opening probes actually REACH their
    # connection instead of short-circuiting on a missing file.
    _make_memory_db(trw_dir, corpus=10, vec=10, max_recall=5, edges=40, shared_tags=True)
    healthy = ph.step_pipeline_health(trw_dir)[probe_name]
    assert healthy["measured"] is True, "fixture precondition: the probe measures healthily first"

    with _inject_inside_probe(ph, probe_name, trw_dir):
        aggregate = ph.step_pipeline_health(trw_dir)
    crashed = aggregate[probe_name]

    assert crashed != healthy, "a probe that crashed must not render as one that measured healthy"
    assert crashed["measured"] is False
    assert crashed.get("advisory"), "the reason must survive the aggregator's empty-advisory strip"
    assert probe_name in str(crashed["advisory"])
    assert crashed["degraded"] is False
    # Neither degraded nor healthy — counted in neither, listed as unmeasured.
    assert probe_name in aggregate["unmeasured"]
    assert aggregate["degraded"] is False
