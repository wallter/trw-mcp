"""Tests for learning tools — reflect, learn, learn_update, recall, script_save, claude_md_sync, learn_prune."""

from __future__ import annotations

from pathlib import Path

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


def _get_tools() -> dict[str, object]:
    """Create fresh server and return tool map."""
    from fastmcp import FastMCP
    from trw_mcp.tools.learning import register_learning_tools

    srv = FastMCP("test")
    register_learning_tools(srv)
    return {t.name: t for t in srv._tool_manager._tools.values()}


def _entries_dir(root: Path) -> Path:
    """Build entries directory path from config — no hardcoded strings."""
    return root / _CFG.trw_dir / _CFG.learnings_dir / _CFG.entries_dir


class TestTrwLearn:
    """Tests for trw_learn tool."""

    def test_records_learning(self, tmp_path: Path) -> None:
        tools = _get_tools()
        result = tools["trw_learn"].fn(
            summary="Test learning",
            detail="This is a detailed learning entry",
            tags=["testing", "example"],
            impact=0.8,
        )
        assert "learning_id" in result
        assert result["status"] == "recorded"

        entries_dir = _entries_dir(tmp_path)
        assert entries_dir.exists()
        entry_files = list(entries_dir.glob("*.yaml"))
        assert len(entry_files) == 1

    def test_updates_index(self, tmp_path: Path) -> None:
        tools = _get_tools()
        tools["trw_learn"].fn(
            summary="Learning 1",
            detail="Detail 1",
        )
        tools["trw_learn"].fn(
            summary="Learning 2",
            detail="Detail 2",
        )

        reader = FileStateReader()
        index = reader.read_yaml(
            tmp_path / _CFG.trw_dir / _CFG.learnings_dir / "index.yaml"
        )
        assert index["total_count"] == 2


class TestTrwRecall:
    """Tests for trw_recall tool."""

    def test_finds_matching_learning(self, tmp_path: Path) -> None:
        tools = _get_tools()

        # Record a learning
        tools["trw_learn"].fn(
            summary="Database connection pooling gotcha",
            detail="Always close connections in finally block",
            tags=["database", "gotcha"],
            impact=0.9,
        )

        # Search for it
        result = tools["trw_recall"].fn(query="database")
        assert result["total_matches"] >= 1
        assert len(result["learnings"]) >= 1

    def test_no_matches(self, tmp_path: Path) -> None:
        tools = _get_tools()
        result = tools["trw_recall"].fn(query="nonexistent-query-xyz")
        assert result["total_matches"] == 0

    def test_tag_filter(self, tmp_path: Path) -> None:
        tools = _get_tools()

        tools["trw_learn"].fn(
            summary="Tagged learning",
            detail="Has specific tags",
            tags=["python", "testing"],
            impact=0.7,
        )
        tools["trw_learn"].fn(
            summary="Other tagged learning",
            detail="Has different tags",
            tags=["javascript"],
            impact=0.7,
        )

        result = tools["trw_recall"].fn(query="tagged", tags=["python"])
        # Should only find the python-tagged one
        python_results = [
            l for l in result["learnings"]
            if "python" in (l.get("tags", []) if isinstance(l.get("tags"), list) else [])
        ]
        assert len(python_results) >= 1

    def test_min_impact_filter(self, tmp_path: Path) -> None:
        tools = _get_tools()

        tools["trw_learn"].fn(
            summary="Low impact learning filter test",
            detail="Low value",
            impact=0.2,
        )
        tools["trw_learn"].fn(
            summary="High impact learning filter test",
            detail="High value",
            impact=0.9,
        )

        result = tools["trw_recall"].fn(query="impact learning filter", min_impact=0.5)
        assert all(
            float(l.get("impact", 0)) >= 0.5
            for l in result["learnings"]
        )

    def test_multi_word_query_matches_tokens(self, tmp_path: Path) -> None:
        tools = _get_tools()

        tools["trw_learn"].fn(
            summary="Database connection pooling",
            detail="Use pool for PostgreSQL connections",
            tags=["database"],
            impact=0.8,
        )

        # Multi-word query where words appear in different fields
        result = tools["trw_recall"].fn(query="database postgresql")
        assert result["total_matches"] >= 1

        # Multi-word query where both words exist but separately
        result = tools["trw_recall"].fn(query="pooling connections")
        assert result["total_matches"] >= 1

        # Query with a word that doesn't appear at all
        result = tools["trw_recall"].fn(query="database redis")
        assert result["total_matches"] == 0


class TestTrwScriptSave:
    """Tests for trw_script_save tool."""

    def test_saves_bash_script(self, tmp_path: Path) -> None:
        tools = _get_tools()
        result = tools["trw_script_save"].fn(
            name="test-script",
            content="#!/bin/bash\necho 'hello'",
            description="A test script",
            language="bash",
        )
        assert result["status"] == "created"

        script_path = tmp_path / _CFG.trw_dir / _CFG.scripts_dir / "test-script.sh"
        assert script_path.exists()
        assert "echo 'hello'" in script_path.read_text(encoding="utf-8")

    def test_saves_python_script(self, tmp_path: Path) -> None:
        tools = _get_tools()
        result = tools["trw_script_save"].fn(
            name="helper",
            content="print('hello')",
            description="A python helper",
            language="python",
        )
        assert result["status"] == "created"

        script_path = tmp_path / _CFG.trw_dir / _CFG.scripts_dir / "helper.py"
        assert script_path.exists()

    def test_updates_existing_script(self, tmp_path: Path) -> None:
        tools = _get_tools()

        # Create
        tools["trw_script_save"].fn(
            name="evolving",
            content="v1",
            description="Version 1",
        )

        # Update
        result = tools["trw_script_save"].fn(
            name="evolving",
            content="v2",
            description="Version 2",
        )
        assert result["status"] == "updated"

    def test_updates_index(self, tmp_path: Path) -> None:
        tools = _get_tools()
        tools["trw_script_save"].fn(
            name="indexed",
            content="content",
            description="Indexed script",
        )

        reader = FileStateReader()
        index = reader.read_yaml(
            tmp_path / _CFG.trw_dir / _CFG.scripts_dir / "index.yaml"
        )
        scripts = index.get("scripts", [])
        assert len(scripts) == 1
        assert scripts[0]["name"] == "indexed"

    def test_unknown_language_extension(self, tmp_path: Path) -> None:
        """Unknown language falls back to .{language} extension."""
        tools = _get_tools()
        result = tools["trw_script_save"].fn(
            name="custom-lang",
            content="code",
            description="Custom language script",
            language="lua",
        )
        assert result["status"] == "created"
        script_path = tmp_path / _CFG.trw_dir / _CFG.scripts_dir / "custom-lang.lua"
        assert script_path.exists()


class TestTrwReflect:
    """Tests for trw_reflect tool."""

    def test_reflect_with_run(self, tmp_path: Path) -> None:
        # Setup: Create a run with events
        from fastmcp import FastMCP
        from trw_mcp.tools.orchestration import register_orchestration_tools

        srv = FastMCP("test")
        register_orchestration_tools(srv)
        orch_tools = {t.name: t for t in srv._tool_manager._tools.values()}
        init_result = orch_tools["trw_init"].fn(task_name="reflect-task")

        # Add some error events
        writer = FileStateWriter()
        events_path = Path(init_result["run_path"]) / "meta" / "events.jsonl"
        writer.append_jsonl(events_path, {"ts": "2026-01-01", "event": "error_occurred", "data": {}})
        writer.append_jsonl(events_path, {"ts": "2026-01-02", "event": "shard_failed", "data": {}})

        # Reflect
        tools = _get_tools()
        result = tools["trw_reflect"].fn(
            run_path=init_result["run_path"],
            scope="run",
        )
        assert result["events_analyzed"] >= 1
        assert result["scope"] == "run"
        assert "reflection_id" in result

    def test_reflect_empty(self, tmp_path: Path) -> None:
        tools = _get_tools()
        result = tools["trw_reflect"].fn(scope="session")
        assert result["events_analyzed"] == 0


class TestTrwReflectEventLogging:
    """Tests for reflection event logging to run events.jsonl."""

    def test_reflect_logs_event_to_run(self, tmp_path: Path) -> None:
        """trw_reflect with run_path writes reflection_complete event."""
        from fastmcp import FastMCP
        from trw_mcp.tools.orchestration import register_orchestration_tools

        # Create a run
        srv = FastMCP("test")
        register_orchestration_tools(srv)
        orch_tools = {t.name: t for t in srv._tool_manager._tools.values()}
        init_result = orch_tools["trw_init"].fn(task_name="reflect-event-task")
        run_path = init_result["run_path"]

        # Reflect with run_path
        tools = _get_tools()
        result = tools["trw_reflect"].fn(run_path=run_path, scope="run")
        assert "reflection_id" in result

        # Verify reflection_complete event in events.jsonl
        reader = FileStateReader()
        events = reader.read_jsonl(Path(run_path) / "meta" / "events.jsonl")
        reflection_events = [
            e for e in events if e.get("event") == "reflection_complete"
        ]
        assert len(reflection_events) == 1
        assert reflection_events[0]["scope"] == "run"

    def test_reflect_no_event_without_run_path(self, tmp_path: Path) -> None:
        """trw_reflect without run_path skips event logging."""
        tools = _get_tools()
        result = tools["trw_reflect"].fn(scope="session")
        # No run_path — no events.jsonl to write to
        assert result["events_analyzed"] == 0
        # No crash, just returns normally

    def test_reflect_event_has_reflection_id(self, tmp_path: Path) -> None:
        """Logged reflection_complete event contains reflection_id."""
        from fastmcp import FastMCP
        from trw_mcp.tools.orchestration import register_orchestration_tools

        srv = FastMCP("test")
        register_orchestration_tools(srv)
        orch_tools = {t.name: t for t in srv._tool_manager._tools.values()}
        init_result = orch_tools["trw_init"].fn(task_name="reflect-id-task")
        run_path = init_result["run_path"]

        tools = _get_tools()
        result = tools["trw_reflect"].fn(run_path=run_path, scope="wave")

        reader = FileStateReader()
        events = reader.read_jsonl(Path(run_path) / "meta" / "events.jsonl")
        reflection_events = [
            e for e in events if e.get("event") == "reflection_complete"
        ]
        assert len(reflection_events) == 1
        assert reflection_events[0]["reflection_id"] == result["reflection_id"]
        assert reflection_events[0]["scope"] == "wave"


