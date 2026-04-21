"""L-fovv fix: trw_session_start wildcard recall now unions high-impact
baseline with fresh low-impact learnings so chain-mode link 2+ can see
fresh prior-link learnings even when they default to impact=0.5.

These tests cover the `perform_session_recalls` wildcard branch:
  - old-and-low-impact learnings stay filtered (baseline behavior preserved)
  - fresh-and-low-impact learnings surface via the bypass
  - high-impact learnings always surface regardless of age
  - bypass days=0 disables the bypass (backward compatibility)
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from trw_mcp.models.config import TRWConfig
from trw_mcp.state.persistence import FileStateReader
from trw_mcp.tools._session_recall_helpers import perform_session_recalls


@pytest.fixture
def _config(tmp_path: Path) -> TRWConfig:
    """Fresh config with recall enabled and bypass active."""
    return TRWConfig(
        root_dir=tmp_path,
        recall_max_results=25,
        session_start_recall_enabled=True,
        session_start_recent_bypass_days=7,
        session_start_recent_bypass_min_impact=0.3,
    )


def _entry(
    *,
    entry_id: str,
    impact: float,
    created_days_ago: int,
) -> dict[str, object]:
    """Build a learning dict matching the shape returned by adapter_recall."""
    created = (
        datetime.now(timezone.utc) - timedelta(days=created_days_ago)
    ).date().isoformat()
    return {
        "id": entry_id,
        "summary": f"test learning {entry_id}",
        "tags": [],
        "impact": impact,
        "status": "active",
        "created": created,
    }


def _install_recall_mock(
    monkeypatch: pytest.MonkeyPatch,
    *,
    baseline_results: list[dict[str, object]],
    fresh_results: list[dict[str, object]],
) -> dict[str, list[dict[str, Any]]]:
    """Patch adapter_recall to return different results based on min_impact
    so we can verify the bypass calls both recalls correctly."""
    calls: dict[str, list[dict[str, Any]]] = {"calls": []}

    def fake_recall(
        trw_dir: Path,
        *,
        query: str = "",
        min_impact: float = 0.0,
        max_results: int = 25,
        compact: bool = False,
        **kwargs: Any,
    ) -> list[dict[str, object]]:
        calls["calls"].append(
            {
                "query": query,
                "min_impact": min_impact,
                "max_results": max_results,
                "compact": compact,
            }
        )
        # 0.7+ is baseline; < 0.7 is the bypass recall
        if min_impact >= 0.7:
            return list(baseline_results)
        return list(fresh_results)

    monkeypatch.setattr(
        "trw_mcp.state.memory_adapter.recall_learnings",
        fake_recall,
    )
    return calls


class TestRecencyBypass:
    def test_fresh_low_impact_learning_surfaces(
        self,
        tmp_path: Path,
        _config: TRWConfig,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Canonical L-fovv case: a 1-day-old impact=0.5 learning (like iter-18's
        chain link 1 wrote) must appear in the wildcard recall."""
        baseline: list[dict[str, object]] = []
        fresh = [_entry(entry_id="L-fresh", impact=0.5, created_days_ago=1)]
        _install_recall_mock(
            monkeypatch, baseline_results=baseline, fresh_results=fresh
        )

        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir(parents=True, exist_ok=True)
        reader = FileStateReader()
        learnings, _ar, _extra = perform_session_recalls(
            trw_dir, query="", config=_config, reader=reader
        )

        ids = {str(e.get("id", "")) for e in learnings}
        assert "L-fresh" in ids, (
            f"expected L-fresh in recall but got {ids}. L-fovv fix broken."
        )

    def test_old_low_impact_learning_stays_filtered(
        self,
        tmp_path: Path,
        _config: TRWConfig,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Old (30 days) low-impact (0.5) learnings must NOT bypass the filter —
        only the RECENT low-impact ones get the courtesy."""
        baseline: list[dict[str, object]] = []
        fresh = [_entry(entry_id="L-old", impact=0.5, created_days_ago=30)]
        _install_recall_mock(
            monkeypatch, baseline_results=baseline, fresh_results=fresh
        )

        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir(parents=True, exist_ok=True)
        reader = FileStateReader()
        learnings, _ar, _extra = perform_session_recalls(
            trw_dir, query="", config=_config, reader=reader
        )
        ids = {str(e.get("id", "")) for e in learnings}
        assert "L-old" not in ids, (
            f"bypass should not surface 30-day-old impact=0.5 entries; got {ids}"
        )

    def test_high_impact_always_surfaces_regardless_of_age(
        self,
        tmp_path: Path,
        _config: TRWConfig,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """High-impact (0.9) learnings from the baseline recall always pass —
        the bypass is additive, not replacement."""
        baseline = [_entry(entry_id="L-old-hi", impact=0.9, created_days_ago=60)]
        fresh: list[dict[str, object]] = []
        _install_recall_mock(
            monkeypatch, baseline_results=baseline, fresh_results=fresh
        )

        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir(parents=True, exist_ok=True)
        reader = FileStateReader()
        learnings, _ar, _extra = perform_session_recalls(
            trw_dir, query="", config=_config, reader=reader
        )
        ids = {str(e.get("id", "")) for e in learnings}
        assert "L-old-hi" in ids, (
            f"high-impact baseline must always surface; got {ids}"
        )

    def test_fresh_entries_ordered_before_baseline(
        self,
        tmp_path: Path,
        _config: TRWConfig,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Fresh in-session learnings should be surfaced BEFORE old high-impact
        ones — the freshly-relevant context is most likely to help the current
        task."""
        baseline = [_entry(entry_id="L-old-hi", impact=0.9, created_days_ago=90)]
        fresh = [_entry(entry_id="L-new-lo", impact=0.5, created_days_ago=0)]
        _install_recall_mock(
            monkeypatch, baseline_results=baseline, fresh_results=fresh
        )

        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir(parents=True, exist_ok=True)
        reader = FileStateReader()
        learnings, _ar, _extra = perform_session_recalls(
            trw_dir, query="", config=_config, reader=reader
        )
        ids_order = [str(e.get("id", "")) for e in learnings]
        assert ids_order[0] == "L-new-lo", (
            f"fresh entry should appear first, got order {ids_order}"
        )
        assert "L-old-hi" in ids_order

    def test_bypass_days_zero_disables_bypass(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Backward compat: session_start_recent_bypass_days=0 restores the
        pre-L-fovv behavior (high-impact only)."""
        config = TRWConfig(
            root_dir=tmp_path,
            session_start_recent_bypass_days=0,
        )
        baseline: list[dict[str, object]] = []
        fresh = [_entry(entry_id="L-fresh", impact=0.5, created_days_ago=1)]
        _install_recall_mock(
            monkeypatch, baseline_results=baseline, fresh_results=fresh
        )

        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir(parents=True, exist_ok=True)
        reader = FileStateReader()
        learnings, _ar, _extra = perform_session_recalls(
            trw_dir, query="", config=config, reader=reader
        )
        ids = {str(e.get("id", "")) for e in learnings}
        assert "L-fresh" not in ids, (
            "bypass_days=0 must restore pre-L-fovv filter behavior"
        )

    def test_duplicate_id_not_surfaced_twice(
        self,
        tmp_path: Path,
        _config: TRWConfig,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """If a high-impact fresh entry is in both baseline and fresh lists,
        it should appear only once."""
        entry = _entry(entry_id="L-both", impact=0.8, created_days_ago=1)
        baseline = [entry]
        fresh = [entry]
        _install_recall_mock(
            monkeypatch, baseline_results=baseline, fresh_results=fresh
        )

        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir(parents=True, exist_ok=True)
        reader = FileStateReader()
        learnings, _ar, _extra = perform_session_recalls(
            trw_dir, query="", config=_config, reader=reader
        )
        ids = [str(e.get("id", "")) for e in learnings]
        assert ids.count("L-both") == 1, (
            f"duplicate id should dedupe; got {ids}"
        )

    def test_bypass_respects_effective_max(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Union must not exceed recall_max_results — prevents unbounded
        growth in token budget."""
        config = TRWConfig(
            root_dir=tmp_path,
            recall_max_results=5,
            session_start_recent_bypass_days=7,
        )
        baseline = [
            _entry(entry_id=f"L-old-{i}", impact=0.9, created_days_ago=30)
            for i in range(5)
        ]
        fresh = [
            _entry(entry_id=f"L-fresh-{i}", impact=0.5, created_days_ago=1)
            for i in range(10)
        ]
        _install_recall_mock(
            monkeypatch, baseline_results=baseline, fresh_results=fresh
        )

        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir(parents=True, exist_ok=True)
        reader = FileStateReader()
        learnings, _ar, _extra = perform_session_recalls(
            trw_dir, query="", config=config, reader=reader
        )
        assert len(learnings) <= 5, (
            f"union exceeded recall_max_results=5; got {len(learnings)}"
        )
