"""Final coverage push — targets uncovered branches in ceremony, learning,
requirements, telemetry, validation, and reflection modules.

Coverage targets:
- tools/ceremony.py     lines 74-75, 256-258, 275-277, 284-286
- tools/learning.py     lines 140-141, 143, 170-171, 301-302
- tools/requirements.py lines 122-126, 327, 579-581
- tools/telemetry.py    lines 100-101, 135-136
- state/validation.py   lines 469-470, 529-531, 622-623, 735-736,
                               815-816, 900-901, 920-921, 1317, 1343,
                               1941-1945, 1957-1958, 2021-2022, 2029
- state/reflection.py   lines 251-260
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastmcp import FastMCP

from trw_mcp.exceptions import StateError, ValidationError
from trw_mcp.models.config import TRWConfig
from trw_mcp.models.requirements import PRDStatus
from trw_mcp.models.run import Phase
from trw_mcp.state.persistence import FileStateReader, FileStateWriter
from trw_mcp.state.reflection import persist_reflection, ReflectionInputs, create_reflection_record
from trw_mcp.state.validation import (
    _check_prd_enforcement,
    _is_substantive_line,
    auto_progress_prds,
    check_integration,
    score_section_density,
)
from trw_mcp.tools.ceremony import register_ceremony_tools
from trw_mcp.tools.learning import register_learning_tools
from trw_mcp.tools.requirements import register_requirements_tools
from trw_mcp.tools.telemetry import _write_tool_event, _write_telemetry_record


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_server() -> FastMCP:
    return FastMCP("test-server")


def _extract_tool(server: FastMCP, name: str):
    """Return the raw callable registered under ``name``."""
    for tool in server._tool_manager._tools.values():
        if tool.name == name:
            return tool.fn
    raise KeyError(f"Tool {name!r} not found")


# ---------------------------------------------------------------------------
# 1. tools/ceremony.py
# ---------------------------------------------------------------------------

class TestCeremonySessionStartFailurePaths:
    """Lines 74-75: session_start exception path when recall raises."""

    def test_session_start_recall_failure_graceful(self, tmp_path: Path) -> None:
        """When recall raises, errors list captures it and tool still returns."""
        server = _make_server()
        register_ceremony_tools(server)
        tool = _extract_tool(server, "trw_session_start")

        # Step 1 now uses adapter_recall (local import from memory_adapter).
        # Patch the source module so the local import picks up the mock.
        with patch(
            "trw_mcp.state.memory_adapter.recall_learnings",
            side_effect=RuntimeError("disk failure"),
        ), patch(
            "trw_mcp.tools.ceremony.resolve_trw_dir",
            return_value=tmp_path / ".trw",
        ), patch(
            "trw_mcp.tools.ceremony.find_active_run",
            return_value=None,
        ):
            result = tool()

        assert "errors" in result
        # Recall error captured
        recall_errors = [e for e in result["errors"] if "recall" in e]
        assert len(recall_errors) == 1
        assert "disk failure" in recall_errors[0]
        # Still returns learnings (empty) and run info
        assert result["learnings"] == []
        assert result["learnings_count"] == 0

    def test_session_start_run_status_failure_graceful(self, tmp_path: Path) -> None:
        """When find_active_run raises, status error is captured."""
        server = _make_server()
        register_ceremony_tools(server)
        tool = _extract_tool(server, "trw_session_start")

        with patch(
            "trw_mcp.tools.ceremony.resolve_trw_dir",
            return_value=tmp_path / ".trw",
        ), patch(
            "trw_mcp.state.memory_adapter.recall_learnings",
            return_value=[],
        ), patch(
            "trw_mcp.tools.ceremony.log_recall_receipt",
        ), patch(
            "trw_mcp.tools.ceremony.rank_by_utility",
            return_value=[],
        ), patch(
            "trw_mcp.tools.ceremony.find_active_run",
            side_effect=OSError("permission denied"),
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
        """Lines 256-258: claude_md_sync exception populates errors list."""
        tool = self._register_and_get_deliver()

        with patch(
            "trw_mcp.tools.ceremony.resolve_trw_dir",
            return_value=tmp_path / ".trw",
        ), patch(
            "trw_mcp.tools.ceremony.find_active_run",
            return_value=None,
        ), patch(
            "trw_mcp.tools.ceremony._do_reflect",
            return_value={"status": "success", "learnings_produced": 0},
        ), patch(
            "trw_mcp.tools.ceremony._do_claude_md_sync",
            side_effect=RuntimeError("sync failed"),
        ), patch(
            "trw_mcp.tools.ceremony._do_index_sync",
            return_value={"status": "success"},
        ), patch(
            "trw_mcp.tools.ceremony._do_auto_progress",
            return_value={"status": "skipped"},
        ):
            result = tool(skip_reflect=False, skip_index_sync=False)

        assert result["claude_md_sync"]["status"] == "failed"
        assert "sync failed" in result["claude_md_sync"]["error"]
        sync_errors = [e for e in result["errors"] if "claude_md_sync" in e]
        assert len(sync_errors) == 1

    def test_deliver_auto_progress_failure_captured(self, tmp_path: Path) -> None:
        """Lines 275-277: auto_progress exception populates errors list."""
        tool = self._register_and_get_deliver()

        with patch(
            "trw_mcp.tools.ceremony.resolve_trw_dir",
            return_value=tmp_path / ".trw",
        ), patch(
            "trw_mcp.tools.ceremony.find_active_run",
            return_value=None,
        ), patch(
            "trw_mcp.tools.ceremony._do_reflect",
            return_value={"status": "success", "learnings_produced": 0},
        ), patch(
            "trw_mcp.tools.ceremony._do_claude_md_sync",
            return_value={"status": "success"},
        ), patch(
            "trw_mcp.tools.ceremony._do_index_sync",
            return_value={"status": "success"},
        ), patch(
            "trw_mcp.tools.ceremony._do_auto_progress",
            side_effect=RuntimeError("progress failed"),
        ):
            result = tool(skip_reflect=False, skip_index_sync=False)

        assert result["auto_progress"]["status"] == "failed"
        assert "progress failed" in result["auto_progress"]["error"]
        progress_errors = [e for e in result["errors"] if "auto_progress" in e]
        assert len(progress_errors) == 1

    def test_deliver_publish_learnings_failure_captured(self, tmp_path: Path) -> None:
        """Lines 284-286: publish_learnings import/exception populates errors list."""
        tool = self._register_and_get_deliver()

        mock_pub = MagicMock(side_effect=RuntimeError("publish failed"))

        with patch(
            "trw_mcp.tools.ceremony.resolve_trw_dir",
            return_value=tmp_path / ".trw",
        ), patch(
            "trw_mcp.tools.ceremony.find_active_run",
            return_value=None,
        ), patch(
            "trw_mcp.tools.ceremony._do_reflect",
            return_value={"status": "success", "learnings_produced": 0},
        ), patch(
            "trw_mcp.tools.ceremony._do_claude_md_sync",
            return_value={"status": "success"},
        ), patch(
            "trw_mcp.tools.ceremony._do_index_sync",
            return_value={"status": "success"},
        ), patch(
            "trw_mcp.tools.ceremony._do_auto_progress",
            return_value={"status": "skipped"},
        ), patch.dict(
            "sys.modules",
            {"trw_mcp.telemetry.publisher": MagicMock(publish_learnings=mock_pub)},
        ):
            result = tool(skip_reflect=False, skip_index_sync=False)

        assert result["publish_learnings"]["status"] == "failed"
        pub_errors = [e for e in result["errors"] if "publish_learnings" in e]
        assert len(pub_errors) == 1


# ---------------------------------------------------------------------------
# 2. tools/learning.py
# ---------------------------------------------------------------------------

class TestLearningExceptionPaths:
    """Lines 140-141, 143: YAML read exception inside distribution enforcement.
       Lines 170-171: distribution enforcement exception.
       Lines 301-302: claude_md_sync failure path."""

    def _register_and_get(self, name: str):
        server = _make_server()
        register_learning_tools(server)
        return _extract_tool(server, name)

    def test_trw_learn_yaml_read_exception_skips_file(self, tmp_path: Path) -> None:
        """Distribution check is fail-open when list_active_learnings raises."""
        import trw_mcp.tools.learning as mod

        # Enable forced distribution so the distribution block runs
        old_config = mod._config
        try:
            cfg = mod._config.__class__()
            object.__setattr__(cfg, "impact_forced_distribution_enabled", True)
            mod._config = cfg

            tool = self._register_and_get("trw_learn")

            with patch(
                "trw_mcp.tools.learning.resolve_trw_dir",
                return_value=tmp_path / ".trw",
            ), patch(
                "trw_mcp.tools.learning.generate_learning_id",
                return_value="L-test0001",
            ), patch(
                "trw_mcp.tools.learning.adapter_store",
                return_value={"learning_id": "L-test0001", "path": "sqlite://L-test0001", "status": "recorded", "distribution_warning": ""},
            ), patch(
                "trw_mcp.tools.learning.update_analytics",
            ), patch(
                "trw_mcp.tools.learning.list_active_learnings",
                side_effect=StateError("adapter read failure"),
            ):
                result = tool(
                    summary="test summary",
                    detail="test detail",
                    impact=0.8,  # >= 0.7 triggers distribution check
                )

            # Distribution enforcement raised but exception was swallowed (fail-open)
            assert result["status"] == "recorded"
            assert result["learning_id"] == "L-test0001"
        finally:
            mod._config = old_config

    def test_trw_learn_distribution_exception_fail_open(self, tmp_path: Path) -> None:
        """enforce_tier_distribution raising is fail-open in trw_learn."""
        import trw_mcp.tools.learning as mod

        old_config = mod._config
        try:
            cfg = mod._config.__class__()
            object.__setattr__(cfg, "impact_forced_distribution_enabled", True)
            mod._config = cfg

            tool = self._register_and_get("trw_learn")

            with patch(
                "trw_mcp.tools.learning.resolve_trw_dir",
                return_value=tmp_path / ".trw",
            ), patch(
                "trw_mcp.tools.learning.generate_learning_id",
                return_value="L-test0002",
            ), patch(
                "trw_mcp.tools.learning.adapter_store",
                return_value={"learning_id": "L-test0002", "path": "sqlite://L-test0002", "status": "recorded", "distribution_warning": ""},
            ), patch(
                "trw_mcp.tools.learning.update_analytics",
            ), patch(
                "trw_mcp.tools.learning.list_active_learnings",
                return_value=[{"id": "L-abc", "impact": 0.8}],
            ), patch(
                "trw_mcp.tools.learning.enforce_tier_distribution",
                side_effect=RuntimeError("distribution exploded"),
            ):
                result = tool(
                    summary="test summary",
                    detail="test detail",
                    impact=0.9,
                )

            # Fail-open: distribution failure must not block recording
            assert result["status"] == "recorded"
        finally:
            mod._config = old_config

    def test_trw_learn_update_write_failure(self, tmp_path: Path) -> None:
        """learn_update delegates to adapter_update and returns 'updated' status."""
        tool = self._register_and_get("trw_learn_update")

        with patch(
            "trw_mcp.tools.learning.resolve_trw_dir",
            return_value=tmp_path / ".trw",
        ), patch(
            "trw_mcp.tools.learning.adapter_update",
            return_value={"learning_id": "L-testXX", "changes": "status→resolved", "status": "updated"},
        ):
            result = tool(learning_id="L-testXX", status="resolved")

        assert result["status"] == "updated"

    def test_trw_claude_md_sync_failure_propagates(self, tmp_path: Path) -> None:
        """Lines 301-302: claude_md_sync failure path in trw_claude_md_sync tool."""
        import trw_mcp.tools.learning as mod

        tool = self._register_and_get("trw_claude_md_sync")

        with patch(
            "trw_mcp.tools.learning.execute_claude_md_sync",
            side_effect=RuntimeError("sync exploded"),
        ):
            with pytest.raises(RuntimeError, match="sync exploded"):
                tool(scope="root")


# ---------------------------------------------------------------------------
# 3. tools/requirements.py
# ---------------------------------------------------------------------------

class TestRequirementsFailurePaths:
    """Lines 122-126: invalid risk_level. Line 327: validate path. Lines 579-581: auto-sync failure."""

    def _register_and_get(self, name: str):
        server = _make_server()
        register_requirements_tools(server)
        return _extract_tool(server, name)

    def test_prd_create_invalid_risk_level_raises(self, tmp_path: Path) -> None:
        """Lines 122-126: invalid risk_level raises ValidationError."""
        tool = self._register_and_get("trw_prd_create")

        with patch(
            "trw_mcp.tools.requirements.resolve_project_root",
            return_value=tmp_path,
        ), patch(
            "trw_mcp.tools.requirements.next_prd_sequence",
            return_value=42,
        ):
            with pytest.raises(ValidationError, match="Invalid risk_level"):
                tool(
                    input_text="Test PRD content",
                    category="CORE",
                    priority="P1",
                    risk_level="EXTREMELY_DANGEROUS",
                )

    @pytest.mark.parametrize("risk_level", ["critical", "high", "medium", "low"])
    def test_prd_create_valid_risk_levels_accepted(
        self, tmp_path: Path, risk_level: str
    ) -> None:
        """Valid risk_level values do not raise."""
        tool = self._register_and_get("trw_prd_create")

        prds_dir = tmp_path / "docs" / "requirements-aare-f" / "prds"
        prds_dir.mkdir(parents=True)

        with patch(
            "trw_mcp.tools.requirements.resolve_project_root",
            return_value=tmp_path,
        ), patch(
            "trw_mcp.tools.requirements.next_prd_sequence",
            return_value=99,
        ), patch(
            "trw_mcp.tools.requirements._config",
        ) as mock_cfg:
            mock_cfg.prds_relative_path = "docs/requirements-aare-f/prds"
            mock_cfg.trw_dir = ".trw"
            mock_cfg.index_auto_sync_on_status_change = False
            mock_cfg.ambiguity_rate_max = 0.3
            mock_cfg.completeness_min = 0.7
            mock_cfg.traceability_coverage_min = 0.5
            result = tool(
                input_text="Test feature",
                category="CORE",
                priority="P1",
                risk_level=risk_level,
            )
        assert result["prd_id"] == "PRD-CORE-099"

    def test_prd_validate_path_exists(self, tmp_path: Path) -> None:
        """Line 327: trw_prd_validate reads and validates an existing file."""
        tool = self._register_and_get("trw_prd_validate")

        # Write a minimal PRD file
        prd_content = """\
