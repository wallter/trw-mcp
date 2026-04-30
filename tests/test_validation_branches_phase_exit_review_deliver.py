"""Extra coverage tests for trw_mcp/state/validation.py."""

from __future__ import annotations

from pathlib import Path

from trw_mcp.models.config import TRWConfig
from trw_mcp.models.run import Phase
from trw_mcp.state.persistence import FileStateWriter
from trw_mcp.state.validation import check_phase_exit

from tests._validation_branches_support import _make_run_dir


class TestCheckPhaseExitReview:
    """check_phase_exit for REVIEW phase."""

    def test_review_exit_warns_without_final_report(
        self,
        tmp_path: Path,
        writer: FileStateWriter,
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        config = TRWConfig()
        result = check_phase_exit(Phase.REVIEW, run_dir, config)
        rules = [f.rule for f in result.failures]
        assert "final_report_exists" in rules

    def test_review_exit_warns_without_reflection_event(
        self,
        tmp_path: Path,
        writer: FileStateWriter,
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        meta = run_dir / "meta"
        writer.append_jsonl(
            meta / "events.jsonl",
            {
                "event": "run_init",
                "ts": "2026-01-01T00:00:00Z",
            },
        )
        config = TRWConfig()
        result = check_phase_exit(Phase.REVIEW, run_dir, config)
        rules = [f.rule for f in result.failures]
        assert "reflection_required" in rules

    def test_review_exit_warns_when_no_events_jsonl(
        self,
        tmp_path: Path,
        writer: FileStateWriter,
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        config = TRWConfig()
        result = check_phase_exit(Phase.REVIEW, run_dir, config)
        rules = [f.rule for f in result.failures]
        assert "reflection_required" in rules

    def test_review_exit_passes_with_reflection_event(
        self,
        tmp_path: Path,
        writer: FileStateWriter,
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        meta = run_dir / "meta"
        (run_dir / "reports" / "final.md").write_text("# Final\n", encoding="utf-8")
        writer.append_jsonl(
            meta / "events.jsonl",
            {
                "event": "reflection_complete",
                "ts": "2026-01-01T12:00:00Z",
            },
        )
        config = TRWConfig()
        result = check_phase_exit(Phase.REVIEW, run_dir, config)
        assert not any(f.rule == "reflection_required" for f in result.failures)


class TestCheckPhaseExitDeliver:
    """check_phase_exit for DELIVER phase."""

    def test_deliver_exit_warns_incomplete_run_status(
        self,
        tmp_path: Path,
        writer: FileStateWriter,
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        config = TRWConfig()
        result = check_phase_exit(Phase.DELIVER, run_dir, config)
        rules = [f.rule for f in result.failures]
        assert "status_complete" in rules

    def test_deliver_exit_no_warning_when_run_complete(
        self,
        tmp_path: Path,
        writer: FileStateWriter,
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        writer.write_yaml(
            run_dir / "meta" / "run.yaml",
            {
                "run_id": "20260101T000000Z-extra1234",
                "task": "extra-coverage-test",
                "status": "complete",
                "phase": "deliver",
            },
        )
        config = TRWConfig()
        result = check_phase_exit(Phase.DELIVER, run_dir, config)
        assert not any(f.rule == "status_complete" for f in result.failures)

    def test_deliver_exit_includes_test_advisory(
        self,
        tmp_path: Path,
        writer: FileStateWriter,
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        config = TRWConfig()
        result = check_phase_exit(Phase.DELIVER, run_dir, config)
        rules = [f.rule for f in result.failures]
        assert "phase_test_advisory" in rules

    def test_deliver_exit_warns_when_sync_missing(
        self,
        tmp_path: Path,
        writer: FileStateWriter,
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        meta = run_dir / "meta"
        writer.append_jsonl(
            meta / "events.jsonl",
            {
                "event": "reflection_complete",
                "ts": "2026-01-01T00:00:00Z",
            },
        )
        config = TRWConfig()
        result = check_phase_exit(Phase.DELIVER, run_dir, config)
        rules = [f.rule for f in result.failures]
        assert "sync_required" in rules

    def test_deliver_exit_no_sync_warning_when_synced(
        self,
        tmp_path: Path,
        writer: FileStateWriter,
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        meta = run_dir / "meta"
        writer.append_jsonl(
            meta / "events.jsonl",
            {
                "event": "claude_md_sync",
                "ts": "2026-01-01T00:00:00Z",
            },
        )
        config = TRWConfig()
        result = check_phase_exit(Phase.DELIVER, run_dir, config)
        assert not any(f.rule == "sync_required" for f in result.failures)
