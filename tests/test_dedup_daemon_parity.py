"""trw_learn's dedup answer from the daemon, as ``DaemonMemoryStore`` reads it (PRD-CORE-302 C1/C4).

The daemon decides the verdict (``trw-memory/tests/test_tools_similar.py``); this
file pins how trw-mcp reads each answer shape -- a verdict is taken as given, a
missing embedder or empty text means "store", and every other answer raises --
and proves the no-embedder path end to end against a real (keyword-only) daemon.
"""

from __future__ import annotations

from typing import Any

import pytest
from trw_memory.lifecycle.dedup import DedupResult

from tests._memory_fixtures import DaemonCheckout
from trw_mcp.models.config import TRWConfig
from trw_mcp.state._daemon_store import DaemonMemoryStore


class _Answers:
    """A daemon client whose ``memory_similar`` / ``memory_vectors`` return one scripted answer."""

    def __init__(self, answer: dict[str, Any]) -> None:
        self.answer = answer

    async def similar(self, *_args: object) -> dict[str, Any]:
        return self.answer

    async def vectors(self, *_args: object) -> dict[str, Any]:
        return self.answer


def _store(answer: dict[str, Any]) -> DaemonMemoryStore:
    return DaemonMemoryStore(_Answers(answer), "project:t")  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        (
            {"status": "ok", "action": "merge", "existing_id": "L1", "similarity": 0.9, "mode": "exhaustive"},
            DedupResult("merge", "L1", 0.9),
        ),
        ({"status": "ok", "action": "store", "existing_id": None, "similarity": 0.2}, DedupResult("store", None, 0.2)),
        ({"status": "unavailable", "reason": "model_not_cached", "fix": "trw-mcp models fetch"}, None),
        ({"status": "invalid", "code": "empty_text", "error": "text is empty"}, None),
    ],
    ids=["verdict", "store-verdict", "no-embedder", "empty-text"],
)
def test_the_answers_that_mean_a_verdict_or_store(answer: dict[str, Any], expected: DedupResult | None) -> None:
    assert _store(answer).similar("project:t", "text", 0.95, 0.85, 10) == expected


@pytest.mark.parametrize(
    "answer",
    [
        {"status": "invalid", "code": "bad_thresholds", "error": "thresholds must lie in [-1, 1]"},
        {"status": "invalid", "error": "namespace 'x' is not valid"},
        {"status": "forbidden", "error": "outside the grant"},
        {"unexpected": "shape"},
    ],
    ids=["bad-thresholds", "bad-namespace", "forbidden", "malformed"],
)
def test_every_other_answer_raises_rather_than_reading_as_no_duplicate(answer: dict[str, Any]) -> None:
    with pytest.raises(ValueError, match="memory_similar refused"):
        _store(answer).similar("project:t", "text", 0.95, 0.85, 10)


def test_vectors_carry_the_daemons_threshold_or_nothing_without_an_embedder() -> None:
    vector_set = _store({"status": "ok", "vectors": {"L1": [1, 0]}, "space": {}, "dup_threshold": 0.93}).vectors(["L1"])

    assert vector_set is not None and (vector_set.vectors, vector_set.dup_threshold) == ({"L1": [1.0, 0.0]}, 0.93)
    assert _store({"status": "unavailable", "reason": "embedder_error"}).vectors(["L1"]) is None
    with pytest.raises(ValueError, match="memory_vectors refused"):
        _store({"status": "forbidden", "error": "outside the grant"}).vectors(["L1"])


@pytest.mark.integration
def test_a_keyword_only_daemon_answers_unavailable_and_the_learn_stores(daemon_checkout: DaemonCheckout) -> None:
    """End to end: the test daemon has no model, so the verdict is "store" and nothing loads in this process."""
    from trw_mcp.state.dedup import dedup_verdict
    from trw_mcp.state.memory_adapter import store_learning

    trw_dir = daemon_checkout.trw_dir
    entries_dir = trw_dir / "learnings" / "entries"
    entries_dir.mkdir(parents=True)
    store_learning(trw_dir, "L-parity-1", "daemon parity summary", "daemon parity detail")

    result = dedup_verdict(
        "a rephrased parity summary", "other words", entries_dir, config=TRWConfig(embeddings_enabled=True)
    )

    assert (result.action, result.existing_id) == ("store", None)
