"""PRD-FIX-141-FR05 — ``trw_recall`` names each of its count populations.

PRD-CORE-294 FR01 replaced ``trw_recall``'s response with the stub/budget shape
({query, learnings, total_matches} + advisories): ``store_count``,
``candidate_count``, ``total_available``, and ``ultra_compact`` no longer exist
on ``execute_recall``'s return value, so the tests that pinned them there are
deleted. ``candidate_count`` survives only as a structlog field on the
``trw_recall_searched`` event (see ``trw_mcp.tools._recall_impl``).

``perform_session_recalls`` (the session_start recall path) is a separate
surface with its own ``extra`` dict that still carries ``store_count`` and
``total_available`` — that coverage is unaffected and kept below.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests._memory_store_fake import FakeMemoryStore


def _trw(project_root: Path) -> Path:
    """The ``.trw`` directory inside the ``tmp_project`` fixture's root."""
    return project_root / ".trw"


@pytest.fixture
def fake_memory_store(monkeypatch: pytest.MonkeyPatch) -> FakeMemoryStore:
    """Override of ``tests._memory_fixtures.fake_memory_store`` pinned to "default".

    These tests seed through ``store_learning`` and then keyword-search the same
    rows, so the fake's ``recall()`` (which only searches "default") needs the
    namespace ``selected_store`` returns to match where the rows were written —
    see ``tests/test_tools_learning_recall_modes.py`` for the same workaround.
    """
    from trw_mcp.state import _store_selection

    store = FakeMemoryStore()
    monkeypatch.setattr(_store_selection, "selected_store", lambda _trw_dir: (store, "default"))
    return store


def _seed(trw_dir: Path, count: int, *, prefix: str = "seed") -> None:
    """Write *count* real learnings through the production adapter."""
    from trw_mcp.state.memory_adapter import store_learning

    for i in range(count):
        store_learning(
            trw_dir,
            f"{prefix}-{i}",
            f"{prefix} entry {i} about widget alignment",
            f"detail for {prefix} entry {i}",
            tags=["widget"],
            impact=0.6,
        )


def test_candidate_count_is_logged_as_structured_telemetry(
    tmp_project: Path, fake_memory_store: FakeMemoryStore
) -> None:
    """``candidate_count`` is no longer a response field: it's a structlog event
    field on ``trw_recall_searched`` (PRD-CORE-236 diagnostics-to-structlog rule)."""
    import structlog.testing

    from trw_mcp.models.config import TRWConfig
    from trw_mcp.tools._recall_impl import execute_recall

    _seed(_trw(tmp_project), 12)

    with structlog.testing.capture_logs() as logs:
        result = execute_recall("widget", _trw(tmp_project), TRWConfig(), max_results=3)

    assert "candidate_count" not in result
    assert "store_count" not in result
    assert "total_available" not in result

    searched = [log for log in logs if log.get("event") == "trw_recall_searched"]
    assert len(searched) == 1
    assert searched[0]["candidate_count"] >= len(result["learnings"])
    assert searched[0]["candidate_count"] <= 12


def test_session_start_recall_extras_carry_the_store_count(
    tmp_project: Path, fake_memory_store: FakeMemoryStore
) -> None:
    """session_start's block counts what it SHOWS; the corpus is ``store_count``, separately."""
    from trw_mcp.models.config import TRWConfig
    from trw_mcp.state.persistence import FileStateReader
    from trw_mcp.tools._session_recall_helpers import perform_session_recalls

    _seed(_trw(tmp_project), 7)

    _learnings, extra = perform_session_recalls(
        _trw(tmp_project), "widget", TRWConfig(), FileStateReader(base_dir=_trw(tmp_project))
    )

    assert extra["store_count"] == 7
    assert len(_learnings) + int(extra.get("learnings_omitted", 0)) <= extra["store_count"]
