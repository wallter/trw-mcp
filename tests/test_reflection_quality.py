"""Tests for PRD-QUAL-012: Reflection Quality + Dead Analytics Revival.

Covers:
- compute_reflection_quality() composite scoring
- compute_jaccard_similarity() dedup detection
- find_duplicate_learnings() pair identification
- auto_prune_excess_entries() overflow handling
- update_analytics_extended() field population
- REVIEW gate advisory check wiring
"""

from __future__ import annotations

from pathlib import Path

import pytest

from trw_mcp.state.analytics import (
    auto_prune_excess_entries,
    compute_jaccard_similarity,
    compute_reflection_quality,
    find_duplicate_learnings,
    update_analytics_extended,
)
from trw_mcp.state.persistence import FileStateReader, FileStateWriter

_reader = FileStateReader()
_writer = FileStateWriter()


# --- Fixtures ---


@pytest.fixture()
def trw_dir(tmp_path: Path) -> Path:
    """Create a minimal .trw/ structure for analytics testing."""
    d = tmp_path / ".trw"
    entries = d / "learnings" / "entries"
    entries.mkdir(parents=True)
    (d / "learnings" / "receipts").mkdir()
    (d / "reflections").mkdir()
    (d / "context").mkdir()
    return d


def _write_learning(
    entries_dir: Path,
    name: str,
    *,
    summary: str = "",
    impact: float = 0.5,
    status: str = "active",
    access_count: int = 0,
    q_observations: int = 0,
    source_type: str = "agent",
    tags: list[str] | None = None,
) -> None:
    """Write a learning entry YAML file."""
    if not summary:
        summary = f"Test learning {name}"
    tag_str = ", ".join(f'"{t}"' for t in (tags or []))
    (entries_dir / f"{name}.yaml").write_text(
        f"id: L-{name}\nsummary: {summary}\ndetail: Detail\n"
        f"status: {status}\nimpact: {impact}\n"
        f"q_observations: {q_observations}\nq_value: 0.5\n"
        f"access_count: {access_count}\nsource_type: {source_type}\n"
        f"source_identity: ''\ntags: [{tag_str}]\n"
        f"created: '2026-02-01'\n",
        encoding="utf-8",
    )


def _write_reflection(
    reflections_dir: Path,
    name: str,
    *,
    new_learnings: list[str] | None = None,
) -> None:
    """Write a reflection YAML file."""
    learnings = new_learnings or []
    learnings_str = ", ".join(f'"{l}"' for l in learnings)
    (reflections_dir / f"{name}.yaml").write_text(
        f"id: R-{name}\nscope: session\n"
        f"timestamp: '2026-02-01T00:00:00Z'\n"
        f"events_analyzed: 5\nnew_learnings: [{learnings_str}]\n"
        f"what_worked: []\nwhat_failed: []\n"
        f"repeated_patterns: []\n",
        encoding="utf-8",
    )


# --- compute_jaccard_similarity ---


class TestJaccardSimilarity:
    """Jaccard similarity computation."""

    def test_identical_strings(self) -> None:
        assert compute_jaccard_similarity("hello world", "hello world") == 1.0

    def test_completely_different(self) -> None:
        assert compute_jaccard_similarity("hello world", "foo bar") == 0.0

    def test_partial_overlap(self) -> None:
        sim = compute_jaccard_similarity("the quick brown fox", "the slow brown dog")
        # Overlap: {the, brown} / Union: {the, quick, brown, fox, slow, dog} = 2/6
        assert abs(sim - 2.0 / 6.0) < 0.01

    def test_empty_strings(self) -> None:
        assert compute_jaccard_similarity("", "") == 1.0

    def test_one_empty(self) -> None:
        assert compute_jaccard_similarity("hello", "") == 0.0

    def test_case_insensitive(self) -> None:
        assert compute_jaccard_similarity("Hello World", "hello world") == 1.0


# --- find_duplicate_learnings ---


class TestFindDuplicates:
    """Duplicate learning detection."""

    def test_no_duplicates(self, trw_dir: Path) -> None:
        entries = trw_dir / "learnings" / "entries"
        _write_learning(entries, "a", summary="error pattern timeout")
        _write_learning(entries, "b", summary="repeated operation build")
        result = find_duplicate_learnings(entries)
        assert result == []

    def test_finds_duplicates(self, trw_dir: Path) -> None:
        entries = trw_dir / "learnings" / "entries"
        _write_learning(entries, "a", summary="error pattern connection timeout in api")
        _write_learning(entries, "b", summary="error pattern connection timeout in api handler")
        result = find_duplicate_learnings(entries, threshold=0.7)
        assert len(result) == 1
        assert result[0][0] == "L-a"  # older
        assert result[0][1] == "L-b"  # newer

    def test_ignores_non_active(self, trw_dir: Path) -> None:
        entries = trw_dir / "learnings" / "entries"
        _write_learning(entries, "a", summary="same words exactly", status="resolved")
        _write_learning(entries, "b", summary="same words exactly")
        result = find_duplicate_learnings(entries)
        assert result == []

    def test_empty_dir(self, tmp_path: Path) -> None:
        result = find_duplicate_learnings(tmp_path / "nonexistent")
        assert result == []


# --- compute_reflection_quality ---


