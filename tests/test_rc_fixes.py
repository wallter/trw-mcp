"""Reverse-TDD tests for Sprint 28/29 postmortem root-cause fixes.

Each test maps to one root cause:
- RC-003 + RC-006: trw_deliver build gate (standalone + team parity)
- RC-004: Exit criteria checkbox parser
- RC-002: trw_status reports last activity
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from trw_mcp.state.persistence import FileStateWriter


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_run_with_events(
    base: Path,
    task: str,
    run_id: str,
    events: list[dict[str, object]],
    writer: FileStateWriter,
) -> Path:
    """Create a minimal run dir with run.yaml and events.jsonl."""
    run_dir = base / task / "runs" / run_id
    meta = run_dir / "meta"
    meta.mkdir(parents=True)
    writer.write_yaml(meta / "run.yaml", {
        "run_id": run_id,
        "task": task,
        "status": "active",
        "phase": "implement",
        "framework": "v24.0_TRW",
    })
    events_path = meta / "events.jsonl"
    for ev in events:
        with events_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(ev) + "\n")
    return run_dir


# ---------------------------------------------------------------------------
# RC-003 + RC-006: trw_deliver warns when no build_check_complete event
# ---------------------------------------------------------------------------

class TestDeliverBuildGate:
    """trw_deliver should warn when build_check_complete is missing or failed."""

    def test_deliver_warns_no_build_check(
        self, tmp_path: Path, writer: FileStateWriter,
    ) -> None:
        """Delivery without a build_check_complete event produces a warning."""
        project = tmp_path / "project"
        task_root = project / "docs"
        run_dir = _make_run_with_events(task_root, "my-task", "20260227T100000Z-aaaa", [
            {"event": "run_init", "ts": "2026-02-27T10:00:00Z"},
            {"event": "checkpoint", "ts": "2026-02-27T11:00:00Z"},
            {"event": "tool_invocation", "ts": "2026-02-27T11:30:00Z"},
        ], writer)

        from trw_mcp.tools.ceremony import register_ceremony_tools
        from fastmcp import FastMCP

        server = FastMCP("test")
        register_ceremony_tools(server)
        deliver_fn = server._tool_manager._tools["trw_deliver"].fn

        with (
            patch("trw_mcp.tools.ceremony.find_active_run", return_value=run_dir),
            patch("trw_mcp.tools.ceremony.resolve_trw_dir", return_value=tmp_path / ".trw"),
            patch("trw_mcp.tools.ceremony.resolve_project_root", return_value=project),
        ):
            (tmp_path / ".trw" / "learnings" / "entries").mkdir(parents=True)
            (tmp_path / ".trw" / "reflections").mkdir(parents=True)
            result = deliver_fn()

        assert "build_gate_warning" in result
        assert "no successful build check" in str(result["build_gate_warning"]).lower()

    def test_deliver_no_warning_when_build_passed(
        self, tmp_path: Path, writer: FileStateWriter,
    ) -> None:
        """Delivery WITH a passing build_check_complete event has no warning."""
        project = tmp_path / "project"
        task_root = project / "docs"
        run_dir = _make_run_with_events(task_root, "my-task", "20260227T100000Z-aaaa", [
            {"event": "run_init", "ts": "2026-02-27T10:00:00Z"},
            {"event": "build_check_complete", "ts": "2026-02-27T11:00:00Z",
             "data": {"tests_passed": True, "mypy_clean": True}},
        ], writer)

        from trw_mcp.tools.ceremony import register_ceremony_tools
        from fastmcp import FastMCP

        server = FastMCP("test")
        register_ceremony_tools(server)
        deliver_fn = server._tool_manager._tools["trw_deliver"].fn

        with (
            patch("trw_mcp.tools.ceremony.find_active_run", return_value=run_dir),
            patch("trw_mcp.tools.ceremony.resolve_trw_dir", return_value=tmp_path / ".trw"),
            patch("trw_mcp.tools.ceremony.resolve_project_root", return_value=project),
        ):
            (tmp_path / ".trw" / "learnings" / "entries").mkdir(parents=True)
            (tmp_path / ".trw" / "reflections").mkdir(parents=True)
            result = deliver_fn()

        assert "build_gate_warning" not in result


# ---------------------------------------------------------------------------
# RC-004: Parse exit criteria checkboxes from sprint markdown
# ---------------------------------------------------------------------------

class TestParseExitCriteria:
    """Sprint doc exit criteria parser extracts checkbox state."""

    def test_parse_exit_criteria_checkboxes(self) -> None:
        """Parses markdown checkboxes into structured pass/fail list."""
        from trw_mcp.state.validation import parse_exit_criteria

        sprint_md = """\
# Sprint 29: Platform Polish

## Exit Criteria
- [x] Backend pytest passes (0 failures)
- [x] mypy --strict clean
- [ ] Coverage >= 80%
- [ ] Admin dashboard functional
- [x] All PRDs in done status

## Notes
Some other content here.
"""
        criteria = parse_exit_criteria(sprint_md)

        assert len(criteria) == 5
        checked = [c for c in criteria if c["checked"]]
        unchecked = [c for c in criteria if not c["checked"]]
        assert len(checked) == 3
        assert len(unchecked) == 2
        assert "Coverage >= 80%" in unchecked[0]["text"]
        assert "Admin dashboard" in unchecked[1]["text"]


# ---------------------------------------------------------------------------
# RC-002: trw_status reports last activity timestamp
# ---------------------------------------------------------------------------

class TestStatusLastActivity:
    """trw_status should report last activity and hours since."""

    def test_status_reports_last_activity(
        self, tmp_path: Path, writer: FileStateWriter,
    ) -> None:
        """trw_status includes last_activity_ts and hours_since_activity."""
        project = tmp_path / "project"
        task_root = project / "docs"
        run_dir = _make_run_with_events(task_root, "my-task", "20260227T100000Z-aaaa", [
            {"event": "run_init", "ts": "2026-02-27T10:00:00Z"},
            {"event": "checkpoint", "ts": "2026-02-27T12:00:00Z",
             "data": {"message": "milestone 1"}},
        ], writer)

        # Also write checkpoints.jsonl (what trw_status reads for activity)
        checkpoints_path = run_dir / "meta" / "checkpoints.jsonl"
        with checkpoints_path.open("w", encoding="utf-8") as f:
            f.write(json.dumps({
                "ts": "2026-02-27T12:00:00Z",
                "message": "milestone 1",
            }) + "\n")

        from trw_mcp.tools.orchestration import register_orchestration_tools
        from fastmcp import FastMCP

        server = FastMCP("test")
        register_orchestration_tools(server)
        status_fn = server._tool_manager._tools["trw_status"].fn

        with (
            patch("trw_mcp.tools.orchestration.resolve_run_path", return_value=run_dir),
            patch("trw_mcp.state.analytics_report.resolve_project_root", return_value=project),
        ):
            result = status_fn()

        assert "last_activity_ts" in result
        assert "hours_since_activity" in result
        assert result["last_activity_ts"] == "2026-02-27T12:00:00Z"
