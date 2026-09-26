"""Learn-time dedup is the daemon's verdict over text; trw-mcp encodes nothing (PRD-CORE-302 FR01, C1/C4).

The daemon decides KNN versus exhaustive and the skip/merge/store boundaries
(``trw-memory/tests/test_tools_similar.py``). trw-mcp's part, pinned here: it
sends the learning's text with this project's reference-scale thresholds, takes
the verdict as given, stores when the daemon has no embedder, never loads a
model to decide, and lets any other refusal raise rather than read it as "no
duplicate".
"""

from __future__ import annotations

from pathlib import Path

import pytest
from trw_memory.lifecycle.dedup import DedupResult

from trw_mcp.models.config import TRWConfig
from trw_mcp.state.dedup import dedup_verdict


class _VerdictStore:
    """The store seam answering one daemon verdict; no row is an exact-content duplicate."""

    def __init__(self, verdict: DedupResult | None | Exception) -> None:
        self.verdict = verdict
        self.asked: list[tuple[str, str, float, float, int]] = []

    def similar(
        self, namespace: str, text: str, skip_threshold: float, merge_threshold: float, top_k: int
    ) -> DedupResult | None:
        self.asked.append((namespace, text, skip_threshold, merge_threshold, top_k))
        if isinstance(self.verdict, Exception):
            raise self.verdict
        return self.verdict

    def find_duplicate(self, namespace: str, summary: str, detail: str) -> str | None:
        return None


@pytest.fixture(autouse=True)
def _no_model_in_this_process(monkeypatch: pytest.MonkeyPatch) -> None:
    """Any trw-memory embedder construction in the trw-mcp process fails the test."""

    def _refuse(*_a: object, **_k: object) -> None:
        raise AssertionError("trw-mcp constructed an embedder")

    monkeypatch.setattr("trw_memory.embeddings.local.LocalEmbeddingProvider.__init__", _refuse)
    monkeypatch.setattr("trw_memory.embeddings.get_local_embedder", _refuse)


def _serve(monkeypatch: pytest.MonkeyPatch, verdict: DedupResult | None | Exception) -> _VerdictStore:
    store = _VerdictStore(verdict)
    monkeypatch.setattr("trw_mcp.state._store_selection.selected_store", lambda _trw_dir: (store, "project:t"))
    return store


def _entries(tmp_path: Path) -> Path:
    entries_dir = tmp_path / ".trw" / "learnings" / "entries"
    entries_dir.mkdir(parents=True)
    return entries_dir


@pytest.mark.parametrize("action", ["skip", "merge", "store"])
def test_the_daemons_verdict_is_the_learns_verdict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, action: str
) -> None:
    verdict = DedupResult(action, "L1" if action != "store" else None, 0.97)  # type: ignore[arg-type]
    store = _serve(monkeypatch, verdict)
    config = TRWConfig(embeddings_enabled=True, dedup_skip_threshold=0.97, dedup_merge_threshold=0.8)

    result = dedup_verdict("pin races cache warmup", "a rephrasing", _entries(tmp_path), config=config)

    assert result == verdict
    # The project's own policy travels with the text: the daemon's config is process-wide.
    assert store.asked == [("project:t", "pin races cache warmup a rephrasing", 0.97, 0.8, 10)]


def test_no_daemon_embedder_stores(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _serve(monkeypatch, None)

    result = dedup_verdict("s", "d", _entries(tmp_path), config=TRWConfig(embeddings_enabled=True))

    assert (result.action, result.existing_id) == ("store", None)


def test_a_daemon_refusal_raises_instead_of_reading_as_no_duplicate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _serve(monkeypatch, ValueError("memory_similar refused: bad_thresholds"))

    with pytest.raises(ValueError, match="bad_thresholds"):
        dedup_verdict("s", "d", _entries(tmp_path), config=TRWConfig(embeddings_enabled=True))


def test_embeddings_disabled_never_asks_the_daemon(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = _serve(monkeypatch, DedupResult("skip", "L1", 1.0))

    result = dedup_verdict("s", "d", _entries(tmp_path), config=TRWConfig(embeddings_enabled=False))

    assert (result.action, store.asked) == ("store", [])
