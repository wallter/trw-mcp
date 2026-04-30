from __future__ import annotations

from pathlib import Path

import pytest

from tests._resources_export_sender_support import (
    _get_learnings_resource,
    _write_learning,
    _writer,
)


class TestLearningsSummaryErrorHandling:
    """Lines 99-100 — bad YAML in entries directory is silently skipped."""

    def test_skips_unreadable_entry(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
        trw_dir = tmp_path / ".trw"
        entries_dir = trw_dir / "learnings" / "entries"
        entries_dir.mkdir(parents=True)

        _write_learning(
            entries_dir,
            "good-entry.yaml",
            {"id": "L-001", "summary": "Good one", "detail": "d", "impact": 0.9},
        )
        bad_file = entries_dir / "bad-entry.yaml"
        bad_file.write_text("!!python/object:os.system [rm -rf /]", encoding="utf-8")

        fn = _get_learnings_resource()
        result = fn()
        assert "TRW Learnings Summary" in result
        assert "Good one" in result

    def test_entry_below_impact_threshold_excluded(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
        trw_dir = tmp_path / ".trw"
        entries_dir = trw_dir / "learnings" / "entries"
        entries_dir.mkdir(parents=True)

        _write_learning(
            entries_dir,
            "low-impact.yaml",
            {"id": "L-002", "summary": "Low impact", "detail": "d", "impact": 0.5},
        )

        fn = _get_learnings_resource()
        result = fn()
        assert "Low impact" not in result


class TestLearningsSummaryPatternsSection:
    """Lines 112-122 — patterns_dir exists branch."""

    def test_patterns_included_in_summary(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
        trw_dir = tmp_path / ".trw"
        (trw_dir / "learnings" / "entries").mkdir(parents=True)

        patterns_dir = trw_dir / "patterns"
        patterns_dir.mkdir()
        _writer.write_yaml(
            patterns_dir / "wave-audit.yaml",
            {"name": "Wave Audit Pattern", "description": "Run 3-wave audit"},
        )

        fn = _get_learnings_resource()
        result = fn()
        assert "Discovered Patterns" in result
        assert "Wave Audit Pattern" in result
        assert "Run 3-wave audit" in result

    def test_patterns_index_yaml_skipped(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
        trw_dir = tmp_path / ".trw"
        (trw_dir / "learnings" / "entries").mkdir(parents=True)

        patterns_dir = trw_dir / "patterns"
        patterns_dir.mkdir()
        _writer.write_yaml(
            patterns_dir / "index.yaml",
            {"name": "index", "description": "should not appear"},
        )
        _writer.write_yaml(
            patterns_dir / "real-pattern.yaml",
            {"name": "Real Pattern", "description": "Should appear"},
        )

        fn = _get_learnings_resource()
        result = fn()
        assert "Real Pattern" in result
        assert "should not appear" not in result

    def test_bad_pattern_file_silently_skipped(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
        trw_dir = tmp_path / ".trw"
        (trw_dir / "learnings" / "entries").mkdir(parents=True)

        patterns_dir = trw_dir / "patterns"
        patterns_dir.mkdir()
        bad = patterns_dir / "corrupt.yaml"
        bad.write_text("!!python/object:os.system [ls]", encoding="utf-8")
        _writer.write_yaml(
            patterns_dir / "good.yaml",
            {"name": "Good Pattern", "description": "Fine"},
        )

        fn = _get_learnings_resource()
        result = fn()
        assert "Good Pattern" in result


class TestLearningsSummaryAnalyticsSection:
    """Lines 127-131 — analytics.yaml exists branch."""

    def test_analytics_section_included(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
        trw_dir = tmp_path / ".trw"
        (trw_dir / "learnings" / "entries").mkdir(parents=True)
        context_dir = trw_dir / "context"
        context_dir.mkdir(parents=True)

        _writer.write_yaml(
            context_dir / "analytics.yaml",
            {
                "sessions_tracked": 42,
                "total_learnings": 100,
                "avg_learnings_per_session": 2.38,
            },
        )

        fn = _get_learnings_resource()
        result = fn()
        assert "Analytics" in result
        assert "42" in result
        assert "100" in result
