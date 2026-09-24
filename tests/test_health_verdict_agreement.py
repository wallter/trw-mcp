"""PRD-FIX-141-FR02 — the two health surfaces agree on one store, by construction.

On 2026-09-16 ``trw_session_start`` injected ``pipeline_health_warning`` at
severity ``error`` reading "knowledge graph dead: 0 edges for 1343 memories (min
corpus 10)" while ``trw_pipeline_health`` reported
``graph_edges.degraded = false`` for the same store in the same second
(learning L-Rikf). Three predicates existed for one question, over two
thresholds (100 and 10) and two populations.

These tests drive BOTH real surfaces over ONE store's health block. They are not
about which verdict is right — they are about the two never differing, which no
assertion anywhere could see before, because each surface only ever tested
itself.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from trw_memory.models.memory import MemoryEntry

from tests._memory_fixtures import FAKE_NAMESPACE
from tests._memory_store_fake import FakeMemoryStore
from trw_mcp.models.config import TRWConfig


@pytest.fixture(autouse=True)
def _pinned(fake_memory_store: FakeMemoryStore) -> FakeMemoryStore:
    """Every checkout here is pinned to the fake store."""
    return fake_memory_store


def _make_store(
    store: FakeMemoryStore,
    tmp_path: Path,
    *,
    corpus: int,
    edges: int = 0,
    shared_tags: bool = False,
    edge_namespace: str = FAKE_NAMESPACE,
) -> Path:
    """Stock the pinned namespace and return the checkout's ``.trw`` dir.

    Edges are counted per namespace, because FR02's namespace scoping is exactly
    what one file-wide count would hide.
    """
    trw_dir = tmp_path / ".trw"
    trw_dir.mkdir(parents=True, exist_ok=True)
    store.rows.clear()
    for i in range(corpus):
        tags = ["alpha", "beta"] if shared_tags else [f"t{i}"]
        store.rows[(FAKE_NAMESPACE, f"m{i}")] = MemoryEntry(
            id=f"m{i}", content=f"entry {i}", namespace=FAKE_NAMESPACE, tags=tags
        )
    store.edges = {edge_namespace: edges}
    return trw_dir


def _config(min_corpus: int = 10) -> TRWConfig:
    """A config with sync OFF, so only the graph signature can trip the gate."""
    return TRWConfig(platform_urls=[], pipeline_health_gate_graph_min_corpus=min_corpus)


def _verdicts(trw_dir: Path, config: TRWConfig) -> tuple[bool, bool]:
    """Return ``(probe says degraded, gate reports a graph breakage)``."""
    from trw_mcp.tools._pipeline_health import step_pipeline_health
    from trw_mcp.tools._pipeline_health_gate import check_pipeline_health

    health = step_pipeline_health(trw_dir, config)
    graph = health["graph_edges"]
    assert isinstance(graph, dict)

    verdict = check_pipeline_health(trw_dir, config)
    reasons = [str(r) for r in verdict.get("reasons", [])]
    gate_says_dead = any("knowledge graph" in reason for reason in reasons)
    return bool(graph.get("degraded")), gate_says_dead


@pytest.mark.parametrize("corpus", [0, 1, 9, 10, 11, 25, 150])
def test_probe_and_gate_agree_across_the_threshold_boundary(
    fake_memory_store: FakeMemoryStore, tmp_path: Path, corpus: int
) -> None:
    """One store, two surfaces, identical graph verdict at every corpus size."""
    trw_dir = _make_store(fake_memory_store, tmp_path, corpus=corpus)

    probe_degraded, gate_degraded = _verdicts(trw_dir, _config(min_corpus=10))

    assert probe_degraded == gate_degraded, (
        f"corpus={corpus}: probe degraded={probe_degraded} but gate degraded={gate_degraded}"
    )


def test_the_shared_threshold_is_the_configured_one(fake_memory_store: FakeMemoryStore, tmp_path: Path) -> None:
    """Raising the single knob suppresses BOTH surfaces, not one of them.

    The pre-FR02 defect is only visible with a threshold change: the probe's
    private 100 and the gate's configured 10 produced opposite answers for every
    corpus between them, and nothing in either suite could see it.
    """
    trw_dir = _make_store(fake_memory_store, tmp_path, corpus=50)

    assert _verdicts(trw_dir, _config(min_corpus=10)) == (True, True)
    assert _verdicts(trw_dir, _config(min_corpus=100)) == (False, False)


def test_a_tag_related_corpus_is_healthy_on_both_surfaces(fake_memory_store: FakeMemoryStore, tmp_path: Path) -> None:
    """Derived tag relations materialise no edge row; neither surface may cry wolf."""
    trw_dir = _make_store(fake_memory_store, tmp_path, corpus=150, edges=0, shared_tags=True)

    assert _verdicts(trw_dir, _config()) == (False, False)


def test_a_foreign_namespace_edge_does_not_conceal_an_empty_project_graph(
    fake_memory_store: FakeMemoryStore, tmp_path: Path
) -> None:
    """An edge in another namespace is not evidence about this one.

    ``graph_has_relations`` used to accept ANY materialised edge in the file, so
    one row belonging to a different namespace reported the project graph
    healthy. Both surfaces read the same predicate, so the bug — and its fix —
    land on both at once.
    """
    trw_dir = _make_store(fake_memory_store, tmp_path, corpus=150, edges=5, edge_namespace="team:acme")

    assert _verdicts(trw_dir, _config()) == (True, True)


def test_an_edge_in_this_namespace_still_reports_healthy(fake_memory_store: FakeMemoryStore, tmp_path: Path) -> None:
    """The namespace scoping must not turn every materialised edge into a miss."""
    trw_dir = _make_store(fake_memory_store, tmp_path, corpus=150, edges=5, edge_namespace=FAKE_NAMESPACE)

    assert _verdicts(trw_dir, _config()) == (False, False)


def test_session_start_advisory_reads_the_same_verdict(fake_memory_store: FakeMemoryStore, tmp_path: Path) -> None:
    """``step_graph_health`` is the third former copy of the predicate.

    It now calls the probe, so a store the probe calls healthy can never produce
    a session-start "knowledge graph empty" advisory.
    """
    from trw_mcp.tools._ceremony_session_start_steps import step_graph_health

    healthy = _make_store(fake_memory_store, tmp_path, corpus=150, shared_tags=True)
    assert step_graph_health(healthy) is None

    dead = _make_store(fake_memory_store, tmp_path, corpus=150)
    advisory = step_graph_health(dead)
    assert advisory is not None
    assert advisory["memories"] == 150
    assert "knowledge graph dead" in str(advisory["advisory"])


def test_an_unreachable_store_is_not_reported_as_an_empty_graph(
    fake_memory_store: FakeMemoryStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fail-open is preserved: "could not read" is never "the graph is dead"."""
    from trw_mcp.state._store_selection import StoreUnavailableError
    from trw_mcp.tools._ceremony_session_start_steps import step_graph_health
    from trw_mcp.tools._pipeline_health import step_pipeline_health
    from trw_mcp.tools._pipeline_health_gate import check_pipeline_health

    trw_dir = _make_store(fake_memory_store, tmp_path, corpus=150)

    def _unreachable(_namespace: str) -> None:
        raise StoreUnavailableError("the memory daemon is unreachable")

    monkeypatch.setattr(fake_memory_store, "health", _unreachable)

    graph = step_pipeline_health(trw_dir, _config())["graph_edges"]
    assert isinstance(graph, dict)
    assert graph["measured"] is False
    assert graph["degraded"] is False

    verdict = check_pipeline_health(trw_dir, _config())
    assert verdict["healthy"] is True
    assert not any("knowledge graph" in str(r) for r in verdict.get("reasons", []))

    assert step_graph_health(trw_dir) is None


def test_the_embedding_threshold_is_the_configured_one(fake_memory_store: FakeMemoryStore, tmp_path: Path) -> None:
    """PRD-FIX-141-FR03 — the probe reads the config knob, not a private constant.

    ``embeddings_coverage_warn_threshold`` is the number the OTHER coverage
    surface already reads; both defaulted to 0.10, so only a non-default value
    can prove which one the probe actually consults.
    """
    from trw_mcp.tools._pipeline_health import probe_embedding_coverage

    trw_dir = _make_store(fake_memory_store, tmp_path, corpus=10)
    fake_memory_store.stored_vectors.update({f"m{i}": [1.0] for i in range(3)})

    lenient = probe_embedding_coverage(trw_dir, TRWConfig(embeddings_coverage_warn_threshold=0.10))
    strict = probe_embedding_coverage(trw_dir, TRWConfig(embeddings_coverage_warn_threshold=0.50))

    assert lenient["coverage_ratio"] == pytest.approx(0.3)
    assert lenient["degraded"] is False
    assert lenient["coverage_threshold"] == pytest.approx(0.10)
    assert strict["degraded"] is True
    assert strict["coverage_threshold"] == pytest.approx(0.50)