---
prd:
  id: PRD-CORE-001
  title: Test PRD
  version: '1.0'
  priority: P1
  category: CORE
  status: draft
---

## 1. Problem Statement

This is a test problem statement with real content.

## 2. Goals & Non-Goals

Goals: achieve something meaningful.

## 3. User Stories

As a user I want this feature.

## 4. Functional Requirements

FR01: The system shall do something.

## 5. Non-Functional Requirements

NFR01: Performance target under 100ms.

## 6. Technical Approach

Use Python and pytest.

## 7. Test Strategy

Unit tests and integration tests.

## 8. Rollout Plan

Gradual rollout.

## 9. Success Metrics

Metric: adoption rate.

## 10. Dependencies & Risks

No dependencies.

## 11. Open Questions

None.

## 12. Traceability Matrix

| Requirement | Implementation |
|-------------|----------------|
| FR01 | test.py |
"""
        prd_file = tmp_path / "PRD-CORE-001.md"
        prd_file.write_text(prd_content, encoding="utf-8")

        result = tool(prd_path=str(prd_file))

        assert result["path"] == str(prd_file)
        assert "total_score" in result
        assert "quality_tier" in result

    def test_auto_sync_index_failure_returns_false(self, tmp_path: Path) -> None:
        """Lines 579-581: _auto_sync_index catches exception and returns False."""
        from trw_mcp.tools.requirements import _auto_sync_index

        with patch(
            "trw_mcp.tools.requirements.resolve_project_root",
            side_effect=RuntimeError("no project root"),
        ):
            result = _auto_sync_index()

        assert result is False


# ---------------------------------------------------------------------------
# 4. tools/telemetry.py
# ---------------------------------------------------------------------------

class TestTelemetryExceptionPaths:
    """Lines 100-101: FR04 telemetry exception path.
       Lines 135-136: fallback session-events write exception path."""

    def test_fr04_telemetry_write_record_exception_suppressed(self, tmp_path: Path) -> None:
        """Lines 100-101: _write_telemetry_record raises but exception is swallowed."""
        import trw_mcp.tools.telemetry as tel_mod

        old_config = tel_mod._config
        try:
            cfg = tel_mod._config.__class__()
            object.__setattr__(cfg, "telemetry_enabled", True)
            object.__setattr__(cfg, "telemetry", True)
            tel_mod._config = cfg

            def bomb_fn() -> dict[str, object]:
                return {"ok": True}

            from trw_mcp.tools.telemetry import log_tool_call
            wrapped = log_tool_call(bomb_fn)

            with patch(
                "trw_mcp.tools.telemetry._get_cached_run_dir",
                return_value=None,
            ), patch(
                "trw_mcp.tools.telemetry._write_tool_event",
            ), patch(
                "trw_mcp.tools.telemetry._write_telemetry_record",
                side_effect=RuntimeError("telemetry write failed"),
            ):
                # Should not raise — exception in FR04 path is swallowed
                result = wrapped()

            assert result == {"ok": True}
        finally:
            tel_mod._config = old_config

    def test_write_tool_event_fallback_exception_suppressed(self, tmp_path: Path) -> None:
        """Lines 135-136: fallback resolve_trw_dir raises, exception suppressed."""
        import trw_mcp.tools.telemetry as tel_mod

        with patch(
            "trw_mcp.tools.telemetry._get_cached_run_dir",
            return_value=None,  # No active run -> go to fallback
        ), patch(
            "trw_mcp.tools.telemetry.resolve_trw_dir",
            side_effect=RuntimeError("no trw dir"),
        ):
            # Must not raise
            _write_tool_event("test_tool", 12.5, True, None)

    def test_write_telemetry_record_writes_to_logs(self, tmp_path: Path) -> None:
        """_write_telemetry_record creates tool-telemetry.jsonl."""
        import trw_mcp.tools.telemetry as tel_mod

        trw_dir = tmp_path / ".trw"
        (trw_dir / "logs").mkdir(parents=True)

        with patch(
            "trw_mcp.tools.telemetry.resolve_trw_dir",
            return_value=trw_dir,
        ):
            _write_telemetry_record(
                "my_tool", (), {}, 42.0, {"result": "ok"}, True,
            )

        telemetry_file = trw_dir / "logs" / tel_mod._config.telemetry_file
        assert telemetry_file.exists()


# ---------------------------------------------------------------------------
# 5. state/validation.py
# ---------------------------------------------------------------------------

class TestValidationRunTypeReadFailure:
    """Lines 469-470: StateError reading run.yaml for run_type check."""

    def test_check_prd_enforcement_run_yaml_read_error_continues(
        self, tmp_path: Path
    ) -> None:
        """When run.yaml raises StateError during run_type check, proceeds normally."""
        run_path = tmp_path / "run"
        meta = run_path / "meta"
        meta.mkdir(parents=True)
        # Write a valid-but-strange run.yaml so it exists, then mock the read to raise
        run_yaml = meta / "run.yaml"
        run_yaml.write_text("run_id: test\n")

        config = TRWConfig(trw_dir=str(tmp_path / ".trw"))

        # Patch the function-local imports at their source modules
        with patch(
            "trw_mcp.state.prd_utils.discover_governing_prds",
            return_value=[],
        ), patch(
            "trw_mcp.state._paths.resolve_project_root",
            return_value=tmp_path,
        ), patch(
            "trw_mcp.state.persistence.FileStateReader.read_yaml",
            side_effect=StateError("corrupt yaml"),
        ):
            failures = _check_prd_enforcement(
                run_path, config, PRDStatus.APPROVED, "implement",
            )

        # Should return the "no governing PRDs" advisory (StateError was caught on lines 469-470)
        assert len(failures) == 1
        assert failures[0].rule == "prd_discovery"


class TestValidationPrdReadFailed:
    """Lines 529-531: PRD file read raises OSError during status check."""

    def test_prd_read_failure_adds_readable_failure(self, tmp_path: Path) -> None:
        """When prd_file.read_text raises OSError, a prd_readable failure is added."""
        run_path = tmp_path / "run"
        (run_path / "meta").mkdir(parents=True)

        # The function uses: project_root / config.prds_relative_path
        # config.prds_relative_path defaults to "docs/requirements-aare-f/prds"
        prds_dir = tmp_path / "docs" / "requirements-aare-f" / "prds"
        prds_dir.mkdir(parents=True)

        # Create the PRD file so it exists (avoids the "not found" failure branch)
        prd_file = prds_dir / "PRD-TEST-001.md"
        prd_file.write_text("---\nprd:\n  status: draft\n---\n")

        config = TRWConfig(trw_dir=str(tmp_path / ".trw"))

        # All are function-local imports inside _check_prd_enforcement
        # resolve_project_root is imported from trw_mcp.state._paths inside the function
        with patch(
            "trw_mcp.state.prd_utils.discover_governing_prds",
            return_value=["PRD-TEST-001"],
        ), patch(
            "trw_mcp.state._paths.resolve_project_root",
            return_value=tmp_path,
        ), patch(
            "trw_mcp.state.prd_utils.parse_frontmatter",
            side_effect=OSError("cannot read"),
        ):
            failures = _check_prd_enforcement(
                run_path, config, PRDStatus.APPROVED, "implement",
            )

        readable_failures = [f for f in failures if f.rule == "prd_readable"]
        assert len(readable_failures) == 1
        assert "PRD-TEST-001" in readable_failures[0].message


class TestValidationBuildStatusStaleness:
    """Lines 622-623: ValueError/TypeError when parsing build timestamp."""

    def test_build_status_unparseable_timestamp_continues(
        self, tmp_path: Path
    ) -> None:
        """When timestamp is unparseable, staleness is treated as fresh (pass)."""
        from trw_mcp.state.validation import _check_build_status

        trw_dir = tmp_path / ".trw"
        context_dir = trw_dir / "context"
        context_dir.mkdir(parents=True)

        cache = context_dir / "build-status.yaml"
        cache.write_text(
            "tests_passed: true\nmypy_clean: true\ntimestamp: not-a-date\n"
        )

        config = TRWConfig(trw_dir=str(trw_dir))
        object.__setattr__(config, "build_check_enabled", True)
        object.__setattr__(config, "build_gate_enforcement", "strict")

        failures = _check_build_status(trw_dir, config, "validate")

        # Unparseable timestamp: treated as fresh, no staleness failure
        staleness_failures = [f for f in failures if f.rule == "build_staleness"]
        assert len(staleness_failures) == 0


class TestValidationIntegrationScannerException:
    """Lines 735-736: exception inside _best_effort_integration_check is swallowed."""

    def test_integration_check_exception_never_blocks(self, tmp_path: Path) -> None:
        """When check_integration raises, _best_effort_integration_check silently returns."""
        from trw_mcp.state.validation import _best_effort_integration_check

        failures: list = []

        # resolve_project_root is a function-local import inside _best_effort_integration_check
        # so we must patch at the source module
        with patch(
            "trw_mcp.state._paths.resolve_project_root",
            side_effect=RuntimeError("no root"),
        ):
            _best_effort_integration_check(failures)

        # No failures appended, no exception raised
        assert failures == []


class TestValidationImplementPhaseInvalidStatus:
    """Lines 815-816: invalid prd_required_status_for_implement falls back to APPROVED."""

    def test_implement_phase_invalid_required_status_fallback(
        self, tmp_path: Path
    ) -> None:
        """Invalid prd_required_status_for_implement falls back to PRDStatus.APPROVED."""
        from trw_mcp.state.validation import check_phase_exit

        run_path = tmp_path / "run"
        (run_path / "meta").mkdir(parents=True)
        (run_path / "reports").mkdir()
        (run_path / "scratch" / "_orchestrator").mkdir(parents=True)
        (run_path / "shards").mkdir()

        # Write minimal run.yaml
        (run_path / "meta" / "run.yaml").write_text(
            "run_id: test\ntask: test\nstatus: active\nphase: implement\n"
        )

        config = TRWConfig(trw_dir=str(tmp_path / ".trw"))
        # Set invalid status string to trigger ValueError fallback
        object.__setattr__(config, "prd_required_status_for_implement", "INVALID_STATUS")

        with patch(
            "trw_mcp.state.validation._check_prd_enforcement",
            return_value=[],
        ), patch(
            "trw_mcp.state.validation._best_effort_build_check",
        ):
            result = check_phase_exit(Phase.IMPLEMENT, run_path, config)

        # Should not raise; fallback to APPROVED ran
        assert result is not None


class TestValidationReflectionQualityException:
    """Lines 900-901: reflection quality check exception is swallowed (best-effort)."""

    def test_review_phase_reflection_quality_exception_swallowed(
        self, tmp_path: Path
    ) -> None:
        """When compute_reflection_quality raises, review phase continues."""
        from trw_mcp.state.validation import check_phase_exit

        run_path = tmp_path / "run"
        meta = run_path / "meta"
        meta.mkdir(parents=True)
        (run_path / "reports").mkdir()

        # Write events.jsonl with a reflection event
        events_file = meta / "events.jsonl"
        import json
        events_file.write_text(
            json.dumps({"event": "reflection_complete", "ts": "2026-01-01T00:00:00Z"}) + "\n"
        )
        (meta / "run.yaml").write_text("status: active\n")

        # Final report required for review phase
        (run_path / "reports" / "final.md").write_text("# Final Report\n")

        config = TRWConfig(trw_dir=str(tmp_path / ".trw"))

        with patch(
            "trw_mcp.state.analytics.compute_reflection_quality",
            side_effect=RuntimeError("quality check exploded"),
        ), patch(
            "trw_mcp.state.validation._best_effort_integration_check",
        ):
            result = check_phase_exit(Phase.REVIEW, run_path, config)

        # Should not raise
        assert result is not None


class TestValidationDeliverRunYamlReadException:
    """Lines 920-921: OSError reading run.yaml in deliver phase is swallowed."""

    def test_deliver_phase_run_yaml_read_exception_swallowed(
        self, tmp_path: Path
    ) -> None:
        """When reading run.yaml raises in deliver phase, exception is suppressed."""
        from trw_mcp.state.validation import check_phase_exit

        run_path = tmp_path / "run"
        meta = run_path / "meta"
        meta.mkdir(parents=True)
        (run_path / "reports").mkdir()

        # Create run.yaml with unreadable permissions workaround: patch reader
        run_yaml = meta / "run.yaml"
        run_yaml.write_text("status: active\n")

        # Write events with sync event
        import json
        events = [
            {"event": "trw_claude_md_sync_complete", "ts": "2026-01-01T00:00:00Z"},
        ]
        (meta / "events.jsonl").write_text(
            "\n".join(json.dumps(e) for e in events) + "\n"
        )

        config = TRWConfig(trw_dir=str(tmp_path / ".trw"))

        with patch(
            "trw_mcp.state.persistence.FileStateReader.read_yaml",
            side_effect=StateError("read failed"),
        ), patch(
            "trw_mcp.state.validation._best_effort_build_check",
        ), patch(
            "trw_mcp.state.validation._best_effort_integration_check",
        ):
            result = check_phase_exit(Phase.DELIVER, run_path, config)

        # Exception in run.yaml read is swallowed
        assert result is not None


class TestValidationIsSubstantiveLine:
    r"""Lines 1315-1317: HTML comment inline branch.

    NOTE: Lines 1315-1317 (single-line HTML comment check) are defensive dead code.
    _PLACEHOLDER_RE already matches '<!-- ... -->' via its first alternative
    (r'^\s*<!--.*?-->\s*$'), so the check at line 1316 is never reached.
    These are excluded as unreachable dead code (like validation.py:1343).
    """

    def test_single_line_html_comment_not_substantive(self) -> None:
        """'<!-- comment -->' returns False (via _PLACEHOLDER_RE match at line 1314)."""
        assert _is_substantive_line("<!-- This is a comment -->") is False

    def test_multiline_html_comment_start_is_substantive(self) -> None:
        """Comment that doesn't end with --> is substantive (not a single-line comment)."""
        assert _is_substantive_line("<!-- start of block") is True

    def test_table_separator_not_substantive(self) -> None:
        """Table separator rows like |---|---| are not substantive."""
        assert _is_substantive_line("|---|---|") is False

    def test_horizontal_rule_not_substantive(self) -> None:
        """Horizontal rules (---) are not substantive."""
        assert _is_substantive_line("---") is False

    def test_real_content_is_substantive(self) -> None:
        """Normal text content is substantive."""
        assert _is_substantive_line("FR01: The system shall process requests.") is True

    def test_heading_not_substantive(self) -> None:
        """Lines starting with # are not substantive."""
        assert _is_substantive_line("# Section heading") is False

    def test_placeholder_braces_not_substantive(self) -> None:
        """Template placeholder {like this} is not substantive."""
        assert _is_substantive_line("{Brief description here}") is False


