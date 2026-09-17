"""PRD-FIX-141-FR05 — ``trw_recall`` names each of its count populations.

``total_available=25`` over a 1,346-entry store (learning L-Rikf) is a correct
number for a question nobody asked: it is the BOUNDED PRE-CAP match population,
capped by ``max_results * PREFETCH_MULTIPLIER``, and its own source comment
already says so. The defect was never the value — it was that the response
offered exactly one count and a reader had no way to learn which population it
described.

``total_available`` therefore keeps its meaning and its value (NFR03), and the
two counts it was mistaken for are reported alongside it.
"""

from __future__ import annotations

from pathlib import Path

import pytest


def _trw(project_root: Path) -> Path:
    """The ``.trw`` directory inside the ``tmp_project`` fixture's root."""
    return project_root / ".trw"


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


def test_recall_reports_store_count_over_the_whole_store(tmp_project: Path) -> None:
    """``store_count`` is the inventory — independent of the query and the cap."""
    from trw_mcp.models.config import TRWConfig
    from trw_mcp.tools._recall_impl import execute_recall

    _seed(_trw(tmp_project), 12)

    result = execute_recall("widget", _trw(tmp_project), TRWConfig(), max_results=3)

    assert result["store_count"] == 12
    assert len(result["learnings"]) <= 3


def test_candidate_count_is_the_pre_rank_population(tmp_project: Path) -> None:
    """``candidate_count`` counts what the backend returned before ranking and capping."""
    from trw_mcp.models.config import TRWConfig
    from trw_mcp.tools._recall_impl import execute_recall

    _seed(_trw(tmp_project), 12)

    result = execute_recall("widget", _trw(tmp_project), TRWConfig(), max_results=3)

    assert result["candidate_count"] >= len(result["learnings"])
    assert result["candidate_count"] <= result["store_count"]


def test_total_available_keeps_its_pre_change_meaning(tmp_project: Path) -> None:
    """NFR03: the field a consumer already reads is neither removed nor redefined.

    ``total_available`` is learnings-plus-patterns from the bounded pre-cap set,
    so it is at least the returned count and never the store inventory once the
    fetch cap bites.
    """
    from trw_mcp.models.config import TRWConfig
    from trw_mcp.tools._recall_impl import execute_recall

    _seed(_trw(tmp_project), 12)

    result = execute_recall("widget", _trw(tmp_project), TRWConfig(), max_results=3)

    assert result["total_available"] >= result["total_matches"]
    assert "total_available" in result and "total_matches" in result


def test_store_count_is_omitted_rather_than_zeroed_when_unmeasured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A store that could not be read must not be reported as an inventory of zero."""
    from trw_mcp.models.config import TRWConfig
    from trw_mcp.tools import _recall_impl

    trw_dir = tmp_path / ".trw"
    trw_dir.mkdir()
    monkeypatch.setattr(_recall_impl, "store_entry_count", lambda *a, **k: None)

    result = _recall_impl.execute_recall("widget", trw_dir, TRWConfig())

    assert "store_count" not in result


def test_ultra_compact_stays_a_minimal_payload(tmp_project: Path) -> None:
    """The ultra-compact contract is exactly three keys — the counts do not join it.

    ``tests/test_tools_learning_recall_modes.py`` pins that key set because
    ultra-compact exists to be the cheapest possible response; widening it here
    would have been an unannounced contract change on the surface least able to
    afford one.
    """
    from trw_mcp.models.config import TRWConfig
    from trw_mcp.tools._recall_impl import execute_recall

    _seed(_trw(tmp_project), 4)

    result = execute_recall("widget", _trw(tmp_project), TRWConfig(), ultra_compact=True)

    assert "store_count" not in result
    assert "candidate_count" not in result


def test_session_start_recall_extras_carry_the_store_count(tmp_project: Path) -> None:
    """session_start's ``total_available`` is the RETURNED set; the corpus is separate."""
    from trw_mcp.models.config import TRWConfig
    from trw_mcp.state.persistence import FileStateReader
    from trw_mcp.tools._session_recall_helpers import perform_session_recalls

    _seed(_trw(tmp_project), 7)

    _learnings, _items, extra = perform_session_recalls(
        _trw(tmp_project), "widget", TRWConfig(), FileStateReader(base_dir=_trw(tmp_project))
    )

    assert extra["store_count"] == 7
    assert extra["total_available"] <= extra["store_count"]
