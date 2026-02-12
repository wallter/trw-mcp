"""Tests for Sprint 4 Track C — CORE-014 atomic writes + QUAL-001 success patterns.

PRD-CORE-014 Enhancements: Verify critical file writes use atomic persistence.
PRD-QUAL-001: Verify trw_reflect extracts success patterns alongside errors.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from trw_mcp.models.config import TRWConfig
from trw_mcp.state.persistence import FileStateReader, FileStateWriter

_CFG = TRWConfig()


@pytest.fixture(autouse=True)
def set_project_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Set TRW_PROJECT_ROOT to temp directory for all tests."""
    monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
    import trw_mcp.tools.learning as learn_mod

    monkeypatch.setattr(learn_mod, "_config", learn_mod.TRWConfig())
    return tmp_path


def _get_learning_tools() -> dict[str, object]:
    """Create fresh server and return learning tool map."""
    from fastmcp import FastMCP
    from trw_mcp.tools.learning import register_learning_tools

    srv = FastMCP("test")
    register_learning_tools(srv)
    return {t.name: t for t in srv._tool_manager._tools.values()}


def _entries_dir(root: Path) -> Path:
    """Build entries directory path from config."""
    return root / _CFG.trw_dir / _CFG.learnings_dir / _CFG.entries_dir


# ---------------------------------------------------------------------------
# PRD-CORE-014 Enhancements: Atomic Write Tests
# ---------------------------------------------------------------------------


class TestScriptSaveAtomicWrite:
    """PRD-CORE-014: trw_script_save uses atomic writes via _writer."""

    def test_script_save_uses_atomic_write(self, tmp_path: Path) -> None:
        """trw_script_save delegates to _writer.write_text (atomic)."""
        tools = _get_learning_tools()

        with patch(
            "trw_mcp.state.scripts._writer.write_text", wraps=FileStateWriter().write_text,
        ) as mock_write:
            tools["trw_script_save"].fn(
                name="atomic-test",
                content="#!/bin/bash\necho hello",
                description="Test atomic write",
            )
            # Verify _writer.write_text was called (not Path.write_text)
            assert mock_write.call_count >= 1
            call_args = mock_write.call_args
            assert call_args is not None
            written_path = call_args[0][0]
            assert "atomic-test" in str(written_path)

    def test_script_save_content_correct(self, tmp_path: Path) -> None:
        """Written script has correct content after atomic write."""
        tools = _get_learning_tools()
        content = "#!/usr/bin/env python3\nprint('hello')"
        tools["trw_script_save"].fn(
            name="content-check",
            content=content,
            description="Verify content",
            language="python",
        )

        script_path = tmp_path / _CFG.trw_dir / _CFG.scripts_dir / "content-check.py"
        assert script_path.exists()
        assert script_path.read_text(encoding="utf-8") == content

    def test_script_save_no_partial_on_interrupt(self, tmp_path: Path) -> None:
        """If write fails, no partial file remains."""
        from trw_mcp.state.persistence import FileStateWriter

        tools = _get_learning_tools()

        # First, create the script normally
        tools["trw_script_save"].fn(
            name="interrupt-test",
            content="original",
            description="Original content",
        )
        script_path = tmp_path / _CFG.trw_dir / _CFG.scripts_dir / "interrupt-test.sh"
        assert script_path.read_text(encoding="utf-8") == "original"

        # Simulate interrupted write — the atomic writer should protect us
        # by writing to temp then renaming, so original stays intact on error
        original_write_text = FileStateWriter.write_text

        def failing_write(self: FileStateWriter, path: Path, content: str) -> None:
            """Raise after starting the write process."""
            if "interrupt-test" in str(path):
                raise OSError("Simulated disk full")
            original_write_text(self, path, content)

        with patch.object(FileStateWriter, "write_text", failing_write):
            with pytest.raises(Exception):
                tools["trw_script_save"].fn(
                    name="interrupt-test",
                    content="corrupted",
                    description="Should not be written",
                )

        # Original file should be unchanged
        assert script_path.read_text(encoding="utf-8") == "original"