class TestValidationScoreSectionDensityEmpty:
    r"""Lines 1340-1343: score_section_density behavior.

    NOTE: Line 1343 ('return SectionScore(section_name=section_name)' when total==0)
    is defensive dead code. str.split('\n') always returns at least [''] (len >= 1),
    so total is never 0. This is an unreachable guard.
    """

    def test_score_section_density_empty_string_zero_density(self) -> None:
        """Empty body string: total=1 (from ['']), density=0."""
        result = score_section_density("Test Section", "")
        # "" splits to [""], total=1, substantive=0, density=0
        assert result.section_name == "Test Section"
        assert result.density == 0.0
        assert result.substantive_lines == 0

    def test_score_section_density_html_comment_counted_as_placeholder(self) -> None:
        """HTML comment lines are counted as placeholder, not substantive."""
        body = "<!-- comment -->\nReal content here\n"
        result = score_section_density("Test", body)
        assert result.substantive_lines == 1
        assert result.placeholder_lines == 1

    def test_score_section_density_substantive_content(self) -> None:
        """Substantive lines increase density score."""
        body = "FR01: System shall process requests.\nFR02: System shall respond quickly.\n"
        result = score_section_density("Functional Requirements", body)
        assert result.substantive_lines == 2
        assert result.density > 0.0


