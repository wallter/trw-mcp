"""Wiring-defect pattern P5: an error must never look like a normal result.

Each test injects a REAL fault on the REAL path (a broken table, a read-only
directory, a store that cannot enumerate its namespaces) and asserts that the
answer the production caller receives can be told apart from the ordinary
"nothing here" answer. Every one of them fails against the code these tests
shipped with, because that code returned ``False`` / ``None`` / ``0`` / ``PASS``
for both.

Audit rows W06, W07, W08, W09, W10, W11, W12
(``docs/documentation/wiring-defect-patterns.md``; CONSTITUTION §1 truthfulness).
"""

from __future__ import annotations

import sqlite3
import stat
from pathlib import Path
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# W06 — the knowledge-graph relation probe
# ---------------------------------------------------------------------------


def _graph_store(tmp_path: Path) -> sqlite3.Connection:
    """A store holding a materialised-edge table and a corpus, both readable."""
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE memory_graph_edges (source_id TEXT, target_id TEXT)")
    conn.execute("CREATE TABLE memories (id TEXT, namespace TEXT, updated_at TEXT)")
    conn.execute("INSERT INTO memories VALUES ('L-1', 'default', '2026-01-01')")
    return conn


def test_w06_unreadable_root_query_does_not_answer_false(tmp_path: Path) -> None:
    """A store that cannot be READ is not a store with no relations.

    Fails before: the root-memory query sat under ``except sqlite3.Error: return
    False``, so a probe that could not read ``memories`` produced the exact value
    an empty namespace produces, and both health consumers reported a healthy
    corpus as "knowledge graph empty".
    """
    from trw_memory.models.config import MemoryConfig

    from trw_mcp.state._graph_relations import graph_has_relations

    conn = _graph_store(tmp_path)
    conn.execute("DROP TABLE memories")
    config = MemoryConfig(storage_path=str(tmp_path / "memory"))

    with pytest.raises(sqlite3.Error):
        graph_has_relations(conn, namespace="default", config=config)


def test_w06_an_empty_namespace_still_answers_false(tmp_path: Path) -> None:
    """The negative control: a readable empty store must NOT raise."""
    from trw_memory.models.config import MemoryConfig

    from trw_mcp.state._graph_relations import graph_has_relations

    conn = _graph_store(tmp_path)
    conn.execute("DELETE FROM memories")

    assert (
        graph_has_relations(conn, namespace="default", config=MemoryConfig(storage_path=str(tmp_path / "memory")))
        is False
    )


