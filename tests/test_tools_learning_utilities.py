"""Tests for extraction, collection, search, and success-pattern utilities."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from trw_mcp.models.config import TRWConfig
from trw_mcp.state.analytics import (
    extract_learnings_from_llm,
    extract_learnings_mechanical,
    find_success_patterns,
    is_success_event,
)
from trw_mcp.state.claude_md import collect_context_data, collect_patterns, collect_promotable_learnings
from trw_mcp.state.persistence import FileStateReader, FileStateWriter
from trw_mcp.state.recall_search import search_patterns

from tests._tools_learning_shared import set_project_root

class TestRecallSearch:
    """Unit tests for state.recall_search functions."""

    def test_search_patterns_finds_matching(
        self, tmp_project: Path, reader: FileStateReader, writer: FileStateWriter
    ) -> None:
        """search_patterns returns patterns matching query."""
        patterns_dir = tmp_project / ".trw" / "patterns"
        writer.write_yaml(
            patterns_dir / "p1.yaml",
            {
                "name": "research-map-reduce",
                "description": "3-wave research pattern",
            },
        )
        matches = search_patterns(patterns_dir, ["research"], reader)
        assert len(matches) == 1

class TestAnalyticsExtraction:
    """Unit tests for mechanical learning extraction."""

    def test_extract_learnings_mechanical_errors(self, tmp_project: Path) -> None:
        """extract_learnings_mechanical creates entries from error events."""
        trw_dir = tmp_project / ".trw"
        errors = [{"event": "tool_error", "data": "disk full", "ts": "2026-01-01"}]
        result = extract_learnings_mechanical(errors, [], trw_dir)
        assert len(result) == 1
        assert "Error pattern" in result[0]["summary"]

    def test_extract_learnings_mechanical_repeated_suppressed(self, tmp_project: Path) -> None:
        """extract_learnings_mechanical no longer creates entries from repeated ops (PRD-FIX-021)."""
        trw_dir = tmp_project / ".trw"
        ops = [("git_push", 5)]
        result = extract_learnings_mechanical([], ops, trw_dir)
        assert len(result) == 0  # Repeated-ops suppressed as telemetry noise

    def test_extract_mechanical_repeated_ops_no_entries(self, tmp_project: Path) -> None:
        """extract_learnings_mechanical never creates repeated-op entries (PRD-FIX-021)."""
        trw_dir = tmp_project / ".trw"
        ops = [("git_push", 5)]
        result1 = extract_learnings_mechanical([], ops, trw_dir)
        assert len(result1) == 0
        result2 = extract_learnings_mechanical([], ops, trw_dir)
        assert len(result2) == 0

    def test_extract_mechanical_dedup_error_patterns(self, tmp_project: Path) -> None:
        """extract_learnings_mechanical skips error patterns with existing active entries."""
        trw_dir = tmp_project / ".trw"
        errors = [{"event": "tool_error", "data": "disk full", "ts": "2026-01-01"}]
        # First call creates the entry
        result1 = extract_learnings_mechanical(errors, [], trw_dir)
        assert len(result1) == 1
        # Second call with same error should skip (dedup)
        result2 = extract_learnings_mechanical(errors, [], trw_dir)
        assert len(result2) == 0

    def test_extract_learnings_from_llm_saves_entries(self, tmp_project: Path) -> None:
        """extract_learnings_from_llm persists entries to disk."""
        trw_dir = tmp_project / ".trw"
        items: list[dict[str, Any]] = [
            {"summary": "LLM insight", "detail": "details", "tags": ["llm"], "impact": "0.7"},
        ]
        result = extract_learnings_from_llm(items, trw_dir)
        assert len(result) == 1
        assert result[0]["summary"] == "LLM insight"
        # Verify file was written
        entries_dir = trw_dir / "learnings" / "entries"
        assert len(list(entries_dir.glob("*.yaml"))) >= 1

    def test_extract_learnings_from_llm_filters_telemetry_noise(
        self,
        tmp_project: Path,
    ) -> None:
        """PRD-FIX-021: LLM-generated telemetry noise must be suppressed."""
        trw_dir = tmp_project / ".trw"
        items: list[dict[str, Any]] = [
            {"summary": "Repeated operation: file_modified (85x)", "detail": "noise", "impact": "0.5"},
            {"summary": "Success: reflection_complete (6x)", "detail": "noise", "impact": "0.5"},
            {"summary": "repeated operation: checkpoint (3x)", "detail": "noise", "impact": "0.5"},
            {"summary": "Actual actionable insight", "detail": "real", "tags": ["llm"], "impact": "0.7"},
        ]
        result = extract_learnings_from_llm(items, trw_dir)
        assert len(result) == 1
        assert result[0]["summary"] == "Actual actionable insight"

    def test_extract_learnings_from_llm_normalizes_audit_finding_metadata(
        self,
        tmp_project: Path,
    ) -> None:
        """Audit-tagged LLM learnings persist the FR06-required fields."""
        trw_dir = tmp_project / ".trw"
        items: list[dict[str, Any]] = [
            {
                "summary": "Sprint 90: FR06 audit finding",
                "detail": "Runtime path missing.",
                "tags": ["audit-finding", "PRD-QUAL-056", "test_gap"],
                "impact": "0.8",
            },
        ]

        extract_learnings_from_llm(items, trw_dir)

        entries = sorted((trw_dir / "learnings" / "entries").glob("*.yaml"))
        assert entries
        data = FileStateReader().read_yaml(entries[-1])
        assert data["type"] == "incident"
        assert data["confidence"] == "verified"
        assert data["domain"] == ["testing", "quality"]
        assert data["phase_affinity"] == ["implement", "validate"]

class TestClaudeMdCollection:
    """Unit tests for claude_md collection helpers."""

    def test_collect_promotable_learnings(
        self, tmp_project: Path, reader: FileStateReader, writer: FileStateWriter
    ) -> None:
        """collect_promotable_learnings returns high-impact active entries."""
        config = TRWConfig()
        entries_dir = tmp_project / ".trw" / "learnings" / "entries"
        writer.write_yaml(
            entries_dir / "high.yaml",
            {
                "id": "L-high",
                "summary": "important",
                "status": "active",
                "impact": 0.9,
                "q_observations": 0,
                "q_value": 0.5,
            },
        )
        writer.write_yaml(
            entries_dir / "low.yaml",
            {
                "id": "L-low",
                "summary": "trivial",
                "status": "active",
                "impact": 0.2,
                "q_observations": 0,
                "q_value": 0.1,
            },
        )
        result = collect_promotable_learnings(tmp_project / ".trw", config, reader)
        assert any(d["id"] == "L-high" for d in result)
        assert not any(d["id"] == "L-low" for d in result)

    def test_collect_patterns(self, tmp_project: Path, reader: FileStateReader, writer: FileStateWriter) -> None:
        """collect_patterns returns non-index pattern files."""
        config = TRWConfig()
        patterns_dir = tmp_project / ".trw" / "patterns"
        writer.write_yaml(patterns_dir / "p1.yaml", {"name": "test-pattern"})
        writer.write_yaml(patterns_dir / "index.yaml", {"patterns": []})
        result = collect_patterns(tmp_project / ".trw", config, reader)
        assert len(result) == 1
        assert result[0]["name"] == "test-pattern"

    def test_collect_context_data(self, tmp_project: Path, reader: FileStateReader, writer: FileStateWriter) -> None:
        """collect_context_data returns arch and conv data."""
        config = TRWConfig()
        context_dir = tmp_project / ".trw" / "context"
        writer.write_yaml(context_dir / "architecture.yaml", {"style": "hexagonal"})
        writer.write_yaml(context_dir / "conventions.yaml", {"naming": "snake_case"})
        arch, conv = collect_context_data(tmp_project / ".trw", config, reader)
        assert arch["style"] == "hexagonal"
        assert conv["naming"] == "snake_case"

class TestSuccessPatternDetection:
    """PRD-QUAL-001: Unit tests for success pattern detection in analytics."""

    def test_is_success_event_matches(self) -> None:
        """is_success_event detects success-related event types."""

        assert is_success_event({"event": "shard_complete"}) is True
        assert is_success_event({"event": "phase_gate_passed"}) is True
        assert is_success_event({"event": "tests_success"}) is True
        assert is_success_event({"event": "run_done"}) is True
        assert is_success_event({"event": "task_finished"}) is True
        assert is_success_event({"event": "prd_approved"}) is True
        assert is_success_event({"event": "delivery_complete"}) is True

    def test_is_success_event_rejects(self) -> None:
        """is_success_event rejects non-success event types."""

        assert is_success_event({"event": "error_occurred"}) is False
        assert is_success_event({"event": "shard_failed"}) is False
        assert is_success_event({"event": "phase_enter"}) is False
        assert is_success_event({"event": "run_init"}) is False

    def test_find_success_patterns_aggregates(self) -> None:
        """find_success_patterns aggregates success events by type."""

        events: list[dict[str, Any]] = [
            {"event": "shard_complete", "data": {"shard": "S1"}},
            {"event": "shard_complete", "data": {"shard": "S2"}},
            {"event": "shard_complete", "data": {"shard": "S3"}},
            {"event": "phase_gate_passed", "data": {"phase": "validate"}},
            {"event": "error_occurred", "data": {"msg": "should be ignored"}},
        ]

        patterns = find_success_patterns(events)
        assert len(patterns) >= 1

        shard_pattern = next(
            (p for p in patterns if p["event_type"] == "shard_complete"),
            None,
        )
        assert shard_pattern is not None
        assert shard_pattern["count"] == "3"
        assert "3x" in shard_pattern["summary"]

    def test_find_success_patterns_empty(self) -> None:
        """find_success_patterns returns empty for no success events."""

        events: list[dict[str, Any]] = [
            {"event": "error_occurred"},
            {"event": "phase_enter"},
        ]
        assert find_success_patterns(events) == []

    def test_find_success_patterns_sorted_by_count(self) -> None:
        """Patterns are sorted by count descending."""

        events: list[dict[str, Any]] = [
            {"event": "shard_complete"},
            {"event": "shard_complete"},
            {"event": "shard_complete"},
            {"event": "phase_gate_passed"},
        ]

        patterns = find_success_patterns(events)
        assert len(patterns) >= 2
        counts = [int(p["count"]) for p in patterns]
        assert counts == sorted(counts, reverse=True)

    def test_find_success_patterns_capped(self) -> None:
        """Patterns are capped at config.reflect_max_success_patterns."""
        from trw_mcp.models.config import TRWConfig

        config = TRWConfig()
        events: list[dict[str, Any]] = [{"event": f"success_type_{i}_complete"} for i in range(10)]

        patterns = find_success_patterns(events)
        assert len(patterns) <= config.reflect_max_success_patterns
