"""PRD-FIX-COMPOUNDING-2 FR03/FR04 — deliver knowledge-sync + session graph-health.

- FR03 (``step_knowledge_sync``): post-deliver knowledge-graph topic sync,
  fail-open, result surfaced under ``knowledge_sync``.
- FR04 (``step_graph_health``): session-start advisory when the graph is empty
  AND there are >10 memories; omitted otherwise; fail-open.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import cast

import pytest

from trw_mcp.state.memory_adapter import get_backend, store_learning
from trw_mcp.tools._ceremony_deliver_steps import step_knowledge_sync
from trw_mcp.tools._ceremony_session_start_steps import step_graph_health


@pytest.fixture
def trw_dir(tmp_path: Path) -> Path:
    d = tmp_path / ".trw"
    (d / "learnings" / "entries").mkdir(parents=True)
    (d / "memory").mkdir(parents=True)
    return d


def _wipe_edges(trw_dir: Path) -> None:
    """Ensure memory_graph_edges is empty for the graph-empty advisory tests."""
    backend = get_backend(trw_dir)
    conn = backend._conn
    assert isinstance(conn, sqlite3.Connection)
    conn.execute("DELETE FROM memory_graph_edges")
    conn.commit()


class TestStepKnowledgeSyncFR03:
    def test_below_threshold_reports_not_met_fail_open(self, trw_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Below threshold → knowledge_sync present with threshold_met False."""
        # Capture the real config BEFORE patching, then override the threshold.
        high_threshold_cfg = _config_with_threshold(50)
        monkeypatch.setattr(
            "trw_mcp.models.config.get_config",
            lambda: high_threshold_cfg,
        )
        store_learning(trw_dir, "L-ks-1", "apple subject", "apple detail body")

        results: dict[str, object] = {}
        step_knowledge_sync(trw_dir, cast("dict", results))  # type: ignore[arg-type]

        assert "knowledge_sync" in results
        sync = cast("dict[str, object]", results["knowledge_sync"])
        assert sync.get("threshold_met") is False

    def test_failure_is_fail_open_records_failed_status(self, trw_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """A sync exception must not raise; records status='failed'."""

        def boom(*args: object, **kwargs: object) -> dict[str, object]:
            raise RuntimeError("sync exploded")

        monkeypatch.setattr("trw_mcp.state.knowledge_topology.execute_knowledge_sync", boom)

        results: dict[str, object] = {}
        # Must NOT raise.
        step_knowledge_sync(trw_dir, cast("dict", results))  # type: ignore[arg-type]

        sync = cast("dict[str, object]", results["knowledge_sync"])
        assert sync.get("status") == "failed"
        assert "sync exploded" in str(sync.get("error", ""))

    def test_threshold_met_populates_knowledge_dir(self, trw_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Threshold met → execute_knowledge_sync runs (non-dry-run)."""
        low_threshold_cfg = _config_with_threshold(2)
        monkeypatch.setattr(
            "trw_mcp.models.config.get_config",
            lambda: low_threshold_cfg,
        )
        # Two entries sharing tags so a cluster can form.
        store_learning(trw_dir, "L-kd-1", "alpha topic note", "x", tags=["t", "u"])
        store_learning(trw_dir, "L-kd-2", "beta topic detail", "y", tags=["t", "u"])

        results: dict[str, object] = {}
        step_knowledge_sync(trw_dir, cast("dict", results))  # type: ignore[arg-type]

        sync = cast("dict[str, object]", results["knowledge_sync"])
        assert sync.get("threshold_met") is True
        assert sync.get("status") != "failed"


class TestStepKnowledgeSyncGraphBackfillF5:
    """F5 suggestion 2: opportunistic time-boxed graph backfill on deliver."""

    def test_deliver_backfills_ungraphed_corpus(self, trw_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Entries stored without edges get graphed on deliver (singleton conn)."""
        cfg = _config_with_threshold(2)
        monkeypatch.setattr("trw_mcp.models.config.get_config", lambda: cfg)
        # Store two tag-sharing entries but suppress edge creation on the store
        # path so the corpus mirrors the historical un-graphed state.
        monkeypatch.setattr(
            "trw_mcp.state.memory_adapter.update_entry_graph",
            lambda *a, **k: {"similarity_edges": 0, "tag_edges": 0, "consolidation_edges": 0},
        )
        store_learning(trw_dir, "L-bk-1", "alpha topic note", "x", tags=["t", "u"])
        store_learning(trw_dir, "L-bk-2", "beta topic detail", "y", tags=["t", "u"])
        monkeypatch.undo()
        monkeypatch.setattr("trw_mcp.models.config.get_config", lambda: cfg)
        _wipe_edges(trw_dir)

        results: dict[str, object] = {}
        step_knowledge_sync(trw_dir, cast("dict", results))  # type: ignore[arg-type]

        backfill = cast("dict[str, int]", results["graph_backfill"])
        assert backfill["edges_built"] > 0
        backend = get_backend(trw_dir)
        conn = backend._conn
        assert isinstance(conn, sqlite3.Connection)
        assert int(conn.execute("SELECT COUNT(*) FROM memory_graph_edges").fetchone()[0]) > 0

    def test_deliver_backfill_disabled_by_config(self, trw_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """deliver_graph_backfill_enabled=False skips the backfill entirely."""
        cfg = _config_with_threshold(50).model_copy(  # type: ignore[attr-defined]
            update={"deliver_graph_backfill_enabled": False}
        )
        monkeypatch.setattr("trw_mcp.models.config.get_config", lambda: cfg)
        store_learning(trw_dir, "L-bd-1", "gamma note", "z")

        results: dict[str, object] = {}
        step_knowledge_sync(trw_dir, cast("dict", results))  # type: ignore[arg-type]

        assert "graph_backfill" not in results

    def test_deliver_backfill_fail_open(self, trw_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """A backfill exception must not fail the deliver step."""
        cfg = _config_with_threshold(50)
        monkeypatch.setattr("trw_mcp.models.config.get_config", lambda: cfg)
        store_learning(trw_dir, "L-bf-x", "delta note", "z")

        def boom(*args: object, **kwargs: object) -> dict[str, int]:
            raise RuntimeError("backfill exploded")

        monkeypatch.setattr("trw_mcp.state.memory_adapter.backfill_graph", boom)

        results: dict[str, object] = {}
        # Must NOT raise; knowledge_sync still recorded, graph_backfill absent.
        step_knowledge_sync(trw_dir, cast("dict", results))  # type: ignore[arg-type]
        assert "knowledge_sync" in results
        assert "graph_backfill" not in results


class TestStepGraphHealthFR04:
    def test_empty_graph_many_memories_emits_advisory(self, trw_dir: Path) -> None:
        """>10 memories + 0 edges → advisory dict returned."""
        words = _distinct_words()
        for i, w in enumerate(words):
            # Distinct content (avoids semantic dedup) + unique tags (no edges).
            store_learning(trw_dir, f"L-gh-{i}", f"{w} subject {i}", f"{w} body {i}", tags=[f"uniq{i}"])
        _wipe_edges(trw_dir)

        advisory = step_graph_health(trw_dir)

        assert advisory is not None
        assert advisory["status"] == "empty"
        assert int(cast("int", advisory["memories"])) > 10
        assert "knowledge graph empty" in str(advisory["advisory"])

    def test_small_corpus_no_advisory(self, trw_dir: Path) -> None:
        """<=10 memories → no advisory even if graph is empty."""
        for i, w in enumerate(_distinct_words()[:3]):
            store_learning(trw_dir, f"L-sm-{i}", f"{w} subject {i}", f"{w} body {i}", tags=[f"u{i}"])
        _wipe_edges(trw_dir)

        assert step_graph_health(trw_dir) is None

    def test_populated_graph_no_advisory(self, trw_dir: Path) -> None:
        """Edges present → no advisory regardless of memory count."""
        # entries that DO share tags → edges get created by store_learning.
        words = _distinct_words()
        for i, w in enumerate(words):
            store_learning(trw_dir, f"L-pop-{i}", f"{w} shared subject {i}", f"{w} body {i}", tags=["shared", "topic"])

        backend = get_backend(trw_dir)
        conn = backend._conn
        assert isinstance(conn, sqlite3.Connection)
        edge_count = conn.execute("SELECT COUNT(*) FROM memory_graph_edges").fetchone()[0]
        assert edge_count > 0, "precondition: shared-tag stores must create edges"

        assert step_graph_health(trw_dir) is None


class TestLogDeliverCompleteResilience:
    """log_deliver_complete must honor its 'unreadable counts fall back' contract."""

    def test_torn_events_line_does_not_abort_deliver_logging(self, tmp_path: Path) -> None:
        """A torn events.jsonl append must not break deliver-completion logging.

        log_deliver_complete reads run events.jsonl only to populate the
        advisory ``events_logged`` field on the ``deliver_ok`` log line, and its
        docstring already promises "missing/unreadable counts fall back to 0".
        The strict FileStateReader.read_jsonl raised StateError on the first
        torn line, so a single concurrent append could abort the entire
        deliver-completion logging at the most important moment. The resilient
        reader makes the code honor its stated contract: drop the torn line,
        count the intact ones, never raise (regression guard).
        """
        import structlog

        from trw_mcp.models.typed_dicts import DeliverResultDict
        from trw_mcp.tools._ceremony_deliver_steps import log_deliver_complete

        run_dir = tmp_path / "run"
        meta = run_dir / "meta"
        meta.mkdir(parents=True)
        intact_a = '{"ts": "2026-02-11T12:00:00Z", "type": "session_start"}\n'
        torn = '{"ts": "2026-02-11T12:01:00Z", "type": "phase_chan\n'
        intact_b = '{"ts": "2026-02-11T12:02:00Z", "type": "checkpoint"}\n'
        (meta / "events.jsonl").write_text(intact_a + torn + intact_b, encoding="utf-8")

        results = cast("DeliverResultDict", {"run_path": str(run_dir), "critical_steps_completed": 1})

        with structlog.testing.capture_logs() as logs:
            # Before the fix this raised StateError instead of logging.
            log_deliver_complete(
                resolved_run=run_dir,
                results=results,
                errors=[],
                deferred_status="completed",
                critical_elapsed=0.1,
            )

        deliver_ok = [e for e in logs if e.get("event") == "deliver_ok"]
        assert deliver_ok, "deliver_ok must still be emitted despite a torn events line"
        # Torn line dropped; the two intact events are still counted.
        assert deliver_ok[0]["events_logged"] == 2
        assert any(e.get("event") == "trw_deliver_complete" for e in logs)


def _config_with_threshold(threshold: int) -> object:
    """Return a TRWConfig with knowledge_sync_threshold overridden."""
    from trw_mcp.models.config import get_config

    cfg = get_config()
    return cfg.model_copy(update={"knowledge_sync_threshold": threshold})


def _distinct_words() -> list[str]:
    """14 lexically-distinct stems so semantic dedup keeps every entry."""
    return [
        "apple",
        "bridge",
        "cloud",
        "delta",
        "echo",
        "frost",
        "grove",
        "harbor",
        "ivory",
        "jade",
        "karma",
        "lunar",
        "maple",
        "nexus",
    ]
