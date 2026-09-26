"""Tests for _pipeline_health — PRD-FIX-COMPOUNDING-6.

Covers the five compounding-pipeline probes and the step_pipeline_health
aggregator. All paths are fail-open (never raise). Tests follow the
pattern established in test_sync_health.py.

These tests use tmp_path for filesystem fixtures, so they are integration
tier (default when not in _UNIT_FILES).
"""

from __future__ import annotations

import json
from contextlib import AbstractContextManager, nullcontext
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from trw_memory.models.memory import MemoryEntry

from tests._memory_fixtures import FAKE_NAMESPACE
from tests._memory_store_fake import FakeMemoryStore
from tests._structlog_capture import captured_structlog  # noqa: F401
from trw_mcp.state._store_selection import selected_store as _REAL_SELECTED_STORE

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


@pytest.fixture(autouse=True)
def _pinned(fake_memory_store: FakeMemoryStore) -> FakeMemoryStore:
    """Every checkout here is pinned to the fake store, so the store-reading probes measure."""
    return fake_memory_store


def _stock_store(
    store: FakeMemoryStore,
    *,
    corpus: int = 0,
    vec: int = 0,
    max_recall: int = 0,
    edges: int = 0,
    shared_tags: bool = False,
) -> None:
    """Stock the fake store's namespace with what ``memory_status``'s health block measures.

    ``shared_tags`` gives every entry the same two tags: a corpus whose only
    relations are derived ones (PRD-CORE-245 FR07).
    """
    for i in range(corpus):
        tags = ["alpha", "beta"] if shared_tags else [f"t{i}"]
        recall = max_recall if i == 0 else 0
        entry = MemoryEntry(id=f"m{i}", content=f"entry {i}", namespace=FAKE_NAMESPACE, tags=tags, recall_count=recall)
        store.rows[(FAKE_NAMESPACE, entry.id)] = entry
    store.stored_vectors.update({f"m{i}": [1.0] for i in range(vec)})
    store.edges[FAKE_NAMESPACE] = edges


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


def test_probe_graph_edges_degraded(fake_memory_store: FakeMemoryStore, tmp_path: Path) -> None:
    """0 edges, corpus >= 100 => degraded=True."""
    from trw_mcp.tools._pipeline_health import probe_graph_edges

    trw_dir = _make_trw_dir(tmp_path)
    _stock_store(fake_memory_store, corpus=150, edges=0)

    result = probe_graph_edges(trw_dir)

    assert result["degraded"] is True
    assert result["edge_count"] == 0
    assert result["corpus_count"] == 150
    assert result["advisory"] != ""


def test_probe_graph_edges_empty_corpus_suppressed(fake_memory_store: FakeMemoryStore, tmp_path: Path) -> None:
    """0 edges, corpus < 100 => degraded=False (suppress for small corpora)."""
    from trw_mcp.tools._pipeline_health import probe_graph_edges

    trw_dir = _make_trw_dir(tmp_path)
    _stock_store(fake_memory_store, corpus=10, edges=0)

    result = probe_graph_edges(trw_dir)

    assert result["degraded"] is False


def test_probe_graph_edges_tag_only_corpus_is_not_degraded(fake_memory_store: FakeMemoryStore, tmp_path: Path) -> None:
    """A corpus related only by shared tags is healthy despite 0 edges (CORE-245 FR07).

    Tag co-occurrence materialises no edge row any more. A probe that read the
    edge table alone called this corpus — the ordinary shape for a store with no
    embeddings — permanently degraded, and its advisory pointed at a backfill
    that had nothing to build.
    """
    from trw_mcp.tools._pipeline_health import probe_graph_edges

    trw_dir = _make_trw_dir(tmp_path)
    _stock_store(fake_memory_store, corpus=150, edges=0, shared_tags=True)

    result = probe_graph_edges(trw_dir)

    assert result["degraded"] is False
    assert result["edge_count"] == 0
    assert result["advisory"] == ""


def test_probe_graph_edges_healthy(fake_memory_store: FakeMemoryStore, tmp_path: Path) -> None:
    """50 edges, 150 entries => not degraded."""
    from trw_mcp.tools._pipeline_health import probe_graph_edges

    trw_dir = _make_trw_dir(tmp_path)
    _stock_store(fake_memory_store, corpus=150, edges=50)

    result = probe_graph_edges(trw_dir)

    assert result["degraded"] is False
    assert result["edge_count"] == 50


