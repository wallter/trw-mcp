"""Coverage tests for review and deliver phase exit gates."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests._validation_gates_support import _make_run_dir
from trw_mcp.models.config import TRWConfig
from trw_mcp.models.run import Phase
from trw_mcp.state.persistence import FileStateWriter
from trw_mcp.state.validation import check_phase_exit


class TestCheckPhaseExitReview:
    """Review exit criteria: final report, reflection event, quality checks."""

    def test_review_exit_warns_when_no_final_report(
        self,
        tmp_path: Path,
        writer: FileStateWriter,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        meta = run_dir / "meta"
        writer.append_jsonl(
            meta / "events.jsonl",
            {
                "event": "reflection_complete",
                "ts": "2026-01-01T12:00:00Z",
            },
        )
        monkeypatch.setattr(
            "trw_mcp.state._paths.resolve_trw_dir",
            lambda: tmp_path,
        )
        monkeypatch.setattr(
            "trw_mcp.state.analytics.compute_reflection_quality",
            lambda _: {"score": 1.0},
        )
        config = TRWConfig()
        result = check_phase_exit(Phase.REVIEW, run_dir, config)
        report_f = [f for f in result.failures if f.rule == "final_report_exists"]
        assert len(report_f) == 1
        assert report_f[0].severity == "warning"

    def test_review_exit_warns_when_no_events_jsonl(
        self,
        tmp_path: Path,
        writer: FileStateWriter,
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        config = TRWConfig()
        result = check_phase_exit(Phase.REVIEW, run_dir, config)
        refl_f = [f for f in result.failures if f.rule == "reflection_required"]
        assert len(refl_f) == 1
        assert "unknown" in refl_f[0].message.lower()

    def test_review_exit_warns_when_events_but_no_reflection(
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
        refl_f = [f for f in result.failures if f.rule == "reflection_required"]
        assert len(refl_f) == 1
        assert "trw_reflect()" in refl_f[0].message

    def test_review_exit_no_reflection_warning_with_reflection_event(
        self,
        tmp_path: Path,
        writer: FileStateWriter,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        meta = run_dir / "meta"
        writer.append_jsonl(
            meta / "events.jsonl",
            {
                "event": "reflection_complete",
                "ts": "2026-01-01T12:00:00Z",
            },
        )
        monkeypatch.setattr(
            "trw_mcp.state._paths.resolve_trw_dir",
            lambda: tmp_path,
        )
        monkeypatch.setattr(
            "trw_mcp.state.analytics.compute_reflection_quality",
            lambda _: {"score": 1.0},
        )
        config = TRWConfig()
        result = check_phase_exit(Phase.REVIEW, run_dir, config)
        rules = [f.rule for f in result.failures]
        assert "reflection_required" not in rules


class TestCheckPhaseExitDeliver:
    """Deliver exit criteria: run status, sync event, integration/orphan checks."""

    def test_deliver_exit_warns_when_run_status_not_complete(
        self,
        tmp_path: Path,
        writer: FileStateWriter,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        monkeypatch.setattr(
            "trw_mcp.state.validation.phase_gates._best_effort_integration_check",
            lambda *a, **kw: None,
        )
        monkeypatch.setattr(
            "trw_mcp.state.validation.phase_gates._best_effort_orphan_check",
            lambda *a, **kw: None,
        )
        monkeypatch.setattr(
            "trw_mcp.state.validation.phase_gates._best_effort_build_check",
            lambda *a, **kw: None,
        )
        config = TRWConfig()
        result = check_phase_exit(Phase.DELIVER, run_dir, config)
        status_f = [f for f in result.failures if f.rule == "status_complete"]
        assert len(status_f) == 1
        assert status_f[0].severity == "warning"

    def test_deliver_exit_no_status_warning_when_complete(
        self,
        tmp_path: Path,
        writer: FileStateWriter,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        writer.write_yaml(
            run_dir / "meta" / "run.yaml",
            {
                "run_id": "20260101T000000Z-test1234",
                "task": "coverage-test",
                "status": "complete",
                "phase": "deliver",
            },
        )
        monkeypatch.setattr(
            "trw_mcp.state.validation.phase_gates._best_effort_integration_check",
            lambda *a, **kw: None,
        )
        monkeypatch.setattr(
            "trw_mcp.state.validation.phase_gates._best_effort_orphan_check",
            lambda *a, **kw: None,
        )
        monkeypatch.setattr(
            "trw_mcp.state.validation.phase_gates._best_effort_build_check",
            lambda *a, **kw: None,
        )
        config = TRWConfig()
        result = check_phase_exit(Phase.DELIVER, run_dir, config)
        rules = [f.rule for f in result.failures]
        assert "status_complete" not in rules

    def test_deliver_exit_always_includes_test_advisory(
        self,
        tmp_path: Path,
        writer: FileStateWriter,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        monkeypatch.setattr(
            "trw_mcp.state.validation.phase_gates._best_effort_integration_check",
            lambda *a, **kw: None,
        )
        monkeypatch.setattr(
            "trw_mcp.state.validation.phase_gates._best_effort_orphan_check",
            lambda *a, **kw: None,
        )
        monkeypatch.setattr(
            "trw_mcp.state.validation.phase_gates._best_effort_build_check",
            lambda *a, **kw: None,
        )
        config = TRWConfig()
        result = check_phase_exit(Phase.DELIVER, run_dir, config)
        advisory = [f for f in result.failures if f.rule == "phase_test_advisory"]
        assert len(advisory) == 1
        assert "DELIVER" in advisory[0].message

    def test_deliver_exit_warns_when_sync_missing(
        self,
        tmp_path: Path,
        writer: FileStateWriter,
        monkeypatch: pytest.MonkeyPatch,
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
        monkeypatch.setattr(
            "trw_mcp.state.validation.phase_gates._best_effort_integration_check",
            lambda *a, **kw: None,
        )
        monkeypatch.setattr(
            "trw_mcp.state.validation.phase_gates._best_effort_orphan_check",
            lambda *a, **kw: None,
        )
        monkeypatch.setattr(
            "trw_mcp.state.validation.phase_gates._best_effort_build_check",
            lambda *a, **kw: None,
        )
        config = TRWConfig()
        result = check_phase_exit(Phase.DELIVER, run_dir, config)
        sync_f = [f for f in result.failures if f.rule == "sync_required"]
        assert len(sync_f) == 1
        assert "trw_instructions_sync()" in sync_f[0].message

    def test_deliver_exit_no_sync_warning_with_sync_event(
        self,
        tmp_path: Path,
        writer: FileStateWriter,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        meta = run_dir / "meta"
        writer.append_jsonl(
            meta / "events.jsonl",
            {
                "event": "claude_md_sync",
                "ts": "2026-01-01T12:00:00Z",
            },
        )
        monkeypatch.setattr(
            "trw_mcp.state.validation.phase_gates._best_effort_integration_check",
            lambda *a, **kw: None,
        )
        monkeypatch.setattr(
            "trw_mcp.state.validation.phase_gates._best_effort_orphan_check",
            lambda *a, **kw: None,
        )
        monkeypatch.setattr(
            "trw_mcp.state.validation.phase_gates._best_effort_build_check",
            lambda *a, **kw: None,
        )
        config = TRWConfig()
        result = check_phase_exit(Phase.DELIVER, run_dir, config)
        rules = [f.rule for f in result.failures]
        assert "sync_required" not in rules

    def test_deliver_exit_no_sync_warning_when_no_events(
        self,
        tmp_path: Path,
        writer: FileStateWriter,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        monkeypatch.setattr(
            "trw_mcp.state.validation.phase_gates._best_effort_integration_check",
            lambda *a, **kw: None,
        )
        monkeypatch.setattr(
            "trw_mcp.state.validation.phase_gates._best_effort_orphan_check",
            lambda *a, **kw: None,
        )
        monkeypatch.setattr(
            "trw_mcp.state.validation.phase_gates._best_effort_build_check",
            lambda *a, **kw: None,
        )
        config = TRWConfig()
        result = check_phase_exit(Phase.DELIVER, run_dir, config)
        rules = [f.rule for f in result.failures]
        assert "sync_required" not in rules
