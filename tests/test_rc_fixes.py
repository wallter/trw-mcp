"""Reverse-TDD tests for Sprint 28/29 postmortem root-cause fixes.

Each test maps to one root cause:
- RC-003 + RC-006: trw_deliver build gate (standalone + team parity)
- RC-004: Exit criteria checkbox parser
- RC-002: trw_status reports last activity
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from tests.conftest import get_tools_sync
from trw_mcp.models.config import TRWConfig
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
    task_type: str | None = None,
) -> Path:
    """Create a minimal run dir with run.yaml and events.jsonl.

    ``task_type`` (coding/rca/eval vs docs/…/unknown) selects which deliver-gate
    posture the build gate takes: build-bearing types HARD-block on missing build
    evidence under the default ``block_coding`` mode; the rest stay advisory.
    """
    run_dir = base / task / "runs" / run_id
    meta = run_dir / "meta"
    meta.mkdir(parents=True)
    run_yaml: dict[str, object] = {
        "run_id": run_id,
        "task": task,
        "status": "active",
        "phase": "implement",
        "framework": "v24.0_TRW",
    }
    if task_type is not None:
        run_yaml["task_type"] = task_type
    writer.write_yaml(meta / "run.yaml", run_yaml)
    events_path = meta / "events.jsonl"
    for ev in events:
        with events_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(ev) + "\n")
    return run_dir


# ---------------------------------------------------------------------------
# RC-003 + RC-006: trw_deliver blocks when no build_check_complete event
# ---------------------------------------------------------------------------


def _future_iso() -> str:
    """A 30-day-out expiry date for a valid acceptable-failure record."""
    from datetime import datetime, timedelta, timezone

    return (datetime.now(timezone.utc) + timedelta(days=30)).date().isoformat()


def _structured_reason(expiry: str | None = None) -> str:
    """A well-formed PRD-CORE-191 acceptable-failure record as JSON."""
    return json.dumps(
        {
            "failed_command": "pytest trw-mcp/tests/ -q",
            "residual_risk": "one flaky integration test; core logic verified manually",
            "owner": "agent-run-abc123",
            "expiry_iso": expiry or _future_iso(),
        }
    )


class TestDeliverBuildGate:
    """trw_deliver build gate under the v26.1 task-type posture.

    Build-bearing runs (task_type=coding) HARD-block on missing build evidence;
    the block is overridable ONLY by a structured PRD-CORE-191 record. Advisory
    runs (unknown/docs) surface a warning but are never promoted to a block.
    """

    def test_deliver_blocks_no_build_check(
        self,
        tmp_path: Path,
        writer: FileStateWriter,
    ) -> None:
        """A CODING run with no build evidence trips the HARD delivery_blocked gate."""
        project = tmp_path / "project"
        task_root = project / "docs"
        run_dir = _make_run_with_events(
            task_root,
            "my-task",
            "20260227T100000Z-aaaa",
            [
                {"event": "run_init", "ts": "2026-02-27T10:00:00Z"},
                {"event": "checkpoint", "ts": "2026-02-27T11:00:00Z"},
                {"event": "file_modified", "ts": "2026-02-27T11:30:00Z", "data": {"path": "src/x.py"}},
            ],
            writer,
            task_type="coding",
        )

        from fastmcp import FastMCP

        from trw_mcp.tools.ceremony import register_ceremony_tools

        server = FastMCP("test")
        register_ceremony_tools(server)
        deliver_fn = get_tools_sync(server)["trw_deliver"].fn

        with (
            patch("trw_mcp.tools.ceremony.find_active_run", return_value=run_dir),
            patch("trw_mcp.tools.ceremony.resolve_trw_dir", return_value=tmp_path / ".trw"),
            patch("trw_mcp.state._paths.resolve_project_root", return_value=project),
        ):
            (tmp_path / ".trw" / "learnings" / "entries").mkdir(parents=True)
            (tmp_path / ".trw" / "reflections").mkdir(parents=True)
            result = deliver_fn()

        assert result["success"] is False
        assert result.get("delivery_blocked")
        assert result.get("blocked_task_type") == "coding"
        assert result.get("missing_gate") == "build_check"

    def test_deliver_advisory_for_unknown_task_type(
        self,
        tmp_path: Path,
        writer: FileStateWriter,
    ) -> None:
        """An unknown/docs run with no build evidence stays ADVISORY (v26.1).

        The build_gate_warning is surfaced but never promoted to a block: no
        build_gate_block, no delivery_blocked, and no truthfulness_gate_bypassed.
        This documents the new advisory posture that replaced the old universal
        soft-block + free-text escape.
        """
        project = tmp_path / "project"
        task_root = project / "docs"
        run_dir = _make_run_with_events(
            task_root,
            "my-task",
            "20260227T100000Z-aaaa",
            [
                {"event": "run_init", "ts": "2026-02-27T10:00:00Z"},
                {"event": "checkpoint", "ts": "2026-02-27T11:00:00Z"},
                {"event": "file_modified", "ts": "2026-02-27T11:30:00Z", "data": {"path": "docs/x.md"}},
            ],
            writer,
            task_type=None,  # -> task_type=unknown, which stays advisory
        )

        from fastmcp import FastMCP

        from trw_mcp.tools.ceremony import register_ceremony_tools

        server = FastMCP("test")
        register_ceremony_tools(server)
        deliver_fn = get_tools_sync(server)["trw_deliver"].fn

        with (
            patch("trw_mcp.tools.ceremony.find_active_run", return_value=run_dir),
            patch("trw_mcp.tools.ceremony.resolve_trw_dir", return_value=tmp_path / ".trw"),
            patch("trw_mcp.state._paths.resolve_project_root", return_value=project),
            # legacy raw-event build-gate contract: opt into observe so the
            # v26.1 typed-BuildReceipt enforce default does not supersede the
            # "no successful build check" message this RC fix documents.
            patch(
                "trw_mcp.tools._delivery_helpers.get_config",
                return_value=TRWConfig(evidence_receipt_mode="observe"),
            ),
        ):
            (tmp_path / ".trw" / "learnings" / "entries").mkdir(parents=True)
            (tmp_path / ".trw" / "reflections").mkdir(parents=True)
            result = deliver_fn()

        assert result["success"] is True
        assert "build_gate_warning" in result
        assert "no successful build check" in str(result["build_gate_warning"]).lower()
        assert "build_gate_block" not in result
        assert "delivery_blocked" not in result
        assert "truthfulness_gate_bypassed" not in result

    def test_deliver_structured_override_proceeds_and_ledgers(
        self,
        tmp_path: Path,
        writer: FileStateWriter,
    ) -> None:
        """A structured PRD-CORE-191 record sanctions the HARD coding build block.

        Delivery PROCEEDS, the parsed record is surfaced, and a durable override
        ledger entry is written (gate_type=delivery_blocked). Free text can no
        longer sanction the block — only this structured, auditable record.
        """
        project = tmp_path / "project"
        task_root = project / "docs"
        run_dir = _make_run_with_events(
            task_root,
            "my-task",
            "20260227T100000Z-aaaa",
            [
                {"event": "run_init", "ts": "2026-02-27T10:00:00Z"},
                {"event": "checkpoint", "ts": "2026-02-27T11:00:00Z"},
                {"event": "file_modified", "ts": "2026-02-27T11:30:00Z", "data": {"path": "src/x.py"}},
            ],
            writer,
            task_type="coding",
        )

        from fastmcp import FastMCP

        from trw_mcp.tools.ceremony import register_ceremony_tools

        server = FastMCP("test")
        register_ceremony_tools(server)
        deliver_fn = get_tools_sync(server)["trw_deliver"].fn

        with (
            patch("trw_mcp.tools.ceremony.find_active_run", return_value=run_dir),
            patch("trw_mcp.tools.ceremony.resolve_trw_dir", return_value=tmp_path / ".trw"),
            patch("trw_mcp.state._paths.resolve_project_root", return_value=project),
        ):
            (tmp_path / ".trw" / "learnings" / "entries").mkdir(parents=True)
            (tmp_path / ".trw" / "reflections").mkdir(parents=True)
            result = deliver_fn(
                allow_unverified=True,
                unverified_reason=_structured_reason(),
            )

        assert result["success"] is True
        record = result.get("acceptable_failure_record")
        assert isinstance(record, dict)
        assert record["owner"] == "agent-run-abc123"
        assert result.get("truthfulness_gate_bypassed")
        assert "build_gate_block" not in result
        ledger = list((tmp_path / ".trw" / "overrides").glob("*.yaml"))
        assert ledger, "structured override must write a durable ledger entry"

    def test_deliver_no_warning_when_build_passed(
        self,
        tmp_path: Path,
        writer: FileStateWriter,
    ) -> None:
        """Delivery WITH a passing build_check_complete event has no warning."""
        project = tmp_path / "project"
        task_root = project / "docs"
        run_dir = _make_run_with_events(
            task_root,
            "my-task",
            "20260227T100000Z-aaaa",
            [
                {"event": "run_init", "ts": "2026-02-27T10:00:00Z"},
                {
                    "event": "build_check_complete",
                    "ts": "2026-02-27T11:00:00Z",
                    "data": {"tests_passed": True, "mypy_clean": True},
                },
            ],
            writer,
        )

        from fastmcp import FastMCP

        from trw_mcp.tools.ceremony import register_ceremony_tools

        server = FastMCP("test")
        register_ceremony_tools(server)
        deliver_fn = get_tools_sync(server)["trw_deliver"].fn

        with (
            patch("trw_mcp.tools.ceremony.find_active_run", return_value=run_dir),
            patch("trw_mcp.tools.ceremony.resolve_trw_dir", return_value=tmp_path / ".trw"),
            patch("trw_mcp.state._paths.resolve_project_root", return_value=project),
            # legacy raw-event build-gate contract: observe mode lets the passing
            # build_check_complete event clear the gate (the v26.1 enforce default
            # would still require a typed BuildReceipt and warn on its absence).
            patch(
                "trw_mcp.tools._delivery_helpers.get_config",
                return_value=TRWConfig(evidence_receipt_mode="observe"),
            ),
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
        assert "Coverage >= 80%" in str(unchecked[0]["text"])
        assert "Admin dashboard" in str(unchecked[1]["text"])


# ---------------------------------------------------------------------------
# RC-002: trw_status reports last activity timestamp
# ---------------------------------------------------------------------------


class TestStatusLastActivity:
    """trw_status should report last activity and hours since."""

    def test_status_reports_last_activity(
        self,
        tmp_path: Path,
        writer: FileStateWriter,
    ) -> None:
        """trw_status includes last_activity_ts and hours_since_activity."""
        project = tmp_path / "project"
        task_root = project / "docs"
        run_dir = _make_run_with_events(
            task_root,
            "my-task",
            "20260227T100000Z-aaaa",
            [
                {"event": "run_init", "ts": "2026-02-27T10:00:00Z"},
                {"event": "checkpoint", "ts": "2026-02-27T12:00:00Z", "data": {"message": "milestone 1"}},
            ],
            writer,
        )

        # Also write checkpoints.jsonl (what trw_status reads for activity)
        checkpoints_path = run_dir / "meta" / "checkpoints.jsonl"
        with checkpoints_path.open("w", encoding="utf-8") as f:
            f.write(
                json.dumps(
                    {
                        "ts": "2026-02-27T12:00:00Z",
                        "message": "milestone 1",
                    }
                )
                + "\n"
            )

        from fastmcp import FastMCP

        from trw_mcp.tools.orchestration import register_orchestration_tools

        server = FastMCP("test")
        register_orchestration_tools(server)
        status_fn = get_tools_sync(server)["trw_status"].fn

        with (
            patch("trw_mcp.tools.orchestration.resolve_run_path", return_value=run_dir),
            patch("trw_mcp.state.analytics.report.resolve_project_root", return_value=project),
        ):
            result = status_fn()

        assert "last_activity_ts" in result
        assert "hours_since_activity" in result
        assert result["last_activity_ts"] == "2026-02-27T12:00:00Z"