def test_probe_graph_edges_empty_store(tmp_path: Path) -> None:
    """A pinned store with no entries is a measured empty corpus, not a degraded one."""
    from trw_mcp.tools._pipeline_health import probe_graph_edges

    trw_dir = _make_trw_dir(tmp_path)

    result = probe_graph_edges(trw_dir)

    assert result["degraded"] is False
    assert result["measured"] is True


# ---------------------------------------------------------------------------
# probe_embedding_coverage
# ---------------------------------------------------------------------------


def test_probe_embedding_coverage_degraded(fake_memory_store: FakeMemoryStore, tmp_path: Path) -> None:
    """vec_count/total < 0.10 => degraded=True."""
    from trw_mcp.tools._pipeline_health import probe_embedding_coverage

    trw_dir = _make_trw_dir(tmp_path)
    # 3.6% coverage: 27 vec out of 750 total (simulating the real-world case)
    _stock_store(fake_memory_store, corpus=750, vec=27)

    result = probe_embedding_coverage(trw_dir)

    assert result["measured"] is True
    assert result["degraded"] is True
    assert result.get("coverage_ratio", 1.0) < 0.10
    assert result["advisory"] != ""


def test_probe_embedding_coverage_healthy(fake_memory_store: FakeMemoryStore, tmp_path: Path) -> None:
    """95% coverage => not degraded."""
    from trw_mcp.tools._pipeline_health import probe_embedding_coverage

    trw_dir = _make_trw_dir(tmp_path)
    _stock_store(fake_memory_store, corpus=100, vec=95)

    result = probe_embedding_coverage(trw_dir)

    assert result["measured"] is True
    assert result["coverage_ratio"] == 0.95
    assert result["degraded"] is False


def test_probe_embedding_coverage_empty_store(tmp_path: Path) -> None:
    """A pinned store with no entries is a measured empty corpus, not a degraded one."""
    from trw_mcp.tools._pipeline_health import probe_embedding_coverage

    trw_dir = _make_trw_dir(tmp_path)

    result = probe_embedding_coverage(trw_dir)

    assert result["degraded"] is False
    assert result["measured"] is True


def test_probe_embedding_coverage_store_without_vectors(fake_memory_store: FakeMemoryStore, tmp_path: Path) -> None:
    """A store that keeps no vectors never computed a ratio: not measured, never a zero."""
    from trw_mcp.tools._pipeline_health import probe_embedding_coverage

    trw_dir = _make_trw_dir(tmp_path)
    _stock_store(fake_memory_store, corpus=100)
    measured = fake_memory_store.health(FAKE_NAMESPACE)
    fake_memory_store.health = lambda _namespace: {**measured, "embedded": None}  # type: ignore[method-assign]

    result = probe_embedding_coverage(trw_dir)

    assert result["degraded"] is False
    assert result["measured"] is False
    assert "store_keeps_no_vectors" in result["advisory"]
    assert result["coverage_ratio"] is None


def _unmigrated_store(trw_dir: Path) -> Path:
    """A real checkout memory.db holding a learning: ``holds_rows`` would open it to word its error."""
    from trw_memory.storage.sqlite_backend import SQLiteBackend

    db = trw_dir / "memory" / "memory.db"
    backend = SQLiteBackend(db)
    backend.store(MemoryEntry(id="L-unmigrated", content="an unmigrated learning"))
    backend.close()
    return db


def _real_selection(monkeypatch: pytest.MonkeyPatch) -> None:
    """Undo the autouse fake: the checkout goes through the real, unpinned selection path."""
    from trw_mcp.state import _store_selection

    monkeypatch.setattr(_store_selection, "selected_store", _REAL_SELECTED_STORE)


def _refuse_open(*_args: object, **_kwargs: object) -> None:
    raise AssertionError("a pipeline-health probe opened a store")