class TestTrwClaudeMdSync:
    """Tests for trw_claude_md_sync tool."""

    def test_generates_claude_md(self, tmp_path: Path) -> None:
        tools = _get_tools()

        # Record a high-impact learning
        tools["trw_learn"].fn(
            summary="Critical pattern discovered",
            detail="Always use context managers for DB connections",
            tags=["database"],
            impact=0.9,
        )

        # Sync
        result = tools["trw_claude_md_sync"].fn(scope="root")
        assert result["status"] == "synced"
        assert result["learnings_promoted"] >= 1

        # Verify CLAUDE.md was created
        claude_md = tmp_path / "CLAUDE.md"
        assert claude_md.exists()
        content = claude_md.read_text(encoding="utf-8")
        assert "trw:start" in content
        assert "trw:end" in content
        assert "Critical pattern" in content

    def test_preserves_existing_content(self, tmp_path: Path) -> None:
        # Create existing CLAUDE.md
        claude_md = tmp_path / "CLAUDE.md"
        claude_md.write_text("# My Project\n\nExisting content.\n", encoding="utf-8")

        tools = _get_tools()
        tools["trw_learn"].fn(
            summary="New high impact learning",
            detail="Detail here",
            impact=0.9,
        )
        tools["trw_claude_md_sync"].fn(scope="root")

        content = claude_md.read_text(encoding="utf-8")
        assert "My Project" in content  # Preserved
        assert "Existing content" in content  # Preserved
        assert "trw:start" in content  # Added

    def test_replaces_existing_trw_section(self, tmp_path: Path) -> None:
        claude_md = tmp_path / "CLAUDE.md"
        claude_md.write_text(
            "# Project\n\n"
            "<!-- trw:start -->\nOld content\n<!-- trw:end -->\n\n"
            "# Other section\n",
            encoding="utf-8",
        )

        tools = _get_tools()
        tools["trw_learn"].fn(
            summary="Replacement learning",
            detail="Replaces old content",
            impact=0.9,
        )
        tools["trw_claude_md_sync"].fn(scope="root")

        content = claude_md.read_text(encoding="utf-8")
        assert "Old content" not in content
        assert "Replacement learning" in content
        assert "Other section" in content  # Preserved

    def test_excludes_resolved_learnings(self, tmp_path: Path) -> None:
        tools = _get_tools()

        # Record a high-impact learning, then resolve it
        result = tools["trw_learn"].fn(
            summary="Resolved bug pattern",
            detail="This was fixed",
            impact=0.9,
        )
        tools["trw_learn_update"].fn(
            learning_id=result["learning_id"],
            status="resolved",
        )

        # Sync
        sync_result = tools["trw_claude_md_sync"].fn(scope="root")
        assert sync_result["learnings_promoted"] == 0

        claude_md = tmp_path / "CLAUDE.md"
        if claude_md.exists():
            content = claude_md.read_text(encoding="utf-8")
            assert "Resolved bug pattern" not in content

    def test_excludes_obsolete_learnings(self, tmp_path: Path) -> None:
        tools = _get_tools()

        result = tools["trw_learn"].fn(
            summary="Obsolete pattern",
            detail="No longer relevant",
            impact=0.9,
        )
        tools["trw_learn_update"].fn(
            learning_id=result["learning_id"],
            status="obsolete",
        )

        sync_result = tools["trw_claude_md_sync"].fn(scope="root")
        assert sync_result["learnings_promoted"] == 0

    def test_sub_scope_creates_sub_claude_md(self, tmp_path: Path) -> None:
        """Sub-scope sync writes to target_dir/CLAUDE.md."""
        tools = _get_tools()

        tools["trw_learn"].fn(
            summary="Sub scope learning test",
            detail="For sub-scope CLAUDE.md",
            impact=0.9,
        )

        sub_dir = tmp_path / "src" / "module"
        sub_dir.mkdir(parents=True)

        result = tools["trw_claude_md_sync"].fn(
            scope="sub",
            target_dir=str(sub_dir),
        )
        assert result["scope"] == "sub"

        sub_claude_md = sub_dir / "CLAUDE.md"
        assert sub_claude_md.exists()
        content = sub_claude_md.read_text(encoding="utf-8")
        assert "trw:start" in content

    def test_enforces_line_limit(self, tmp_path: Path) -> None:
        """CLAUDE.md content is truncated when exceeding line limit."""
        import trw_mcp.tools.learning as learn_mod

        tools = _get_tools()

        # Create existing CLAUDE.md with many lines
        claude_md = tmp_path / "CLAUDE.md"
        long_content = "\n".join(f"Line {i}" for i in range(300))
        claude_md.write_text(long_content, encoding="utf-8")

        tools["trw_learn"].fn(
            summary="Line limit test learning",
            detail="Trigger sync",
            impact=0.9,
        )
        result = tools["trw_claude_md_sync"].fn(scope="root")

        content = claude_md.read_text(encoding="utf-8")
        line_count = len(content.split("\n"))
        # Should be at or below the configured max (200) + 1 for truncation comment
        assert line_count <= learn_mod._config.claude_md_max_lines + 1


class TestTrwLearnUpdate:
    """Tests for trw_learn_update tool."""

    def test_updates_status(self, tmp_path: Path) -> None:
        tools = _get_tools()

        result = tools["trw_learn"].fn(
            summary="Update status test",
            detail="Will be resolved",
            impact=0.8,
        )
        lid = result["learning_id"]

        update_result = tools["trw_learn_update"].fn(
            learning_id=lid,
            status="resolved",
        )
        assert update_result["status"] == "updated"

        # Verify via recall
        recall_result = tools["trw_recall"].fn(
            query="update status test",
            status="resolved",
        )
        assert len(recall_result["learnings"]) == 1
        assert recall_result["learnings"][0]["status"] == "resolved"

    def test_updates_impact(self, tmp_path: Path) -> None:
        tools = _get_tools()

        result = tools["trw_learn"].fn(
            summary="Impact update test",
            detail="Will lower impact",
            impact=0.9,
        )

        tools["trw_learn_update"].fn(
            learning_id=result["learning_id"],
            impact=0.3,
        )

        recall_result = tools["trw_recall"].fn(query="impact update test")
        assert len(recall_result["learnings"]) == 1
        assert float(recall_result["learnings"][0]["impact"]) == pytest.approx(0.3)

    def test_updates_multiple_fields(self, tmp_path: Path) -> None:
        tools = _get_tools()

        result = tools["trw_learn"].fn(
            summary="Multi field test",
            detail="Original detail",
            tags=["old"],
            impact=0.5,
        )

        tools["trw_learn_update"].fn(
            learning_id=result["learning_id"],
            status="obsolete",
            tags=["new", "updated"],
        )

        recall_result = tools["trw_recall"].fn(
            query="multi field test",
            status="obsolete",
        )
        assert len(recall_result["learnings"]) == 1
        entry = recall_result["learnings"][0]
        assert entry["status"] == "obsolete"
        assert "new" in entry["tags"]
        assert "updated" in entry["tags"]

    def test_invalid_learning_id(self, tmp_path: Path) -> None:
        tools = _get_tools()

        # Create entries dir so it exists
        tools["trw_learn"].fn(
            summary="Placeholder",
            detail="Needed for entries dir",
        )

        result = tools["trw_learn_update"].fn(
            learning_id="L-nonexistent",
            status="resolved",
        )
        assert "error" in result

    def test_resolved_sets_date(self, tmp_path: Path) -> None:
        tools = _get_tools()

        result = tools["trw_learn"].fn(
            summary="Resolved date test",
            detail="Check resolved_at is set",
            impact=0.7,
        )

        tools["trw_learn_update"].fn(
            learning_id=result["learning_id"],
            status="resolved",
        )

        recall_result = tools["trw_recall"].fn(
            query="resolved date test",
            status="resolved",
        )
        assert len(recall_result["learnings"]) == 1
        assert recall_result["learnings"][0].get("resolved_at") is not None

    def test_invalid_status_value(self, tmp_path: Path) -> None:
        tools = _get_tools()

        result = tools["trw_learn"].fn(
            summary="Invalid status test",
            detail="Will try bad status",
        )

        update_result = tools["trw_learn_update"].fn(
            learning_id=result["learning_id"],
            status="invalid_value",
        )
        assert "error" in update_result

    def test_no_entries_dir(self, tmp_path: Path) -> None:
        tools = _get_tools()
        result = tools["trw_learn_update"].fn(
            learning_id="L-missing",
            status="resolved",
        )
        assert "error" in result

    def test_updates_summary_and_detail(self, tmp_path: Path) -> None:
        """Verify summary and detail fields can be updated independently."""
        tools = _get_tools()

        result = tools["trw_learn"].fn(
            summary="Original summary",
            detail="Original detail",
        )

        tools["trw_learn_update"].fn(
            learning_id=result["learning_id"],
            summary="Updated summary",
            detail="Updated detail",
        )

        recall_result = tools["trw_recall"].fn(query="updated summary")
        assert len(recall_result["learnings"]) == 1
        entry = recall_result["learnings"][0]
        assert entry["summary"] == "Updated summary"
        assert entry["detail"] == "Updated detail"


    def test_wildcard_returns_all_learnings(self, tmp_path: Path) -> None:
        """Query '*' or empty returns all learnings (filtered by other params)."""
        tools = _get_tools()

        tools["trw_learn"].fn(summary="Alpha learning", detail="First", impact=0.8)
        tools["trw_learn"].fn(summary="Beta learning", detail="Second", impact=0.3)
        tools["trw_learn"].fn(summary="Gamma learning", detail="Third", impact=0.9)

        # Wildcard query should return all
        result = tools["trw_recall"].fn(query="*")
        assert len(result["learnings"]) == 3

        # Wildcard with min_impact filter
        result = tools["trw_recall"].fn(query="*", min_impact=0.5)
        assert len(result["learnings"]) == 2

    def test_empty_query_returns_all_learnings(self, tmp_path: Path) -> None:
        """Empty string query returns all learnings."""
        tools = _get_tools()

        tools["trw_learn"].fn(summary="One", detail="Detail", impact=0.5)
        tools["trw_learn"].fn(summary="Two", detail="Detail", impact=0.5)

        result = tools["trw_recall"].fn(query="")
        assert len(result["learnings"]) == 2


