"""Integration tests for phase gate exit criteria checkers."""

from __future__ import annotations

from pathlib import Path

from trw_mcp.models.config import TRWConfig
from trw_mcp.models.requirements import ValidationFailure
from trw_mcp.state.persistence import FileStateWriter
from trw_mcp.state.validation.phase_gates import (
    _check_deliver_exit,
    _check_implement_exit,
    _check_plan_exit,
    _check_review_exit,
    _check_validate_exit,
)

from ._phase_gates_support import _make_run_dir, _write_events


class TestCheckImplementExit:
    """Tests for implement phase exit checker."""

    def test_shards_exist_no_manifest_adds_warning(self, tmp_path: Path, writer: FileStateWriter) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        failures: list[ValidationFailure] = []
        config = TRWConfig(build_check_enabled=False)
        _check_implement_exit(run_dir, config, failures)
        rules = [f.rule for f in failures]
        assert "manifest_exists" in rules

    def test_no_shards_dir_no_manifest_warning(self, tmp_path: Path, writer: FileStateWriter) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        (run_dir / "shards").rmdir()
        failures: list[ValidationFailure] = []
        config = TRWConfig(build_check_enabled=False)
        _check_implement_exit(run_dir, config, failures)
        rules = [f.rule for f in failures]
        assert "manifest_exists" not in rules

    def test_shards_with_manifest_no_warning(self, tmp_path: Path, writer: FileStateWriter) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        (run_dir / "shards" / "manifest.yaml").write_text("shards: []\n", encoding="utf-8")
        failures: list[ValidationFailure] = []
        config = TRWConfig(build_check_enabled=False)
        _check_implement_exit(run_dir, config, failures)
        rules = [f.rule for f in failures]
        assert "manifest_exists" not in rules

    def test_invalid_prd_required_status_falls_back_to_approved(self, tmp_path: Path, writer: FileStateWriter) -> None:
        """If prd_required_status_for_implement is invalid, no ValueError raised."""
        run_dir = _make_run_dir(tmp_path, writer)
        failures: list[ValidationFailure] = []
        config = TRWConfig(
            build_check_enabled=False,
            prd_required_status_for_implement="NOT_A_VALID_STATUS",
        )
        _check_implement_exit(run_dir, config, failures)


class TestCheckValidateExit:
    """Tests for validate phase exit checker."""

    def test_advisory_info_always_appended(self, tmp_path: Path, writer: FileStateWriter) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        failures: list[ValidationFailure] = []
        config = TRWConfig(build_check_enabled=False)
        _check_validate_exit(run_dir, config, failures)
        rules = [f.rule for f in failures]
        assert "phase_test_advisory" in rules

    def test_phase_test_advisory_severity_is_info(self, tmp_path: Path, writer: FileStateWriter) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        failures: list[ValidationFailure] = []
        config = TRWConfig(build_check_enabled=False)
        _check_validate_exit(run_dir, config, failures)
        info_failures = [f for f in failures if f.rule == "phase_test_advisory"]
        assert len(info_failures) == 1
        assert info_failures[0].severity == "info"


class TestCheckReviewExit:
    """Tests for review phase exit checker."""

    def test_missing_final_report_adds_warning(self, tmp_path: Path, writer: FileStateWriter) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        failures: list[ValidationFailure] = []
        config = TRWConfig()
        _check_review_exit(run_dir, config, failures)
        rules = [f.rule for f in failures]
        assert "final_report_exists" in rules

    def test_final_report_exists_no_warning(self, tmp_path: Path, writer: FileStateWriter) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        (run_dir / "reports" / "final.md").write_text("# Final Report\n", encoding="utf-8")
        failures: list[ValidationFailure] = []
        config = TRWConfig()
        _check_review_exit(run_dir, config, failures)
        rules = [f.rule for f in failures]
        assert "final_report_exists" not in rules

    def test_no_events_adds_reflection_warning(self, tmp_path: Path, writer: FileStateWriter) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        (run_dir / "reports" / "final.md").write_text("# Final\n", encoding="utf-8")
        failures: list[ValidationFailure] = []
        config = TRWConfig()
        _check_review_exit(run_dir, config, failures)
        rules = [f.rule for f in failures]
        assert "reflection_required" in rules

    def test_events_with_reflection_no_warning(self, tmp_path: Path, writer: FileStateWriter) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        (run_dir / "reports" / "final.md").write_text("# Final\n", encoding="utf-8")
        _write_events(
            run_dir / "meta",
            [{"ts": "2026-01-01T00:00:00Z", "event": "reflection_complete"}],
        )
        failures: list[ValidationFailure] = []
        config = TRWConfig()
        _check_review_exit(run_dir, config, failures)
        rules = [f.rule for f in failures]
        assert "reflection_required" not in rules

    def test_events_without_reflection_adds_warning(self, tmp_path: Path, writer: FileStateWriter) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        (run_dir / "reports" / "final.md").write_text("# Final\n", encoding="utf-8")
        _write_events(
            run_dir / "meta",
            [{"ts": "2026-01-01T00:00:00Z", "event": "run_init"}],
        )
        failures: list[ValidationFailure] = []
        config = TRWConfig()
        _check_review_exit(run_dir, config, failures)
        rules = [f.rule for f in failures]
        assert "reflection_required" in rules