@pytest.mark.parametrize("pinned", [True, False], ids=["pinned", "unpinned"])
def test_no_probe_opens_the_checkout_memory_db(tmp_path: Path, pinned: bool, monkeypatch: pytest.MonkeyPatch) -> None:
    """Session-start health and the pipeline-health probe never open a checkout's store (PRD-CORE-280).

    ``probe_embedding_coverage`` used to open ``.trw/memory/memory.db`` with a
    WRITABLE ``sqlite3.connect``, and an unpinned checkout's selection read it to
    word its error. An unmigrated file must stay byte-identical, with nothing
    beside it, whether or not the checkout is pinned.
    """
    from trw_mcp.tools._pipeline_health import step_pipeline_health

    trw_dir = _make_trw_dir(tmp_path)
    store = _unmigrated_store(trw_dir)
    before = store.read_bytes()
    if not pinned:
        _real_selection(monkeypatch)

    with patch("sqlite3.connect", side_effect=_refuse_open):
        result = step_pipeline_health(trw_dir)

    assert store.read_bytes() == before
    assert sorted(p.name for p in store.parent.iterdir()) == ["memory.db"]
    for probe in ("graph_edges", "embedding_coverage", "recall_feedback"):
        assert "AssertionError" not in str(result[probe].get("advisory", "")), probe
        assert result[probe]["measured"] is pinned, probe


@pytest.mark.parametrize("probe_name", ["graph_edges", "embedding_coverage", "recall_feedback"])
def test_an_unpinned_checkout_is_not_measured(tmp_path: Path, probe_name: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """No probe reads a checkout's memory.db: an unpinned checkout has no store to measure (PRD-CORE-280).

    Its memory.db still holds rows (it has not been migrated), which the old
    probes counted as this project's corpus.
    """
    from trw_mcp.tools import _pipeline_health as ph

    trw_dir = _make_trw_dir(tmp_path)
    _unmigrated_store(trw_dir)
    _real_selection(monkeypatch)

    with patch("sqlite3.connect", side_effect=_refuse_open):
        result = getattr(ph, f"probe_{probe_name}")(trw_dir)

    assert result["measured"] is False
    assert result["degraded"] is False
    assert "StoreUnavailableError" in result["advisory"]


# ---------------------------------------------------------------------------
# probe_recall_feedback
# ---------------------------------------------------------------------------


def test_probe_recall_feedback_all_zero(fake_memory_store: FakeMemoryStore, tmp_path: Path) -> None:
    """MAX(recall_count)=0, corpus >= 100 => degraded=True."""
    from trw_mcp.tools._pipeline_health import probe_recall_feedback

    trw_dir = _make_trw_dir(tmp_path)
    _stock_store(fake_memory_store, corpus=150, max_recall=0)

    result = probe_recall_feedback(trw_dir)

    assert result["degraded"] is True
    assert result.get("max_recall_count") == 0
    assert result["advisory"] != ""


def test_probe_recall_feedback_small_corpus_suppressed(fake_memory_store: FakeMemoryStore, tmp_path: Path) -> None:
    """MAX(recall_count)=0, corpus < 100 => degraded=False (suppressed)."""
    from trw_mcp.tools._pipeline_health import probe_recall_feedback

    trw_dir = _make_trw_dir(tmp_path)
    _stock_store(fake_memory_store, corpus=10, max_recall=0)

    result = probe_recall_feedback(trw_dir)

    assert result["degraded"] is False


def test_probe_recall_feedback_healthy(fake_memory_store: FakeMemoryStore, tmp_path: Path) -> None:
    """MAX(recall_count)=42 => not degraded."""
    from trw_mcp.tools._pipeline_health import probe_recall_feedback

    trw_dir = _make_trw_dir(tmp_path)
    _stock_store(fake_memory_store, corpus=150, max_recall=42)

    result = probe_recall_feedback(trw_dir)

    assert result["degraded"] is False
    assert result.get("max_recall_count") == 42


def test_probe_recall_feedback_empty_store(tmp_path: Path) -> None:
    """A pinned store with no entries is a measured empty corpus, not a degraded one."""
    from trw_mcp.tools._pipeline_health import probe_recall_feedback

    trw_dir = _make_trw_dir(tmp_path)

    result = probe_recall_feedback(trw_dir)

    assert result["degraded"] is False
    assert result["measured"] is True


# ---------------------------------------------------------------------------
# step_pipeline_health (aggregator)
# ---------------------------------------------------------------------------


def test_step_pipeline_health_all_healthy(fake_memory_store: FakeMemoryStore, tmp_path: Path) -> None:
    """All four probes healthy => degraded=False, advisory empty."""
    from trw_mcp.tools._pipeline_health import step_pipeline_health

    trw_dir = _make_trw_dir(tmp_path)
    _write_sync_state(trw_dir, {"consecutive_failures": 0, "last_push_at": _iso_ago(0.5)})
    _stock_store(fake_memory_store, corpus=50, vec=48, max_recall=10, edges=20)

    result = step_pipeline_health(trw_dir)

    assert result["degraded"] is False
    assert result["advisory"] == "" or result["advisory"] is None or result["advisory"] == "None"
    # All four signal keys must be present
    assert "sync_push" in result
    assert "graph_edges" in result
    assert "embedding_coverage" in result
    assert "recall_feedback" in result


def test_step_pipeline_health_all_degraded(fake_memory_store: FakeMemoryStore, tmp_path: Path) -> None:
    """All 4 probes degraded => degraded=True, advisory non-empty listing all signals."""
    from trw_mcp.tools._pipeline_health import step_pipeline_health

    trw_dir = _make_trw_dir(tmp_path)
    _write_sync_state(trw_dir, {"consecutive_failures": 15, "last_push_at": _iso_ago(48)})
    _stock_store(fake_memory_store, corpus=150, vec=5, max_recall=0, edges=0)

    result = step_pipeline_health(trw_dir)

    assert result["degraded"] is True
    advisory = str(result.get("advisory", ""))
    assert advisory != ""
    # Advisory should mention sync_push
    assert "sync_push" in advisory or "sync" in advisory


def test_step_pipeline_health_partial_degraded(fake_memory_store: FakeMemoryStore, tmp_path: Path) -> None:
    """Only sync_push degraded => degraded=True, advisory mentions sync_push."""
    from trw_mcp.tools._pipeline_health import step_pipeline_health

    trw_dir = _make_trw_dir(tmp_path)
    _write_sync_state(trw_dir, {"consecutive_failures": 11, "last_push_at": _iso_ago(0.5)})
    # Healthy: small corpus so graph/recall don't trigger
    _stock_store(fake_memory_store, corpus=50, vec=48, max_recall=10, edges=20)

    result = step_pipeline_health(trw_dir)

    assert result["degraded"] is True
    advisory = str(result.get("advisory", ""))
    assert "sync_push" in advisory or "sync" in advisory


def test_step_pipeline_health_one_probe_raises(fake_memory_store: FakeMemoryStore, tmp_path: Path) -> None:
    """One probe raises => others still run, aggregator returns result."""
    from trw_mcp.tools._pipeline_health import step_pipeline_health

    trw_dir = _make_trw_dir(tmp_path)
    _write_sync_state(trw_dir, {"consecutive_failures": 0, "last_push_at": _iso_ago(0.5)})
    _stock_store(fake_memory_store, corpus=50, max_recall=10, edges=20)

    # Make probe_graph_edges raise
    with patch(
        "trw_mcp.tools._pipeline_health.probe_graph_edges",
        side_effect=RuntimeError("DB exploded"),
    ):
        result = step_pipeline_health(trw_dir)

    # Aggregator must not raise and must still have all keys
    assert "sync_push" in result
    assert "graph_edges" in result
    assert "embedding_coverage" in result
    assert "recall_feedback" in result
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
    ):
        result = step_pipeline_health(trw_dir)

    assert "degraded" in result
    # All probes failed-open => not degraded overall
    assert result["degraded"] is False
    # All signal keys present
    for key in ("sync_push", "graph_edges", "embedding_coverage", "recall_feedback"):
        assert key in result
        assert result[key].get("degraded") is False