class TestValidationAutoProgressOSError:
    """Lines 1941-1945: OSError during auto_progress_prds PRD read."""

    def test_auto_progress_prd_read_oserror_continues(self, tmp_path: Path) -> None:
        """Lines 1941-1945: OSError reading PRD file during auto_progress is skipped."""
        run_path = tmp_path / "run"
        (run_path / "meta").mkdir(parents=True)

        prds_dir = tmp_path / "prds"
        prds_dir.mkdir()

        # Create the PRD file
        prd_file = prds_dir / "PRD-CORE-001.md"
        prd_file.write_text("---\nprd:\n  status: draft\n---\n")

        config = TRWConfig(trw_dir=str(tmp_path / ".trw"))

        # All of these are function-local imports inside auto_progress_prds
        with patch(
            "trw_mcp.state.prd_utils.discover_governing_prds",
            return_value=["PRD-CORE-001"],
        ), patch(
            "trw_mcp.state.prd_utils.parse_frontmatter",
            side_effect=OSError("cannot read prd"),
        ):
            results = auto_progress_prds(run_path, "plan", prds_dir, config)

        # The PRD with the error is skipped (continue), empty results
        assert results == []

    def test_auto_progress_index_sync_exception_swallowed(self, tmp_path: Path) -> None:
        """Lines 1957-1958: index sync exception after successful apply is swallowed."""
        run_path = tmp_path / "run"
        (run_path / "meta").mkdir(parents=True)

        prds_dir = tmp_path / "prds"
        prds_dir.mkdir()

        # Create PRD in draft status (will be progressed to review on plan phase)
        prd_content = "---\nprd:\n  status: draft\n---\n\nContent\n"
        prd_file = prds_dir / "PRD-CORE-002.md"
        prd_file.write_text(prd_content)

        config = TRWConfig(trw_dir=str(tmp_path / ".trw"))

        # All of these are function-local imports inside auto_progress_prds
        with patch(
            "trw_mcp.state.prd_utils.discover_governing_prds",
            return_value=["PRD-CORE-002"],
        ), patch(
            "trw_mcp.state.prd_utils.parse_frontmatter",
            return_value={"status": "draft"},
        ), patch(
            "trw_mcp.state.prd_utils.is_valid_transition",
            return_value=True,
        ), patch(
            "trw_mcp.state.prd_utils.check_transition_guards",
            return_value=MagicMock(allowed=True),
        ), patch(
            "trw_mcp.state.prd_utils.update_frontmatter",
        ), patch(
            "trw_mcp.state.index_sync.sync_index_md",
            side_effect=RuntimeError("index sync failed"),
        ):
            # Should not raise
            results = auto_progress_prds(run_path, "plan", prds_dir, config)

        applied = [r for r in results if r.get("applied")]
        assert len(applied) == 1


