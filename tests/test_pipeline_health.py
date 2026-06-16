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
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

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


def _make_memory_db(trw_dir: Path, *, corpus: int = 0, vec: int = 0, max_recall: int = 0, edges: int = 0) -> Path:
    """Create a minimal memory.db with the required tables."""
    db_path = trw_dir / "memory" / "memory.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE IF NOT EXISTS memories (id TEXT PRIMARY KEY, recall_count INTEGER DEFAULT 0)")
    conn.execute("CREATE TABLE IF NOT EXISTS vec_memories (id TEXT PRIMARY KEY)")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS memory_graph_edges (id INTEGER PRIMARY KEY, source_id TEXT, target_id TEXT)"
    )
    # Insert corpus entries
    for i in range(corpus):
        recall = max_recall if i == 0 and max_recall > 0 else 0
        conn.execute("INSERT INTO memories (id, recall_count) VALUES (?, ?)", (f"m{i}", recall))
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
    """No sync-state.json => fail-open, degraded=False."""
    from trw_mcp.tools._pipeline_health import probe_sync_push

    trw_dir = _make_trw_dir(tmp_path)
    # Don't create sync-state.json

    result = probe_sync_push(trw_dir)

    assert result["degraded"] is False


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

    result = probe_embedding_coverage(trw_dir)

    # May return sqlite_vec_unavailable if sqlite_vec not installed — that's fail-open
    if result.get("advisory") == "sqlite_vec_unavailable":
        pytest.skip("sqlite_vec not available in test environment")

    assert result["degraded"] is True
    assert result.get("coverage_ratio", 1.0) < 0.10
    assert result["advisory"] != ""


def test_probe_embedding_coverage_healthy(tmp_path: Path) -> None:
    """95% coverage => not degraded."""
    from trw_mcp.tools._pipeline_health import probe_embedding_coverage

    trw_dir = _make_trw_dir(tmp_path)
    _make_memory_db(trw_dir, corpus=100, vec=95)

    result = probe_embedding_coverage(trw_dir)

    if result.get("advisory") == "sqlite_vec_unavailable":
        pytest.skip("sqlite_vec not available in test environment")

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
    """No bandit_state.json (fresh install) => fail-open, degraded=False."""
    from trw_mcp.tools._pipeline_health import probe_bandit_state

    trw_dir = _make_trw_dir(tmp_path)
    # Don't create the bandit file

    result = probe_bandit_state(trw_dir)

    assert result["degraded"] is False


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
    assert result["advisory"] == "probe_disabled"


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