def test_step_pipeline_health_advisory_names_the_cli_command(
    fake_memory_store: FakeMemoryStore, tmp_path: Path
) -> None:
    """Advisory when degraded must name ``trw-mcp telemetry pipeline-health`` (PRD-CORE-300 S3b)."""
    from trw_mcp.tools._pipeline_health import step_pipeline_health

    trw_dir = _make_trw_dir(tmp_path)
    _write_sync_state(trw_dir, {"consecutive_failures": 15, "last_push_at": _iso_ago(48)})
    _stock_store(fake_memory_store, corpus=10)

    result = step_pipeline_health(trw_dir)

    if result["degraded"]:
        advisory = str(result.get("advisory", ""))
        assert "trw-mcp telemetry pipeline-health" in advisory


def test_pipeline_health_probe_no_write(fake_memory_store: FakeMemoryStore, tmp_path: Path) -> None:
    """The probes only measure: the one store call they make is ``health``."""
    from trw_mcp.tools._pipeline_health import step_pipeline_health

    trw_dir = _make_trw_dir(tmp_path)
    _write_sync_state(trw_dir, {"consecutive_failures": 0, "last_push_at": _iso_ago(0.5)})
    _stock_store(fake_memory_store, corpus=10)

    step_pipeline_health(trw_dir)

    assert {name for name, _ in fake_memory_store.calls} == {"health"}


