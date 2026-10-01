"""Embedding coverage counts vectors IN the active space, and doctor warns with the exact fix (FB-INSTALL-05).

A first-upgrade store held 2,600 vectors from another embedding space; ``embeddings_coverage_ratio`` said 1 (a vector in
any space counted) and the doctor retrieval row said PASS with the outside count only appended as text.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from trw_memory.models.memory import MemoryEntry

from tests._memory_fixtures import FAKE_NAMESPACE
from tests._memory_store_fake import FakeMemoryStore
from trw_mcp.models.config import TRWConfig


def _stock(store: FakeMemoryStore, total: int, active: int, outside: int) -> None:
    for i in range(total):
        entry = MemoryEntry(id=f"m{i}", content=f"entry {i}", namespace=FAKE_NAMESPACE, tags=[f"t{i}"])
        store.rows[(FAKE_NAMESPACE, entry.id)] = entry
    store.stored_vectors.update({f"m{i}": [1.0] for i in range(total)})  # every row HAS a vector, in some space
    store.vector_coverage = {
        "active_space": active,
        "other_space": outside,
        "unknown_provenance": 0,
        "outside_active_space": outside,
        "no_vector": 0,
    }


@pytest.fixture
def trw_dir(tmp_path: Path) -> Path:
    path = tmp_path / ".trw"
    (path / "memory").mkdir(parents=True)
    (path / "meta").mkdir()
    return path


def test_coverage_ratio_does_not_count_vectors_outside_the_active_space(
    trw_dir: Path, fake_memory_store: FakeMemoryStore
) -> None:
    from trw_mcp.tools._pipeline_health import probe_embedding_coverage

    _stock(fake_memory_store, total=100, active=5, outside=95)

    result = probe_embedding_coverage(trw_dir)

    assert result["measured"] is True
    assert result["coverage_ratio"] == pytest.approx(0.05)  # was 1.0
    assert result["degraded"] is True and "trw-mcp memory reembed" in str(result["advisory"])


def test_a_fully_active_store_is_still_fully_covered(trw_dir: Path, fake_memory_store: FakeMemoryStore) -> None:
    from trw_mcp.tools._pipeline_health import probe_embedding_coverage

    _stock(fake_memory_store, total=100, active=100, outside=0)

    result = probe_embedding_coverage(trw_dir)

    assert result["coverage_ratio"] == 1.0 and result["degraded"] is False


def test_an_unmeasured_active_space_leaves_the_old_ratio(trw_dir: Path, fake_memory_store: FakeMemoryStore) -> None:
    """Until the daemon loads its model the outside count is None: report what is known, never invent a number."""
    from trw_mcp.tools._pipeline_health import probe_embedding_coverage

    _stock(fake_memory_store, total=100, active=0, outside=0)
    fake_memory_store.vector_coverage = None

    assert probe_embedding_coverage(trw_dir)["coverage_ratio"] == 1.0


def test_the_doctor_retrieval_row_warns_below_95_percent_with_the_exact_command(
    trw_dir: Path, fake_memory_store: FakeMemoryStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    from trw_mcp.server import _subcommands_doctor as doctor
    from trw_mcp.state import _retrieval_capability as capability

    monkeypatch.setattr(capability, "probe_retrieval", lambda *_a, **_k: ())  # every component healthy
    _stock(fake_memory_store, total=2603, active=3, outside=2600)

    result = doctor._check_retrieval(trw_dir.parent, TRWConfig(embeddings_enabled=True))

    assert result.status == "WARN"
    assert "2600 stored vector(s) outside the active space" in result.message
    assert "trw-mcp memory reembed" in result.message


def test_the_doctor_row_stays_pass_at_or_above_95_percent(
    trw_dir: Path, fake_memory_store: FakeMemoryStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    from trw_mcp.server import _subcommands_doctor as doctor
    from trw_mcp.state import _retrieval_capability as capability

    monkeypatch.setattr(capability, "probe_retrieval", lambda *_a, **_k: ())
    _stock(fake_memory_store, total=100, active=96, outside=4)

    result = doctor._check_retrieval(trw_dir.parent, TRWConfig(embeddings_enabled=True))

    assert result.status == "PASS" and "4 stored vector(s) outside" in result.message