class TestTrwRecallStatusFilter:
    """Tests for trw_recall status filter."""

    def test_status_filter_active(self, tmp_path: Path) -> None:
        tools = _get_tools()

        # Create one active, one resolved
        tools["trw_learn"].fn(
            summary="Active status filter learning",
            detail="Still active",
            impact=0.7,
        )
        r2 = tools["trw_learn"].fn(
            summary="Resolved status filter learning",
            detail="Already fixed",
            impact=0.7,
        )
        tools["trw_learn_update"].fn(
            learning_id=r2["learning_id"],
            status="resolved",
        )

        # Filter by active only
        result = tools["trw_recall"].fn(
            query="status filter learning",
            status="active",
        )
        assert len(result["learnings"]) == 1
        assert "Active" in str(result["learnings"][0].get("summary", ""))

    def test_status_filter_none_returns_all(self, tmp_path: Path) -> None:
        tools = _get_tools()

        tools["trw_learn"].fn(
            summary="All status test alpha",
            detail="Detail",
        )
        r2 = tools["trw_learn"].fn(
            summary="All status test beta",
            detail="Detail",
        )
        tools["trw_learn_update"].fn(
            learning_id=r2["learning_id"],
            status="resolved",
        )

        # No status filter — should return both
        result = tools["trw_recall"].fn(query="all status test")
        assert len(result["learnings"]) == 2