class TestReflectionQuality:
    """Reflection quality composite scoring."""

    def test_empty_state(self, trw_dir: Path) -> None:
        result = compute_reflection_quality(trw_dir)
        assert result["score"] == 0.0
        assert "components" in result
        assert "diagnostics" in result

    def test_with_reflections_and_learnings(self, trw_dir: Path) -> None:
        entries = trw_dir / "learnings" / "entries"
        reflections = trw_dir / "reflections"
        _write_reflection(reflections, "r1", new_learnings=["L-a", "L-b"])
        _write_reflection(reflections, "r2", new_learnings=["L-c"])
        _write_reflection(reflections, "r3", new_learnings=["L-d", "L-e"])
        _write_learning(entries, "a", access_count=3, q_observations=2,
                        tags=["error", "gotcha"])
        _write_learning(entries, "b", access_count=1, source_type="human",
                        tags=["testing"])
        result = compute_reflection_quality(trw_dir)
        score = result["score"]
        assert score > 0.0
        # Should have non-zero components
        components = result["components"]
        assert components["reflection_frequency"] == 1.0  # 3 reflections >= 3
        assert components["productivity"] > 0.0
        assert components["diversity"] > 0.0

    def test_diagnostics_counts(self, trw_dir: Path) -> None:
        entries = trw_dir / "learnings" / "entries"
        _write_learning(entries, "a", access_count=0, q_observations=0)
        _write_learning(entries, "b", access_count=5, q_observations=3)
        result = compute_reflection_quality(trw_dir)
        diag = result["diagnostics"]
        assert diag["total_entries"] == 2
        assert diag["accessed_entries"] == 1
        assert diag["q_activated_entries"] == 1

    def test_score_range(self, trw_dir: Path) -> None:
        result = compute_reflection_quality(trw_dir)
        assert 0.0 <= result["score"] <= 1.0


# --- auto_prune_excess_entries ---


class TestAutoPruneExcess:
    """Auto-pruning when entries exceed threshold."""

    def test_no_action_under_threshold(self, trw_dir: Path) -> None:
        entries = trw_dir / "learnings" / "entries"
        _write_learning(entries, "a")
        _write_learning(entries, "b")
        result = auto_prune_excess_entries(trw_dir, max_entries=10)
        assert result["actions_taken"] == 0

    def test_dry_run_reports_but_no_action(self, trw_dir: Path) -> None:
        entries = trw_dir / "learnings" / "entries"
        # Create 5 entries with identical summaries to trigger dedup
        for i in range(5):
            _write_learning(entries, f"dup{i}", summary="same exact words repeated")
        result = auto_prune_excess_entries(trw_dir, max_entries=3, dry_run=True)
        assert result["actions_taken"] == 0
        # Should still report candidates
        assert len(result["dedup_candidates"]) > 0

    def test_prunes_when_over_threshold(self, trw_dir: Path) -> None:
        entries = trw_dir / "learnings" / "entries"
        for i in range(5):
            _write_learning(entries, f"e{i}", summary=f"unique topic number {i} about {i*2}")
        # Also add some duplicates
        _write_learning(entries, "d1", summary="duplicate summary words exactly here")
        _write_learning(entries, "d2", summary="duplicate summary words exactly here too")
        result = auto_prune_excess_entries(trw_dir, max_entries=3, dry_run=False)
        assert result["actions_taken"] > 0


# --- update_analytics_extended ---


class TestAnalyticsExtended:
    """Extended analytics field population."""

    def test_populates_reflection_count(self, trw_dir: Path) -> None:
        update_analytics_extended(trw_dir, 2, is_reflection=True)
        data = _reader.read_yaml(trw_dir / "context" / "analytics.yaml")
        assert data["reflections_completed"] == 1

    def test_populates_success_rate(self, trw_dir: Path) -> None:
        update_analytics_extended(trw_dir, 1, is_success=True)
        update_analytics_extended(trw_dir, 0, is_success=False)
        data = _reader.read_yaml(trw_dir / "context" / "analytics.yaml")
        assert data["success_rate"] == 0.5
        assert data["total_outcomes"] == 2
        assert data["successful_outcomes"] == 1

    def test_populates_q_learning_activations(self, trw_dir: Path) -> None:
        entries = trw_dir / "learnings" / "entries"
        _write_learning(entries, "a", q_observations=3)
        _write_learning(entries, "b", q_observations=0)
        update_analytics_extended(trw_dir, 0)
        data = _reader.read_yaml(trw_dir / "context" / "analytics.yaml")
        assert data["q_learning_activations"] == 1

    def test_populates_high_impact(self, trw_dir: Path) -> None:
        entries = trw_dir / "learnings" / "entries"
        _write_learning(entries, "a", impact=0.9)
        _write_learning(entries, "b", impact=0.3)
        update_analytics_extended(trw_dir, 0)
        data = _reader.read_yaml(trw_dir / "context" / "analytics.yaml")
        assert data["high_impact_learnings"] == 1

    def test_backward_compatible_with_existing(self, trw_dir: Path) -> None:
        # Pre-populate with existing format
        _writer.write_yaml(
            trw_dir / "context" / "analytics.yaml",
            {"sessions_tracked": 10, "total_learnings": 20, "claude_md_syncs": 5},
        )
        update_analytics_extended(trw_dir, 3, is_reflection=True, is_success=True)
        data = _reader.read_yaml(trw_dir / "context" / "analytics.yaml")
        assert data["sessions_tracked"] == 11
        assert data["total_learnings"] == 23
        assert data["claude_md_syncs"] == 5  # untouched
        assert data["reflections_completed"] == 1