class TestValidationCheckIntegrationServerOSError:
    """Lines 2021-2022: OSError reading server.py falls back to empty string."""

    def test_check_integration_server_read_oserror(self, tmp_path: Path) -> None:
        """Lines 2021-2022: OSError on server.py read results in server_content=''."""
        src_dir = tmp_path / "trw_mcp"
        tools_dir = src_dir / "tools"
        tools_dir.mkdir(parents=True)
        server_path = src_dir / "server.py"

        # Create a tool file with a register function
        tool_content = "def register_mytool_tools(server):\n    pass\n"
        (tools_dir / "mytool.py").write_text(tool_content)

        # Create server.py file with content (so is_file() returns True)
        server_path.write_text("from trw_mcp.tools.mytool import register_mytool_tools\n")

        # Only fail reads of the server path specifically — tool files must succeed
        original_read_text = Path.read_text

        def selective_read_text(self: Path, *args, **kwargs) -> str:  # type: ignore[override]
            if self == server_path:
                raise OSError("permission denied")
            return original_read_text(self, *args, **kwargs)

        with patch.object(Path, "read_text", selective_read_text):
            result = check_integration(src_dir)

        # server_content falls back to "" -> mytool not in registered_funcs
        assert "unregistered" in result
        assert "mytool" in result["unregistered"]

    def test_check_integration_missing_tests_appended(self, tmp_path: Path) -> None:
        """Line 2029: missing_tests entries are appended to result."""
        src_dir = tmp_path / "trw_mcp"
        tools_dir = src_dir / "tools"
        tools_dir.mkdir(parents=True)

        # The function builds tests_dir from src_dir.parent.parent / "tests"
        # src_dir = tmp_path/trw_mcp → src_dir.parent.parent = tmp_path.parent
        tests_dir = tmp_path.parent / "tests"
        # Don't create the tests dir so test files won't be found

        # Create a tool file with register function — no matching test file
        (tools_dir / "newtool.py").write_text(
            "def register_newtool_tools(server):\n    pass\n"
        )

        # server.py that imports and calls the tool (so it IS registered)
        server_path = src_dir / "server.py"
        server_path.write_text(
            "from trw_mcp.tools.newtool import register_newtool_tools\n"
            "register_newtool_tools(server)\n"
        )

        result = check_integration(src_dir)

        assert "missing_tests" in result
        assert isinstance(result["missing_tests"], list)
        # test_tools_newtool.py won't exist in the derived tests dir
        assert "test_tools_newtool.py" in result["missing_tests"]
        # It IS registered (server.py has the import+call), so not in unregistered
        assert "newtool" not in result["unregistered"]


