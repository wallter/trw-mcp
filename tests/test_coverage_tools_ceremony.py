from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from tests._coverage_tools_support import _extract_tool, _make_server
from trw_mcp.exceptions import StateError
from trw_mcp.state.persistence import FileStateReader
from trw_mcp.tools.ceremony import register_ceremony_tools


class TestCeremonySessionStartFailurePaths:
    """Lines 74-75: session_start exception path when recall raises."""

    def test_session_start_recall_failure_graceful(self, tmp_path: Path) -> None:
        server = _make_server()
        register_ceremony_tools(server)
        tool = _extract_tool(server, "trw_session_start")

        with (
            patch("trw_mcp.state.memory_adapter.recall_learnings", side_effect=RuntimeError("disk failure")),
            patch("trw_mcp.tools.ceremony.resolve_trw_dir", return_value=tmp_path / ".trw"),
            patch("trw_mcp.tools.ceremony.find_active_run", return_value=None),
        ):
            result = tool()

        # Recall is fail-open by contract: a recall-only failure must NOT flip
        # ``success`` (which would mislead agents into needless retries). The
        # failure is surfaced under the non-fatal ``warnings`` channel instead.
        assert result["success"] is True
        recall_warnings = [w for w in result.get("warnings", []) if "recall" in w]
        assert len(recall_warnings) == 1
        assert "disk failure" in recall_warnings[0]
        assert "recall" not in " ".join(result.get("errors", []))
        assert result["learnings"] == []
        assert result["learnings_count"] == 0

    def test_session_start_run_status_failure_graceful(self, tmp_path: Path) -> None:
        server = _make_server()
        register_ceremony_tools(server)
        tool = _extract_tool(server, "trw_session_start")

        with (
            patch("trw_mcp.tools.ceremony.resolve_trw_dir", return_value=tmp_path / ".trw"),
            patch("trw_mcp.state.memory_adapter.recall_learnings", return_value=[]),
            patch("trw_mcp.tools.ceremony.find_active_run", side_effect=OSError("permission denied")),
        ):
            result = tool()

        status_errors = [e for e in result["errors"] if "status" in e]
        assert len(status_errors) == 1
        assert result["run"]["status"] == "error"

    def test_session_start_pin_failure_does_not_reuse_resolved_run(self, tmp_path: Path) -> None:
        """A failed pin refresh cannot leak its run into downstream steps."""
        server = _make_server()
        register_ceremony_tools(server)
        tool = _extract_tool(server, "trw_session_start")
        trw_dir = tmp_path / ".trw"
        run_dir = trw_dir / "runs" / "task" / "run-1"
        (run_dir / "meta").mkdir(parents=True)
        stamped_runs: list[Path | None] = []

        def capture_surface_run(resolved_run: Path | None, *_args: object) -> str:
            stamped_runs.append(resolved_run)
            return ""

        with (
            patch("trw_mcp.tools.ceremony.resolve_trw_dir", return_value=trw_dir),
            patch("trw_mcp.state.memory_adapter.recall_learnings", return_value=[]),
            patch("trw_mcp.tools.ceremony.find_active_run", return_value=run_dir),
            patch("trw_mcp.state._paths.pin_active_run", side_effect=OSError("pin write failed")),
            patch("trw_mcp.tools._ceremony_step_table.step_surface_stamp", side_effect=capture_surface_run),
        ):
            result = tool()

        assert result["run"] == {"active_run": None, "status": "error"}
        assert any("pin write failed" in error for error in result["errors"])
        assert stamped_runs == [None]


