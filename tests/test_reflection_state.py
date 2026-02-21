"""Tests for state/reflection.py — reflection pipeline functions.

Covers collect_reflection_inputs, generate_reflection_learnings,
create_reflection_record, and helper functions.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from trw_mcp.state.persistence import FileStateWriter
from trw_mcp.state.reflection import (
    ReflectionInputs,
    _build_repeated_patterns,
    _build_what_failed,
    _build_what_worked,
    collect_reflection_inputs,
    create_reflection_record,
    generate_reflection_learnings,
)


@pytest.fixture()
def trw_dir(tmp_path: Path) -> Path:
    """Create a minimal .trw/ structure."""
    d = tmp_path / ".trw"
    (d / "learnings" / "entries").mkdir(parents=True)
    (d / "learnings" / "receipts").mkdir()
    (d / "reflections").mkdir()
    (d / "context").mkdir()
    return d


@pytest.fixture()
def run_dir(tmp_path: Path) -> Path:
    """Create a run directory with events."""
    writer = FileStateWriter()
    rd = tmp_path / "runs" / "test-run"
    (rd / "meta").mkdir(parents=True)
    writer.write_yaml(rd / "meta" / "run.yaml", {
        "run_id": "test-run-001",
        "task": "test",
        "phase": "implement",
        "status": "active",
    })
    writer.append_jsonl(rd / "meta" / "events.jsonl", {
        "ts": "2026-02-16T00:00:00Z",
        "event": "run_init",
        "data": {"task": "test"},
    })
    writer.append_jsonl(rd / "meta" / "events.jsonl", {
        "ts": "2026-02-16T00:01:00Z",
        "event": "error",
        "data": {"message": "import failed"},
    })
    writer.append_jsonl(rd / "meta" / "events.jsonl", {
        "ts": "2026-02-16T00:02:00Z",
        "event": "phase_transition",
        "data": {"from": "research", "to": "plan"},
    })
    return rd


class TestCollectReflectionInputs:
    """Tests for collect_reflection_inputs."""

    def test_with_run_path(self, trw_dir: Path, run_dir: Path) -> None:
        result = collect_reflection_inputs(str(run_dir), trw_dir)
        assert isinstance(result, ReflectionInputs)
        assert len(result.events) >= 3
        assert result.run_id == "test-run-001"
        assert len(result.error_events) >= 1
        assert len(result.phase_transitions) >= 1

    def test_without_run_path(self, trw_dir: Path) -> None:
        result = collect_reflection_inputs(None, trw_dir)
        assert isinstance(result, ReflectionInputs)
        assert result.events == []
        assert result.run_id is None
        assert result.error_events == []

    def test_nonexistent_run_path(self, trw_dir: Path) -> None:
        result = collect_reflection_inputs("/nonexistent/path", trw_dir)
        assert result.events == []
        assert result.run_id is None


class TestGenerateReflectionLearnings:
    """Tests for generate_reflection_learnings (mechanical fallback)."""

    def test_empty_inputs(self, trw_dir: Path) -> None:
        inputs = ReflectionInputs(
            events=[], run_id=None, error_events=[], phase_transitions=[],
            repeated_ops=[], success_patterns=[], tool_sequences=[],
            validated_learnings=[],
        )
        learnings, llm_used, positive = generate_reflection_learnings(inputs, trw_dir)
        assert llm_used is False
        assert isinstance(learnings, list)
        assert positive == 0

    def test_with_errors_produces_learnings(self, trw_dir: Path) -> None:
        inputs = ReflectionInputs(
            events=[{"event": "error", "data": {"message": "test error"}}],
            run_id=None,
            error_events=[{"event": "error", "data": {"message": "test error"}}],
            phase_transitions=[],
            repeated_ops=[("checkpoint", 5)],
            success_patterns=[],
            tool_sequences=[],
            validated_learnings=[],
        )
        learnings, llm_used, positive = generate_reflection_learnings(inputs, trw_dir)
        assert llm_used is False
        assert len(learnings) >= 1

    def test_success_patterns_no_longer_create_learnings(self, trw_dir: Path) -> None:
        """PRD-FIX-021: success patterns are analytics data only — not learnings."""
        inputs = ReflectionInputs(
            events=[], run_id=None, error_events=[], phase_transitions=[],
            repeated_ops=[], tool_sequences=[], validated_learnings=[],
            success_patterns=[
                {"summary": "Success: build_check (3x)", "detail": "good", "count": "3"},
            ],
        )
        learnings, _, positive = generate_reflection_learnings(inputs, trw_dir)
        assert positive == 0
        assert all(
            not l["summary"].startswith("Success:") for l in learnings
        )


class TestCreateReflectionRecord:
    """Tests for create_reflection_record."""

    def test_creates_reflection(self) -> None:
        inputs = ReflectionInputs(
            events=[{"event": "run_init"}] * 5,
            run_id="test-run",
            error_events=[{"event": "error"}],
            phase_transitions=[{"event": "phase_transition"}],
            repeated_ops=[("checkpoint", 4)],
            success_patterns=[{"summary": "worked well"}],
            tool_sequences=[],
            validated_learnings=[],
        )
        new_learnings = [{"id": "L-test001", "summary": "test"}]
        reflection = create_reflection_record(inputs, new_learnings, "session")
        assert reflection.run_id == "test-run"
        assert reflection.scope == "session"
        assert reflection.events_analyzed == 5
        assert "L-test001" in reflection.new_learnings
        assert len(reflection.what_worked) >= 1
        assert len(reflection.what_failed) >= 1
        assert len(reflection.repeated_patterns) >= 1


class TestHelpers:
    """Tests for helper functions."""

    def test_build_what_worked(self) -> None:
        result = _build_what_worked(
            [{"event": "phase_transition"}],
            [{"summary": "success"}],
        )
        assert len(result) == 2

    def test_build_what_failed(self) -> None:
        result = _build_what_failed([{"event": "error1"}, {"event": "error2"}])
        assert len(result) == 2

    def test_build_repeated_patterns(self) -> None:
        result = _build_repeated_patterns([("op1", 3), ("op2", 5)])
        assert result == ["op1 (3x)", "op2 (5x)"]

    def test_build_repeated_patterns_caps_at_max(self) -> None:
        ops = [(f"op{i}", i) for i in range(10)]
        result = _build_repeated_patterns(ops)
        assert len(result) == 3  # _MAX_REPEATED_OPS = 3
