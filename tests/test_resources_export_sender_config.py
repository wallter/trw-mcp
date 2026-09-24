from __future__ import annotations

from pathlib import Path

import pytest

from tests._memory_fixtures import FAKE_NAMESPACE
from tests._memory_store_fake import FakeMemoryStore
from tests._resources_export_sender_support import (
    _get_learnings_resource,
    _writer,
)


class TestLearningsSummaryErrorHandling:
    """Lines 99-100 — bad YAML in entries directory is silently skipped.

    ``test_skips_unreadable_entry`` pinned the retired interim in-process
    ``SqliteMemoryStore``'s YAML-to-SQLite backfill silently skipping a corrupt
    ``entries/`` file on first open (``trw_mcp.state._memory_backfill``,
    PRD-CORE-280 slice e3). Neither ``fake_memory_store`` nor ``daemon_checkout``
    reads ``learnings/entries/`` at all, so there is no store-backed route to the
    same behaviour; deleted rather than left BLOCKED-and-failing. The adjacent
    ``test_entry_below_impact_threshold_excluded`` below covers the store-backed
    "good row renders, filtered row does not" shape this test used to check.
    """

    def test_entry_below_impact_threshold_excluded(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_memory_store: FakeMemoryStore
    ) -> None:
        """PRD-CORE-280 slice e1: seeds both rows straight into fake_memory_store
        (bypassing the YAML entries-dir backfill the original relied on), one
        above and one below the resource's min_impact=0.7 cutoff, so the
        assertion still discriminates on the impact-threshold filter itself
        rather than on whether anything reached the store at all. Seeded under
        FAKE_NAMESPACE (the fake fixture's pin), since list_active_learnings
        queries store.list_entries(namespace, ...) with the namespace
        selected_store resolves, not the "default" store.recall scans."""
        monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
        fake_memory_store.put("High impact", FAKE_NAMESPACE, {"entry_id": "L-hi", "detail": "d", "importance": 0.9})
        fake_memory_store.put("Low impact", FAKE_NAMESPACE, {"entry_id": "L-lo", "detail": "d", "importance": 0.5})

        fn = _get_learnings_resource()
        result = fn()
        assert "High impact" in result
        assert "Low impact" not in result


class TestLearningsSummaryPatternsSection:
    """Lines 112-122 — patterns_dir exists branch."""

    def test_patterns_included_in_summary(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_memory_store: FakeMemoryStore
    ) -> None:
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

    def test_patterns_index_yaml_skipped(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_memory_store: FakeMemoryStore
    ) -> None:
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

    def test_bad_pattern_file_silently_skipped(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_memory_store: FakeMemoryStore
    ) -> None:
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
        # The corrupt file carries a YAML deserialization payload
        # (``!!python/object:os.system``). "Skipped" has to mean the payload
        # never reaches the rendered resource either — asserting only that the
        # GOOD pattern survived would also pass if the tag were echoed verbatim
        # into agent-visible output.
        assert "os.system" not in result
        assert "python/object" not in result


class TestLearningsSummaryAnalyticsSection:
    """Lines 127-131 — analytics.yaml exists branch."""

    def test_analytics_section_included(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_memory_store: FakeMemoryStore
    ) -> None:
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