class TestCeremonyDeliverSubStepFailures:
    """Deliver sub-step failure capture (auto_progress 275-277, publish_learnings 284-286).

    The formerly-unconditional ``claude_md_sync`` response field was trimmed in
    commit 6552b8f926 — it always carried the identical constant
    ``{"status": "skipped", "reason": "PRD-CORE-093"}`` and never ran instruction
    sync, so it conveyed no per-delivery information.
    """

    def _register_and_get_deliver(self):
        server = _make_server()
        register_ceremony_tools(server)
        return _extract_tool(server, "trw_deliver")

    def test_deliver_claude_md_sync_not_surfaced(self, tmp_path: Path) -> None:
        """The trimmed ``claude_md_sync`` field is absent from the deliver response.

        Instruction sync is not a synchronous deliver step (PRD-CORE-093), so a
        raising ``_do_instruction_sync`` cannot affect the deliver result and the
        constant response field stays gone (guards the 6552b8f926 trim).
        """
        tool = self._register_and_get_deliver()

        with (
            patch("trw_mcp.tools.ceremony.resolve_trw_dir", return_value=tmp_path / ".trw"),
            patch("trw_mcp.tools.ceremony.find_active_run", return_value=None),
            patch("trw_mcp.tools.ceremony._do_reflect", return_value={"status": "success", "learnings_produced": 0}),
            patch("trw_mcp.tools.ceremony._do_instruction_sync", side_effect=RuntimeError("sync failed")),
            patch("trw_mcp.tools._deferred_delivery._do_index_sync", return_value={"status": "success"}),
            patch("trw_mcp.tools._deferred_delivery._do_auto_progress", return_value={"status": "skipped"}),
        ):
            result = tool(skip_reflect=False, skip_index_sync=False)

        assert "claude_md_sync" not in result
        assert "timestamp" in result

    def test_deliver_auto_progress_failure_captured(self, tmp_path: Path) -> None:
        from trw_mcp.tools._helpers import _run_step

        results: dict[str, object] = {}
        errors: list[str] = []

        with patch("trw_mcp.tools._deferred_delivery._step_auto_progress", side_effect=RuntimeError("progress failed")):
            _run_step(
                "auto_progress",
                lambda: __import__(
                    "trw_mcp.tools._deferred_delivery", fromlist=["_step_auto_progress"]
                )._step_auto_progress(None),
                results,
                errors,
            )

        assert results["auto_progress"]["status"] == "failed"  # type: ignore[index]
        assert "progress failed" in results["auto_progress"]["error"]  # type: ignore[index]
        progress_errors = [e for e in errors if "auto_progress" in e]
        assert len(progress_errors) == 1

    def test_deliver_publish_learnings_failure_captured(self, tmp_path: Path) -> None:
        from trw_mcp.tools._helpers import _run_step

        results: dict[str, object] = {}
        errors: list[str] = []

        with patch(
            "trw_mcp.tools._deferred_delivery._step_publish_learnings",
            side_effect=RuntimeError("publish failed"),
        ):
            _run_step(
                "publish_learnings",
                lambda: __import__(
                    "trw_mcp.tools._deferred_delivery",
                    fromlist=["_step_publish_learnings"],
                )._step_publish_learnings(),
                results,
                errors,
            )

        assert results["publish_learnings"]["status"] == "failed"  # type: ignore[index]
        pub_errors = [e for e in errors if "publish_learnings" in e]
        assert len(pub_errors) == 1


class TestCeremonyGetRunStatus:
    """Lines 74-75: _get_run_status exception handler."""

    def test_get_run_status_read_error_returns_error_status(self, tmp_path: Path) -> None:
        from trw_mcp.tools.ceremony import _get_run_status

        run_dir = tmp_path / "run"
        meta = run_dir / "meta"
        meta.mkdir(parents=True)
        (meta / "run.yaml").write_text("status: active\n")

        with patch.object(FileStateReader, "read_yaml", side_effect=StateError("corrupt")):
            result = _get_run_status(run_dir)

        assert result["status"] == "error_reading"
        assert result["active_run"] == str(run_dir)

    def test_get_run_status_oserror_caught(self, tmp_path: Path) -> None:
        from trw_mcp.tools.ceremony import _get_run_status

        run_dir = tmp_path / "run"
        meta = run_dir / "meta"
        meta.mkdir(parents=True)
        (meta / "run.yaml").write_text("status: active\n")

        with patch.object(FileStateReader, "read_yaml", side_effect=OSError("disk error")):
            result = _get_run_status(run_dir)

        assert result["status"] == "error_reading"