class TestCheckDeliverExit:
    """Tests for deliver phase exit checker."""

    def test_run_not_complete_adds_warning(self, tmp_path: Path, writer: FileStateWriter) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        failures: list[ValidationFailure] = []
        config = TRWConfig(build_check_enabled=False)
        _check_deliver_exit(run_dir, config, failures)
        rules = [f.rule for f in failures]
        assert "status_complete" in rules

    def test_run_complete_no_status_warning(self, tmp_path: Path, writer: FileStateWriter) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        writer.write_yaml(
            run_dir / "meta" / "run.yaml",
            {
                "run_id": "20260101T000000Z-pg1234",
                "task": "phase-gates-test",
                "framework": "v24.0_TRW",
                "status": "complete",
                "phase": "deliver",
                "confidence": "high",
            },
        )
        failures: list[ValidationFailure] = []
        config = TRWConfig(build_check_enabled=False)
        _check_deliver_exit(run_dir, config, failures)
        rules = [f.rule for f in failures]
        assert "status_complete" not in rules

    def test_advisory_always_appended(self, tmp_path: Path, writer: FileStateWriter) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        failures: list[ValidationFailure] = []
        config = TRWConfig(build_check_enabled=False)
        _check_deliver_exit(run_dir, config, failures)
        rules = [f.rule for f in failures]
        assert "phase_test_advisory" in rules

    def test_events_with_sync_no_warning(self, tmp_path: Path, writer: FileStateWriter) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        writer.write_yaml(
            run_dir / "meta" / "run.yaml",
            {
                "run_id": "20260101T000000Z-pg1234",
                "task": "test",
                "framework": "v24.0_TRW",
                "status": "complete",
                "phase": "deliver",
                "confidence": "high",
            },
        )
        _write_events(
            run_dir / "meta",
            [{"ts": "2026-01-01T00:00:00Z", "event": "claude_md_sync"}],
        )
        failures: list[ValidationFailure] = []
        config = TRWConfig(build_check_enabled=False)
        _check_deliver_exit(run_dir, config, failures)
        rules = [f.rule for f in failures]
        assert "sync_required" not in rules

    def test_events_without_sync_adds_warning(self, tmp_path: Path, writer: FileStateWriter) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        _write_events(
            run_dir / "meta",
            [{"ts": "2026-01-01T00:00:00Z", "event": "run_init"}],
        )
        failures: list[ValidationFailure] = []
        config = TRWConfig(build_check_enabled=False)
        _check_deliver_exit(run_dir, config, failures)
        rules = [f.rule for f in failures]
        assert "sync_required" in rules


class TestCheckPlanExit:
    """Tests for plan phase exit checker."""

    def test_missing_plan_md_adds_error(self, tmp_path: Path, writer: FileStateWriter) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        failures: list[ValidationFailure] = []
        config = TRWConfig()
        _check_plan_exit(run_dir, config, failures)
        rules = [f.rule for f in failures]
        assert "plan_exists" in rules

    def test_plan_md_exists_no_error(self, tmp_path: Path, writer: FileStateWriter) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        (run_dir / "reports" / "plan.md").write_text("# Plan\n", encoding="utf-8")
        failures: list[ValidationFailure] = []
        config = TRWConfig()
        _check_plan_exit(run_dir, config, failures)
        rules = [f.rule for f in failures]
        assert "plan_exists" not in rules