# ---------------------------------------------------------------------------
# PRD-CORE-300 S3b: the pipeline-health probe moved off the MCP tool surface
# onto ``trw-mcp telemetry pipeline-health``. The CLI-publication seam is
# covered by tests/test_telemetry_cli.py; this module keeps the probes +
# aggregator.
# ---------------------------------------------------------------------------


def test_pipeline_health_cli_crash_reports_measured_false(tmp_path: Path) -> None:
    """PRD-CORE-263 DEF-06 attribution.

    Before this fix the CLI's catch-all crash handler returned
    ``degraded: False`` with no ``measured`` key anywhere in the payload — top
    level or per-signal — rendering identically to a healthy aggregate for any
    caller that reads ``degraded`` alone. Reverting the fix (dropping the
    ``measured: False`` entries) turns this red.
    """
    from trw_mcp.tools._telemetry_cli import safe_pipeline_health

    with patch(
        "trw_mcp.state._paths.resolve_trw_dir",
        side_effect=RuntimeError("resolve exploded"),
    ):
        result = safe_pipeline_health()

    assert result["degraded"] is False
    assert result["measured"] is False
    for key in ("sync_push", "graph_edges", "embedding_coverage", "recall_feedback"):
        assert result[key]["measured"] is False, key


def test_pipeline_health_aggregate_returns_dict(
    fake_memory_store: FakeMemoryStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The pipeline-health aggregate returns a dict with all four signal keys."""
    from trw_mcp.tools._pipeline_health import step_pipeline_health

    trw_dir = _make_trw_dir(tmp_path)
    _write_sync_state(trw_dir, {"consecutive_failures": 0, "last_push_at": _iso_ago(0.5)})
    _stock_store(fake_memory_store, corpus=10)

    result = step_pipeline_health(trw_dir)

    assert isinstance(result, dict)
    assert "degraded" in result
    assert "advisory" in result
    for key in ("sync_push", "graph_edges", "embedding_coverage", "recall_feedback"):
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
)


def _healthy_trw_dir(tmp_path: Path) -> Path:
    """A .trw dir on which all four probes measure a healthy result."""
    trw_dir = _make_trw_dir(tmp_path)
    (trw_dir / "sync-state.json").write_text(
        json.dumps({"consecutive_failures": 0, "last_push_at": datetime.now(timezone.utc).isoformat()}),
        encoding="utf-8",
    )
    return trw_dir


@pytest.mark.parametrize("probe_name", _ALL_PROBES)
def test_probe_exception_is_distinguishable_from_a_healthy_measurement(
    tmp_path: Path,
    probe_name: str,
) -> None:
    """PRD-CORE-263-FR03 — parametrised over ALL four probes.

    At HEAD four of the five collapsed an exception into ``degraded: False`` with
    an empty advisory, which the aggregator then stripped, so the crash entry and
    the healthy entry were byte-identical dicts. Attribution: reverting FR03
    turns this red on sync_push, embedding_coverage and recall_feedback
    (graph_edges already did it correctly and is the precedent the
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

    The injections are the failures the PRD names, not synthetic ones: an unreachable
    daemon for the three that read the store, and a state file that is not
    valid UTF-8 for the one that reads text.
    """
    if probe_name == "sync_push":
        (trw_dir / "sync-state.json").write_bytes(b'{"consecutive_failures": 0, "last_push_at": "\xff\xfe"}')
        return nullcontext()
    from trw_mcp.state._store_selection import StoreUnavailableError

    return patch.object(ph, "store_health", side_effect=StoreUnavailableError("the memory daemon is unreachable"))


@pytest.mark.parametrize("probe_name", _ALL_PROBES)
def test_a_probes_own_handler_reports_not_measured(
    fake_memory_store: FakeMemoryStore, tmp_path: Path, probe_name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PRD-CORE-263-FR03 — the injection lands below the probe boundary.

    Attribution: restoring any one probe's ``return safe_default`` crash branch
    turns this red for that probe, which the sibling
    ``test_probe_exception_is_distinguishable_from_a_healthy_measurement`` does
    not, because patching the probe function never reaches the handler being
    reverted.
    """
    from trw_mcp.tools import _pipeline_health as ph

    trw_dir = _healthy_trw_dir(tmp_path)
    # A stocked store, so the three store-reading probes measure healthily first.
    _stock_store(fake_memory_store, corpus=10, vec=10, max_recall=5, edges=40, shared_tags=True)
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
