from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from trw_mcp.exceptions import StateError
from trw_mcp.state.persistence import FileStateReader
from trw_mcp.tools.ceremony import register_ceremony_tools

from tests._coverage_tools_support import _extract_tool, _make_server


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

        assert "errors" in result
        recall_errors = [e for e in result["errors"] if "recall" in e]
        assert len(recall_errors) == 1
        assert "disk failure" in recall_errors[0]
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


class TestCeremonyDeliverSubStepFailures:
    """Lines 256-258 (claude_md_sync), 275-277 (auto_progress), 284-286 (publish_learnings)."""

    def _register_and_get_deliver(self):
        server = _make_server()
        register_ceremony_tools(server)
        return _extract_tool(server, "trw_deliver")

    def test_deliver_claude_md_sync_failure_captured(self, tmp_path: Path) -> None:
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

        assert result["claude_md_sync"]["status"] == "skipped"
        assert result["claude_md_sync"]["reason"] == "PRD-CORE-093"

    def test_deliver_auto_progress_failure_captured(self, tmp_path: Path) -> None:
        from trw_mcp.tools._helpers import _run_step

        results: dict[str, object] = {}
        errors: list[str] = []

        with patch("trw_mcp.tools._deferred_delivery._step_auto_progress", side_effect=RuntimeError("progress failed")):
            _run_step(
                "auto_progress",
                lambda: __import__("trw_mcp.tools._deferred_delivery", fromlist=["_step_auto_progress"])._step_auto_progress(
                    None
                ),
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