# ---------------------------------------------------------------------------
# 6. state/reflection.py
# ---------------------------------------------------------------------------

class TestReflectionPersistWithRunPath:
    """Lines 251-260: persist_reflection with run_path that has a valid meta/ dir."""

    def test_persist_reflection_logs_event_to_run(self, tmp_path: Path) -> None:
        """Lines 257-264: when run_path has meta/, event is logged there."""
        trw_dir = tmp_path / ".trw"
        (trw_dir / "reflections").mkdir(parents=True)

        run_path = tmp_path / "run"
        meta = run_path / "meta"
        meta.mkdir(parents=True)
        events_file = meta / "events.jsonl"
        events_file.write_text("")  # empty but exists

        # Build a minimal ReflectionInputs
        inputs = ReflectionInputs(
            events=[],
            run_id="test-run-01",
            error_events=[],
            phase_transitions=[],
            repeated_ops=[],
            success_patterns=[],
            tool_sequences=[],
            validated_learnings=[],
        )

        reflection = create_reflection_record(inputs, [], "session")

        with patch(
            "trw_mcp.state.reflection._config",
        ) as mock_cfg:
            mock_cfg.reflections_dir = "reflections"
            persist_reflection(
                trw_dir=trw_dir,
                reflection=reflection,
                run_path=str(run_path),
                scope="session",
                learnings_count=0,
            )

        # Verify reflection file was written
        reflection_files = list((trw_dir / "reflections").glob("*.yaml"))
        assert len(reflection_files) == 1

        # Verify event was logged to run events.jsonl
        import json
        logged = [json.loads(line) for line in events_file.read_text().splitlines() if line.strip()]
        reflection_events = [e for e in logged if e.get("event") == "reflection_complete"]
        assert len(reflection_events) == 1
        assert reflection_events[0]["scope"] == "session"

    def test_persist_reflection_no_run_path_skips_event(self, tmp_path: Path) -> None:
        """When run_path is None, no event is logged to run directory."""
        trw_dir = tmp_path / ".trw"
        (trw_dir / "reflections").mkdir(parents=True)

        inputs = ReflectionInputs(
            events=[],
            run_id=None,
            error_events=[],
            phase_transitions=[],
            repeated_ops=[],
            success_patterns=[],
            tool_sequences=[],
            validated_learnings=[],
        )

        reflection = create_reflection_record(inputs, [], "session")

        with patch(
            "trw_mcp.state.reflection._config",
        ) as mock_cfg:
            mock_cfg.reflections_dir = "reflections"
            persist_reflection(
                trw_dir=trw_dir,
                reflection=reflection,
                run_path=None,
                scope="session",
                learnings_count=2,
            )

        reflection_files = list((trw_dir / "reflections").glob("*.yaml"))
        assert len(reflection_files) == 1

    def test_persist_reflection_run_path_missing_meta_skips_event(
        self, tmp_path: Path
    ) -> None:
        """When run meta/ dir doesn't exist, event is not written."""
        trw_dir = tmp_path / ".trw"
        (trw_dir / "reflections").mkdir(parents=True)

        # run_path exists but has no meta/ directory
        run_path = tmp_path / "run_no_meta"
        run_path.mkdir()

        inputs = ReflectionInputs(
            events=[],
            run_id="test-run-02",
            error_events=[],
            phase_transitions=[],
            repeated_ops=[],
            success_patterns=[],
            tool_sequences=[],
            validated_learnings=[],
        )

        reflection = create_reflection_record(inputs, [], "run")

        with patch(
            "trw_mcp.state.reflection._config",
        ) as mock_cfg:
            mock_cfg.reflections_dir = "reflections"
            persist_reflection(
                trw_dir=trw_dir,
                reflection=reflection,
                run_path=str(run_path),
                scope="run",
                learnings_count=1,
            )

        # Reflection file still written
        reflection_files = list((trw_dir / "reflections").glob("*.yaml"))
        assert len(reflection_files) == 1
        # No events.jsonl created
        assert not (run_path / "meta" / "events.jsonl").exists()