class TestClaudeMdSyncAtomicWrite:
    """PRD-CORE-014: merge_trw_section uses atomic writes via _writer."""

    def test_claude_md_sync_uses_atomic_write(self, tmp_path: Path) -> None:
        """trw_claude_md_sync uses _writer.write_text for CLAUDE.md."""
        tools = _get_learning_tools()

        tools["trw_learn"].fn(
            summary="Atomic claude md test learning",
            detail="Triggers sync",
            impact=0.9,
        )

        with patch(
            "trw_mcp.state.claude_md._writer.write_text",
            wraps=FileStateWriter().write_text,
        ) as mock_write:
            result = tools["trw_claude_md_sync"].fn(scope="root")
            assert result["status"] == "synced"
            assert mock_write.call_count >= 1


class TestPrdCreateAtomicWrite:
    """PRD-CORE-014: trw_prd_create uses atomic writes via _writer."""

    def test_prd_create_uses_atomic_write(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """trw_prd_create delegates file creation to _writer.write_text."""
        import trw_mcp.tools.requirements as req_mod

        monkeypatch.setattr(req_mod, "_config", TRWConfig())

        from fastmcp import FastMCP
        from trw_mcp.tools.requirements import register_requirements_tools

        srv = FastMCP("test")
        register_requirements_tools(srv)
        tools = {t.name: t for t in srv._tool_manager._tools.values()}

        # Create prds directory
        prds_dir = tmp_path / Path(_CFG.prds_relative_path)
        prds_dir.mkdir(parents=True)
        (tmp_path / _CFG.trw_dir).mkdir(parents=True, exist_ok=True)

        with patch(
            "trw_mcp.tools.requirements._writer.write_text",
            wraps=FileStateWriter().write_text,
        ) as mock_write:
            result = tools["trw_prd_create"].fn(
                input_text="Test atomic write for PRD creation",
                category="FIX",
                priority="P2",
            )
            assert result["prd_id"].startswith("PRD-FIX-")
            assert mock_write.call_count >= 1


class TestFindingToPrdAtomicWrite:
    """PRD-CORE-014: trw_finding_to_prd uses atomic writes."""

    def test_finding_to_prd_uses_atomic_write(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Finding-to-PRD conversion uses _writer.write_text."""
        import trw_mcp.tools.findings as find_mod

        monkeypatch.setattr(find_mod, "_config", TRWConfig())

        from fastmcp import FastMCP
        from trw_mcp.tools.findings import register_findings_tools
        from trw_mcp.tools.orchestration import register_orchestration_tools

        srv = FastMCP("test")
        register_orchestration_tools(srv)
        register_findings_tools(srv)
        tools = {t.name: t for t in srv._tool_manager._tools.values()}

        # Create a run
        init_result = tools["trw_init"].fn(task_name="atomic-finding-test")
        run_path = init_result["run_path"]

        # Create prds directory
        prds_dir = tmp_path / Path(_CFG.prds_relative_path)
        prds_dir.mkdir(parents=True, exist_ok=True)

        # Register a finding
        reg_result = tools["trw_finding_register"].fn(
            summary="Critical atomic finding",
            detail="Tests atomic write on conversion",
            severity="high",
            run_path=run_path,
        )

        with patch(
            "trw_mcp.tools.findings._writer.write_text",
            wraps=FileStateWriter().write_text,
        ) as mock_write:
            result = tools["trw_finding_to_prd"].fn(
                finding_id=reg_result["finding_id"],
                run_path=run_path,
                category="FIX",
            )
            assert result["prd_id"].startswith("PRD-FIX-")
            assert mock_write.call_count >= 1


class TestShardContextFields:
    """PRD-CORE-014: trw_shard_context returns complete fields."""

    def test_shard_context_with_active_run(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """trw_shard_context returns context for an active run."""
        from fastmcp import FastMCP
        from trw_mcp.tools.orchestration import register_orchestration_tools

        srv = FastMCP("test")
        register_orchestration_tools(srv)
        tools = {t.name: t for t in srv._tool_manager._tools.values()}

        init_result = tools["trw_init"].fn(task_name="shard-ctx-test")
        run_path = init_result["run_path"]

        result = tools["trw_shard_context"].fn(
            run_path=run_path,
            shard_id="S1",
        )
        assert result["shard_id"] == "S1"
        assert "run_path" in result
        assert "trw_dir" in result

    def test_shard_context_fields_complete(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """trw_shard_context includes all expected fields."""
        from fastmcp import FastMCP
        from trw_mcp.tools.orchestration import register_orchestration_tools

        srv = FastMCP("test")
        register_orchestration_tools(srv)
        tools = {t.name: t for t in srv._tool_manager._tools.values()}

        init_result = tools["trw_init"].fn(task_name="shard-fields-test")
        run_path = init_result["run_path"]

        result = tools["trw_shard_context"].fn(
            run_path=run_path,
            shard_id="S2",
        )

        expected_keys = {"shard_id", "run_path", "trw_dir", "scratch_path", "tool_guidance"}
        assert expected_keys.issubset(set(result.keys()))


# ---------------------------------------------------------------------------
# PRD-QUAL-001: Success Pattern Extraction Tests
# ---------------------------------------------------------------------------


class TestSuccessPatternDetection:
    """PRD-QUAL-001: Unit tests for success pattern detection in analytics."""

    def test_is_success_event_matches(self) -> None:
        """is_success_event detects success-related event types."""
        from trw_mcp.state.analytics import is_success_event

        assert is_success_event({"event": "shard_complete"}) is True
        assert is_success_event({"event": "phase_gate_passed"}) is True
        assert is_success_event({"event": "tests_success"}) is True
        assert is_success_event({"event": "run_done"}) is True
        assert is_success_event({"event": "task_finished"}) is True
        assert is_success_event({"event": "prd_approved"}) is True
        assert is_success_event({"event": "delivery_complete"}) is True

    def test_is_success_event_rejects(self) -> None:
        """is_success_event rejects non-success event types."""
        from trw_mcp.state.analytics import is_success_event

        assert is_success_event({"event": "error_occurred"}) is False
        assert is_success_event({"event": "shard_failed"}) is False
        assert is_success_event({"event": "phase_enter"}) is False
        assert is_success_event({"event": "run_init"}) is False

    def test_find_success_patterns_aggregates(self) -> None:
        """find_success_patterns aggregates success events by type."""
        from trw_mcp.state.analytics import find_success_patterns

        events: list[dict[str, object]] = [
            {"event": "shard_complete", "data": {"shard": "S1"}},
            {"event": "shard_complete", "data": {"shard": "S2"}},
            {"event": "shard_complete", "data": {"shard": "S3"}},
            {"event": "phase_gate_passed", "data": {"phase": "validate"}},
            {"event": "error_occurred", "data": {"msg": "should be ignored"}},
        ]

        patterns = find_success_patterns(events)
        assert len(patterns) >= 1

        # shard_complete should appear with count 3
        shard_pattern = next(
            (p for p in patterns if p["event_type"] == "shard_complete"), None,
        )
        assert shard_pattern is not None
        assert shard_pattern["count"] == "3"
        assert "3x" in shard_pattern["summary"]

    def test_find_success_patterns_empty(self) -> None:
        """find_success_patterns returns empty for no success events."""
        from trw_mcp.state.analytics import find_success_patterns

        events: list[dict[str, object]] = [
            {"event": "error_occurred"},
            {"event": "phase_enter"},
        ]
        assert find_success_patterns(events) == []

    def test_find_success_patterns_sorted_by_count(self) -> None:
        """Patterns are sorted by count descending."""
        from trw_mcp.state.analytics import find_success_patterns

        events: list[dict[str, object]] = [
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
        """Patterns are capped at reflect_max_success_patterns config value."""
        from trw_mcp.state.analytics import find_success_patterns

        # Create events with many distinct success types
        events: list[dict[str, object]] = []
        for i in range(10):
            events.append({"event": f"success_type_{i}_complete"})

        patterns = find_success_patterns(events)
        assert len(patterns) <= _CFG.reflect_max_success_patterns


class TestReflectSuccessPatterns:
    """PRD-QUAL-001: trw_reflect extracts success patterns from events."""

    def test_reflect_extracts_success_patterns(self, tmp_path: Path) -> None:
        """trw_reflect returns success_patterns count when events contain successes."""
        from fastmcp import FastMCP
        from trw_mcp.tools.orchestration import register_orchestration_tools

        srv = FastMCP("test")
        register_orchestration_tools(srv)
        orch_tools = {t.name: t for t in srv._tool_manager._tools.values()}
        init_result = orch_tools["trw_init"].fn(task_name="success-reflect-task")

        writer = FileStateWriter()
        events_path = Path(init_result["run_path"]) / "meta" / "events.jsonl"
        writer.append_jsonl(events_path, {"ts": "2026-01-01", "event": "shard_complete", "data": {}})
        writer.append_jsonl(events_path, {"ts": "2026-01-02", "event": "shard_complete", "data": {}})
        writer.append_jsonl(events_path, {"ts": "2026-01-03", "event": "phase_gate_passed", "data": {}})

        tools = _get_learning_tools()
        result = tools["trw_reflect"].fn(
            run_path=init_result["run_path"],
            scope="run",
        )
        assert "success_patterns" in result
        assert result["success_patterns"]["count"] >= 1

    def test_reflect_success_pattern_format(self, tmp_path: Path) -> None:
        """Success pattern learnings have expected format and tags."""
        from fastmcp import FastMCP
        from trw_mcp.tools.orchestration import register_orchestration_tools

        srv = FastMCP("test")
        register_orchestration_tools(srv)
        orch_tools = {t.name: t for t in srv._tool_manager._tools.values()}
        init_result = orch_tools["trw_init"].fn(task_name="success-format-task")

        writer = FileStateWriter()
        events_path = Path(init_result["run_path"]) / "meta" / "events.jsonl"
        writer.append_jsonl(events_path, {"ts": "2026-01-01", "event": "delivery_complete"})

        tools = _get_learning_tools()
        result = tools["trw_reflect"].fn(
            run_path=init_result["run_path"],
            scope="run",
        )

        # Check that at least one learning has success-related content
        assert len(result["new_learnings"]) >= 1
        success_learnings = [
            l for l in result["new_learnings"]
            if "Success" in l.get("summary", "") or "success" in l.get("summary", "").lower()
        ]
        assert len(success_learnings) >= 1

    def test_reflect_success_pattern_tags(self, tmp_path: Path) -> None:
        """Success pattern learnings have 'success' and 'pattern' tags."""
        from fastmcp import FastMCP
        from trw_mcp.tools.orchestration import register_orchestration_tools

        srv = FastMCP("test")
        register_orchestration_tools(srv)
        orch_tools = {t.name: t for t in srv._tool_manager._tools.values()}
        init_result = orch_tools["trw_init"].fn(task_name="success-tags-task")

        writer = FileStateWriter()
        events_path = Path(init_result["run_path"]) / "meta" / "events.jsonl"
        writer.append_jsonl(events_path, {"ts": "2026-01-01", "event": "shard_complete"})

        tools = _get_learning_tools()
        result = tools["trw_reflect"].fn(
            run_path=init_result["run_path"],
            scope="run",
        )

        # Find the success learning on disk and check tags
        reader = FileStateReader()
        entries_dir = _entries_dir(tmp_path)
        success_entries = []
        for entry_file in entries_dir.glob("*.yaml"):
            data = reader.read_yaml(entry_file)
            tags = data.get("tags", [])
            if isinstance(tags, list) and "success" in tags:
                success_entries.append(data)

        assert len(success_entries) >= 1
        for entry in success_entries:
            assert "pattern" in entry["tags"]
            assert "auto-discovered" in entry["tags"]

    def test_reflect_success_pattern_impact_scoring(self, tmp_path: Path) -> None:
        """Success pattern learnings have impact=0.5 baseline."""
        from fastmcp import FastMCP
        from trw_mcp.tools.orchestration import register_orchestration_tools

        srv = FastMCP("test")
        register_orchestration_tools(srv)
        orch_tools = {t.name: t for t in srv._tool_manager._tools.values()}
        init_result = orch_tools["trw_init"].fn(task_name="success-impact-task")

        writer = FileStateWriter()
        events_path = Path(init_result["run_path"]) / "meta" / "events.jsonl"
        writer.append_jsonl(events_path, {"ts": "2026-01-01", "event": "task_finished"})

        tools = _get_learning_tools()
        tools["trw_reflect"].fn(
            run_path=init_result["run_path"],
            scope="run",
        )

        reader = FileStateReader()
        entries_dir = _entries_dir(tmp_path)
        for entry_file in entries_dir.glob("*.yaml"):
            data = reader.read_yaml(entry_file)
            if isinstance(data.get("tags"), list) and "success" in data["tags"]:
                assert float(str(data.get("impact", 0))) == 0.5
                break

    def test_reflect_mixed_success_and_error(self, tmp_path: Path) -> None:
        """trw_reflect extracts both error and success patterns from same event stream."""
        from fastmcp import FastMCP
        from trw_mcp.tools.orchestration import register_orchestration_tools

        srv = FastMCP("test")
        register_orchestration_tools(srv)
        orch_tools = {t.name: t for t in srv._tool_manager._tools.values()}
        init_result = orch_tools["trw_init"].fn(task_name="mixed-patterns-task")

        writer = FileStateWriter()
        events_path = Path(init_result["run_path"]) / "meta" / "events.jsonl"
        writer.append_jsonl(events_path, {"ts": "2026-01-01", "event": "error_occurred"})
        writer.append_jsonl(events_path, {"ts": "2026-01-02", "event": "shard_complete"})
        writer.append_jsonl(events_path, {"ts": "2026-01-03", "event": "shard_failed"})
        writer.append_jsonl(events_path, {"ts": "2026-01-04", "event": "phase_gate_passed"})

        tools = _get_learning_tools()
        result = tools["trw_reflect"].fn(
            run_path=init_result["run_path"],
            scope="run",
        )

        assert result["error_patterns"] >= 2
        assert result["success_patterns"]["count"] >= 1
        # Both error and success learnings should be created
        assert len(result["new_learnings"]) >= 3

    def test_reflect_no_events_graceful(self, tmp_path: Path) -> None:
        """trw_reflect with no events still returns success_patterns=0."""
        tools = _get_learning_tools()
        result = tools["trw_reflect"].fn(scope="session")
        assert result["success_patterns"]["count"] == 0
        assert result["error_patterns"] == 0
        assert result["events_analyzed"] == 0

    def test_reflect_what_worked_includes_success(self, tmp_path: Path) -> None:
        """Reflection log's what_worked includes success patterns."""
        from fastmcp import FastMCP
        from trw_mcp.tools.orchestration import register_orchestration_tools

        srv = FastMCP("test")
        register_orchestration_tools(srv)
        orch_tools = {t.name: t for t in srv._tool_manager._tools.values()}
        init_result = orch_tools["trw_init"].fn(task_name="what-worked-task")

        writer = FileStateWriter()
        events_path = Path(init_result["run_path"]) / "meta" / "events.jsonl"
        writer.append_jsonl(events_path, {"ts": "2026-01-01", "event": "shard_complete"})
        writer.append_jsonl(events_path, {"ts": "2026-01-02", "event": "shard_complete"})

        tools = _get_learning_tools()
        result = tools["trw_reflect"].fn(
            run_path=init_result["run_path"],
            scope="run",
        )

        # Check reflection file's what_worked
        reader = FileStateReader()
        reflections_dir = tmp_path / _CFG.trw_dir / _CFG.reflections_dir
        reflection_files = list(reflections_dir.glob("*.yaml"))
        assert len(reflection_files) >= 1

        reflection_data = reader.read_yaml(reflection_files[-1])
        what_worked = reflection_data.get("what_worked", [])
        assert any("Success" in str(item) or "success" in str(item).lower() for item in what_worked)
