"""Learn-time dedup when the KNN window cannot support a dense verdict (batch 2026-09-19 X3-4).

The daemon's ``memory_similar`` decides whether a window is complete: an
other-space or unknown-provenance hit, or a namespace its census cannot prove to
be in the loaded space, makes it incomplete (``trw-memory/tests/
test_comparable_neighbours.py``). trw-mcp's part, pinned here: an incomplete
window defers to the exhaustive YAML scan, which re-embeds every entry in the
loaded space and so finds a duplicate the window could not; a complete window's
verdict stands; with no loaded space nothing is compared and nothing defers.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from tests._dedup_test_support import write_entry
from tests._embedding_space_support import NEW_SPACE, SpaceProvider
from trw_mcp.models.config import TRWConfig
from trw_mcp.state._store_selection import SimilarHit, SimilarWindow
from trw_mcp.state.dedup import _check_duplicate_via_backend, dedup_verdict
from trw_mcp.state.persistence import FileStateReader, FileStateWriter

_LOADED = "trw_mcp.state._memory_connection.get_initialized_embedder"


class _WindowStore:
    """The store seam answering one KNN window; no row is an exact-content duplicate."""

    def __init__(self, window: SimilarWindow) -> None:
        self.window = window
        self.spaces: list[object] = []

    def similar(self, namespace: str, vector: list[float], space: object, top_k: int) -> SimilarWindow:
        self.spaces.append(space)
        return self.window

    def find_duplicate(self, namespace: str, summary: str, detail: str) -> str | None:
        return None


def _serve(monkeypatch: pytest.MonkeyPatch, window: SimilarWindow) -> _WindowStore:
    store = _WindowStore(window)
    monkeypatch.setattr("trw_mcp.state._store_selection.selected_store", lambda _trw_dir: (store, "project:t"))
    return store


@pytest.mark.parametrize("window_size", [10, 2, 1], ids=["all-other-space", "mixed", "unproven-census"])
def test_an_incomplete_window_defers_to_the_scan_that_finds_the_duplicate(
    tmp_path: Path, reader: FileStateReader, writer: FileStateWriter, monkeypatch: pytest.MonkeyPatch, window_size: int
) -> None:
    entries_dir = tmp_path / ".trw" / "learnings" / "entries"
    entries_dir.mkdir(parents=True)
    write_entry(entries_dir, writer, "L-dup", "cache warmup races the pin", "same finding, other words")
    write_entry(entries_dir, writer, "L-unrelated", "ruff format drift", "nothing alike")
    _serve(monkeypatch, SimilarWindow(window_size, None))

    def embed(text: str) -> list[float]:
        return [0.0, 1.0] if text.startswith("ruff") else [1.0, 0.0]

    with (
        patch(_LOADED, return_value=SpaceProvider(NEW_SPACE)),
        patch("trw_mcp.state.dedup.embed", side_effect=embed),
        patch("trw_mcp.state.dedup._validated_thresholds", return_value=(0.95, 0.85)),
    ):
        result = dedup_verdict(
            "pin races cache warmup", "a rephrasing", entries_dir, reader, config=TRWConfig(embeddings_enabled=True)
        )

    assert (result.action, result.existing_id) == ("skip", "L-dup")


def test_a_complete_window_keeps_the_store_verdict(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression: with every hit comparable the fast path answers, no fallback."""
    store = _serve(monkeypatch, SimilarWindow(1, [SimilarHit("L-new", 1.0, True)]))
    with patch(_LOADED, return_value=SpaceProvider(NEW_SPACE)):
        result = _check_duplicate_via_backend([1.0, 0.0], tmp_path, 0.95, 0.85)

    assert result is not None
    assert (result.action, result.existing_id) == ("skip", "L-new")
    assert store.spaces == [NEW_SPACE]


def test_without_a_loaded_space_nothing_is_compared_and_nothing_defers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _serve(monkeypatch, SimilarWindow(10, []))  # the daemon reports only the window's size
    with patch(_LOADED, return_value=None):
        result = _check_duplicate_via_backend([1.0, 0.0], tmp_path, 0.95, 0.85)

    assert result is not None
    assert (result.action, result.existing_id) == ("store", None)
    assert store.spaces == [None]