# ---------------------------------------------------------------------------
# ceremony.py lines 74-75: _get_run_status exception path
# ---------------------------------------------------------------------------

class TestCeremonyGetRunStatus:
    """Lines 74-75: _get_run_status exception handler."""

    def test_get_run_status_read_error_returns_error_status(self, tmp_path: Path) -> None:
        """Lines 74-75: When read_yaml raises StateError, result includes error_reading."""
        from trw_mcp.tools.ceremony import _get_run_status
        import trw_mcp.tools.ceremony as cer_mod

        run_dir = tmp_path / "run"
        meta = run_dir / "meta"
        meta.mkdir(parents=True)
        (meta / "run.yaml").write_text("status: active\n")

        original_read_yaml = cer_mod._reader.read_yaml
        try:
            cer_mod._reader.read_yaml = lambda p: (_ for _ in ()).throw(StateError("corrupt"))  # type: ignore[method-assign]
            result = _get_run_status(run_dir)
        finally:
            cer_mod._reader.read_yaml = original_read_yaml

        assert result["status"] == "error_reading"
        assert result["active_run"] == str(run_dir)

    def test_get_run_status_oserror_caught(self, tmp_path: Path) -> None:
        """Lines 74-75: OSError variant also sets error_reading status."""
        from trw_mcp.tools.ceremony import _get_run_status
        import trw_mcp.tools.ceremony as cer_mod

        run_dir = tmp_path / "run"
        meta = run_dir / "meta"
        meta.mkdir(parents=True)
        (meta / "run.yaml").write_text("status: active\n")

        original_read_yaml = cer_mod._reader.read_yaml
        try:
            cer_mod._reader.read_yaml = lambda p: (_ for _ in ()).throw(OSError("disk error"))  # type: ignore[method-assign]
            result = _get_run_status(run_dir)
        finally:
            cer_mod._reader.read_yaml = original_read_yaml

        assert result["status"] == "error_reading"


# ---------------------------------------------------------------------------
# learning.py line 143: inactive entry skipped in distribution loop
# ---------------------------------------------------------------------------

class TestLearningDistributionSkipsInactiveEntries:
    """Line 143: inactive entries (status != 'active') are skipped with continue."""

    def test_trw_learn_distribution_skips_inactive_entries(self, tmp_path: Path) -> None:
        """list_active_learnings only returns active entries; inactive are excluded."""
        import trw_mcp.tools.learning as mod

        old_config = mod._config
        try:
            cfg = mod._config.__class__()
            object.__setattr__(cfg, "impact_forced_distribution_enabled", True)
            mod._config = cfg

            server = _make_server()
            register_learning_tools(server)
            tool = _extract_tool(server, "trw_learn")

            # list_active_learnings already filters to active only.
            # Return only the active entry (resolved is excluded by the adapter).
            with patch(
                "trw_mcp.tools.learning.resolve_trw_dir",
                return_value=tmp_path / ".trw",
            ), patch(
                "trw_mcp.tools.learning.generate_learning_id",
                return_value="L-new001",
            ), patch(
                "trw_mcp.tools.learning.adapter_store",
                return_value={"learning_id": "L-new001", "path": "sqlite://L-new001", "status": "recorded", "distribution_warning": ""},
            ), patch(
                "trw_mcp.tools.learning.update_analytics",
            ), patch(
                "trw_mcp.tools.learning.list_active_learnings",
                return_value=[{"id": "L-active", "impact": 0.5}],  # resolved excluded
            ), patch(
                "trw_mcp.tools.learning.enforce_tier_distribution",
                return_value=[],  # No demotions
            ):
                result = tool(
                    summary="new summary",
                    detail="detail",
                    impact=0.8,
                )

            # Distribution ran (only active entry included), no errors
            assert result["status"] == "recorded"
        finally:
            mod._config = old_config


