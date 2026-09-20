"""Learn-time dedup when the KNN window mixes embedding spaces (batch 2026-09-19 X3-4).

Before this fix the backend filtered the top-10 window by space and answered from
whatever survived, so a window of other-space vectors answered ``store`` without
comparing anything, and an other-space textual duplicate was never seen. With a
space loaded, any excluded hit defers to the exhaustive YAML fallback, which
re-embeds every entry in the loaded space. A single-space window is trusted only
when the backend's provenance census shows the WHOLE namespace in the loaded
space; an unsupported census, an unknown-provenance row or an other-space row
past the window also defers. The census reads claims, not blob hashes.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tests._dedup_test_support import write_entry
from tests._embedding_space_support import NEW_SPACE, OLD_SPACE, SpaceProvider, stored
from trw_mcp.models.config import TRWConfig
from trw_mcp.state.dedup import _check_duplicate_via_backend, check_duplicate
from trw_mcp.state.persistence import FileStateReader, FileStateWriter

_LOADED = "trw_mcp.state._memory_connection.get_initialized_embedder"
_BACKEND = "trw_mcp.state.memory_adapter.get_backend"

_WINDOW = [(f"L-old{i}", 0.01 * i) for i in range(10)]  # top-10: every hit is other-space


def _backend(records: dict[str, object]) -> MagicMock:
    backend = MagicMock()
    backend.search_vectors.return_value = _WINDOW
    backend.get_vector_records.return_value = records
    backend.find_active_by_content.return_value = None  # not an exact-content duplicate
    backend.vector_space_census.return_value = {NEW_SPACE: len(records)}
    return backend


def test_an_all_other_space_window_defers_to_the_exhaustive_fallback(tmp_path: Path) -> None:
    backend = _backend({entry_id: stored((1.0, 0.0), OLD_SPACE) for entry_id, _ in _WINDOW})
    with patch(_BACKEND, return_value=backend), patch(_LOADED, return_value=SpaceProvider(NEW_SPACE)):
        result = _check_duplicate_via_backend([1.0, 0.0], tmp_path, 0.95, 0.85)

    assert result is None, "a window with no comparable hit is not evidence of 'no duplicate'"


def test_a_loaded_space_duplicate_outside_the_window_is_still_skipped(
    tmp_path: Path, reader: FileStateReader, writer: FileStateWriter
) -> None:
    """End to end through check_duplicate: the fallback re-embeds and finds it."""
    trw_dir = tmp_path / ".trw"
    entries_dir = trw_dir / "learnings" / "entries"
    entries_dir.mkdir(parents=True)
    write_entry(entries_dir, writer, "L-dup", "cache warmup races the pin", "same finding, other words")
    backend = _backend({entry_id: stored((1.0, 0.0), OLD_SPACE) for entry_id, _ in _WINDOW})

    with (
        patch(_BACKEND, return_value=backend),
        patch(_LOADED, return_value=SpaceProvider(NEW_SPACE)),
        patch("trw_mcp.state.dedup.embed", return_value=[1.0, 0.0]),
        patch("trw_mcp.state.dedup._thresholds", return_value=(0.95, 0.85)),
    ):
        result = check_duplicate(
            "pin races cache warmup", "a rephrasing", entries_dir, reader, config=TRWConfig(embeddings_enabled=True)
        )

    assert (result.action, result.existing_id) == ("skip", "L-dup")


def test_a_mixed_window_does_not_answer_from_its_admitted_non_duplicate(
    tmp_path: Path, reader: FileStateReader, writer: FileStateWriter
) -> None:
    """An admitted hit that is not a duplicate must not hide an other-space textual duplicate."""
    trw_dir = tmp_path / ".trw"
    entries_dir = trw_dir / "learnings" / "entries"
    entries_dir.mkdir(parents=True)
    write_entry(entries_dir, writer, "L-unmigrated", "cache warmup races the pin", "old-space vector, same finding")
    write_entry(entries_dir, writer, "L-unrelated", "ruff format drift", "nothing alike")
    backend = _backend({"L-unmigrated": stored((1.0, 0.0), OLD_SPACE), "L-unrelated": stored((0.0, 1.0), NEW_SPACE)})
    backend.search_vectors.return_value = [("L-unmigrated", 0.0), ("L-unrelated", 1.4)]

    def embed(text: str) -> list[float]:
        return [0.0, 1.0] if text.startswith("ruff") else [1.0, 0.0]

    with (
        patch(_BACKEND, return_value=backend),
        patch(_LOADED, return_value=SpaceProvider(NEW_SPACE)),
        patch("trw_mcp.state.dedup.embed", side_effect=embed),
        patch("trw_mcp.state.dedup._thresholds", return_value=(0.95, 0.85)),
    ):
        result = check_duplicate(
            "pin races cache warmup", "a rephrasing", entries_dir, reader, config=TRWConfig(embeddings_enabled=True)
        )

    assert (result.action, result.existing_id) == ("skip", "L-unmigrated")


def test_a_single_space_window_keeps_the_backend_verdict(tmp_path: Path) -> None:
    """Regression: with every hit admitted the fast path answers, no fallback."""
    backend = _backend({"L-new": stored((1.0, 0.0), NEW_SPACE)})
    backend.search_vectors.return_value = [("L-new", 0.0)]
    backend.get.return_value = MagicMock(status=MagicMock(value="active"))
    with patch(_BACKEND, return_value=backend), patch(_LOADED, return_value=SpaceProvider(NEW_SPACE)):
        result = _check_duplicate_via_backend([1.0, 0.0], tmp_path, 0.95, 0.85)

    assert result is not None
    assert (result.action, result.existing_id) == ("skip", "L-new")


def _single_space_window(census: object) -> MagicMock:
    backend = _backend({"L-new": stored((1.0, 0.0), NEW_SPACE)})
    backend.search_vectors.return_value = [("L-new", 0.5)]  # admitted, below both thresholds
    backend.get.return_value = MagicMock(status=MagicMock(value="active"))
    backend.vector_space_census.return_value = census
    return backend


def test_an_other_space_row_past_a_single_space_window_is_still_skipped(
    tmp_path: Path, reader: FileStateReader, writer: FileStateWriter
) -> None:
    """End to end: the window is all loaded-space, but the census shows an old-space row beyond it."""
    trw_dir = tmp_path / ".trw"
    entries_dir = trw_dir / "learnings" / "entries"
    entries_dir.mkdir(parents=True)
    write_entry(entries_dir, writer, "L-far-unmigrated", "cache warmup races the pin", "old-space vector")
    backend = _single_space_window({NEW_SPACE: 1, OLD_SPACE: 1})

    with (
        patch(_BACKEND, return_value=backend),
        patch(_LOADED, return_value=SpaceProvider(NEW_SPACE)),
        patch("trw_mcp.state.dedup.embed", return_value=[1.0, 0.0]),
        patch("trw_mcp.state.dedup._thresholds", return_value=(0.95, 0.85)),
    ):
        result = check_duplicate(
            "pin races cache warmup", "a rephrasing", entries_dir, reader, config=TRWConfig(embeddings_enabled=True)
        )

    assert (result.action, result.existing_id) == ("skip", "L-far-unmigrated")


@pytest.mark.parametrize(
    "census",
    [None, {NEW_SPACE: 1, None: 1}, MagicMock(), {}, {NEW_SPACE: 0}, {NEW_SPACE: True}, {NEW_SPACE: "1"}],
    ids=["unsupported", "unknown-provenance-row", "not-a-mapping", "empty", "zero-count", "bool-count", "str-count"],
)
def test_an_unproven_namespace_defers_even_with_a_single_space_window(tmp_path: Path, census: object) -> None:
    backend = _single_space_window(census)
    with patch(_BACKEND, return_value=backend), patch(_LOADED, return_value=SpaceProvider(NEW_SPACE)):
        assert _check_duplicate_via_backend([1.0, 0.0], tmp_path, 0.95, 0.85) is None


def test_a_census_smaller_than_the_window_proves_nothing(tmp_path: Path) -> None:
    """Two window rows but a census of one: the census missed rows, so it cannot certify them."""
    backend = _backend({"L-a": stored((1.0, 0.0), NEW_SPACE), "L-b": stored((1.0, 0.0), NEW_SPACE)})
    backend.search_vectors.return_value = [("L-a", 0.5), ("L-b", 0.6)]
    backend.vector_space_census.return_value = {NEW_SPACE: 1}
    with patch(_BACKEND, return_value=backend), patch(_LOADED, return_value=SpaceProvider(NEW_SPACE)):
        assert _check_duplicate_via_backend([1.0, 0.0], tmp_path, 0.95, 0.85) is None


def test_without_a_loaded_space_nothing_is_compared_and_nothing_defers(tmp_path: Path) -> None:
    """Unchanged: no provable space means no dense comparison and no fallback scan."""
    backend = _backend({entry_id: stored((1.0, 0.0), OLD_SPACE) for entry_id, _ in _WINDOW})
    with patch(_BACKEND, return_value=backend), patch(_LOADED, return_value=None):
        result = _check_duplicate_via_backend([1.0, 0.0], tmp_path, 0.95, 0.85)

    assert result is not None
    assert (result.action, result.existing_id) == ("store", None)
