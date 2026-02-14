"""Tests for PRD-CORE-019: Session ceremony composite tools.

Covers:
- trw_session_start: recall + status bundling, partial failure resilience
- trw_deliver: reflect + checkpoint + claude_md_sync + index_sync bundling
- _find_active_run helper
- _do_checkpoint, _do_reflect, _do_claude_md_sync, _do_index_sync internals
- _do_auto_progress: PRD auto-progression during delivery (GAP-PROC-001)
- _do_debt_md_sync: Debt markdown auto-generation (GAP-PROC-004)
- _generate_debt_markdown: Markdown rendering from registry entries
- Integration tests for partial failure resilience (Sprint 13, GAP-TEST-003)
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from trw_mcp.tools.ceremony import (
    _do_auto_progress,
    _do_checkpoint,
    _do_claude_md_sync,
    _do_debt_md_sync,
    _do_index_sync,
    _do_reflect,
    _find_active_run,
    _generate_debt_markdown,
    _get_run_status,
)


# --- Fixtures ---


@pytest.fixture()
def trw_project(tmp_path: Path) -> Path:
    """Create a minimal .trw/ project structure."""
    trw_dir = tmp_path / ".trw"
    learnings_dir = trw_dir / "learnings" / "entries"
    learnings_dir.mkdir(parents=True)
    (trw_dir / "reflections").mkdir()
    (trw_dir / "context").mkdir()

    # Create a sample learning entry
    (learnings_dir / "2026-02-10-sample.yaml").write_text(
        "id: L-sample001\nsummary: Test learning\ndetail: Some detail\n"
        "status: active\nimpact: 0.8\ntags:\n  - testing\n"
        "access_count: 0\nq_observations: 0\nq_value: 0.5\n"
        "source_type: agent\nsource_identity: ''\n",
        encoding="utf-8",
    )

    # Create index.yaml
    (trw_dir / "learnings" / "index.yaml").write_text(
        "total_entries: 1\n", encoding="utf-8",
    )

    return tmp_path


@pytest.fixture()
def run_dir(tmp_path: Path) -> Path:
    """Create a minimal run directory structure."""
    d = tmp_path / "docs" / "task" / "runs" / "20260211T120000Z-test"
    meta = d / "meta"
    meta.mkdir(parents=True)
    (meta / "run.yaml").write_text(
        "run_id: test-run\nstatus: active\nphase: implement\ntask_name: test-task\n",
        encoding="utf-8",
    )
    # Create empty events.jsonl
    (meta / "events.jsonl").write_text("", encoding="utf-8")
    return d


# --- _find_active_run ---


class TestFindActiveRun:
    """Helper function for locating active runs."""

    def test_returns_none_when_no_task_root(self, tmp_path: Path) -> None:
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        with patch("trw_mcp.tools.ceremony.resolve_trw_dir", return_value=trw_dir):
            result = _find_active_run()
        assert result is None

    def test_finds_run_directory(self, tmp_path: Path, run_dir: Path) -> None:
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        with patch("trw_mcp.tools.ceremony.resolve_trw_dir", return_value=trw_dir):
            with patch("trw_mcp.tools.ceremony._config") as mock_config:
                mock_config.task_root = "docs"
                result = _find_active_run()
        assert result is not None
        assert "20260211T120000Z-test" in str(result)


# --- _get_run_status ---


class TestGetRunStatus:
    """Status extraction from run directory."""

    def test_extracts_status(self, run_dir: Path) -> None:
        result = _get_run_status(run_dir)
        assert result["phase"] == "implement"
        assert result["status"] == "active"
        assert result["task_name"] == "test-task"

    def test_handles_missing_run_yaml(self, tmp_path: Path) -> None:
        result = _get_run_status(tmp_path)
        assert result["active_run"] == str(tmp_path)


# --- _do_checkpoint ---


class TestDoCheckpoint:
    """Checkpoint creation during delivery."""

    def test_creates_checkpoint_file(self, run_dir: Path) -> None:
        _do_checkpoint(run_dir, "delivery")
        cp_path = run_dir / "meta" / "checkpoints.jsonl"
        assert cp_path.exists()
        data = json.loads(cp_path.read_text(encoding="utf-8").strip())
        assert data["message"] == "delivery"
        assert "ts" in data

    def test_appends_checkpoint_event(self, run_dir: Path) -> None:
        _do_checkpoint(run_dir, "delivery")
        events_path = run_dir / "meta" / "events.jsonl"
        lines = [l for l in events_path.read_text(encoding="utf-8").strip().split("\n") if l]
        assert len(lines) == 1
        event = json.loads(lines[0])
        assert event["event"] == "checkpoint"


# --- _do_reflect ---


class TestDoReflect:
    """Reflection during delivery ceremony."""

    def test_returns_success_with_empty_events(self, trw_project: Path) -> None:
        trw_dir = trw_project / ".trw"
        result = _do_reflect(trw_dir, None)
        assert result["status"] == "success"
        assert result["events_analyzed"] == 0

    def test_analyzes_events_from_run(self, trw_project: Path, run_dir: Path) -> None:
        # Add some events
        events_path = run_dir / "meta" / "events.jsonl"
        events = [
            {"ts": "2026-02-11T12:00:00Z", "event": "phase_enter", "data": {"phase": "implement"}},
            {"ts": "2026-02-11T12:01:00Z", "event": "shard_complete", "data": {}},
        ]
        events_path.write_text(
            "\n".join(json.dumps(e) for e in events) + "\n",
            encoding="utf-8",
        )
        trw_dir = trw_project / ".trw"
        result = _do_reflect(trw_dir, run_dir)
        assert result["status"] == "success"
        assert result["events_analyzed"] == 2


# --- _do_claude_md_sync ---


class TestDoClaudeMdSync:
    """CLAUDE.md sync during delivery ceremony."""

    def test_creates_or_updates_claude_md(self, trw_project: Path) -> None:
        trw_dir = trw_project / ".trw"
        with (
            patch("trw_mcp.tools.ceremony.resolve_project_root", return_value=trw_project),
            patch("trw_mcp.tools.ceremony.resolve_trw_dir", return_value=trw_dir),
            patch("trw_mcp.state.claude_md.resolve_project_root", return_value=trw_project),
        ):
            result = _do_claude_md_sync(trw_dir)
        assert result["status"] == "success"
        assert "learnings_promoted" in result

    def test_deliver_includes_ceremony_sections(self, trw_project: Path) -> None:
        """trw_deliver path produces CLAUDE.md with ceremony content."""
        trw_dir = trw_project / ".trw"
        with (
            patch("trw_mcp.tools.ceremony.resolve_project_root", return_value=trw_project),
            patch("trw_mcp.tools.ceremony.resolve_trw_dir", return_value=trw_dir),
            patch("trw_mcp.state.claude_md.resolve_project_root", return_value=trw_project),
        ):
            result = _do_claude_md_sync(trw_dir)
        assert result["status"] == "success"

        claude_md = trw_project / "CLAUDE.md"
        content = claude_md.read_text(encoding="utf-8")
        # Ceremony sections present
        assert "### Execution Phases" in content
        assert "### Tool Lifecycle" in content
        assert "### Example Flows" in content
        assert "`trw_session_start`" in content
        assert "`trw_deliver`" in content
        # No unreplaced placeholders
        assert "{{ceremony_phases}}" not in content
        assert "{{ceremony_table}}" not in content
        assert "{{ceremony_flows}}" not in content


# --- _do_index_sync ---


class TestDoIndexSync:
    """INDEX.md/ROADMAP.md sync during delivery ceremony."""

    def test_syncs_index_and_roadmap(self, tmp_path: Path) -> None:
        prds_dir = tmp_path / "docs" / "requirements-aare-f" / "prds"
        prds_dir.mkdir(parents=True)
        (prds_dir / "PRD-CORE-001.md").write_text(
            "---\nprd:\n  id: PRD-CORE-001\n  title: Test\n"
            "  status: done\n  priority: P0\n  category: CORE\n---\n",
            encoding="utf-8",
        )
        with patch("trw_mcp.tools.ceremony.resolve_project_root", return_value=tmp_path):
            result = _do_index_sync()
        assert result["status"] == "success"
        assert (prds_dir.parent / "INDEX.md").exists()
        assert (prds_dir.parent / "ROADMAP.md").exists()


# --- _do_auto_progress ---


class TestDoAutoProgress:
    """PRD auto-progression during delivery ceremony (GAP-PROC-001)."""

    def test_skips_when_no_active_run(self) -> None:
        result = _do_auto_progress(None)
        assert result["status"] == "skipped"
        assert result["reason"] == "no_active_run"

    def test_skips_when_prds_dir_missing(self, tmp_path: Path) -> None:
        run_dir = tmp_path / "run"
        (run_dir / "meta").mkdir(parents=True)
        (run_dir / "meta" / "run.yaml").write_text(
            "run_id: test\nstatus: active\nphase: deliver\nprd_scope: []\n",
            encoding="utf-8",
        )
        with patch("trw_mcp.tools.ceremony.resolve_project_root", return_value=tmp_path):
            result = _do_auto_progress(run_dir)
        assert result["status"] == "skipped"
        assert result["reason"] == "prds_dir_not_found"

    def test_progresses_implemented_to_done_on_deliver(self, tmp_path: Path) -> None:
        # Set up PRD
        prds_dir = tmp_path / "docs" / "requirements-aare-f" / "prds"
        prds_dir.mkdir(parents=True)
        (prds_dir / "PRD-CORE-099.md").write_text(
            "---\nprd:\n  id: PRD-CORE-099\n  title: Test\n"
            "  status: implemented\n  priority: P1\n  category: CORE\n---\n"
            "# PRD-CORE-099\nSome content for density.\n",
            encoding="utf-8",
        )
        # Set up run with prd_scope
        run_dir = tmp_path / "docs" / "task" / "runs" / "20260214T000000Z-test"
        (run_dir / "meta").mkdir(parents=True)
        (run_dir / "meta" / "run.yaml").write_text(
            "run_id: test\nstatus: active\nphase: deliver\n"
            "prd_scope:\n  - PRD-CORE-099\n",
            encoding="utf-8",
        )
        (run_dir / "meta" / "events.jsonl").write_text("", encoding="utf-8")
        with patch("trw_mcp.tools.ceremony.resolve_project_root", return_value=tmp_path):
            result = _do_auto_progress(run_dir)
        assert result["status"] == "success"
        assert result["applied"] >= 1
        # Verify file was updated
        content = (prds_dir / "PRD-CORE-099.md").read_text(encoding="utf-8")
        assert "status: done" in content

    def test_returns_zero_applied_for_terminal_statuses(self, tmp_path: Path) -> None:
        prds_dir = tmp_path / "docs" / "requirements-aare-f" / "prds"
        prds_dir.mkdir(parents=True)
        (prds_dir / "PRD-CORE-099.md").write_text(
            "---\nprd:\n  id: PRD-CORE-099\n  title: Test\n"
            "  status: done\n  priority: P1\n  category: CORE\n---\n",
            encoding="utf-8",
        )
        run_dir = tmp_path / "docs" / "task" / "runs" / "20260214T000000Z-test"
        (run_dir / "meta").mkdir(parents=True)
        (run_dir / "meta" / "run.yaml").write_text(
            "run_id: test\nstatus: active\nphase: deliver\n"
            "prd_scope:\n  - PRD-CORE-099\n",
            encoding="utf-8",
        )
        (run_dir / "meta" / "events.jsonl").write_text("", encoding="utf-8")
        with patch("trw_mcp.tools.ceremony.resolve_project_root", return_value=tmp_path):
            result = _do_auto_progress(run_dir)
        assert result["status"] == "success"
        assert result["applied"] == 0


# --- _generate_debt_markdown ---


class TestGenerateDebtMarkdown:
    """Markdown rendering from debt registry entries (GAP-PROC-004)."""

    def test_renders_active_entries(self) -> None:
        entries: list[dict[str, object]] = [
            {
                "id": "DEBT-001",
                "title": "Test debt item",
                "description": "Something needs fixing",
                "priority": "medium",
                "status": "discovered",
                "affected_files": ["tools/foo.py"],
                "estimated_effort": "1 hour",
            },
        ]
        md = _generate_debt_markdown(entries)
        assert "# Technical Debt Registry" in md
        assert "DEBT-001" in md
        assert "Test debt item" in md
        assert "Medium" in md
        assert "tools/foo.py" in md
        assert "**Total active** | **1**" in md

    def test_renders_resolved_entries(self) -> None:
        entries: list[dict[str, object]] = [
            {
                "id": "DEBT-002",
                "title": "Resolved item",
                "priority": "high",
                "status": "resolved",
                "resolved_by_prd": "PRD-FIX-001",
                "resolved_at": "2026-02-13",
            },
        ]
        md = _generate_debt_markdown(entries)
        assert "## Resolved Debt" in md
        assert "~~DEBT-002~~" in md
        assert "PRD-FIX-001" in md
        assert "**Total active** | **0**" in md
        assert "**Total resolved** | **1**" in md

    def test_renders_mixed_entries_grouped_by_priority(self) -> None:
        entries: list[dict[str, object]] = [
            {"id": "DEBT-A", "title": "Critical", "priority": "critical", "status": "discovered"},
            {"id": "DEBT-B", "title": "Low item", "priority": "low", "status": "discovered"},
            {"id": "DEBT-C", "title": "Done", "priority": "high", "status": "resolved"},
        ]
        md = _generate_debt_markdown(entries)
        assert "### Critical Priority" in md
        assert "### Low Priority" in md
        assert "~~DEBT-C~~" in md

    def test_empty_registry_renders_header(self) -> None:
        md = _generate_debt_markdown([])
        assert "# Technical Debt Registry" in md
        assert "**Total active** | **0**" in md


# --- _do_debt_md_sync ---


class TestDoDebtMdSync:
    """Debt markdown auto-generation during delivery (GAP-PROC-004)."""

    def test_generates_debt_markdown_from_registry(self, tmp_path: Path) -> None:
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        registry_yaml = (
            "version: '1.0'\n"
            "entries:\n"
            "- id: DEBT-010\n"
            "  title: Non-Atomic Writes\n"
            "  description: Two writes are non-atomic\n"
            "  priority: medium\n"
            "  status: discovered\n"
            "  classification: deferrable-local\n"
            "  category: code_quality\n"
            "  affected_files:\n"
            "  - tools/requirements.py\n"
            "  decay_score: 0.5\n"
            "  assessment_count: 1\n"
        )
        (trw_dir / "debt-registry.yaml").write_text(registry_yaml, encoding="utf-8")

        # Create target directory
        debt_dir = tmp_path / "docs" / "requirements-aare-f"
        debt_dir.mkdir(parents=True)

        with patch("trw_mcp.tools.ceremony.resolve_project_root", return_value=tmp_path):
            result = _do_debt_md_sync(trw_dir)

        assert result["status"] == "success"
        assert result["active_entries"] == 1
        assert result["resolved_entries"] == 0

        target_path = debt_dir / "TECHNICAL-DEBT.md"
        assert target_path.exists()
        content = target_path.read_text(encoding="utf-8")
        assert "DEBT-010" in content
        assert "Non-Atomic Writes" in content

    def test_skips_when_no_registry(self, tmp_path: Path) -> None:
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()

        with patch("trw_mcp.tools.ceremony.resolve_project_root", return_value=tmp_path):
            result = _do_debt_md_sync(trw_dir)

        assert result["status"] == "skipped"
        assert result["reason"] == "no_debt_registry"


# --- Integration tests: partial failure resilience (GAP-TEST-003) ---


def _make_ceremony_server(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> dict[str, object]:
    """Create a FastMCP server with ceremony tools and patched project root."""
    from fastmcp import FastMCP
    from trw_mcp.tools.ceremony import register_ceremony_tools
    import trw_mcp.tools.ceremony as ceremony_mod

    monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setattr(ceremony_mod, "_config", ceremony_mod.TRWConfig())

    srv = FastMCP("test")
    register_ceremony_tools(srv)
    return {t.name: t for t in srv._tool_manager._tools.values()}


@pytest.mark.integration
class TestSessionStartPartialFailure:
    """trw_session_start resilience when sub-operations fail."""

    def test_returns_result_when_recall_fails(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """If recall raises, status step still runs and result is returned."""
        tools = _make_ceremony_server(monkeypatch, tmp_path)
        trw_dir = tmp_path / ".trw"
        (trw_dir / "learnings" / "entries").mkdir(parents=True)
        (trw_dir / "context").mkdir(parents=True)

        with (
            patch(
                "trw_mcp.tools.ceremony.resolve_trw_dir",
                side_effect=Exception("recall boom"),
            ),
            patch(
                "trw_mcp.tools.ceremony._find_active_run",
                return_value=None,
            ),
        ):
            result = tools["trw_session_start"].fn()

        assert result["success"] is False
        assert len(result["errors"]) >= 1
        assert "recall" in result["errors"][0]
        # Run status should still be present
        assert "run" in result

    def test_returns_result_when_status_fails(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """If status check raises, recall still runs and result is returned."""
        tools = _make_ceremony_server(monkeypatch, tmp_path)
        trw_dir = tmp_path / ".trw"
        (trw_dir / "learnings" / "entries").mkdir(parents=True)
        (trw_dir / "context").mkdir(parents=True)

        with (
            patch("trw_mcp.tools.ceremony.resolve_trw_dir", return_value=trw_dir),
            patch(
                "trw_mcp.tools.ceremony._find_active_run",
                side_effect=Exception("status boom"),
            ),
        ):
            result = tools["trw_session_start"].fn()

        assert result["success"] is False
        assert any("status" in e for e in result["errors"])
        # Learnings should still be populated (even if empty)
        assert "learnings" in result

    def test_success_when_all_steps_work(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Both recall and status succeed."""
        tools = _make_ceremony_server(monkeypatch, tmp_path)
        trw_dir = tmp_path / ".trw"
        (trw_dir / "learnings" / "entries").mkdir(parents=True)
        (trw_dir / "context").mkdir(parents=True)

        with (
            patch("trw_mcp.tools.ceremony.resolve_trw_dir", return_value=trw_dir),
            patch("trw_mcp.tools.ceremony._find_active_run", return_value=None),
        ):
            result = tools["trw_session_start"].fn()

        assert result["success"] is True
        assert result["errors"] == []
        assert "timestamp" in result