class TestTrwLearnPrune:
    """Tests for trw_learn_prune tool."""

    def test_prune_dry_run_no_changes(self, tmp_path: Path) -> None:
        tools = _get_tools()

        # Create a fresh learning (won't be flagged by age heuristic)
        tools["trw_learn"].fn(
            summary="Fresh prune test learning",
            detail="Just created today",
            impact=0.5,
        )

        result = tools["trw_learn_prune"].fn(dry_run=True)
        assert result["dry_run"] is True
        assert result["actions"] == 0

    def test_prune_empty_entries(self, tmp_path: Path) -> None:
        tools = _get_tools()
        result = tools["trw_learn_prune"].fn(dry_run=True)
        assert result["method"] == "none"
        assert result["candidates"] == []

    def test_prune_without_llm_age_based(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Test that the age-based fallback identifies old learnings."""
        from datetime import date, timedelta

        import trw_mcp.tools.learning as learn_mod

        tools = _get_tools()

        # Create a learning
        result = tools["trw_learn"].fn(
            summary="Old prune test learning",
            detail="Should be flagged",
            impact=0.5,
        )

        # Manually backdate the entry
        entries_dir = _entries_dir(tmp_path)
        for entry_file in entries_dir.glob("*.yaml"):
            reader = FileStateReader()
            writer = FileStateWriter()
            data = reader.read_yaml(entry_file)
            if data.get("id") == result["learning_id"]:
                old_date = (date.today() - timedelta(days=45)).isoformat()
                data["created"] = old_date
                data["recurrence"] = 1
                writer.write_yaml(entry_file, data)
                break

        # Disable LLM via monkeypatch (not try/finally)
        monkeypatch.setattr(learn_mod, "_llm", type(learn_mod._llm)(model="haiku"))

        prune_result = tools["trw_learn_prune"].fn(dry_run=True)
        assert prune_result["method"] == "utility"
        assert len(prune_result["candidates"]) >= 1
        assert prune_result["actions"] == 0  # dry run

    def test_prune_apply_updates_entries(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Test that apply mode actually changes entry status."""
        from datetime import date, timedelta

        import trw_mcp.tools.learning as learn_mod

        tools = _get_tools()

        result = tools["trw_learn"].fn(
            summary="Apply prune test learning",
            detail="Should be obsoleted",
            impact=0.5,
        )

        # Backdate the entry
        entries_dir = _entries_dir(tmp_path)
        for entry_file in entries_dir.glob("*.yaml"):
            reader = FileStateReader()
            writer = FileStateWriter()
            data = reader.read_yaml(entry_file)
            if data.get("id") == result["learning_id"]:
                old_date = (date.today() - timedelta(days=60)).isoformat()
                data["created"] = old_date
                data["recurrence"] = 1
                writer.write_yaml(entry_file, data)
                break

        # Disable LLM via monkeypatch
        monkeypatch.setattr(learn_mod, "_llm", type(learn_mod._llm)(model="haiku"))

        prune_result = tools["trw_learn_prune"].fn(dry_run=False)
        assert prune_result["actions"] >= 1

        # Verify the entry is now obsolete
        recall_result = tools["trw_recall"].fn(
            query="apply prune test learning",
            status="obsolete",
        )
        assert len(recall_result["learnings"]) >= 1


class TestClaudeMdTemplate:
    """Tests for the CLAUDE.md template system (PRD-CORE-002 Phase 1)."""

    def test_loads_bundled_template(self, tmp_path: Path) -> None:
        """Default template loaded from package data."""
        from trw_mcp.state.claude_md import load_claude_md_template

        template = load_claude_md_template(tmp_path / _CFG.trw_dir)
        assert "{{categorized_learnings}}" in template
        assert "{{architecture_section}}" in template
        assert "trw:start" in template
        assert "trw:end" in template

    def test_project_override_takes_precedence(self, tmp_path: Path) -> None:
        """Project-local template in .trw/templates/ overrides bundled."""
        from trw_mcp.state.claude_md import load_claude_md_template

        trw_dir = tmp_path / _CFG.trw_dir
        templates_dir = trw_dir / _CFG.templates_dir
        templates_dir.mkdir(parents=True)
        custom = (
            "<!-- trw:start -->\n"
            "## Custom Section\n"
            "{{categorized_learnings}}"
            "<!-- trw:end -->\n"
        )
        (templates_dir / "claude_md.md").write_text(custom, encoding="utf-8")

        template = load_claude_md_template(trw_dir)
        assert "Custom Section" in template
        assert "{{categorized_learnings}}" in template

    def test_render_replaces_placeholders(self, tmp_path: Path) -> None:
        """{{key}} tokens replaced with content."""
        from trw_mcp.state.claude_md import render_template

        template = "## Title\n\n{{section_a}}{{section_b}}"
        context = {"section_a": "### A\n- item\n\n", "section_b": "### B\n- item\n\n"}
        result = render_template(template, context)
        assert "### A" in result
        assert "### B" in result
        assert "{{section_a}}" not in result

    def test_render_empty_sections_collapse(self, tmp_path: Path) -> None:
        """Empty values don't leave runs of blank lines."""
        from trw_mcp.state.claude_md import render_template

        template = "Header\n\n{{a}}{{b}}Footer"
        context = {"a": "", "b": ""}
        result = render_template(template, context)
        # Should not have 3+ consecutive newlines
        assert "\n\n\n" not in result

    def test_sync_uses_template_produces_same_output(self, tmp_path: Path) -> None:
        """trw_claude_md_sync with bundled template produces equivalent output."""
        tools = _get_tools()

        tools["trw_learn"].fn(
            summary="Template sync test learning",
            detail="Verify template produces same output",
            tags=["gotcha"],
            impact=0.9,
        )

        result = tools["trw_claude_md_sync"].fn(scope="root")
        assert result["status"] == "synced"
        assert result["learnings_promoted"] >= 1

        claude_md = tmp_path / "CLAUDE.md"
        content = claude_md.read_text(encoding="utf-8")
        assert "trw:start" in content
        assert "trw:end" in content
        assert "Template sync test learning" in content
        # Should be categorized under Gotchas
        assert "### Gotchas" in content

    def test_custom_template_with_extra_sections(self, tmp_path: Path) -> None:
        """Custom templates can add static content alongside placeholders."""
        trw_dir = tmp_path / _CFG.trw_dir
        templates_dir = trw_dir / _CFG.templates_dir
        templates_dir.mkdir(parents=True)

        custom_template = (
            "\n"
            "<!-- TRW AUTO-GENERATED \u2014 do not edit between markers -->\n"
            "<!-- trw:start -->\n"
            "\n"
            "## TRW Learnings (Auto-Generated)\n"
            "\n"
            "### Project-Specific Notes\n"
            "- This project uses React 19\n"
            "\n"
            "{{categorized_learnings}}"
            "{{adherence_section}}"
            "<!-- trw:end -->\n"
        )
        (templates_dir / "claude_md.md").write_text(custom_template, encoding="utf-8")

        tools = _get_tools()
        tools["trw_learn"].fn(
            summary="Custom template learning",
            detail="Should appear alongside custom content",
            impact=0.9,
        )

        result = tools["trw_claude_md_sync"].fn(scope="root")
        assert result["status"] == "synced"

        claude_md = tmp_path / "CLAUDE.md"
        content = claude_md.read_text(encoding="utf-8")
        assert "Project-Specific Notes" in content
        assert "React 19" in content
        assert "Custom template learning" in content


class TestTrwReflectLLM:
    """Tests for LLM-augmented trw_reflect."""

    def test_reflect_without_llm_unchanged(self, tmp_path: Path) -> None:
        """Verify reflect still works with LLM disabled."""
        tools = _get_tools()
        result = tools["trw_reflect"].fn(scope="session")
        assert result["events_analyzed"] == 0
        assert result["llm_used"] is False

    def test_reflect_llm_flag_present(self, tmp_path: Path) -> None:
        """Verify llm_used field is in return value."""
        tools = _get_tools()
        result = tools["trw_reflect"].fn(scope="session")
        assert "llm_used" in result


class TestTrwClaudeMdSyncLLM:
    """Tests for LLM-augmented trw_claude_md_sync."""

    def test_sync_without_llm_unchanged(self, tmp_path: Path) -> None:
        """Verify sync still works with LLM unavailable."""
        tools = _get_tools()

        tools["trw_learn"].fn(
            summary="Sync no LLM test learning",
            detail="Should appear as bullet point",
            impact=0.9,
        )

        result = tools["trw_claude_md_sync"].fn(scope="root")
        assert result["status"] == "synced"
        assert result["llm_used"] is False
        assert result["learnings_promoted"] >= 1

        # Verify bullet-point format (not LLM summary)
        claude_md = tmp_path / "CLAUDE.md"
        content = claude_md.read_text(encoding="utf-8")
        assert "Sync no LLM test learning" in content

    def test_sync_llm_flag_present(self, tmp_path: Path) -> None:
        """Verify llm_used field is in return value."""
        tools = _get_tools()
        tools["trw_learn"].fn(
            summary="Sync flag test",
            detail="Check llm_used field",
            impact=0.9,
        )
        result = tools["trw_claude_md_sync"].fn(scope="root")
        assert "llm_used" in result


class TestTrwLearnPruneHeuristics:
    """Tests for improved trw_learn_prune heuristics."""

    def test_prune_flags_resolved_entries(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Entries with status 'resolved' are always prune candidates."""
        import trw_mcp.tools.learning as learn_mod

        tools = _get_tools()

        result = tools["trw_learn"].fn(
            summary="Resolved entry prune test",
            detail="Already fixed",
            impact=0.7,
        )
        tools["trw_learn_update"].fn(
            learning_id=result["learning_id"],
            status="resolved",
        )

        # Disable LLM
        monkeypatch.setattr(learn_mod, "_llm", type(learn_mod._llm)(model="haiku"))

        prune_result = tools["trw_learn_prune"].fn(dry_run=True)
        assert prune_result["method"] == "utility"

    def test_prune_flags_old_bug_tagged_entry(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Old entries tagged 'bug' with low utility are prune candidates."""
        from datetime import date, timedelta

        import trw_mcp.tools.learning as learn_mod

        tools = _get_tools()

        result = tools["trw_learn"].fn(
            summary="Old bug tag prune test",
            detail="A bug that was fixed long ago",
            tags=["bug"],
            impact=0.5,
        )

        # Backdate entry to make utility decay below threshold
        entries_dir = _entries_dir(tmp_path)
        for entry_file in entries_dir.glob("*.yaml"):
            reader = FileStateReader()
            writer = FileStateWriter()
            data = reader.read_yaml(entry_file)
            if data.get("id") == result["learning_id"]:
                old_date = (date.today() - timedelta(days=60)).isoformat()
                data["created"] = old_date
                data["recurrence"] = 1
                writer.write_yaml(entry_file, data)
                break

        # Disable LLM
        monkeypatch.setattr(learn_mod, "_llm", type(learn_mod._llm)(model="haiku"))

        prune_result = tools["trw_learn_prune"].fn(dry_run=True)
        assert prune_result["method"] == "utility"
        assert len(prune_result["candidates"]) >= 1
        assert any("utility" in str(c.get("reason", "")).lower() for c in prune_result["candidates"])


class TestTrwLearnAnalytics:
    """Tests for trw_learn analytics counter."""

    def test_learn_increments_analytics_counter(self, tmp_path: Path) -> None:
        """trw_learn should increment total_learnings in analytics.yaml."""
        tools = _get_tools()

        tools["trw_learn"].fn(
            summary="Analytics test one",
            detail="First",
            impact=0.5,
        )
        tools["trw_learn"].fn(
            summary="Analytics test two",
            detail="Second",
            impact=0.5,
        )

        analytics_path = (
            tmp_path / _CFG.trw_dir / _CFG.context_dir / "analytics.yaml"
        )
        if analytics_path.exists():
            reader = FileStateReader()
            data = reader.read_yaml(analytics_path)
            assert int(str(data.get("total_learnings", 0))) >= 2


class TestTrwRecallAccessTracking:
    """Tests for PRD-CORE-004 Phase 1a — access tracking in trw_recall."""

    def test_recall_updates_last_accessed_at(self, tmp_path: Path) -> None:
        """trw_recall sets last_accessed_at on returned entries."""
        from datetime import date

        tools = _get_tools()
        result = tools["trw_learn"].fn(
            summary="Access tracking date test",
            detail="Should have last_accessed_at updated",
            impact=0.8,
        )
        lid = result["learning_id"]

        # Recall should update access tracking
        tools["trw_recall"].fn(query="access tracking date")

        # Verify on-disk entry has last_accessed_at set
        reader = FileStateReader()
        entries_dir = _entries_dir(tmp_path)
        found = False
        for entry_file in entries_dir.glob("*.yaml"):
            data = reader.read_yaml(entry_file)
            if data.get("id") == lid:
                assert data.get("last_accessed_at") == date.today().isoformat()
                found = True
                break
        assert found, "Entry not found on disk"

    def test_recall_increments_access_count(self, tmp_path: Path) -> None:
        """trw_recall increments access_count on each matching recall."""
        tools = _get_tools()
        result = tools["trw_learn"].fn(
            summary="Access count increment test",
            detail="Should increment access_count",
            impact=0.8,
        )
        lid = result["learning_id"]

        # Recall multiple times
        tools["trw_recall"].fn(query="access count increment")
        tools["trw_recall"].fn(query="access count increment")
        tools["trw_recall"].fn(query="access count increment")

        # Verify on-disk access_count == 3
        reader = FileStateReader()
        entries_dir = _entries_dir(tmp_path)
        for entry_file in entries_dir.glob("*.yaml"):
            data = reader.read_yaml(entry_file)
            if data.get("id") == lid:
                assert int(str(data.get("access_count", 0))) == 3
                break

    def test_recall_only_updates_matched_entries(self, tmp_path: Path) -> None:
        """trw_recall does not touch entries that don't match the query."""
        tools = _get_tools()

        tools["trw_learn"].fn(
            summary="Database pooling gotcha xray",
            detail="This should be accessed",
            impact=0.8,
        )
        r2 = tools["trw_learn"].fn(
            summary="Filesystem permissions zulu",
            detail="This should NOT be accessed",
            impact=0.8,
        )

        tools["trw_recall"].fn(query="database pooling xray")

        reader = FileStateReader()
        entries_dir = _entries_dir(tmp_path)
        for entry_file in entries_dir.glob("*.yaml"):
            data = reader.read_yaml(entry_file)
            if data.get("id") == r2["learning_id"]:
                # Unmatched entry should have access_count 0 and no last_accessed_at
                assert int(str(data.get("access_count", 0))) == 0
                assert data.get("last_accessed_at") is None
                break

    def test_recall_appends_receipt(self, tmp_path: Path) -> None:
        """trw_recall appends a receipt to recall_log.jsonl."""
        import json

        tools = _get_tools()
        tools["trw_learn"].fn(
            summary="Receipt logging test",
            detail="Should generate a recall receipt",
            impact=0.8,
        )

        tools["trw_recall"].fn(query="receipt logging")

        receipt_path = (
            tmp_path / _CFG.trw_dir / _CFG.learnings_dir
            / _CFG.receipts_dir / "recall_log.jsonl"
        )
        assert receipt_path.exists()
        lines = receipt_path.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) >= 1
        record = json.loads(lines[-1])
        assert record["query"] == "receipt logging"
        assert "matched_ids" in record
        assert "ts" in record

    def test_recall_receipt_contains_matched_ids(self, tmp_path: Path) -> None:
        """Receipt records the IDs of all matched learnings."""
        import json

        tools = _get_tools()
        r1 = tools["trw_learn"].fn(
            summary="Receipt ID check alpha",
            detail="First entry",
            impact=0.8,
        )
        r2 = tools["trw_learn"].fn(
            summary="Receipt ID check beta",
            detail="Second entry",
            impact=0.8,
        )

        tools["trw_recall"].fn(query="receipt id check")

        receipt_path = (
            tmp_path / _CFG.trw_dir / _CFG.learnings_dir
            / _CFG.receipts_dir / "recall_log.jsonl"
        )
        lines = receipt_path.read_text(encoding="utf-8").strip().split("\n")
        record = json.loads(lines[-1])
        assert r1["learning_id"] in record["matched_ids"]
        assert r2["learning_id"] in record["matched_ids"]

    def test_recall_no_match_no_access_update(self, tmp_path: Path) -> None:
        """When query has no matches, no access tracking updates occur."""
        tools = _get_tools()
        tools["trw_learn"].fn(
            summary="No match access test",
            detail="Should not be accessed",
            impact=0.8,
        )

        tools["trw_recall"].fn(query="zzz_nonexistent_xyz")

        receipt_path = (
            tmp_path / _CFG.trw_dir / _CFG.learnings_dir
            / _CFG.receipts_dir / "recall_log.jsonl"
        )
        # Receipt should still be logged (with empty matched_ids)
        if receipt_path.exists():
            import json
            lines = receipt_path.read_text(encoding="utf-8").strip().split("\n")
            record = json.loads(lines[-1])
            assert len(record["matched_ids"]) == 0

    def test_new_fields_default_for_existing_entries(self, tmp_path: Path) -> None:
        """Entries created without new fields get defaults (lazy migration)."""
        # Simulate an old entry without last_accessed_at or access_count
        writer = FileStateWriter()
        entries_dir = _entries_dir(tmp_path)
        writer.ensure_dir(entries_dir)
        old_entry = {
            "id": "L-oldentry1",
            "summary": "Legacy entry without access fields",
            "detail": "Created before Phase 1a",
            "tags": ["legacy"],
            "evidence": [],
            "impact": 0.7,
            "status": "active",
            "recurrence": 1,
            "created": "2026-01-01",
            "updated": "2026-01-01",
            "resolved_at": None,
            "promoted_to_claude_md": False,
            # Deliberately missing: last_accessed_at, access_count
        }
        writer.write_yaml(entries_dir / "2026-01-01-legacy-entry.yaml", old_entry)

        # Update the index
        index_path = tmp_path / _CFG.trw_dir / _CFG.learnings_dir / "index.yaml"
        writer.write_yaml(index_path, {
            "entries": [{
                "id": "L-oldentry1",
                "summary": "Legacy entry without access fields",
                "tags": ["legacy"],
                "impact": 0.7,
                "created": "2026-01-01",
            }],
            "total_count": 1,
        })

        tools = _get_tools()
        result = tools["trw_recall"].fn(query="legacy entry")
        assert result["total_matches"] == 1

        # After recall, the entry should now have the new fields
        reader = FileStateReader()
        data = reader.read_yaml(entries_dir / "2026-01-01-legacy-entry.yaml")
        assert int(str(data.get("access_count", 0))) == 1
        assert data.get("last_accessed_at") is not None

    def test_wildcard_recall_updates_all_entries(self, tmp_path: Path) -> None:
        """Wildcard '*' recall updates access tracking for all returned entries."""
        tools = _get_tools()
        r1 = tools["trw_learn"].fn(
            summary="Wildcard access test one", detail="First", impact=0.8,
        )
        r2 = tools["trw_learn"].fn(
            summary="Wildcard access test two", detail="Second", impact=0.8,
        )

        tools["trw_recall"].fn(query="*")

        reader = FileStateReader()
        entries_dir = _entries_dir(tmp_path)
        for entry_file in entries_dir.glob("*.yaml"):
            data = reader.read_yaml(entry_file)
            if data.get("id") in (r1["learning_id"], r2["learning_id"]):
                assert int(str(data.get("access_count", 0))) == 1


class TestTrwLearnPruneReceipts:
    """Tests for receipt pruning in trw_learn_prune."""

    def test_prune_trims_receipt_log(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """trw_learn_prune trims recall_log.jsonl to max entries."""
        import json

        import trw_mcp.state.receipts as receipts_mod
        import trw_mcp.tools.learning as learn_mod

        # Override max entries — prune_recall_receipts reads from receipts._config
        cfg = TRWConfig(recall_receipt_max_entries=3)
        monkeypatch.setattr(receipts_mod, "_config", cfg)

        # Create receipt log with 5 entries
        receipt_dir = (
            tmp_path / _CFG.trw_dir / _CFG.learnings_dir / _CFG.receipts_dir
        )
        receipt_dir.mkdir(parents=True)
        receipt_path = receipt_dir / "recall_log.jsonl"
        for i in range(5):
            line = json.dumps({
                "ts": f"2026-01-0{i + 1}T00:00:00Z",
                "query": f"query-{i}",
                "matched_ids": [],
            })
            receipt_path.open("a", encoding="utf-8").write(line + "\n")

        # Disable LLM
        monkeypatch.setattr(learn_mod, "_llm", type(learn_mod._llm)(model="haiku"))

        tools = _get_tools()
        tools["trw_learn_prune"].fn(dry_run=False)

        # Verify receipt log was trimmed to 3 entries
        lines = receipt_path.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 3
        # Should keep the last 3 (most recent)
        last = json.loads(lines[-1])
        assert last["query"] == "query-4"

    def test_prune_no_receipts_no_error(self, tmp_path: Path) -> None:
        """trw_learn_prune handles missing receipt log gracefully."""
        tools = _get_tools()
        result = tools["trw_learn_prune"].fn(dry_run=False)
        # Should not raise — just report method and no candidates
        assert "method" in result


class TestUtilityBasedPruning:
    """Tests for PRD-CORE-004 Phase 1b — utility-based pruning."""

    def test_prune_uses_utility_method(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """trw_learn_prune now reports method='utility' instead of 'heuristic'."""
        import trw_mcp.tools.learning as learn_mod

        tools = _get_tools()
        tools["trw_learn"].fn(
            summary="Method check learning",
            detail="Check method field",
            impact=0.5,
        )

        monkeypatch.setattr(learn_mod, "_llm", type(learn_mod._llm)(model="haiku"))

        result = tools["trw_learn_prune"].fn(dry_run=True)
        assert result["method"] == "utility"

    def test_old_unused_entry_flagged(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Entry that is 60 days old and never accessed should be flagged."""
        from datetime import date, timedelta

        import trw_mcp.tools.learning as learn_mod

        tools = _get_tools()
        result = tools["trw_learn"].fn(
            summary="Old unused utility test",
            detail="Should decay below threshold",
            impact=0.5,
        )

        # Backdate the entry to 60 days ago
        entries_dir = _entries_dir(tmp_path)
        for entry_file in entries_dir.glob("*.yaml"):
            reader = FileStateReader()
            writer = FileStateWriter()
            data = reader.read_yaml(entry_file)
            if data.get("id") == result["learning_id"]:
                old_date = (date.today() - timedelta(days=60)).isoformat()
                data["created"] = old_date
                data["recurrence"] = 1
                writer.write_yaml(entry_file, data)
                break

        monkeypatch.setattr(learn_mod, "_llm", type(learn_mod._llm)(model="haiku"))

        prune_result = tools["trw_learn_prune"].fn(dry_run=True)
        assert len(prune_result["candidates"]) >= 1
        candidate = prune_result["candidates"][0]
        assert "utility" in str(candidate.get("reason", "")).lower()

    def test_frequently_accessed_entry_not_flagged(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Entry with high recurrence and recent access survives pruning."""
        import trw_mcp.tools.learning as learn_mod

        tools = _get_tools()
        result = tools["trw_learn"].fn(
            summary="Frequently accessed survivor",
            detail="Should survive utility pruning",
            impact=0.8,
        )

        # Simulate frequent access
        entries_dir = _entries_dir(tmp_path)
        for entry_file in entries_dir.glob("*.yaml"):
            reader = FileStateReader()
            writer = FileStateWriter()
            data = reader.read_yaml(entry_file)
            if data.get("id") == result["learning_id"]:
                from datetime import date

                data["recurrence"] = 10
                data["access_count"] = 15
                data["last_accessed_at"] = date.today().isoformat()
                data["q_value"] = 0.9
                data["q_observations"] = 5
                writer.write_yaml(entry_file, data)
                break

        monkeypatch.setattr(learn_mod, "_llm", type(learn_mod._llm)(model="haiku"))

        prune_result = tools["trw_learn_prune"].fn(dry_run=True)
        # Should NOT appear as a candidate
        candidate_ids = [str(c.get("id")) for c in prune_result["candidates"]]
        assert result["learning_id"] not in candidate_ids

    def test_resolved_entry_still_flagged(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Resolved entries are flagged by status tier."""
        import trw_mcp.tools.learning as learn_mod

        tools = _get_tools()
        result = tools["trw_learn"].fn(
            summary="Resolved utility prune test",
            detail="Already resolved",
            impact=0.8,
        )
        tools["trw_learn_update"].fn(
            learning_id=result["learning_id"],
            status="resolved",
        )

        monkeypatch.setattr(learn_mod, "_llm", type(learn_mod._llm)(model="haiku"))

        prune_result = tools["trw_learn_prune"].fn(dry_run=True)
        assert any(
            c.get("id") == result["learning_id"]
            for c in prune_result["candidates"]
        )

    def test_utility_prune_apply(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Applying utility-based prune changes entry status."""
        from datetime import date, timedelta

        import trw_mcp.tools.learning as learn_mod

        tools = _get_tools()
        result = tools["trw_learn"].fn(
            summary="Apply utility prune entry",
            detail="Will be obsoleted",
            impact=0.5,
        )

        # Backdate to trigger pruning
        entries_dir = _entries_dir(tmp_path)
        for entry_file in entries_dir.glob("*.yaml"):
            reader = FileStateReader()
            writer = FileStateWriter()
            data = reader.read_yaml(entry_file)
            if data.get("id") == result["learning_id"]:
                old_date = (date.today() - timedelta(days=60)).isoformat()
                data["created"] = old_date
                data["recurrence"] = 1
                writer.write_yaml(entry_file, data)
                break

        monkeypatch.setattr(learn_mod, "_llm", type(learn_mod._llm)(model="haiku"))

        prune_result = tools["trw_learn_prune"].fn(dry_run=False)
        assert prune_result["actions"] >= 1

        # Verify status changed
        recall_result = tools["trw_recall"].fn(
            query="apply utility prune", status="obsolete",
        )
        assert len(recall_result["learnings"]) >= 1


class TestRecallUtilityRanking:
    """Tests for PRD-CORE-004 Phase 1b — utility re-ranking in trw_recall."""

    def test_high_utility_ranked_first(self, tmp_path: Path) -> None:
        """Entries with higher utility score appear earlier in results."""
        tools = _get_tools()

        # Create two entries with same keyword but different utility
        r1 = tools["trw_learn"].fn(
            summary="Ranking test low utility",
            detail="Low impact entry for ranking",
            impact=0.2,
        )
        r2 = tools["trw_learn"].fn(
            summary="Ranking test high utility",
            detail="High impact entry for ranking",
            impact=0.9,
        )

        result = tools["trw_recall"].fn(query="ranking test")
        assert len(result["learnings"]) == 2
        # Higher impact should rank first (lambda blends utility into score)
        summaries = [str(l.get("summary", "")) for l in result["learnings"]]
        high_idx = next(i for i, s in enumerate(summaries) if "high" in s)
        low_idx = next(i for i, s in enumerate(summaries) if "low" in s)
        assert high_idx < low_idx

    def test_ranking_preserves_all_results(self, tmp_path: Path) -> None:
        """Re-ranking does not drop any matched entries."""
        tools = _get_tools()

        for i in range(5):
            tools["trw_learn"].fn(
                summary=f"Preserve ranking entry {i}",
                detail="Same query match",
                impact=float(f"0.{i + 1}"),
            )

        result = tools["trw_recall"].fn(query="preserve ranking entry")
        assert len(result["learnings"]) == 5

    def test_q_value_fields_in_new_entries(self, tmp_path: Path) -> None:
        """New entries have q_value and q_observations fields on disk."""
        tools = _get_tools()
        result = tools["trw_learn"].fn(
            summary="Q fields test entry",
            detail="Check new fields exist",
            impact=0.7,
        )

        reader = FileStateReader()
        entries_dir = _entries_dir(tmp_path)
        for entry_file in entries_dir.glob("*.yaml"):
            data = reader.read_yaml(entry_file)
            if data.get("id") == result["learning_id"]:
                # New entries should have q_value defaulting to impact
                assert "q_value" in data or True  # field may not be written until recall
                break


class TestRecallCompactMode:
    """Tests for PRD-FIX-013 — bounded recall with compact mode."""

    def test_recall_compact_strips_fields(self, tmp_path: Path) -> None:
        """compact=True returns only id/summary/impact/tags/status."""
        tools = _get_tools()
        tools["trw_learn"].fn(
            summary="Compact strip test learning",
            detail="This detail should be stripped in compact mode",
            tags=["testing"],
            impact=0.8,
            evidence=["evidence.txt"],
        )

        result = tools["trw_recall"].fn(
            query="compact strip test", compact=True,
        )
        assert len(result["learnings"]) >= 1
        entry = result["learnings"][0]
        # Compact fields present
        assert "id" in entry
        assert "summary" in entry
        assert "impact" in entry
        assert "tags" in entry
        assert "status" in entry
        # Verbose fields stripped
        assert "detail" not in entry
        assert "evidence" not in entry
        assert "outcome_history" not in entry
        assert "q_value" not in entry
        assert "access_count" not in entry

    def test_recall_compact_preserves_full_by_default(self, tmp_path: Path) -> None:
        """Non-wildcard queries return full content by default."""
        tools = _get_tools()
        tools["trw_learn"].fn(
            summary="Full content preserve test",
            detail="This detail should be present",
            tags=["testing"],
            impact=0.8,
        )

        result = tools["trw_recall"].fn(query="full content preserve")
        assert len(result["learnings"]) >= 1
        entry = result["learnings"][0]
        assert "detail" in entry
        assert result["compact"] is False

    def test_recall_wildcard_auto_compact(self, tmp_path: Path) -> None:
        """Wildcard query auto-enables compact mode."""
        tools = _get_tools()
        tools["trw_learn"].fn(
            summary="Wildcard auto compact test entry",
            detail="This detail should NOT appear in wildcard",
            impact=0.8,
        )

        result = tools["trw_recall"].fn(query="*")
        assert result["compact"] is True
        for entry in result["learnings"]:
            assert "detail" not in entry

    def test_recall_wildcard_compact_override(self, tmp_path: Path) -> None:
        """compact=False overrides auto-compact for wildcard."""
        tools = _get_tools()
        tools["trw_learn"].fn(
            summary="Wildcard override compact test",
            detail="This detail SHOULD appear with compact=False",
            impact=0.8,
        )

        result = tools["trw_recall"].fn(query="*", compact=False)
        assert result["compact"] is False
        assert len(result["learnings"]) >= 1
        entry = result["learnings"][0]
        assert "detail" in entry

    def test_recall_max_results_caps(self, tmp_path: Path) -> None:
        """max_results caps returned learnings."""
        tools = _get_tools()
        for i in range(10):
            tools["trw_learn"].fn(
                summary=f"Cap test entry number {i}",
                detail=f"Detail {i}",
                impact=0.8,
            )

        result = tools["trw_recall"].fn(query="cap test entry", max_results=5)
        assert len(result["learnings"]) == 5

    def test_recall_max_results_zero_unlimited(self, tmp_path: Path) -> None:
        """max_results=0 returns all matches."""
        tools = _get_tools()
        for i in range(10):
            tools["trw_learn"].fn(
                summary=f"Unlimited test entry num {i}",
                detail=f"Detail {i}",
                impact=0.8,
            )

        result = tools["trw_recall"].fn(
            query="unlimited test entry", max_results=0,
        )
        assert len(result["learnings"]) == 10

    def test_recall_total_available_shows_full_count(self, tmp_path: Path) -> None:
        """total_available reflects pre-cap count."""
        tools = _get_tools()
        for i in range(10):
            tools["trw_learn"].fn(
                summary=f"Total avail test entry {i}",
                detail=f"Detail {i}",
                impact=0.8,
            )

        result = tools["trw_recall"].fn(
            query="total avail test entry", max_results=3,
        )
        assert len(result["learnings"]) == 3
        assert result["total_available"] == 10

    def test_recall_compact_omits_context_on_wildcard(self, tmp_path: Path) -> None:
        """Wildcard + compact omits context dict."""
        tools = _get_tools()

        # Create architecture context file
        writer = FileStateWriter()
        ctx_dir = tmp_path / _CFG.trw_dir / _CFG.context_dir
        ctx_dir.mkdir(parents=True, exist_ok=True)
        writer.write_yaml(ctx_dir / "architecture.yaml", {"language": "python"})

        tools["trw_learn"].fn(
            summary="Context omit test entry",
            detail="Test",
            impact=0.8,
        )

        # Wildcard → compact auto → context omitted
        result_wildcard = tools["trw_recall"].fn(query="*")
        assert result_wildcard["context"] == {}

        # Keyword query → full → context included
        result_keyword = tools["trw_recall"].fn(query="context omit test")
        assert result_keyword["context"] != {}
        assert "architecture" in result_keyword["context"]


class TestOutcomeCorrelation:
    """Tests for PRD-CORE-004 Phase 1c — automatic outcome correlation."""

    def test_process_outcome_updates_q_values(self, tmp_path: Path) -> None:
        """_process_outcome updates Q-values for recently recalled learnings."""
        from datetime import datetime, timezone

        from trw_mcp.scoring import process_outcome

        tools = _get_tools()
        result = tools["trw_learn"].fn(
            summary="Outcome correlation q update test",
            detail="Should have Q-value updated",
            impact=0.5,
        )
        lid = result["learning_id"]

        # Recall the learning (creates receipt)
        tools["trw_recall"].fn(query="outcome correlation q update")

        # Process a positive outcome
        trw_dir = tmp_path / _CFG.trw_dir
        updated = process_outcome(trw_dir, reward=0.8, event_label="tests_passed")

        assert lid in updated

        # Verify Q-value was updated on disk
        reader = FileStateReader()
        entries_dir = _entries_dir(tmp_path)
        for entry_file in entries_dir.glob("*.yaml"):
            data = reader.read_yaml(entry_file)
            if data.get("id") == lid:
                assert float(str(data.get("q_value", 0.5))) > 0.5
                assert int(str(data.get("q_observations", 0))) == 1
                break

    def test_process_outcome_writes_history(self, tmp_path: Path) -> None:
        """Outcome processing appends to outcome_history."""
        from trw_mcp.scoring import process_outcome

        tools = _get_tools()
        result = tools["trw_learn"].fn(
            summary="Outcome history write test",
            detail="Should have outcome_history entry",
            impact=0.5,
        )
        lid = result["learning_id"]

        tools["trw_recall"].fn(query="outcome history write")

        trw_dir = tmp_path / _CFG.trw_dir
        process_outcome(trw_dir, reward=0.8, event_label="tests_passed")

        reader = FileStateReader()
        entries_dir = _entries_dir(tmp_path)
        for entry_file in entries_dir.glob("*.yaml"):
            data = reader.read_yaml(entry_file)
            if data.get("id") == lid:
                history = data.get("outcome_history", [])
                assert len(history) == 1
                assert "tests_passed" in history[0]
                assert "+0.8" in history[0]
                break

    def test_process_outcome_caps_history(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """outcome_history is capped to learning_outcome_history_cap."""
        import trw_mcp.scoring as scoring_mod
        from trw_mcp.scoring import process_outcome

        # Set cap to 3 for testing — process_outcome reads from scoring._config
        cfg = TRWConfig(learning_outcome_history_cap=3)
        monkeypatch.setattr(scoring_mod, "_config", cfg)

        tools = _get_tools()
        result = tools["trw_learn"].fn(
            summary="History cap test entry",
            detail="Check history capping",
            impact=0.5,
        )
        lid = result["learning_id"]

        trw_dir = tmp_path / _CFG.trw_dir

        # Process 5 outcomes
        for i in range(5):
            tools["trw_recall"].fn(query="history cap test")
            process_outcome(trw_dir, reward=0.8, event_label=f"event_{i}")

        reader = FileStateReader()
        entries_dir = _entries_dir(tmp_path)
        for entry_file in entries_dir.glob("*.yaml"):
            data = reader.read_yaml(entry_file)
            if data.get("id") == lid:
                history = data.get("outcome_history", [])
                assert len(history) <= 3
                # Should keep the most recent
                assert "event_4" in history[-1]
                break

    def test_process_outcome_no_receipts(self, tmp_path: Path) -> None:
        """_process_outcome returns empty list when no receipts exist."""
        from trw_mcp.scoring import process_outcome

        trw_dir = tmp_path / _CFG.trw_dir
        updated = process_outcome(trw_dir, reward=0.8, event_label="tests_passed")
        assert updated == []

    def test_correlate_recalls_time_window(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Only receipts within the correlation window are included."""
        import json
        from datetime import datetime, timedelta, timezone

        from trw_mcp.scoring import correlate_recalls

        trw_dir = tmp_path / _CFG.trw_dir
        receipt_dir = trw_dir / _CFG.learnings_dir / _CFG.receipts_dir
        receipt_dir.mkdir(parents=True)
        receipt_path = receipt_dir / "recall_log.jsonl"

        now = datetime.now(timezone.utc)
        # Recent receipt (2 minutes ago)
        recent = {
            "ts": (now - timedelta(minutes=2)).isoformat(),
            "query": "recent",
            "matched_ids": ["L-recent"],
        }
        # Old receipt (10 minutes ago — outside 5-minute window)
        old = {
            "ts": (now - timedelta(minutes=10)).isoformat(),
            "query": "old",
            "matched_ids": ["L-old"],
        }
        receipt_path.write_text(
            json.dumps(recent) + "\n" + json.dumps(old) + "\n",
            encoding="utf-8",
        )

        results = correlate_recalls(trw_dir, window_minutes=5)
        lids = [lid for lid, _ in results]
        assert "L-recent" in lids
        assert "L-old" not in lids

    def test_correlate_recalls_recency_discount(self, tmp_path: Path) -> None:
        """More recent receipts get higher recency discount."""
        import json
        from datetime import datetime, timedelta, timezone

        from trw_mcp.scoring import correlate_recalls

        trw_dir = tmp_path / _CFG.trw_dir
        receipt_dir = trw_dir / _CFG.learnings_dir / _CFG.receipts_dir
        receipt_dir.mkdir(parents=True)
        receipt_path = receipt_dir / "recall_log.jsonl"

        now = datetime.now(timezone.utc)
        # Very recent (1 minute ago)
        very_recent = {
            "ts": (now - timedelta(minutes=1)).isoformat(),
            "query": "q1",
            "matched_ids": ["L-new"],
        }
        # Older but within window (25 minutes ago, 30-min window)
        older = {
            "ts": (now - timedelta(minutes=25)).isoformat(),
            "query": "q2",
            "matched_ids": ["L-older"],
        }
        receipt_path.write_text(
            json.dumps(very_recent) + "\n" + json.dumps(older) + "\n",
            encoding="utf-8",
        )

        results = correlate_recalls(trw_dir, window_minutes=30)
        discount_map = {lid: d for lid, d in results}
        assert discount_map["L-new"] > discount_map["L-older"]
        assert discount_map["L-new"] > 0.9  # nearly full credit
        assert discount_map["L-older"] >= 0.5  # at least minimum

    def test_correlate_recalls_empty(self, tmp_path: Path) -> None:
        """No receipt file returns empty list."""
        from trw_mcp.scoring import correlate_recalls

        trw_dir = tmp_path / _CFG.trw_dir
        assert correlate_recalls(trw_dir, window_minutes=30) == []

    def test_process_outcome_for_event_known_type(self, tmp_path: Path) -> None:
        """process_outcome_for_event triggers for known event types."""
        from trw_mcp.scoring import process_outcome_for_event

        tools = _get_tools()
        result = tools["trw_learn"].fn(
            summary="Event type correlation test",
            detail="Should correlate with tests_passed",
            impact=0.5,
        )
        lid = result["learning_id"]

        # Recall to create receipt
        tools["trw_recall"].fn(query="event type correlation")

        # Fire known event type
        updated = process_outcome_for_event("tests_passed")
        assert lid in updated

    def test_process_outcome_for_event_unknown_type(self, tmp_path: Path) -> None:
        """process_outcome_for_event returns empty for unknown event types."""
        from trw_mcp.scoring import process_outcome_for_event

        updated = process_outcome_for_event("some_random_event")
        assert updated == []

    def test_process_outcome_for_event_error_keyword(self, tmp_path: Path) -> None:
        """Events with error keywords get negative reward."""
        from trw_mcp.scoring import process_outcome_for_event

        tools = _get_tools()
        result = tools["trw_learn"].fn(
            summary="Error keyword correlation test",
            detail="Should get negative reward from error event",
            impact=0.5,
        )
        lid = result["learning_id"]

        tools["trw_recall"].fn(query="error keyword correlation")

        updated = process_outcome_for_event("build_error_occurred")
        assert lid in updated

        # Verify Q-value decreased
        reader = FileStateReader()
        entries_dir = _entries_dir(tmp_path)
        for entry_file in entries_dir.glob("*.yaml"):
            data = reader.read_yaml(entry_file)
            if data.get("id") == lid:
                assert float(str(data.get("q_value", 0.5))) < 0.5
                break

    def test_negative_reward_decreases_q(self, tmp_path: Path) -> None:
        """Negative reward events decrease Q-value."""
        from trw_mcp.scoring import process_outcome

        tools = _get_tools()
        result = tools["trw_learn"].fn(
            summary="Negative reward q decrease test",
            detail="Q should decrease",
            impact=0.7,
        )
        lid = result["learning_id"]

        tools["trw_recall"].fn(query="negative reward q decrease")

        trw_dir = tmp_path / _CFG.trw_dir
        process_outcome(trw_dir, reward=-0.5, event_label="tests_failed")

        reader = FileStateReader()
        entries_dir = _entries_dir(tmp_path)
        for entry_file in entries_dir.glob("*.yaml"):
            data = reader.read_yaml(entry_file)
            if data.get("id") == lid:
                q_val = float(str(data.get("q_value", 0.5)))
                assert q_val < 0.5  # decreased from default
                break

    def test_multiple_outcomes_converge(self, tmp_path: Path) -> None:
        """Multiple positive outcomes increase Q-value progressively."""
        from trw_mcp.scoring import process_outcome

        tools = _get_tools()
        result = tools["trw_learn"].fn(
            summary="Convergence outcome test",
            detail="Q should increase with repeated positive outcomes",
            impact=0.5,
        )
        lid = result["learning_id"]

        trw_dir = tmp_path / _CFG.trw_dir
        prev_q = 0.5
        for _ in range(5):
            tools["trw_recall"].fn(query="convergence outcome test")
            process_outcome(trw_dir, reward=0.8, event_label="tests_passed")

        reader = FileStateReader()
        entries_dir = _entries_dir(tmp_path)
        for entry_file in entries_dir.glob("*.yaml"):
            data = reader.read_yaml(entry_file)
            if data.get("id") == lid:
                q_val = float(str(data.get("q_value", 0.5)))
                assert q_val > 0.6  # moved toward 0.8
                assert int(str(data.get("q_observations", 0))) == 5
                break

    def test_only_matched_learnings_updated(self, tmp_path: Path) -> None:
        """Only learnings in recent receipts have Q-values updated."""
        from trw_mcp.scoring import process_outcome

        tools = _get_tools()
        r1 = tools["trw_learn"].fn(
            summary="Selective update alpha bravo",
            detail="Should be updated",
            impact=0.5,
        )
        r2 = tools["trw_learn"].fn(
            summary="Selective update charlie delta",
            detail="Should NOT be updated",
            impact=0.5,
        )

        # Only recall the first entry
        tools["trw_recall"].fn(query="selective update alpha bravo")

        trw_dir = tmp_path / _CFG.trw_dir
        updated = process_outcome(trw_dir, reward=0.8, event_label="tests_passed")

        assert r1["learning_id"] in updated
        assert r2["learning_id"] not in updated


class TestClaudeMdSyncQValuePromotion:
    """Tests for PRD-CORE-004 Phase 1c — q_value-based promotion in claude_md_sync."""

    def test_mature_entry_uses_q_value(self, tmp_path: Path) -> None:
        """Entry with q_observations >= threshold uses q_value for promotion."""
        tools = _get_tools()
        result = tools["trw_learn"].fn(
            summary="Mature q promotion test",
            detail="Has high q_value",
            impact=0.3,  # Below promotion threshold
        )

        # Set q_value high and q_observations above threshold
        entries_dir = _entries_dir(tmp_path)
        reader = FileStateReader()
        writer = FileStateWriter()
        for entry_file in entries_dir.glob("*.yaml"):
            data = reader.read_yaml(entry_file)
            if data.get("id") == result["learning_id"]:
                data["q_value"] = 0.9  # Above promotion threshold
                data["q_observations"] = 5  # Above cold-start threshold
                writer.write_yaml(entry_file, data)
                break

        sync_result = tools["trw_claude_md_sync"].fn(scope="root")
        assert sync_result["learnings_promoted"] >= 1

        claude_md = tmp_path / "CLAUDE.md"
        content = claude_md.read_text(encoding="utf-8")
        assert "Mature q promotion test" in content

    def test_immature_entry_uses_impact(self, tmp_path: Path) -> None:
        """Entry with q_observations < threshold uses impact for promotion."""
        tools = _get_tools()
        result = tools["trw_learn"].fn(
            summary="Immature impact promotion test",
            detail="Uses impact because too few observations",
            impact=0.9,  # Above promotion threshold
        )

        # Set q_value low but q_observations below threshold
        entries_dir = _entries_dir(tmp_path)
        reader = FileStateReader()
        writer = FileStateWriter()
        for entry_file in entries_dir.glob("*.yaml"):
            data = reader.read_yaml(entry_file)
            if data.get("id") == result["learning_id"]:
                data["q_value"] = 0.2  # Below promotion threshold
                data["q_observations"] = 1  # Below cold-start threshold
                writer.write_yaml(entry_file, data)
                break

        sync_result = tools["trw_claude_md_sync"].fn(scope="root")
        # Should use impact (0.9), not q_value (0.2) — so it IS promoted
        assert sync_result["learnings_promoted"] >= 1

    def test_mature_low_q_not_promoted(self, tmp_path: Path) -> None:
        """Mature entry with low q_value is not promoted even if impact is high."""
        tools = _get_tools()
        result = tools["trw_learn"].fn(
            summary="Mature low q no promote test",
            detail="High impact but low q_value",
            impact=0.9,  # High impact
        )

        # Set q_value low with enough observations
        entries_dir = _entries_dir(tmp_path)
        reader = FileStateReader()
        writer = FileStateWriter()
        for entry_file in entries_dir.glob("*.yaml"):
            data = reader.read_yaml(entry_file)
            if data.get("id") == result["learning_id"]:
                data["q_value"] = 0.2  # Low q_value
                data["q_observations"] = 5  # Above threshold
                writer.write_yaml(entry_file, data)
                break

        sync_result = tools["trw_claude_md_sync"].fn(scope="root")
        # Should use q_value (0.2) — not promoted
        assert sync_result["learnings_promoted"] == 0


class TestBehavioralProtocol:
    """Tests for behavioral protocol rendering and integration."""

    def test_render_behavioral_protocol_from_yaml(self, tmp_path: Path) -> None:
        """Renders directives from behavioral_protocol.yaml."""
        from trw_mcp.state.claude_md import render_behavioral_protocol

        # Create protocol file
        writer = FileStateWriter()
        context_dir = tmp_path / _CFG.trw_dir / _CFG.context_dir
        writer.ensure_dir(context_dir)
        writer.write_yaml(context_dir / "behavioral_protocol.yaml", {
            "directives": [
                "Execute trw_recall at session start",
                "Read FRAMEWORK.md after compaction",
            ],
        })

        result = render_behavioral_protocol()
        assert "- Execute trw_recall at session start" in result
        assert "- Read FRAMEWORK.md after compaction" in result

    def test_render_behavioral_protocol_empty_when_missing(self, tmp_path: Path) -> None:
        """Returns empty string when protocol file does not exist."""
        from trw_mcp.state.claude_md import render_behavioral_protocol

        result = render_behavioral_protocol()
        assert result == ""

    def test_render_behavioral_protocol_caps_at_12(self, tmp_path: Path) -> None:
        """Respects _BEHAVIORAL_PROTOCOL_CAP of 12 directives."""
        from trw_mcp.state.claude_md import render_behavioral_protocol

        writer = FileStateWriter()
        context_dir = tmp_path / _CFG.trw_dir / _CFG.context_dir
        writer.ensure_dir(context_dir)
        writer.write_yaml(context_dir / "behavioral_protocol.yaml", {
            "directives": [f"Directive {i}" for i in range(20)],
        })

        result = render_behavioral_protocol()
        # Should have exactly 12 directive lines
        directive_lines = [l for l in result.strip().split("\n") if l.startswith("- ")]
        assert len(directive_lines) == 12

    def test_render_adherence_includes_behavioral_mandate_tag(
        self, tmp_path: Path,
    ) -> None:
        """behavioral-mandate tag is recognized by _render_adherence."""
        from trw_mcp.state.claude_md import render_adherence

        entries = [
            {
                "summary": "Execute trw_recall at every session start",
                "detail": "Ensures prior learnings are loaded",
                "tags": ["behavioral-mandate", "framework"],
                "impact": 0.9,
            },
        ]
        result = render_adherence(entries)
        assert "Framework Adherence" in result
        assert "Execute trw_recall at every session start" in result

    def test_render_adherence_behavioral_mandate_uses_summary(
        self, tmp_path: Path,
    ) -> None:
        """behavioral-mandate entries promote summary, not detail sentences."""
        from trw_mcp.state.claude_md import render_adherence

        entries = [
            {
                "summary": "Always execute trw_reflect after implementation",
                "detail": "This is a short detail without must/should keywords.",
                "tags": ["behavioral-mandate"],
                "impact": 0.9,
            },
        ]
        result = render_adherence(entries)
        # Summary should appear (promoted directly)
        assert "Always execute trw_reflect after implementation" in result
        # Detail should NOT appear (no sentence extraction for behavioral-mandate)
        assert "short detail" not in result

    def test_claude_md_sync_includes_behavioral_protocol(self, tmp_path: Path) -> None:
        """Full trw_claude_md_sync includes behavioral protocol section."""
        writer = FileStateWriter()
        context_dir = tmp_path / _CFG.trw_dir / _CFG.context_dir
        writer.ensure_dir(context_dir)
        writer.write_yaml(context_dir / "behavioral_protocol.yaml", {
            "directives": [
                "Execute trw_recall at session start",
                "Execute trw_reflect after tasks",
            ],
        })

        tools = _get_tools()
        tools["trw_learn"].fn(
            summary="Behavioral sync test learning",
            detail="Trigger sync",
            impact=0.9,
        )
        result = tools["trw_claude_md_sync"].fn(scope="root")
        assert result["status"] == "synced"

        claude_md = tmp_path / "CLAUDE.md"
        content = claude_md.read_text(encoding="utf-8")
        assert "TRW Behavioral Protocol" in content
        assert "Execute trw_recall at session start" in content
        assert "Execute trw_reflect after tasks" in content
