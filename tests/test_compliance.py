"""Tests for PRD-QUAL-003: Automated compliance enforcement."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from trw_mcp.exceptions import StateError
from trw_mcp.models.compliance import (
    ComplianceDimension,
    ComplianceMode,
    ComplianceReport,
    ComplianceStatus,
    DimensionResult,
)
from trw_mcp.models.config import TRWConfig
from trw_mcp.state.persistence import FileStateReader, FileStateWriter
from trw_mcp.tools.compliance import (
    _check_changelog_compliance,
    _check_checkpoint_compliance,
    _check_claude_md_sync_compliance,
    _check_event_compliance,
    _check_recall_compliance,
    _check_reflection_compliance,
    _compute_compliance_score,
    _determine_overall_status,
    _is_changelog_exempt,
    _load_run_events,
)


@pytest.fixture
def compliance_config() -> TRWConfig:
    """Config with compliance defaults."""
    return TRWConfig()


@pytest.fixture
def full_events() -> list[dict[str, object]]:
    """Events list representing a fully compliant session."""
    return [
        {"ts": "2026-02-09T10:00:00Z", "event": "run_init", "task": "test"},
        {"ts": "2026-02-09T10:00:01Z", "event": "recall_query", "query": "*"},
        {"ts": "2026-02-09T10:01:00Z", "event": "phase_enter", "phase": "implement"},
        {"ts": "2026-02-09T10:05:00Z", "event": "checkpoint", "message": "mid"},
        {"ts": "2026-02-09T10:10:00Z", "event": "shard_complete", "shard_id": "S1"},
        {"ts": "2026-02-09T10:15:00Z", "event": "reflection_complete"},
        {"ts": "2026-02-09T10:16:00Z", "event": "claude_md_synced"},
    ]


class TestLoadRunEvents:
    """Tests for the _load_run_events helper (DRY extraction)."""

    def test_load_with_valid_run(
        self, tmp_path: Path, writer: FileStateWriter,
    ) -> None:
        """Events and run_id returned from valid run path."""
        run_dir = tmp_path / "runs" / "run-001" / "meta"
        run_dir.mkdir(parents=True)
        writer.write_yaml(run_dir / "run.yaml", {"run_id": "run-001"})
        writer.append_jsonl(run_dir / "events.jsonl", {"event": "run_init"})

        with patch(
            "trw_mcp.tools.compliance.resolve_run_path",
            return_value=run_dir.parent,
        ):
            events, run_id = _load_run_events(str(run_dir.parent))
            assert len(events) == 1
            assert run_id == "run-001"

    def test_load_with_invalid_path_graceful(self) -> None:
        """Invalid run path returns empty events, not an exception."""
        with patch(
            "trw_mcp.tools.compliance.resolve_run_path",
            side_effect=StateError("not found"),
        ):
            events, run_id = _load_run_events("/nonexistent")
            assert events == []
            assert run_id == ""

    def test_load_auto_detect_graceful(self) -> None:
        """Auto-detect (None) path returns empty on failure without logging warning."""
        with patch(
            "trw_mcp.tools.compliance.resolve_run_path",
            side_effect=StateError("not found"),
        ):
            events, run_id = _load_run_events(None)
            assert events == []


class TestRecallCompliance:
    """Tests for recall dimension check."""

    def test_recall_present_via_query(self, compliance_config: TRWConfig, tmp_path: Path) -> None:
        events = [{"event": "recall_query", "query": "*"}]
        result = _check_recall_compliance(events, compliance_config, tmp_path)
        assert result.status == ComplianceStatus.PASS

    def test_recall_present_via_executed(self, compliance_config: TRWConfig, tmp_path: Path) -> None:
        """Alternative event type 'recall_executed' also counts."""
        events = [{"event": "recall_executed"}]
        result = _check_recall_compliance(events, compliance_config, tmp_path)
        assert result.status == ComplianceStatus.PASS

    def test_recall_missing(self, compliance_config: TRWConfig, tmp_path: Path) -> None:
        events = [{"event": "run_init"}]
        result = _check_recall_compliance(events, compliance_config, tmp_path)
        assert result.status == ComplianceStatus.FAIL
        assert result.remediation != ""

    def test_recall_via_receipts(self, compliance_config: TRWConfig, tmp_path: Path) -> None:
        """Recall detected via receipt files even without events."""
        receipts_dir = tmp_path / compliance_config.learnings_dir / compliance_config.receipts_dir
        receipts_dir.mkdir(parents=True)
        (receipts_dir / "session.yaml").write_text("receipt: true")
        result = _check_recall_compliance([], compliance_config, tmp_path)
        assert result.status == ComplianceStatus.PASS


class TestEventCompliance:
    """Tests for events dimension check."""

    def test_events_present(self, compliance_config: TRWConfig) -> None:
        events = [
            {"event": "run_init"},
            {"event": "phase_enter", "phase": "implement"},
        ]
        result = _check_event_compliance(events, compliance_config)
        assert result.status == ComplianceStatus.PASS

    def test_events_empty(self, compliance_config: TRWConfig) -> None:
        result = _check_event_compliance([], compliance_config)
        assert result.status == ComplianceStatus.FAIL

    def test_events_only_run_init(self, compliance_config: TRWConfig) -> None:
        events = [{"event": "run_init"}]
        result = _check_event_compliance(events, compliance_config)
        assert result.status == ComplianceStatus.WARNING


class TestReflectionCompliance:
    """Tests for reflection dimension check."""

    def test_reflection_present_via_complete(self, compliance_config: TRWConfig) -> None:
        events = [{"event": "reflection_complete"}]
        result = _check_reflection_compliance(events, compliance_config, "gate")
        assert result.status == ComplianceStatus.PASS

    def test_reflection_present_via_executed(self, compliance_config: TRWConfig) -> None:
        """Alternative event type 'reflect_executed' also counts."""
        events = [{"event": "reflect_executed"}]
        result = _check_reflection_compliance(events, compliance_config, "gate")
        assert result.status == ComplianceStatus.PASS

    def test_reflection_missing_advisory(self, compliance_config: TRWConfig) -> None:
        events = [{"event": "run_init"}]
        result = _check_reflection_compliance(events, compliance_config, "advisory")
        assert result.status == ComplianceStatus.PENDING

    def test_reflection_missing_gate(self, compliance_config: TRWConfig) -> None:
        events = [{"event": "run_init"}]
        result = _check_reflection_compliance(events, compliance_config, "gate")
        assert result.status == ComplianceStatus.FAIL


class TestCheckpointCompliance:
    """Tests for checkpoint dimension check."""

    def test_short_session_exempt(self, compliance_config: TRWConfig) -> None:
        events = [{"event": "run_init"}, {"event": "phase_enter"}]
        result = _check_checkpoint_compliance(events, compliance_config)
        assert result.status == ComplianceStatus.EXEMPT

    def test_long_session_with_checkpoint(self, compliance_config: TRWConfig) -> None:
        events = [
            {"event": "run_init"},
            {"event": "phase_enter"},
            {"event": "shard_complete"},
            {"event": "checkpoint"},
            {"event": "shard_complete"},
            {"event": "phase_check"},
        ]
        result = _check_checkpoint_compliance(events, compliance_config)
        assert result.status == ComplianceStatus.PASS

    def test_long_session_no_checkpoint(self, compliance_config: TRWConfig) -> None:
        events = [
            {"event": "run_init"},
            {"event": "phase_enter"},
            {"event": "shard_complete"},
            {"event": "shard_complete"},
            {"event": "shard_complete"},
            {"event": "phase_check"},
        ]
        result = _check_checkpoint_compliance(events, compliance_config)
        assert result.status == ComplianceStatus.FAIL

    def test_exactly_at_threshold(self, compliance_config: TRWConfig) -> None:
        """Exactly at threshold (5 events) without checkpoint should fail."""
        events = [{"event": f"e{i}"} for i in range(5)]
        result = _check_checkpoint_compliance(events, compliance_config)
        assert result.status == ComplianceStatus.FAIL


class TestIsChangelogExempt:
    """Tests for the extracted _is_changelog_exempt helper."""

    def test_no_events_is_exempt(self) -> None:
        result = _is_changelog_exempt([])
        assert result is not None
        assert result.status == ComplianceStatus.EXEMPT

    def test_research_run_is_exempt(self) -> None:
        events = [{"event": "run_init", "run_type": "research"}]
        result = _is_changelog_exempt(events)
        assert result is not None
        assert result.status == ComplianceStatus.EXEMPT

    def test_implementation_run_not_exempt(self) -> None:
        events = [
            {"event": "run_init"},
            {"event": "phase_enter", "phase": "implement"},
        ]
        result = _is_changelog_exempt(events)
        assert result is None  # Not exempt — check must proceed


class TestChangelogCompliance:
    """Tests for changelog dimension check."""

    def test_changelog_exists_with_unreleased(
        self, compliance_config: TRWConfig, tmp_path: Path,
    ) -> None:
        changelog = tmp_path / "CHANGELOG.md"
        changelog.write_text("# Changelog\n\n## [Unreleased]\n\n- Added feature\n")
        events = [
            {"event": "run_init"},
            {"event": "phase_enter", "phase": "implement"},
        ]
        result = _check_changelog_compliance(events, compliance_config, tmp_path)
        assert result.status == ComplianceStatus.PASS

    def test_changelog_exists_without_unreleased(
        self, compliance_config: TRWConfig, tmp_path: Path,
    ) -> None:
        """CHANGELOG exists but has no [Unreleased] section."""
        changelog = tmp_path / "CHANGELOG.md"
        changelog.write_text("# Changelog\n\n## [1.0.0]\n\n- Release\n")
        events = [
            {"event": "run_init"},
            {"event": "phase_enter", "phase": "implement"},
        ]
        result = _check_changelog_compliance(events, compliance_config, tmp_path)
        assert result.status == ComplianceStatus.WARNING

    def test_changelog_exempt_no_implementation(
        self, compliance_config: TRWConfig, tmp_path: Path,
    ) -> None:
        """Non-implementation runs exempt from changelog check."""
        events: list[dict[str, object]] = []
        result = _check_changelog_compliance(events, compliance_config, tmp_path)
        assert result.status == ComplianceStatus.EXEMPT

    def test_changelog_exempt_research_run(
        self, compliance_config: TRWConfig, tmp_path: Path,
    ) -> None:
        events = [
            {"event": "run_init", "run_type": "research"},
            {"event": "phase_enter", "phase": "implement"},
        ]
        result = _check_changelog_compliance(events, compliance_config, tmp_path)
        assert result.status == ComplianceStatus.EXEMPT

    def test_changelog_missing(
        self, compliance_config: TRWConfig, tmp_path: Path,
    ) -> None:
        events = [
            {"event": "run_init"},
            {"event": "phase_enter", "phase": "implement"},
        ]
        result = _check_changelog_compliance(events, compliance_config, tmp_path)
        assert result.status == ComplianceStatus.WARNING

    def test_changelog_uses_config_filename(
        self, tmp_path: Path,
    ) -> None:
        """Changelog filename comes from config, not hardcoded."""
        config = TRWConfig(compliance_changelog_filename="CHANGES.md")
        changelog = tmp_path / "CHANGES.md"
        changelog.write_text("# Changes\n\n## [Unreleased]\n")
        events = [
            {"event": "run_init"},
            {"event": "phase_enter", "phase": "deliver"},
        ]
        result = _check_changelog_compliance(events, config, tmp_path)
        assert result.status == ComplianceStatus.PASS
        assert "CHANGES.md" in result.message


class TestClaudeMdSyncCompliance:
    """Tests for claude_md_sync dimension check."""

    def test_sync_present_via_synced(self, compliance_config: TRWConfig) -> None:
        events = [{"event": "claude_md_synced"}]
        result = _check_claude_md_sync_compliance(events, compliance_config)
        assert result.status == ComplianceStatus.PASS

    def test_sync_present_via_executed(self, compliance_config: TRWConfig) -> None:
        """Alternative event type also counts."""
        events = [{"event": "claude_md_sync_executed"}]
        result = _check_claude_md_sync_compliance(events, compliance_config)
        assert result.status == ComplianceStatus.PASS

    def test_sync_missing(self, compliance_config: TRWConfig) -> None:
        events = [{"event": "run_init"}]
        result = _check_claude_md_sync_compliance(events, compliance_config)
        assert result.status == ComplianceStatus.PENDING


class TestScoreCalculation:
    """Tests for compliance score computation."""

    def test_all_pass(self) -> None:
        results = [
            DimensionResult(
                dimension=ComplianceDimension.RECALL,
                status=ComplianceStatus.PASS,
                message="ok",
            ),
            DimensionResult(
                dimension=ComplianceDimension.EVENTS,
                status=ComplianceStatus.PASS,
                message="ok",
            ),
        ]
        score, applicable, passing = _compute_compliance_score(results)
        assert score == 1.0
        assert applicable == 2
        assert passing == 2

    def test_all_fail(self) -> None:
        """All applicable dimensions failing should yield score 0.0."""
        results = [
            DimensionResult(
                dimension=ComplianceDimension.RECALL,
                status=ComplianceStatus.FAIL,
                message="fail",
            ),
            DimensionResult(
                dimension=ComplianceDimension.EVENTS,
                status=ComplianceStatus.FAIL,
                message="fail",
            ),
        ]
        score, applicable, passing = _compute_compliance_score(results)
        assert score == 0.0
        assert applicable == 2
        assert passing == 0

    def test_mixed_results(self) -> None:
        results = [
            DimensionResult(
                dimension=ComplianceDimension.RECALL,
                status=ComplianceStatus.PASS,
                message="ok",
            ),
            DimensionResult(
                dimension=ComplianceDimension.EVENTS,
                status=ComplianceStatus.FAIL,
                message="fail",
            ),
        ]
        score, applicable, passing = _compute_compliance_score(results)
        assert score == 0.5
        assert applicable == 2
        assert passing == 1

    def test_exempt_excluded(self) -> None:
        results = [
            DimensionResult(
                dimension=ComplianceDimension.RECALL,
                status=ComplianceStatus.PASS,
                message="ok",
            ),
            DimensionResult(
                dimension=ComplianceDimension.CHECKPOINT,
                status=ComplianceStatus.EXEMPT,
                message="short session",
            ),
        ]
        score, applicable, passing = _compute_compliance_score(results)
        assert score == 1.0
        assert applicable == 1
        assert passing == 1

    def test_pending_counts_as_passing(self) -> None:
        results = [
            DimensionResult(
                dimension=ComplianceDimension.REFLECTION,
                status=ComplianceStatus.PENDING,
                message="pending",
            ),
        ]
        score, applicable, passing = _compute_compliance_score(results)
        assert score == 1.0

    def test_all_exempt(self) -> None:
        results = [
            DimensionResult(
                dimension=ComplianceDimension.CHECKPOINT,
                status=ComplianceStatus.EXEMPT,
                message="exempt",
            ),
        ]
        score, applicable, passing = _compute_compliance_score(results)
        assert score == 1.0
        assert applicable == 0

    def test_warning_counts_as_not_passing(self) -> None:
        """WARNING status is not PASS/PENDING — counts as not passing."""
        results = [
            DimensionResult(
                dimension=ComplianceDimension.EVENTS,
                status=ComplianceStatus.WARNING,
                message="warning",
            ),
        ]
        score, applicable, passing = _compute_compliance_score(results)
        assert score == 0.0
        assert passing == 0


class TestOverallStatus:
    """Tests for overall status determination."""

    def test_high_score_passes(self, compliance_config: TRWConfig) -> None:
        result = _determine_overall_status(0.9, "gate", "strict", compliance_config)
        assert result == "pass"

    def test_advisory_mode_warning(self, compliance_config: TRWConfig) -> None:
        result = _determine_overall_status(0.3, "advisory", "lenient", compliance_config)
        assert result == "warning"

    def test_gate_strict_fails(self, compliance_config: TRWConfig) -> None:
        result = _determine_overall_status(0.6, "gate", "strict", compliance_config)
        assert result == "fail"

    def test_gate_lenient_warns(self, compliance_config: TRWConfig) -> None:
        result = _determine_overall_status(0.6, "gate", "lenient", compliance_config)
        assert result == "warning"

    def test_below_warning_threshold_gate(self, compliance_config: TRWConfig) -> None:
        result = _determine_overall_status(0.3, "gate", "lenient", compliance_config)
        assert result == "fail"

    def test_exact_pass_threshold(self, compliance_config: TRWConfig) -> None:
        """Score exactly at pass threshold (0.8) should pass."""
        result = _determine_overall_status(0.8, "gate", "strict", compliance_config)
        assert result == "pass"

    def test_exact_warning_threshold(self, compliance_config: TRWConfig) -> None:
        """Score exactly at warning threshold (0.5) in advisory mode should warn."""
        result = _determine_overall_status(0.5, "advisory", "lenient", compliance_config)
        assert result == "warning"

    def test_just_below_pass_threshold(self, compliance_config: TRWConfig) -> None:
        """Score just below pass (0.79) should not pass."""
        result = _determine_overall_status(0.79, "gate", "lenient", compliance_config)
        assert result != "pass"


class TestComplianceToolIntegration:
    """Integration tests for the full compliance check tool."""

    def test_full_compliance_pass(
        self,
        tmp_path: Path,
        writer: FileStateWriter,
        full_events: list[dict[str, object]],
    ) -> None:
        """All events present should produce passing compliance."""
        run_dir = tmp_path / "docs" / "test" / "runs" / "run-001"
        meta = run_dir / "meta"
        meta.mkdir(parents=True)
        writer.write_yaml(meta / "run.yaml", {
            "run_id": "run-001",
            "task": "test",
            "phase": "deliver",
        })
        for evt in full_events:
            writer.append_jsonl(meta / "events.jsonl", evt)

        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir(exist_ok=True)
        changelog = tmp_path / "CHANGELOG.md"
        changelog.write_text("# Changelog\n\n## [Unreleased]\n\n- Added\n")

        with patch(
            "trw_mcp.tools.compliance.resolve_run_path",
            return_value=run_dir,
        ), patch(
            "trw_mcp.tools.compliance.resolve_project_root",
            return_value=tmp_path,
        ), patch(
            "trw_mcp.tools.compliance.resolve_trw_dir",
            return_value=trw_dir,
        ):
            from trw_mcp.tools.compliance import register_compliance_tools
            from fastmcp import FastMCP

            server = FastMCP("test")
            register_compliance_tools(server)

            check_fn = server._tool_manager._tools["trw_compliance_check"].fn  # type: ignore[attr-defined]
            result = check_fn(run_path=str(run_dir), mode="gate")

            assert result["overall_status"] == "pass"
            assert result["compliance_score"] >= 0.8
            assert result["mode"] == "gate"

    def test_compliance_off_returns_exempt(self, tmp_path: Path) -> None:
        """Strictness=off should return exempt immediately."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()

        with patch(
            "trw_mcp.tools.compliance.resolve_trw_dir",
            return_value=trw_dir,
        ), patch(
            "trw_mcp.tools.compliance.resolve_project_root",
            return_value=tmp_path,
        ):
            from trw_mcp.tools.compliance import register_compliance_tools
            from fastmcp import FastMCP

            server = FastMCP("test")
            register_compliance_tools(server)

            check_fn = server._tool_manager._tools["trw_compliance_check"].fn  # type: ignore[attr-defined]
            result = check_fn(strictness="off")

            assert result["overall_status"] == "exempt"
            assert result["compliance_score"] == 1.0

    def test_compliance_advisory_mode_downgrades(
        self,
        tmp_path: Path,
        writer: FileStateWriter,
    ) -> None:
        """Advisory mode should produce warnings not failures."""
        run_dir = tmp_path / "docs" / "test" / "runs" / "run-001"
        meta = run_dir / "meta"
        meta.mkdir(parents=True)
        writer.write_yaml(meta / "run.yaml", {"run_id": "run-001"})
        # Only run_init — missing recall, reflection, etc.
        writer.append_jsonl(meta / "events.jsonl", {
            "event": "run_init",
        })

        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir(exist_ok=True)

        with patch(
            "trw_mcp.tools.compliance.resolve_run_path",
            return_value=run_dir,
        ), patch(
            "trw_mcp.tools.compliance.resolve_project_root",
            return_value=tmp_path,
        ), patch(
            "trw_mcp.tools.compliance.resolve_trw_dir",
            return_value=trw_dir,
        ):
            from trw_mcp.tools.compliance import register_compliance_tools
            from fastmcp import FastMCP

            server = FastMCP("test")
            register_compliance_tools(server)

            check_fn = server._tool_manager._tools["trw_compliance_check"].fn  # type: ignore[attr-defined]
            result = check_fn(run_path=str(run_dir), mode="advisory")

            # Advisory mode: failures downgraded to warning overall
            assert result["overall_status"] == "warning"

    def test_compliance_history_persisted(
        self,
        tmp_path: Path,
        writer: FileStateWriter,
    ) -> None:
        """Verify compliance report is appended to history.jsonl."""
        run_dir = tmp_path / "docs" / "test" / "runs" / "run-001"
        meta = run_dir / "meta"
        meta.mkdir(parents=True)
        writer.write_yaml(meta / "run.yaml", {"run_id": "run-001"})
        writer.append_jsonl(meta / "events.jsonl", {
            "ts": "2026-02-09T10:00:00Z",
            "event": "run_init",
        })

        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir(exist_ok=True)

        with patch(
            "trw_mcp.tools.compliance.resolve_run_path",
            return_value=run_dir,
        ), patch(
            "trw_mcp.tools.compliance.resolve_project_root",
            return_value=tmp_path,
        ), patch(
            "trw_mcp.tools.compliance.resolve_trw_dir",
            return_value=trw_dir,
        ):
            from trw_mcp.tools.compliance import register_compliance_tools
            from fastmcp import FastMCP

            server = FastMCP("test")
            register_compliance_tools(server)

            check_fn = server._tool_manager._tools["trw_compliance_check"].fn  # type: ignore[attr-defined]
            check_fn(run_path=str(run_dir))

            history_path = trw_dir / "compliance" / "history.jsonl"
            assert history_path.exists()
            reader = FileStateReader()
            records = reader.read_jsonl(history_path)
            assert len(records) == 1
            assert "compliance_score" in records[0]

    def test_compliance_no_events_file(self, tmp_path: Path) -> None:
        """Run path with no events file should produce warnings, not crash."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()

        with patch(
            "trw_mcp.tools.compliance.resolve_run_path",
            side_effect=StateError("No run found"),
        ), patch(
            "trw_mcp.tools.compliance.resolve_project_root",
            return_value=tmp_path,
        ), patch(
            "trw_mcp.tools.compliance.resolve_trw_dir",
            return_value=trw_dir,
        ):
            from trw_mcp.tools.compliance import register_compliance_tools
            from fastmcp import FastMCP

            server = FastMCP("test")
            register_compliance_tools(server)

            check_fn = server._tool_manager._tools["trw_compliance_check"].fn  # type: ignore[attr-defined]
            result = check_fn()
            assert "compliance_score" in result

    def test_compliance_malformed_events_graceful(
        self,
        tmp_path: Path,
        writer: FileStateWriter,
    ) -> None:
        """Malformed events should be handled gracefully."""
        run_dir = tmp_path / "docs" / "test" / "runs" / "run-001"
        meta = run_dir / "meta"
        meta.mkdir(parents=True)
        writer.write_yaml(meta / "run.yaml", {"run_id": "run-001"})
        writer.append_jsonl(meta / "events.jsonl", {
            "ts": "2026-02-09T10:00:00Z",
            "event": "run_init",
        })

        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir(exist_ok=True)

        with patch(
            "trw_mcp.tools.compliance.resolve_run_path",
            return_value=run_dir,
        ), patch(
            "trw_mcp.tools.compliance.resolve_project_root",
            return_value=tmp_path,
        ), patch(
            "trw_mcp.tools.compliance.resolve_trw_dir",
            return_value=trw_dir,
        ):
            from trw_mcp.tools.compliance import register_compliance_tools
            from fastmcp import FastMCP

            server = FastMCP("test")
            register_compliance_tools(server)

            check_fn = server._tool_manager._tools["trw_compliance_check"].fn  # type: ignore[attr-defined]
            result = check_fn(run_path=str(run_dir))
            assert "compliance_score" in result
            assert isinstance(result["dimensions"], list)


class TestComplianceModelType:
    """Tests for ComplianceReport.mode type constraint."""

    def test_report_mode_accepts_advisory(self) -> None:
        report = ComplianceReport(
            overall_status=ComplianceStatus.PASS,
            compliance_score=1.0,
            dimensions=[],
            mode="advisory",
            timestamp="2026-02-09T00:00:00Z",
            applicable_count=0,
            passing_count=0,
        )
        assert report.mode == "advisory"

    def test_report_mode_accepts_gate(self) -> None:
        report = ComplianceReport(
            overall_status=ComplianceStatus.PASS,
            compliance_score=1.0,
            dimensions=[],
            mode="gate",
            timestamp="2026-02-09T00:00:00Z",
            applicable_count=0,
            passing_count=0,
        )
        assert report.mode == "gate"


class TestConfigFields:
    """Tests for compliance config fields in TRWConfig."""

    def test_compliance_dir_default(self) -> None:
        config = TRWConfig()
        assert config.compliance_dir == "compliance"

    def test_compliance_history_file_default(self) -> None:
        config = TRWConfig()
        assert config.compliance_history_file == "history.jsonl"

    def test_compliance_changelog_filename_default(self) -> None:
        config = TRWConfig()
        assert config.compliance_changelog_filename == "CHANGELOG.md"

    def test_compliance_thresholds_default(self) -> None:
        config = TRWConfig()
        assert config.compliance_pass_threshold == 0.8
        assert config.compliance_warning_threshold == 0.5
        assert config.compliance_long_session_event_threshold == 5