def test_w06_pipeline_probe_reports_not_measured(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``probe_graph_edges`` distinguishes "did not look" from "looked, fine".

    Fails before: the probe's fail-open ``except`` returned ``degraded=False,
    edge_count=0, advisory=""`` — byte-identical to a healthy small corpus.
    """
    from trw_mcp.tools import _pipeline_health

    trw_dir = tmp_path / ".trw"
    (trw_dir / "memory").mkdir(parents=True)
    db_path = trw_dir / "memory" / "memory.db"
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute("CREATE TABLE memory_graph_edges (source_id TEXT)")
        conn.execute("CREATE TABLE memories (id TEXT, namespace TEXT, updated_at TEXT)")

    healthy = _pipeline_health.probe_graph_edges(trw_dir)
    assert healthy["measured"] is True
    assert healthy["advisory"] == ""

    def _boom(*_args: object, **_kwargs: object) -> bool:
        raise sqlite3.OperationalError("no such table: memories")

    monkeypatch.setattr(_pipeline_health, "sqlite3", sqlite3)
    monkeypatch.setattr("trw_mcp.state._graph_relations.graph_has_relations", _boom)

    unmeasured = _pipeline_health.probe_graph_edges(trw_dir)
    assert unmeasured["measured"] is False
    assert unmeasured["degraded"] is False, "an unreadable store is not evidence of a dead graph"
    assert "not measured" in unmeasured["advisory"]
    assert unmeasured != healthy, "the failed probe answered exactly like the healthy one"


def test_w06_the_fail_closed_gate_will_not_escalate_an_unmeasured_probe() -> None:
    """A gate that never read the store must not call its graph dead."""
    from trw_mcp.tools._pipeline_health_gate import _check_empty_graph

    unmeasured: dict[str, Any] = {
        "graph_edges": {"measured": False, "edge_count": 0, "corpus_count": 5000, "degraded": False}
    }
    assert _check_empty_graph(unmeasured, None) is None

    measured = {"graph_edges": {"measured": True, "edge_count": 0, "corpus_count": 5000, "degraded": True}}
    assert _check_empty_graph(measured, None) is not None


# ---------------------------------------------------------------------------
# W07 — the WAL checkpoint marker
# ---------------------------------------------------------------------------


def test_w07_a_marker_write_that_fails_is_reported(tmp_path: Path) -> None:
    """The marker write returns its outcome instead of swallowing OSError.

    Fails before: ``record_checkpoint_attempt`` returned ``None`` on every path,
    so the caller could not tell a persisted clock from a lost one.
    """
    from trw_mcp.state._wal_triggers import checkpoint_marker_path, record_checkpoint_attempt

    memory_dir = tmp_path / "memory"
    memory_dir.mkdir()
    db_path = memory_dir / "memory.db"
    assert record_checkpoint_attempt(db_path) is True
    assert checkpoint_marker_path(db_path).is_file()

    blocked = tmp_path / "blocked"
    blocked.mkdir()
    blocked.chmod(stat.S_IRUSR | stat.S_IXUSR)
    try:
        assert record_checkpoint_attempt(blocked / "memory.db") is False
    finally:
        blocked.chmod(stat.S_IRWXU)


def test_w07_checkpoint_result_flags_a_lost_marker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``checkpointed: true`` alone asserted a hot-loop protection that is not in force.

    Fails before: the result dict carried no marker field at all, so a checkpoint
    whose clock never landed was reported as an unqualified success.
    """
    from trw_mcp.state import _memory_lookups
    from trw_mcp.state._wal_triggers import WalTrigger

    trw_dir = tmp_path / ".trw"
    (trw_dir / "memory").mkdir(parents=True)
    db_path = trw_dir / "memory" / "memory.db"
    wal_path = db_path.with_suffix(".db-wal")
    db_path.touch()
    wal_path.write_bytes(b"x" * 2048)

    monkeypatch.setattr(
        _memory_lookups,
        "_bare_passive_checkpoint",
        lambda _path: {"busy": 0, "checkpointed": 4, "mode": "PASSIVE"},
    )
    monkeypatch.setattr(
        "trw_mcp.state._wal_triggers.evaluate_wal_trigger",
        lambda *_a, **_k: WalTrigger(due=True, reason="size", wal_size_bytes=2048, age_seconds=None),
    )
    monkeypatch.setattr("trw_mcp.state._wal_triggers.sole_live_writer", lambda *_a, **_k: False)
    monkeypatch.setattr("trw_mcp.state._memory_connection.peek_backend", lambda: None)

    healthy = _memory_lookups.maybe_checkpoint_wal(trw_dir)
    assert healthy["checkpointed"] is True
    assert healthy["markers_persisted"] is True
    assert "advisory" not in healthy

    monkeypatch.setattr("trw_mcp.state._wal_triggers.record_checkpoint_attempt", lambda *_a, **_k: False)
    monkeypatch.setattr("trw_mcp.state._wal_triggers.record_effective_checkpoint", lambda *_a, **_k: False)

    partial = _memory_lookups.maybe_checkpoint_wal(trw_dir)
    assert partial["checkpointed"] is True
    assert partial["markers_persisted"] is False
    assert partial["reason"] == "checkpoint_marker_write_failed"
    assert "age is unknown" in partial["advisory"]


def test_w07_the_maintenance_step_carries_the_partial_result(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A field nobody reads is a new defect — the session-start payload must carry it."""
    from trw_mcp.tools._ceremony_maintenance_steps import _run_wal_maintenance

    monkeypatch.setattr(
        "trw_mcp.state.memory_adapter.maybe_checkpoint_wal",
        lambda _dir: {"checkpointed": True, "markers_persisted": False, "advisory": "age is unknown"},
    )
    maintenance: dict[str, Any] = {}
    _run_wal_maintenance(tmp_path, maintenance)

    assert maintenance["wal_checkpoint"]["markers_persisted"] is False


# ---------------------------------------------------------------------------
# W08 / W09 — the doctor's agent-parity check
# ---------------------------------------------------------------------------


def test_w08_a_missing_bundle_cannot_produce_pass(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``PASS: all 0 bundled agents present`` certified a broken install as complete.

    Fails before: ``_bundled_agent_stems`` returned ``[]`` for a missing
    directory, every client trivially held all zero of it, and the verdict was
    PASS.
    """
    from trw_mcp.server._doctor_agent_parity import agent_parity_report

    (tmp_path / ".trw").mkdir(exist_ok=True)
    (tmp_path / ".trw" / "config.yaml").write_text("target_platforms:\n  - claude-code\n", encoding="utf-8")
    monkeypatch.setattr("trw_mcp.bootstrap._utils._DATA_DIR", tmp_path / "no-such-bundle")

    status, message, rows = agent_parity_report(tmp_path)

    assert status == "WARN", message
    assert "NOT MEASURED" in message
    assert rows[0]["reason"] == "bundle_unavailable"


def test_w09_an_unreadable_config_cannot_produce_pass(tmp_path: Path) -> None:
    """Substituting the default client measured a surface the user never selected.

    Fails before: the read failure reached only ``logger.info``; the check then
    measured the packaged default ``target_platforms`` and could report PASS.
    """
    from trw_mcp.server._doctor_agent_parity import agent_parity_report

    (tmp_path / ".trw").mkdir()
    config = tmp_path / ".trw" / "config.yaml"
    config.write_text("target_platforms: [unclosed\n", encoding="utf-8")

    status, message, rows = agent_parity_report(tmp_path)

    assert status == "WARN", message
    assert "NOT MEASURED" in message
    assert rows[0]["reason"] == "config_unreadable"


def test_w09_a_project_with_no_config_at_all_is_still_measured(tmp_path: Path) -> None:
    """Negative control: not choosing is not a read failure."""
    from trw_mcp.server._doctor_agent_parity import agent_parity_report

    status, message, _ = agent_parity_report(tmp_path)

    assert status in {"PASS", "WARN", "SKIP"}
    assert "NOT MEASURED" not in message


# ---------------------------------------------------------------------------
# W10 — the moved-checkout readback
# ---------------------------------------------------------------------------


def test_w10_a_census_error_is_not_measured_not_absent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A failed census used to omit the key exactly as a clean one does.

    Fails before: ``step_moved_checkout`` returned ``None`` for both, and the
    session-start key was absent either way — the ambiguity the step exists to
    remove.
    """
    pytest.importorskip("trw_memory.namespaces.curate")
    from trw_mcp.tools._moved_checkout_readback import step_moved_checkout

    user_dir = tmp_path / "userhome"
    (user_dir / "memory").mkdir(parents=True)
    monkeypatch.setenv("TRW_USER_DIR", str(user_dir))
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)

    def _boom(_config: object) -> dict[str, int]:
        raise sqlite3.DatabaseError("file is not a database")

    monkeypatch.setattr("trw_memory.namespaces.curate.store_census", _boom)

    observed = step_moved_checkout()

    assert observed["status"] == "not_measured"
    assert observed["reason"] == "DatabaseError"


def test_w10_session_start_sets_the_key_for_not_measured(monkeypatch: pytest.MonkeyPatch) -> None:
    """The step-table adapter must SURFACE the tri-state, not re-flatten it."""
    from trw_mcp.tools import _ceremony_step_table as table

    class _Ctx:
        def __init__(self) -> None:
            self.results: dict[str, Any] = {}

    monkeypatch.setattr(
        "trw_mcp.tools._moved_checkout_readback.step_moved_checkout",
        lambda: {"status": "not_measured", "reason": "DatabaseError"},
    )
    ctx = _Ctx()
    table._ss_moved_checkout(ctx)  # type: ignore[arg-type]
    assert ctx.results["moved_checkout"]["status"] == "not_measured"

    monkeypatch.setattr(
        "trw_mcp.tools._moved_checkout_readback.step_moved_checkout",
        lambda: {"status": "absent"},
    )
    clean = _Ctx()
    table._ss_moved_checkout(clean)  # type: ignore[arg-type]
    assert "moved_checkout" not in clean.results, "a clean session must carry no extra payload"


# ---------------------------------------------------------------------------
# W11 — namespace enumeration
# ---------------------------------------------------------------------------


class _UnenumerableBackend:
    """A store holding one row in a non-default namespace, unable to list namespaces."""

    def __init__(self, *, holds: dict[str, str] | None = None) -> None:
        self._holds = holds or {}

    def list_namespaces(self) -> list[str]:
        raise sqlite3.OperationalError("no such table: namespaces")

    def get(self, entry_id: str, *, namespace: str) -> object | None:
        return object() if self._holds.get(entry_id) == namespace else None


def test_w11_enumeration_failure_is_not_absence() -> None:
    """A miss across an INCOMPLETE enumeration is not evidence the row is gone.

    Fails before: the helper fell back to probing ``default`` alone and returned
    ``None``, so a valid ``project:``/``user:`` row was reported ``not_found``.
    """
    from trw_mcp.exceptions import NamespaceEnumerationError
    from trw_mcp.state._backend_id_lookup import resolve_entry_in_backend

    backend = _UnenumerableBackend(holds={"L-1": "project:alpha"})

    with pytest.raises(NamespaceEnumerationError):
        resolve_entry_in_backend(backend, "L-1")  # type: ignore[arg-type]


def test_w11_the_definite_attempt_is_still_made() -> None:
    """Negative control: a hit in the unnamed namespace still resolves."""
    from trw_mcp.state._backend_id_lookup import resolve_entry_in_backend

    backend = _UnenumerableBackend(holds={"L-1": "default"})

    assert resolve_entry_in_backend(backend, "L-1") is not None  # type: ignore[arg-type]


def test_w11_update_learning_reports_lookup_unavailable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``not_found`` invited a duplicate write into a store that already held the row."""
    from trw_mcp.exceptions import NamespaceEnumerationError
    from trw_mcp.state import _memory_update

    def _boom(*_args: object, **_kwargs: object) -> object:
        raise NamespaceEnumerationError("could not enumerate namespaces")

    monkeypatch.setattr(_memory_update, "get_backend", lambda _dir: object())
    monkeypatch.setattr(_memory_update, "resolve_entry_in_backend", _boom)

    result = _memory_update.update_learning(tmp_path, "L-1", status="resolved")

    assert result["status"] == "lookup_unavailable"
    assert result["status"] != "not_found"


def test_w11_graph_related_reports_lookup_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    """``found: False`` was the same answer for "absent" and "unsearchable"."""
    from trw_mcp.exceptions import NamespaceEnumerationError
    from trw_mcp.tools import knowledge

    def _boom(*_args: object, **_kwargs: object) -> object:
        raise NamespaceEnumerationError("could not enumerate namespaces")

    monkeypatch.setattr(knowledge, "get_backend", lambda: object())
    monkeypatch.setattr(knowledge, "resolve_entry_in_backend", _boom)

    result = knowledge.graph_related("L-1")

    assert result["found"] is False
    assert result["lookup_status"] == "unavailable"


# ---------------------------------------------------------------------------
# W12 — team-learning merge accounting
# ---------------------------------------------------------------------------


def test_w12_rejected_items_are_counted_and_reported(tmp_path: Path) -> None:
    """A batch of one clean item and one of fifty-with-49-refused returned the same 1.

    Fails before: ``merge_team_learnings`` returned ``inserted + merged`` and
    logged ``outcome="success"`` regardless, so no number in the response could
    be compared against what was sent.
    """
    from unittest.mock import patch

    from trw_memory.storage.sqlite_backend import SQLiteBackend

    from trw_mcp.sync.pull import SyncPuller

    (tmp_path / "memory").mkdir(parents=True)
    backend = SQLiteBackend(tmp_path / "memory" / "memory.db", dim=8)
    puller = SyncPuller(backend_url="http://example.com", api_key="key", client_id="sync-client-1", trw_dir=tmp_path)

    payload = [
        {"source_learning_id": "R-1", "summary": "kept", "detail": "d", "tags": ["t"]},
        {"summary": "no id at all"},
        {"source_learning_id": "R-3", "summary": "bad", "status": "not-a-status"},
    ]
    with patch("trw_mcp.state._memory_connection.get_backend", return_value=backend):
        result = puller.merge_team_learnings(payload, namespace="project:alpha")

    assert result.attempted == 3
    assert result.applied == 1
    assert result.skipped_no_id == 1
    assert result.invalid == 1
    assert result.rejected == 2
    assert result.status == "partial"


def test_w12_a_clean_batch_is_success_and_an_impossible_one_is_unavailable() -> None:
    """The three statuses are distinct: success, partial, unavailable."""
    from trw_mcp.sync._team_merge_result import TeamMergeResult
    from trw_mcp.sync.pull import SyncPuller

    assert TeamMergeResult(attempted=2, inserted=2).status == "success"
    assert TeamMergeResult(attempted=2, inserted=1, failed=1).status == "partial"

    puller = SyncPuller(backend_url="http://example.com", api_key="key", client_id="c", trw_dir=None)
    unavailable = puller.merge_team_learnings([{"source_learning_id": "R-1"}])
    assert unavailable.status == "unavailable"
    assert unavailable.applied == 0


def test_w12_the_sync_cycle_consumes_the_counts() -> None:
    """A count nobody reads is a new defect — the cycle must reach for them."""
    import inspect

    from trw_mcp.sync import _client_cycle

    source = inspect.getsource(_client_cycle)
    assert "merge_result.rejected" in source
    assert "merge_status=merge_result.status" in source
