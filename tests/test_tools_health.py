"""Tests for PRD-CORE-027: Flywheel Health Diagnostic Tool.

Covers:
- Learning metrics aggregation (Q-learning, access, source attribution)
- Event stream health scanning
- Recall receipt counting
- Health assessment (go/caution/blocked)
- HealthReport model
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from trw_mcp.models.health import HealthReport
from trw_mcp.tools.health import (
    _assess,
    _count_recall_receipts,
    _scan_events,
    _scan_learnings,
    compute_health,
)


# --- Fixtures ---


@pytest.fixture()
def trw_dir(tmp_path: Path) -> Path:
    """Create a minimal .trw/ structure."""
    d = tmp_path / ".trw"
    entries = d / "learnings" / "entries"
    entries.mkdir(parents=True)
    (d / "learnings" / "receipts").mkdir()
    return d


@pytest.fixture()
def run_dir(tmp_path: Path) -> Path:
    """Create a minimal run directory."""
    d = tmp_path / "docs" / "task" / "runs" / "20260211T120000Z-test"
    meta = d / "meta"
    meta.mkdir(parents=True)
    (meta / "events.jsonl").write_text("", encoding="utf-8")
    return d


def _write_learning(
    entries_dir: Path,
    name: str,
    *,
    impact: float = 0.5,
    status: str = "active",
    q_observations: int = 0,
    access_count: int = 0,
    source_type: str = "agent",
) -> None:
    """Write a learning entry YAML file."""
    (entries_dir / f"{name}.yaml").write_text(
        f"id: L-{name}\nsummary: Test {name}\ndetail: Detail\n"
        f"status: {status}\nimpact: {impact}\n"
        f"q_observations: {q_observations}\nq_value: 0.5\n"
        f"access_count: {access_count}\nsource_type: {source_type}\n"
        f"source_identity: ''\ntags: []\n",
        encoding="utf-8",
    )


# --- _scan_learnings ---


class TestScanLearnings:
    """Learning metrics aggregation."""

    def test_empty_dir(self, trw_dir: Path) -> None:
        entries = trw_dir / "learnings" / "entries"
        result = _scan_learnings(entries)
        assert result["total"] == 0

    def test_counts_total_and_active(self, trw_dir: Path) -> None:
        entries = trw_dir / "learnings" / "entries"
        _write_learning(entries, "a", status="active")
        _write_learning(entries, "b", status="resolved")
        _write_learning(entries, "c", status="active")
        result = _scan_learnings(entries)
        assert result["total"] == 3
        assert result["active"] == 2

    def test_high_impact_count(self, trw_dir: Path) -> None:
        entries = trw_dir / "learnings" / "entries"
        _write_learning(entries, "a", impact=0.8)
        _write_learning(entries, "b", impact=0.3)
        _write_learning(entries, "c", impact=0.9)
        result = _scan_learnings(entries)
        assert result["high_impact"] == 2

    def test_q_activations(self, trw_dir: Path) -> None:
        entries = trw_dir / "learnings" / "entries"
        _write_learning(entries, "a", q_observations=3)
        _write_learning(entries, "b", q_observations=0)
        _write_learning(entries, "c", q_observations=5)
        result = _scan_learnings(entries)
        assert result["q_activations"] == 2
        assert result["q_avg_observations"] == 4.0  # (3+5)/2

    def test_access_metrics(self, trw_dir: Path) -> None:
        entries = trw_dir / "learnings" / "entries"
        _write_learning(entries, "a", access_count=5)
        _write_learning(entries, "b", access_count=0)
        _write_learning(entries, "c", access_count=3)
        result = _scan_learnings(entries)
        assert result["access_total"] == 8
        assert result["entries_never_accessed"] == 1

    def test_source_attribution(self, trw_dir: Path) -> None:
        entries = trw_dir / "learnings" / "entries"
        _write_learning(entries, "a", source_type="human")
        _write_learning(entries, "b", source_type="agent")
        _write_learning(entries, "c", source_type="")
        result = _scan_learnings(entries)
        assert result["source_human"] == 1
        assert result["source_agent"] == 1
        assert result["source_unset"] == 1

    def test_nonexistent_dir(self, tmp_path: Path) -> None:
        result = _scan_learnings(tmp_path / "nonexistent")
        assert result["total"] == 0


# --- _scan_events ---


class TestScanEvents:
    """Event stream health scanning."""

    def test_empty_events(self, run_dir: Path) -> None:
        result = _scan_events(run_dir)
        assert result["events_total"] == 0

    def test_counts_events(self, run_dir: Path) -> None:
        events_path = run_dir / "meta" / "events.jsonl"
        events = [
            {"ts": "2026-01-01T00:00:00Z", "event": "run_init"},
            {"ts": "2026-01-01T00:01:00Z", "event": "file_modified"},
            {"ts": "2026-01-01T00:02:00Z", "event": "file_modified"},
            {"ts": "2026-01-01T00:03:00Z", "event": "reflection_complete"},
        ]
        events_path.write_text(
            "\n".join(json.dumps(e) for e in events) + "\n",
            encoding="utf-8",
        )
        result = _scan_events(run_dir)
        assert result["events_total"] == 4
        assert result["event_type_distribution"]["file_modified"] == 2
        assert result["reflections_found"] == 1

    def test_no_run_dir(self) -> None:
        result = _scan_events(None)
        assert result["events_total"] == 0

    def test_counts_syncs(self, run_dir: Path) -> None:
        events_path = run_dir / "meta" / "events.jsonl"
        events_path.write_text(
            json.dumps({"ts": "t", "event": "claude_md_synced"}) + "\n",
            encoding="utf-8",
        )
        result = _scan_events(run_dir)
        assert result["claude_md_syncs_found"] == 1


# --- _count_recall_receipts ---


class TestCountRecallReceipts:
    """Recall receipt counting."""

    def test_counts_receipts(self, trw_dir: Path) -> None:
        receipts = trw_dir / "learnings" / "receipts"
        (receipts / "receipt-001.yaml").write_text("x: 1\n", encoding="utf-8")
        (receipts / "receipt-002.yaml").write_text("x: 2\n", encoding="utf-8")
        assert _count_recall_receipts(trw_dir) == 2

    def test_no_receipts_dir(self, tmp_path: Path) -> None:
        d = tmp_path / ".trw"
        d.mkdir()
        assert _count_recall_receipts(d) == 0


# --- _assess ---


class TestAssess:
    """Health assessment logic."""

    def test_go_when_healthy(self) -> None:
        report = HealthReport(
            q_activations=5,
            events_total=10,
            total_learnings=20,
            entries_never_accessed=5,
            reflections_found=2,
            high_impact_learnings=3,
            source_unset=2,
        )
        rec, issues = _assess(report)
        assert rec == "go"
        assert len(issues) == 0

    def test_caution_with_one_issue(self) -> None:
        report = HealthReport(
            q_activations=0,
            events_total=10,
            total_learnings=5,
            reflections_found=1,
        )
        rec, issues = _assess(report)
        assert rec == "caution"
        assert any("Q-learning" in i for i in issues)

    def test_blocked_with_many_issues(self) -> None:
        report = HealthReport(
            q_activations=0,
            events_total=0,
            total_learnings=15,
            entries_never_accessed=15,
            reflections_found=0,
            high_impact_learnings=0,
            source_unset=10,
        )
        rec, issues = _assess(report)
        assert rec == "blocked"
        assert len(issues) >= 3

    def test_no_reflection_with_events(self) -> None:
        report = HealthReport(
            q_activations=3,
            events_total=10,
            reflections_found=0,
            total_learnings=5,
        )
        rec, issues = _assess(report)
        assert any("reflection" in i.lower() for i in issues)


# --- compute_health ---


class TestComputeHealth:
    """Full health computation integration."""

    def test_computes_report(self, trw_dir: Path) -> None:
        entries = trw_dir / "learnings" / "entries"
        _write_learning(entries, "a", impact=0.8, q_observations=2, access_count=3)
        _write_learning(entries, "b", impact=0.3, source_type="human")
        report = compute_health(trw_dir)
        assert report.total_learnings == 2
        assert report.q_activations == 1
        assert report.source_human == 1
        assert report.recommendation in ("go", "caution", "blocked")

    def test_with_run_dir(self, trw_dir: Path, run_dir: Path) -> None:
        events_path = run_dir / "meta" / "events.jsonl"
        events_path.write_text(
            json.dumps({"ts": "t", "event": "file_modified"}) + "\n",
            encoding="utf-8",
        )
        report = compute_health(trw_dir, run_dir)
        assert report.events_total == 1