@pytest.mark.integration
class TestDeliverPartialFailure:
    """trw_deliver resilience when sub-operations fail."""

    def test_reflect_failure_does_not_block_checkpoint(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """If reflect raises, checkpoint still runs."""
        tools = _make_ceremony_server(monkeypatch, tmp_path)
        trw_dir = tmp_path / ".trw"
        (trw_dir / "learnings" / "entries").mkdir(parents=True)
        (trw_dir / "reflections").mkdir(parents=True)
        (trw_dir / "context").mkdir(parents=True)

        run_dir = tmp_path / "docs" / "task" / "runs" / "20260214T000000Z-test"
        (run_dir / "meta").mkdir(parents=True)
        (run_dir / "meta" / "run.yaml").write_text(
            "run_id: test\nstatus: active\nphase: deliver\nprd_scope: []\n",
            encoding="utf-8",
        )
        (run_dir / "meta" / "events.jsonl").write_text("", encoding="utf-8")

        with (
            patch("trw_mcp.tools.ceremony.resolve_trw_dir", return_value=trw_dir),
            patch(
                "trw_mcp.tools.ceremony._do_reflect",
                side_effect=Exception("reflect boom"),
            ),
            patch("trw_mcp.tools.ceremony._find_active_run", return_value=run_dir),
            patch(
                "trw_mcp.tools.ceremony._do_claude_md_sync",
                return_value={"status": "success", "learnings_promoted": 0,
                              "path": "", "total_lines": 0},
            ),
            patch(
                "trw_mcp.tools.ceremony._do_index_sync",
                return_value={"status": "success", "index": {}, "roadmap": {}},
            ),
            patch(
                "trw_mcp.tools.ceremony._do_debt_md_sync",
                return_value={"status": "success", "items_rendered": 0},
            ),
            patch("trw_mcp.tools.ceremony.resolve_project_root", return_value=tmp_path),
        ):
            result = tools["trw_deliver"].fn()

        assert result["success"] is False
        assert result["reflect"]["status"] == "failed"
        # Checkpoint should have run
        assert result["checkpoint"]["status"] == "success"

    def test_checkpoint_failure_does_not_block_sync(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """If checkpoint raises, claude_md_sync still runs."""
        tools = _make_ceremony_server(monkeypatch, tmp_path)
        trw_dir = tmp_path / ".trw"
        (trw_dir / "learnings" / "entries").mkdir(parents=True)
        (trw_dir / "reflections").mkdir(parents=True)
        (trw_dir / "context").mkdir(parents=True)

        run_dir = tmp_path / "docs" / "task" / "runs" / "20260214T000000Z-test"
        (run_dir / "meta").mkdir(parents=True)
        (run_dir / "meta" / "run.yaml").write_text(
            "run_id: test\nstatus: active\nphase: deliver\nprd_scope: []\n",
            encoding="utf-8",
        )
        (run_dir / "meta" / "events.jsonl").write_text("", encoding="utf-8")

        with (
            patch("trw_mcp.tools.ceremony.resolve_trw_dir", return_value=trw_dir),
            patch(
                "trw_mcp.tools.ceremony._do_reflect",
                return_value={"status": "success", "events_analyzed": 0,
                              "learnings_produced": 0},
            ),
            patch("trw_mcp.tools.ceremony._find_active_run", return_value=run_dir),
            patch(
                "trw_mcp.tools.ceremony._do_checkpoint",
                side_effect=Exception("checkpoint boom"),
            ),
            patch(
                "trw_mcp.tools.ceremony._do_claude_md_sync",
                return_value={"status": "success", "learnings_promoted": 0,
                              "path": "", "total_lines": 0},
            ),
            patch(
                "trw_mcp.tools.ceremony._do_index_sync",
                return_value={"status": "success", "index": {}, "roadmap": {}},
            ),
            patch(
                "trw_mcp.tools.ceremony._do_debt_md_sync",
                return_value={"status": "success", "items_rendered": 0},
            ),
            patch("trw_mcp.tools.ceremony.resolve_project_root", return_value=tmp_path),
        ):
            result = tools["trw_deliver"].fn()

        assert result["success"] is False
        assert result["checkpoint"]["status"] == "failed"
        assert result["claude_md_sync"]["status"] == "success"

    def test_index_sync_failure_does_not_block_auto_progress(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """If index_sync raises, auto_progress still runs."""
        tools = _make_ceremony_server(monkeypatch, tmp_path)
        trw_dir = tmp_path / ".trw"
        (trw_dir / "learnings" / "entries").mkdir(parents=True)
        (trw_dir / "reflections").mkdir(parents=True)
        (trw_dir / "context").mkdir(parents=True)

        with (
            patch("trw_mcp.tools.ceremony.resolve_trw_dir", return_value=trw_dir),
            patch(
                "trw_mcp.tools.ceremony._do_reflect",
                return_value={"status": "success", "events_analyzed": 0,
                              "learnings_produced": 0},
            ),
            patch("trw_mcp.tools.ceremony._find_active_run", return_value=None),
            patch(
                "trw_mcp.tools.ceremony._do_claude_md_sync",
                return_value={"status": "success", "learnings_promoted": 0,
                              "path": "", "total_lines": 0},
            ),
            patch(
                "trw_mcp.tools.ceremony._do_index_sync",
                side_effect=Exception("index_sync boom"),
            ),
            patch(
                "trw_mcp.tools.ceremony._do_debt_md_sync",
                return_value={"status": "success", "items_rendered": 0},
            ),
        ):
            result = tools["trw_deliver"].fn()

        assert result["success"] is False
        assert result["index_sync"]["status"] == "failed"
        # auto_progress should still have run (skipped because no run)
        assert result["auto_progress"]["status"] == "skipped"

    def test_skip_reflect_flag(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """skip_reflect=True skips the reflect step entirely."""
        tools = _make_ceremony_server(monkeypatch, tmp_path)
        trw_dir = tmp_path / ".trw"
        (trw_dir / "learnings" / "entries").mkdir(parents=True)
        (trw_dir / "context").mkdir(parents=True)

        with (
            patch("trw_mcp.tools.ceremony.resolve_trw_dir", return_value=trw_dir),
            patch("trw_mcp.tools.ceremony._find_active_run", return_value=None),
            patch(
                "trw_mcp.tools.ceremony._do_claude_md_sync",
                return_value={"status": "success", "learnings_promoted": 0,
                              "path": "", "total_lines": 0},
            ),
            patch(
                "trw_mcp.tools.ceremony._do_index_sync",
                return_value={"status": "success", "index": {}, "roadmap": {}},
            ),
            patch(
                "trw_mcp.tools.ceremony._do_debt_md_sync",
                return_value={"status": "success", "items_rendered": 0},
            ),
        ):
            result = tools["trw_deliver"].fn(skip_reflect=True)

        assert result["reflect"]["status"] == "skipped"
        assert result["success"] is True

    def test_skip_index_sync_flag(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """skip_index_sync=True skips index sync step."""
        tools = _make_ceremony_server(monkeypatch, tmp_path)
        trw_dir = tmp_path / ".trw"
        (trw_dir / "learnings" / "entries").mkdir(parents=True)
        (trw_dir / "context").mkdir(parents=True)

        with (
            patch("trw_mcp.tools.ceremony.resolve_trw_dir", return_value=trw_dir),
            patch("trw_mcp.tools.ceremony._find_active_run", return_value=None),
            patch(
                "trw_mcp.tools.ceremony._do_reflect",
                return_value={"status": "success", "events_analyzed": 0,
                              "learnings_produced": 0},
            ),
            patch(
                "trw_mcp.tools.ceremony._do_claude_md_sync",
                return_value={"status": "success", "learnings_promoted": 0,
                              "path": "", "total_lines": 0},
            ),
            patch(
                "trw_mcp.tools.ceremony._do_debt_md_sync",
                return_value={"status": "success", "items_rendered": 0},
            ),
        ):
            result = tools["trw_deliver"].fn(skip_index_sync=True)

        assert result["index_sync"]["status"] == "skipped"
        assert result["success"] is True

    def test_event_logging_during_delivery(
        self, tmp_path: Path,
    ) -> None:
        """Verify events are logged to events.jsonl during delivery sub-steps."""
        trw_dir = tmp_path / ".trw"
        (trw_dir / "learnings" / "entries").mkdir(parents=True)
        (trw_dir / "reflections").mkdir(parents=True)
        (trw_dir / "context").mkdir(parents=True)

        run_dir = tmp_path / "docs" / "task" / "runs" / "20260214T000000Z-test"
        (run_dir / "meta").mkdir(parents=True)
        (run_dir / "meta" / "run.yaml").write_text(
            "run_id: test\nstatus: active\nphase: deliver\nprd_scope: []\n",
            encoding="utf-8",
        )
        (run_dir / "meta" / "events.jsonl").write_text("", encoding="utf-8")

        # Run reflect + checkpoint directly
        _do_reflect(trw_dir, run_dir)
        _do_checkpoint(run_dir, "test-delivery")

        events_path = run_dir / "meta" / "events.jsonl"
        lines = [
            l for l in events_path.read_text(encoding="utf-8").strip().split("\n") if l
        ]
        assert len(lines) >= 2
        event_types = [json.loads(l)["event"] for l in lines]
        assert "reflection_complete" in event_types
        assert "checkpoint" in event_types
