"""docs/sprint-mcp7/PLAN.md §3b item 3 -- the retrieval-capability doctor row and session_start field.

Each component is driven through its real probe with one seam replaced: which
modules ``find_spec`` sees, what the Hugging Face cache probe answers, and
whether ``sqlite_vec.load`` succeeds. No test imports torch or loads a model.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

pytest.importorskip("sqlite_vec")

import sqlite_vec
from trw_memory.embeddings import _hf_cache
from trw_memory.embeddings._hf_cache import CacheState

from trw_mcp.models.config import TRWConfig
from trw_mcp.state import _retrieval_capability as capability
from trw_mcp.state._retrieval_capability import RetrievalComponent, probe_retrieval, retrieval_row, retrieval_summary

pytestmark = pytest.mark.unit

_MODEL = "BAAI/bge-small-en-v1.5"


@pytest.fixture
def world(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """A fully provisioned install; each test takes one piece away."""
    state = SimpleNamespace(missing=set(), cache=CacheState.COMPLETE, load_error=None, probed=[])

    def _find_spec(name: str) -> object | None:
        return None if name in state.missing else object()

    def _probe(model: str) -> SimpleNamespace:
        state.probed.append(model)
        return SimpleNamespace(state=state.cache)

    def _load(_conn: sqlite3.Connection) -> None:
        if state.load_error is not None:
            raise state.load_error

    monkeypatch.setattr(capability, "find_spec", _find_spec)
    monkeypatch.setattr(_hf_cache, "probe_model_cache", _probe)
    monkeypatch.setattr(sqlite_vec, "load", _load)
    return state


def _by_name(components: tuple[RetrievalComponent, ...]) -> dict[str, RetrievalComponent]:
    return {c.name: c for c in components}


def test_a_provisioned_install_is_active_everywhere(world: SimpleNamespace) -> None:
    components = probe_retrieval(_MODEL, embeddings_enabled=True)

    assert [c.name for c in components] == ["vectors", "embeddings", "weights", "bm25"]
    assert {c.state for c in components} == {"active"}
    assert retrieval_summary(components) == "active"
    assert retrieval_row(components)[0] == "PASS"
    assert world.probed == [_MODEL]


def test_missing_sqlite_vec_is_degraded_with_the_reinstall_fix(world: SimpleNamespace) -> None:
    world.missing.add("sqlite_vec")

    vectors = _by_name(probe_retrieval(_MODEL, embeddings_enabled=True))["vectors"]

    assert vectors.state == "degraded"
    assert "reinstall trw-mcp" in vectors.fix


def test_an_interpreter_that_cannot_load_extensions_is_degraded(world: SimpleNamespace) -> None:
    """sqlite-vec installed is not enough: a SQLite built without extension loading cannot use it."""
    import trw_memory.storage._dbapi  # noqa: F401 -- the engine the probe resolves

    world.load_error = sys.modules["sqlite3"].OperationalError("not authorized")

    components = probe_retrieval(_MODEL, embeddings_enabled=True)
    vectors = _by_name(components)["vectors"]

    assert vectors.state == "degraded"
    assert "OperationalError" in vectors.detail
    assert "extension loading" in vectors.fix
    status, message = retrieval_row(components)
    assert status == "WARN"
    assert "vectors degraded" in message


def test_missing_sentence_transformers_is_degraded_with_the_install_fix(world: SimpleNamespace) -> None:
    world.missing.add("sentence_transformers")

    components = probe_retrieval(_MODEL, embeddings_enabled=True)

    assert _by_name(components)["embeddings"].state == "degraded"
    summary = retrieval_summary(components)
    assert summary.startswith("degraded: embeddings")
    assert "pip install 'trw-memory[embeddings]'" in summary


def test_uncached_weights_are_degraded_and_the_fix_names_the_configured_model(world: SimpleNamespace) -> None:
    world.cache = CacheState.ABSENT

    weights = _by_name(probe_retrieval(_MODEL, embeddings_enabled=True))["weights"]

    assert weights.state == "degraded"
    assert "absent" in weights.detail
    assert f"SentenceTransformer('{_MODEL}')" in weights.fix


def test_embeddings_disabled_is_keyword_only_not_a_warning(world: SimpleNamespace) -> None:
    """``embeddings_enabled: false`` is the operator's choice: reported, never WARN, weights not probed."""
    world.missing.add("sentence_transformers")
    world.cache = CacheState.ABSENT

    components = probe_retrieval(_MODEL, embeddings_enabled=False)

    assert _by_name(components)["embeddings"].state == "off"
    assert _by_name(components)["weights"].state == "off"
    assert world.probed == []
    assert retrieval_summary(components) == "keyword-only: embeddings_enabled=false"
    assert retrieval_row(components)[0] == "PASS"


def test_missing_bm25_is_listed_but_never_degrades_retrieval(world: SimpleNamespace) -> None:
    world.missing.add("rank_bm25")

    components = probe_retrieval(_MODEL, embeddings_enabled=True)
    bm25 = _by_name(components)["bm25"]

    assert bm25.state == "off"
    assert "FTS5" in bm25.detail
    assert retrieval_summary(components) == "active"
    status, message = retrieval_row(components)
    assert status == "PASS"
    assert "bm25 off" in message


def test_the_doctor_row_probes_the_configured_model(world: SimpleNamespace, tmp_path: Path) -> None:
    from trw_mcp.server import _subcommands_doctor as doctor

    world.cache = CacheState.INCOMPLETE
    config = TRWConfig(retrieval_embedding_model="org/configured-model", embeddings_enabled=True)

    result = doctor._check_retrieval(tmp_path, config)

    assert result.name == "retrieval"
    assert result.status == "WARN"
    assert "org/configured-model" in result.message
    assert world.probed == ["org/configured-model"]


def test_session_start_always_carries_the_retrieval_field(world: SimpleNamespace) -> None:
    from trw_mcp.tools import ceremony
    from trw_mcp.tools._ceremony_step_table import SESSION_START_STEPS, SessionStartContext

    assert "retrieval" in [step.key for step in SESSION_START_STEPS]
    world.missing.add("sentence_transformers")
    sctx = SimpleNamespace(config=TRWConfig(embeddings_enabled=True), results={})

    ceremony._ss_retrieval(cast("SessionStartContext", sctx))

    assert sctx.results["retrieval"].startswith("degraded: embeddings")
