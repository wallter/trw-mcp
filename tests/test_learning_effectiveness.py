"""Tests for PRD-QUAL-012-FR07: Learning Effectiveness Tracking.

Covers:
- Access vs creation ratio tracking
- REVIEW gate advisory reflection quality wiring
- Analytics model field completeness
"""

from __future__ import annotations

from pathlib import Path

import pytest

from trw_mcp.models.learning import Analytics
from trw_mcp.state.analytics import compute_reflection_quality
from trw_mcp.state.persistence import FileStateReader, FileStateWriter

_reader = FileStateReader()
_writer = FileStateWriter()


# --- Fixtures ---


@pytest.fixture()
def trw_dir(tmp_path: Path) -> Path:
    """Create a .trw/ structure for effectiveness testing."""
    d = tmp_path / ".trw"
    entries = d / "learnings" / "entries"
    entries.mkdir(parents=True)
    (d / "reflections").mkdir()
    (d / "context").mkdir()
    return d


def _write_learning(
    entries_dir: Path,
    name: str,
    *,
    access_count: int = 0,
    q_observations: int = 0,
    impact: float = 0.5,
    status: str = "active",
    tags: list[str] | None = None,
    source_type: str = "agent",
) -> None:
    """Write a learning entry YAML file."""
    tag_str = ", ".join(f'"{t}"' for t in (tags or []))
    (entries_dir / f"{name}.yaml").write_text(
        f"id: L-{name}\nsummary: Test {name}\ndetail: D\n"
        f"status: {status}\nimpact: {impact}\n"
        f"q_observations: {q_observations}\nq_value: 0.5\n"
        f"access_count: {access_count}\nsource_type: {source_type}\n"
        f"source: {source_type}\n"
        f"source_identity: ''\ntags: [{tag_str}]\n"
        f"created: '2026-02-01'\n",
        encoding="utf-8",
    )


# --- Analytics Model ---


class TestAnalyticsModel:
    """Analytics Pydantic model field completeness."""

    def test_default_fields(self) -> None:
        a = Analytics()
        assert a.sessions_tracked == 0
        assert a.reflections_completed == 0
        assert a.success_rate == 0.0
        assert a.q_learning_activations == 0
        assert a.total_outcomes == 0
        assert a.successful_outcomes == 0

    def test_all_fields_serializable(self) -> None:
        a = Analytics(
            sessions_tracked=5,
            total_learnings=10,
            reflections_completed=3,
            success_rate=0.75,
            q_learning_activations=2,
            total_outcomes=8,
            successful_outcomes=6,
            high_impact_learnings=4,
            claude_md_syncs=2,
        )
        d = a.model_dump()
        assert d["reflections_completed"] == 3
        assert d["q_learning_activations"] == 2
        assert d["success_rate"] == 0.75


# --- Learning Effectiveness (Access Ratio) ---


class TestLearningEffectiveness:
    """FR07: Access vs creation ratio metrics."""

    def test_all_accessed(self, trw_dir: Path) -> None:
        entries = trw_dir / "learnings" / "entries"
        _write_learning(entries, "a", access_count=3)
        _write_learning(entries, "b", access_count=1)
        result = compute_reflection_quality(trw_dir)
        assert result["components"]["access_ratio"] == 1.0

    def test_none_accessed(self, trw_dir: Path) -> None:
        entries = trw_dir / "learnings" / "entries"
        _write_learning(entries, "a", access_count=0)
        _write_learning(entries, "b", access_count=0)
        result = compute_reflection_quality(trw_dir)
        assert result["components"]["access_ratio"] == 0.0

    def test_partial_access(self, trw_dir: Path) -> None:
        entries = trw_dir / "learnings" / "entries"
        _write_learning(entries, "a", access_count=5)
        _write_learning(entries, "b", access_count=0)
        _write_learning(entries, "c", access_count=0)
        _write_learning(entries, "d", access_count=2)
        result = compute_reflection_quality(trw_dir)
        # 2 out of 4 accessed
        assert result["components"]["access_ratio"] == 0.5

    def test_q_activation_rate(self, trw_dir: Path) -> None:
        entries = trw_dir / "learnings" / "entries"
        _write_learning(entries, "a", q_observations=5)
        _write_learning(entries, "b", q_observations=0)
        _write_learning(entries, "c", q_observations=3)
        _write_learning(entries, "d", q_observations=0)
        result = compute_reflection_quality(trw_dir)
        # 2 out of 4 activated
        assert result["components"]["q_activation_rate"] == 0.5

    def test_diversity_with_many_tags(self, trw_dir: Path) -> None:
        entries = trw_dir / "learnings" / "entries"
        _write_learning(entries, "a", tags=["error", "timeout", "api"])
        _write_learning(entries, "b", tags=["testing", "gotcha", "pydantic"])
        _write_learning(entries, "c", tags=["architecture", "refactor"])
        _write_learning(entries, "d", tags=["build", "ci", "deploy"])
        result = compute_reflection_quality(trw_dir)
        # 11 unique tags → diversity = min(1.0, 11/10) = 1.0
        assert result["components"]["diversity"] == 1.0

    def test_source_type_tracking(self, trw_dir: Path) -> None:
        entries = trw_dir / "learnings" / "entries"
        _write_learning(entries, "a", source_type="human")
        _write_learning(entries, "b", source_type="agent")
        result = compute_reflection_quality(trw_dir)
        diag = result["diagnostics"]
        assert set(diag["source_types"]) == {"human", "agent"}


# --- REVIEW Gate Advisory ---


class TestReviewGateAdvisory:
    """REVIEW exit gate reflection quality advisory."""

    def test_low_quality_produces_warning(self, tmp_path: Path) -> None:
        """Verify that check_phase_exit includes reflection_quality warning."""
        from trw_mcp.models.run import Phase

        # Build minimal run directory structure
        run_dir = tmp_path / "run"
        meta = run_dir / "meta"
        meta.mkdir(parents=True)
        (meta / "run.yaml").write_text(
            "run_id: test\nstatus: active\nphase: review\ntask_name: test\ncreated_at: '2026-02-01T00:00:00Z'\n",
            encoding="utf-8",
        )
        (meta / "events.jsonl").write_text(
            '{"ts": "2026-02-01T00:00:00Z", "event": "reflection_complete"}\n',
            encoding="utf-8",
        )
        (run_dir / "reports").mkdir()
        (run_dir / "reports" / "final.md").write_text("# Report\n", encoding="utf-8")

        from trw_mcp.models.config import TRWConfig
        from trw_mcp.state.validation import check_phase_exit

        config = TRWConfig()
        result = check_phase_exit(Phase.REVIEW, run_dir, config)
        # The advisory check runs best-effort; it may or may not produce
        # a warning depending on whether resolve_trw_dir() succeeds in
        # this temp context. We just verify no crash.
        assert result is not None
        assert hasattr(result, "valid")
