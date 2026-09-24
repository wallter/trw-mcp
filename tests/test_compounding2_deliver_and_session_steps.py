"""PRD-FIX-COMPOUNDING-2 FR03/FR04 — deliver knowledge-sync + session graph-health.

- FR03 (``step_knowledge_sync``): post-deliver knowledge-graph topic sync,
  fail-open, result surfaced under ``knowledge_sync``.
- FR04 (``step_graph_health``): session-start advisory when the graph is empty
  AND there are >10 memories; omitted otherwise; fail-open.
"""

from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest
from trw_memory.models.memory import MemoryEntry

from tests._memory_fixtures import FAKE_NAMESPACE, DaemonCheckout
from tests._memory_store_fake import FakeMemoryStore
from trw_mcp.state._store_counts import store_health
from trw_mcp.state.memory_adapter import store_learning
from trw_mcp.tools._ceremony_deliver_steps import step_knowledge_sync
from trw_mcp.tools._ceremony_session_start_steps import step_graph_health


@pytest.fixture
def trw_dir(tmp_path: Path) -> Path:
    d = tmp_path / ".trw"
    (d / "learnings" / "entries").mkdir(parents=True)
    (d / "memory").mkdir(parents=True)
    return d


class TestStepKnowledgeSyncFR03:
    def test_below_threshold_reports_not_met_fail_open(
        self, daemon_checkout: DaemonCheckout, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Below threshold → knowledge_sync present with threshold_met False."""
        trw_dir = daemon_checkout.trw_dir
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

    def test_threshold_met_populates_knowledge_dir(
        self, daemon_checkout: DaemonCheckout, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Threshold met → execute_knowledge_sync runs (non-dry-run)."""
        trw_dir = daemon_checkout.trw_dir
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

    def test_deliver_backfills_ungraphed_corpus(
        self, trw_dir: Path, fake_memory_store: FakeMemoryStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Deliver runs one time-boxed backfill page over the checkout's store and reports it.

        That a page builds the edges a corpus never got (consolidation lineage;
        PRD-CORE-245 FR07 derives tag relations at query time) is trw-memory's
        ``test_graph_backfill_page``.
        """
        cfg = _config_with_threshold(2)
        monkeypatch.setattr("trw_mcp.models.config.get_config", lambda: cfg)
        for entry_id in ("L-bk-1", "L-bk-2"):
            fake_memory_store.rows[(FAKE_NAMESPACE, entry_id)] = MemoryEntry(
                id=entry_id, content=f"{entry_id} topic note", namespace=FAKE_NAMESPACE
            )

        results: dict[str, object] = {}
        step_knowledge_sync(trw_dir, cast("dict", results))  # type: ignore[arg-type]

        backfill = cast("dict[str, int]", results["graph_backfill"])
        assert backfill["processed"] == 2
        ((_name, (namespace, _after, _limit, deadline)),) = [
            call for call in fake_memory_store.calls if call[0] == "graph_backfill"
        ]
        assert namespace == FAKE_NAMESPACE
        assert deadline is not None, "deliver time-boxes the backfill"

    def test_deliver_backfill_disabled_by_config(
        self, daemon_checkout: DaemonCheckout, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """deliver_graph_backfill_enabled=False skips the backfill entirely."""
        trw_dir = daemon_checkout.trw_dir
        cfg = _config_with_threshold(50).model_copy(  # type: ignore[attr-defined]
            update={"deliver_graph_backfill_enabled": False}
        )
        monkeypatch.setattr("trw_mcp.models.config.get_config", lambda: cfg)
        store_learning(trw_dir, "L-bd-1", "gamma note", "z")

        results: dict[str, object] = {}
        step_knowledge_sync(trw_dir, cast("dict", results))  # type: ignore[arg-type]

        assert "graph_backfill" not in results

    def test_deliver_backfill_fail_open(self, daemon_checkout: DaemonCheckout, monkeypatch: pytest.MonkeyPatch) -> None:
        """A backfill exception must not fail the deliver step."""
        trw_dir = daemon_checkout.trw_dir
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
    """End to end through the daemon: the advisory reads the store's own ``health`` block."""

    def test_empty_graph_many_memories_emits_advisory(self, daemon_checkout: DaemonCheckout) -> None:
        """>10 memories + no relation → advisory dict returned."""
        trw_dir = daemon_checkout.trw_dir
        for i, w in enumerate(_distinct_words()):
            # Distinct content (avoids semantic dedup) + unique tags (no derived relation).
            store_learning(trw_dir, f"L-gh-{i}", f"{w} subject {i}", f"{w} body {i}", tags=[f"uniq{i}"])
        assert store_health(trw_dir)["edges"] == 0, "precondition: nothing materialised an edge"

        advisory = step_graph_health(trw_dir)

        assert advisory is not None
        assert advisory["status"] == "empty"
        assert int(cast("int", advisory["memories"])) > 10
        # "dead", not "empty": 8af881a11 (PRD-FIX-141-FR02) made the advisory
        # the graph_edges probe's own string, because a tag co-occurrence
        # relation is DERIVED and materialises no row, so "empty" named the
        # wrong question. The trailing remedy is asserted too — the advisory is
        # only actionable if it says what to do next.
        assert "knowledge graph dead" in str(advisory["advisory"])
        assert "trw_deliver" in str(advisory["advisory"])

    def test_small_corpus_no_advisory(self, daemon_checkout: DaemonCheckout) -> None:
        """<=10 memories → no advisory even if graph is empty."""
        trw_dir = daemon_checkout.trw_dir
        for i, w in enumerate(_distinct_words()[:3]):
            store_learning(trw_dir, f"L-sm-{i}", f"{w} subject {i}", f"{w} body {i}", tags=[f"u{i}"])

        assert step_graph_health(trw_dir) is None

    def test_populated_graph_no_advisory(self, daemon_checkout: DaemonCheckout) -> None:
        """A DERIVED tag relation is a populated graph → no advisory (CORE-245 FR07).

        This is the case the old probe got wrong. Entries related purely by
        shared tags materialise no ``memory_graph_edges`` row at all now — the
        relation is derived from ``memory_tags`` at query time — so a probe that
        counted edges called this healthy corpus empty and would have said so on
        every session for the rest of the project's life.
        """
        trw_dir = daemon_checkout.trw_dir
        for i, w in enumerate(_distinct_words()):
            store_learning(trw_dir, f"L-pop-{i}", f"{w} shared subject {i}", f"{w} body {i}", tags=["shared", "topic"])
        health = store_health(trw_dir)
        assert health["edges"] == 0, "precondition: tag co-occurrence must materialise no edge"
        assert health["has_relations"], "precondition: shared-tag entries derive a relation"

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