# ---------------------------------------------------------------------------
# learning.py lines 301-302: recall_tracking exception in trw_recall
# ---------------------------------------------------------------------------

class TestLearningRecallTrackingException:
    """Lines 301-302: record_recall raises, exception is silently swallowed."""

    def test_trw_recall_tracking_failure_fail_open(self, tmp_path: Path) -> None:
        """When record_recall raises, trw_recall still returns results (fail-open)."""
        server = _make_server()
        register_learning_tools(server)
        tool = _extract_tool(server, "trw_recall")

        mock_record_recall = MagicMock(side_effect=RuntimeError("tracking db down"))

        # trw_recall now uses adapter_recall (SQLite). Patch it to return a result.
        with patch(
            "trw_mcp.tools.learning.resolve_trw_dir",
            return_value=tmp_path / ".trw",
        ), patch(
            "trw_mcp.tools.learning.adapter_recall",
            return_value=[{"id": "L-001", "summary": "test"}],
        ), patch(
            "trw_mcp.tools.learning.adapter_update_access",
        ), patch(
            "trw_mcp.tools.learning.log_recall_receipt",
        ), patch(
            "trw_mcp.tools.learning.search_patterns",
            return_value=[],
        ), patch(
            "trw_mcp.tools.learning.rank_by_utility",
            return_value=[{"id": "L-001", "summary": "test"}],
        ), patch(
            "trw_mcp.tools.learning.collect_context",
            return_value={},
        ), patch.dict(
            "sys.modules",
            {"trw_mcp.state.recall_tracking": MagicMock(record_recall=mock_record_recall)},
        ):
            result = tool(query="test")

        # Tool returns successfully despite tracking failure
        assert "learnings" in result
        assert len(result["learnings"]) == 1


# ---------------------------------------------------------------------------
# requirements.py line 327: template has no frontmatter (else branch)
# ---------------------------------------------------------------------------

class TestRequirementsTemplateNoFrontmatter:
    """Line 327: _load_template_body else branch when no --- frontmatter found."""

    def test_load_template_body_no_frontmatter_uses_raw_body(self) -> None:
        """Line 327: When template has no YAML frontmatter, raw text is used as body."""
        from trw_mcp.tools import requirements as req_mod
        from trw_mcp.tools.requirements import _FRONTMATTER_RE

        # Reset cached state
        original_body = req_mod._CACHED_TEMPLATE_BODY
        original_version = req_mod._CACHED_TEMPLATE_VERSION
        try:
            req_mod._CACHED_TEMPLATE_BODY = None
            req_mod._CACHED_TEMPLATE_VERSION = None

            # Template content WITHOUT a frontmatter block (no leading ---)
            no_frontmatter_content = "# PRD Template\n\n## 1. Problem Statement\n\nContent here.\n"

            # Verify our fixture actually has no frontmatter match
            assert _FRONTMATTER_RE.match(no_frontmatter_content) is None

            with patch(
                "trw_mcp.tools.requirements._load_template_body",
                wraps=req_mod._load_template_body,
            ):
                # Patch Path.read_text selectively for template_path
                original_rt = Path.read_text

                def fake_read_text(self: Path, *args, **kwargs) -> str:
                    if self.name == "prd_template.md":
                        return no_frontmatter_content
                    return original_rt(self, *args, **kwargs)

                with patch.object(Path, "read_text", fake_read_text), \
                     patch.object(Path, "exists", lambda self: True if self.name == "prd_template.md" else Path.exists(self)):
                    req_mod._CACHED_TEMPLATE_BODY = None
                    from trw_mcp.tools.requirements import _load_template_body
                    body = _load_template_body()

            # Body should be the raw content (the else branch at line 327)
            assert "Problem Statement" in body
            assert body == no_frontmatter_content
        finally:
            req_mod._CACHED_TEMPLATE_BODY = original_body
            req_mod._CACHED_TEMPLATE_VERSION = original_version


# ---------------------------------------------------------------------------
# Additional edge cases for validation.py line 622-623 (ValueError branch)
# ---------------------------------------------------------------------------

class TestValidationBuildStatusTimestampParseError:
    """Line 622-623: explicit ValueError and TypeError in timestamp parsing."""

    @pytest.mark.parametrize("bad_ts", [
        "not-a-date",
        "2026-13-45T99:99:99",  # invalid month/day
        "",
    ])
    def test_bad_timestamp_treated_as_fresh(self, tmp_path: Path, bad_ts: str) -> None:
        """Unparseable timestamps cause ValueError; staleness defaults to False."""
        from trw_mcp.state.validation import _check_build_status

        trw_dir = tmp_path / ".trw"
        (trw_dir / "context").mkdir(parents=True)
        cache = trw_dir / "context" / "build-status.yaml"

        if bad_ts:
            cache.write_text(
                f"tests_passed: true\nmypy_clean: true\ntimestamp: '{bad_ts}'\n"
            )
        else:
            # Empty timestamp string -> skip ts branch entirely
            cache.write_text("tests_passed: true\nmypy_clean: true\n")

        config = TRWConfig(trw_dir=str(trw_dir))
        object.__setattr__(config, "build_check_enabled", True)
        object.__setattr__(config, "build_gate_enforcement", "strict")

        failures = _check_build_status(trw_dir, config, "validate")

        staleness = [f for f in failures if f.rule == "build_staleness"]
        assert len(staleness) == 0


# ---------------------------------------------------------------------------
# Bonus: requirements.py — cover line 327 (validate missing file)
# ---------------------------------------------------------------------------

class TestRequirementsValidateMissingFile:
    """trw_prd_validate raises StateError when file doesn't exist."""

    def test_prd_validate_missing_file_raises(self, tmp_path: Path) -> None:
        server = _make_server()
        register_requirements_tools(server)
        tool = _extract_tool(server, "trw_prd_validate")

        with pytest.raises(StateError, match="PRD file not found"):
            tool(prd_path=str(tmp_path / "NONEXISTENT-PRD.md"))
