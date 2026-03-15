"""Coverage tests for trw_mcp/state/validation.py.

Targets uncovered branches in:
- check_phase_input (phase gate prerequisites)
- check_integration (tool registration/test scanning)
- _coerce_v1_failures (V1 failures coercion)
- derive_risk_level (explicit override path)
- get_risk_scaled_config (invalid risk level path)
- _check_prd_enforcement (enforcement branches)
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests._factories import make_run_dir_with_structure
from trw_mcp.models.config import TRWConfig
from trw_mcp.models.requirements import PRDStatus, ValidationFailure
from trw_mcp.models.run import Phase
from trw_mcp.state.persistence import FileStateWriter
from trw_mcp.state.validation import (
    _check_prd_enforcement,
    _coerce_v1_failures,
    check_integration,
    check_phase_input,
    derive_risk_level,
    get_risk_scaled_config,
)
from trw_mcp.state.validation.integration_check import check_orphan_modules

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_run_dir(tmp_path: Path, writer: FileStateWriter) -> Path:
    """Create a minimal run directory with run.yaml present."""
    return make_run_dir_with_structure(
        tmp_path,
        task="coverage-test",
        writer=writer,
        with_scratch_orchestrator=True,
    )


# ---------------------------------------------------------------------------
# check_phase_input
# ---------------------------------------------------------------------------


class TestCheckPhaseInputNoRunYaml:
    """check_phase_input returns valid=False when run.yaml is absent."""

    def test_missing_run_yaml_returns_invalid(self, tmp_path: Path) -> None:
        run_dir = tmp_path / "no_run"
        run_dir.mkdir()
        config = TRWConfig()
        result = check_phase_input(Phase.RESEARCH, run_dir, config)
        assert result.valid is False
        rules = [f.rule for f in result.failures]
        assert "run_initialized" in rules

    def test_completeness_score_is_zero_when_no_run_yaml(self, tmp_path: Path) -> None:
        run_dir = tmp_path / "no_run2"
        run_dir.mkdir()
        config = TRWConfig()
        result = check_phase_input(Phase.PLAN, run_dir, config)
        assert result.completeness_score == 0.0


class TestCheckPhaseInputResearch:
    """Research phase has no per-phase prerequisites beyond run.yaml."""

    def test_research_phase_passes_with_run_yaml(
        self,
        tmp_path: Path,
        writer: FileStateWriter,
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        config = TRWConfig()
        result = check_phase_input(Phase.RESEARCH, run_dir, config)
        # research has no additional prereqs — only errors would cause failure
        error_failures = [f for f in result.failures if f.severity == "error"]
        assert len(error_failures) == 0


class TestCheckPhaseInputPlan:
    """Plan phase requires research synthesis."""

    def test_plan_fails_without_synthesis(
        self,
        tmp_path: Path,
        writer: FileStateWriter,
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        config = TRWConfig(strict_input_criteria=True)
        result = check_phase_input(Phase.PLAN, run_dir, config)
        rules = [f.rule for f in result.failures]
        assert "research_complete" in rules

    def test_plan_passes_with_orchestrator_synthesis(
        self,
        tmp_path: Path,
        writer: FileStateWriter,
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        synthesis = run_dir / "scratch" / "_orchestrator" / "research_synthesis.md"
        synthesis.write_text("# Synthesis\nFindings here.", encoding="utf-8")
        config = TRWConfig()
        result = check_phase_input(Phase.PLAN, run_dir, config)
        rules = [f.rule for f in result.failures]
        assert "research_complete" not in rules

    def test_plan_passes_with_reports_synthesis(
        self,
        tmp_path: Path,
        writer: FileStateWriter,
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        alt = run_dir / "reports" / "research_synthesis.md"
        alt.write_text("# Alt Synthesis\nFindings.", encoding="utf-8")
        config = TRWConfig()
        result = check_phase_input(Phase.PLAN, run_dir, config)
        rules = [f.rule for f in result.failures]
        assert "research_complete" not in rules


class TestCheckPhaseInputImplement:
    """Implement phase requires plan.md and manifest.yaml."""

    def test_implement_fails_without_plan(
        self,
        tmp_path: Path,
        writer: FileStateWriter,
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        config = TRWConfig(
            strict_input_criteria=True,
            phase_gate_enforcement="off",
        )
        result = check_phase_input(Phase.IMPLEMENT, run_dir, config)
        rules = [f.rule for f in result.failures]
        assert "plan_exists" in rules

    def test_implement_fails_without_manifest(
        self,
        tmp_path: Path,
        writer: FileStateWriter,
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        # Write plan.md so that failure is from missing manifest
        plan = run_dir / "reports" / "plan.md"
        plan.write_text("# Plan\n", encoding="utf-8")
        config = TRWConfig(
            strict_input_criteria=True,
            phase_gate_enforcement="off",
        )
        result = check_phase_input(Phase.IMPLEMENT, run_dir, config)
        rules = [f.rule for f in result.failures]
        assert "manifest_exists" in rules
        assert "plan_exists" not in rules

    def test_implement_passes_with_plan_and_manifest(
        self,
        tmp_path: Path,
        writer: FileStateWriter,
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        plan = run_dir / "reports" / "plan.md"
        plan.write_text("# Plan\nContent.", encoding="utf-8")
        manifest = run_dir / "shards" / "manifest.yaml"
        writer.write_yaml(manifest, {"waves": []})
        config = TRWConfig(phase_gate_enforcement="off")
        result = check_phase_input(Phase.IMPLEMENT, run_dir, config)
        error_failures = [f for f in result.failures if f.severity == "error"]
        assert len(error_failures) == 0


class TestCheckPhaseInputValidate:
    """Validate phase requires shard outputs to exist."""

    def test_validate_fails_with_empty_shards(
        self,
        tmp_path: Path,
        writer: FileStateWriter,
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        # shards dir exists but is empty (created by _make_run_dir)
        config = TRWConfig(strict_input_criteria=True)
        result = check_phase_input(Phase.VALIDATE, run_dir, config)
        rules = [f.rule for f in result.failures]
        assert "implementation_complete" in rules

    def test_validate_fails_when_shards_dir_missing(
        self,
        tmp_path: Path,
        writer: FileStateWriter,
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        shards = run_dir / "shards"
        # Remove the empty shards dir
        shards.rmdir()
        config = TRWConfig(strict_input_criteria=True)
        result = check_phase_input(Phase.VALIDATE, run_dir, config)
        rules = [f.rule for f in result.failures]
        assert "implementation_complete" in rules

    def test_validate_passes_with_shard_files(
        self,
        tmp_path: Path,
        writer: FileStateWriter,
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        shard_file = run_dir / "shards" / "shard-01.yaml"
        writer.write_yaml(shard_file, {"id": "shard-01", "status": "complete"})
        config = TRWConfig()
        result = check_phase_input(Phase.VALIDATE, run_dir, config)
        rules = [f.rule for f in result.failures]
        assert "implementation_complete" not in rules


class TestCheckPhaseInputReview:
    """Review phase requires a validate_passed phase_check event."""

    def test_review_fails_without_validate_pass_event(
        self,
        tmp_path: Path,
        writer: FileStateWriter,
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        meta = run_dir / "meta"
        # Write events without a validate pass
        writer.append_jsonl(
            meta / "events.jsonl",
            {
                "event": "run_init",
                "ts": "2026-01-01T00:00:00Z",
            },
        )
        config = TRWConfig(strict_input_criteria=True)
        result = check_phase_input(Phase.REVIEW, run_dir, config)
        rules = [f.rule for f in result.failures]
        assert "validate_passed" in rules

    def test_review_passes_with_validate_pass_event(
        self,
        tmp_path: Path,
        writer: FileStateWriter,
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        meta = run_dir / "meta"
        writer.append_jsonl(
            meta / "events.jsonl",
            {
                "event": "phase_check",
                "data": {"phase": "validate", "valid": True},
            },
        )
        config = TRWConfig()
        result = check_phase_input(Phase.REVIEW, run_dir, config)
        rules = [f.rule for f in result.failures]
        assert "validate_passed" not in rules

    def test_review_no_failure_when_events_empty(
        self,
        tmp_path: Path,
        writer: FileStateWriter,
    ) -> None:
        """When events.jsonl does not exist, review does not fail on validate_passed."""
        run_dir = _make_run_dir(tmp_path, writer)
        # No events.jsonl written (meta dir exists, file absent)
        config = TRWConfig()
        result = check_phase_input(Phase.REVIEW, run_dir, config)
        rules = [f.rule for f in result.failures]
        # Empty events => no validate_passed failure (the code only checks if events exist)
        assert "validate_passed" not in rules


class TestCheckPhaseInputDeliver:
    """Deliver phase requires reflection event in events.jsonl."""

    def test_deliver_fails_without_events(
        self,
        tmp_path: Path,
        writer: FileStateWriter,
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        # No events.jsonl written
        config = TRWConfig(strict_input_criteria=True)
        result = check_phase_input(Phase.DELIVER, run_dir, config)
        rules = [f.rule for f in result.failures]
        assert "events_exist" in rules

    def test_deliver_fails_without_reflection_event(
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
        config = TRWConfig(strict_input_criteria=True)
        result = check_phase_input(Phase.DELIVER, run_dir, config)
        rules = [f.rule for f in result.failures]
        assert "reflection_complete" in rules

    def test_deliver_passes_with_reflection_complete_event(
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
                "ts": "2026-01-01T12:00:00Z",
            },
        )
        config = TRWConfig()
        result = check_phase_input(Phase.DELIVER, run_dir, config)
        rules = [f.rule for f in result.failures]
        assert "reflection_complete" not in rules
        assert "events_exist" not in rules

    def test_deliver_passes_with_trw_reflect_complete_event(
        self,
        tmp_path: Path,
        writer: FileStateWriter,
    ) -> None:
        """The 'trw_reflect_complete' alias also satisfies the reflection check."""
        run_dir = _make_run_dir(tmp_path, writer)
        meta = run_dir / "meta"
        writer.append_jsonl(
            meta / "events.jsonl",
            {
                "event": "trw_reflect_complete",
                "ts": "2026-01-01T12:00:00Z",
            },
        )
        config = TRWConfig()
        result = check_phase_input(Phase.DELIVER, run_dir, config)
        rules = [f.rule for f in result.failures]
        assert "reflection_complete" not in rules

    def test_deliver_severity_warning_in_lenient_mode(
        self,
        tmp_path: Path,
        writer: FileStateWriter,
    ) -> None:
        """In lenient mode missing reflection is a warning, not an error."""
        run_dir = _make_run_dir(tmp_path, writer)
        meta = run_dir / "meta"
        writer.append_jsonl(
            meta / "events.jsonl",
            {
                "event": "run_init",
                "ts": "2026-01-01T00:00:00Z",
            },
        )
        # strict_input_criteria=False (default) → warnings, not errors
        config = TRWConfig(strict_input_criteria=False)
        result = check_phase_input(Phase.DELIVER, run_dir, config)
        reflection_failures = [f for f in result.failures if f.rule == "reflection_complete"]
        assert len(reflection_failures) == 1
        assert reflection_failures[0].severity == "warning"
        # Should still be valid because only warnings
        assert result.valid is True


# ---------------------------------------------------------------------------
# check_integration
# ---------------------------------------------------------------------------


class TestCheckIntegrationEmptyToolsDir:
    """check_integration handles empty or absent tools directory."""

    def test_empty_tools_dir_returns_empty_lists(self, tmp_path: Path) -> None:
        src_dir = tmp_path / "src" / "mypackage"
        (src_dir / "tools").mkdir(parents=True)
        (src_dir / "server.py").write_text("# empty server\n", encoding="utf-8")
        result = check_integration(src_dir)
        assert result["unregistered"] == []
        assert result["all_registered"] is True
        assert result["tool_modules_scanned"] == 0

    def test_absent_tools_dir_returns_empty(self, tmp_path: Path) -> None:
        src_dir = tmp_path / "src" / "mypackage"
        src_dir.mkdir(parents=True)
        (src_dir / "server.py").write_text("# empty\n", encoding="utf-8")
        result = check_integration(src_dir)
        assert result["unregistered"] == []


class TestCheckIntegrationUnregisteredModule:
    """Module with register_*_tools not wired into server.py is unregistered."""

    def test_unregistered_tool_detected(self, tmp_path: Path) -> None:
        src_dir = tmp_path / "src" / "pkg"
        tools_dir = src_dir / "tools"
        tools_dir.mkdir(parents=True)
        # tests_dir = source_dir.parent.parent / "tests" = tmp_path / "tests"
        (tmp_path / "tests").mkdir(parents=True)

        # Tool module with register function
        (tools_dir / "foo.py").write_text(
            "def register_foo_tools(server):\n    pass\n",
            encoding="utf-8",
        )
        # server.py that does NOT import foo
        (src_dir / "server.py").write_text(
            "# no imports for foo\n",
            encoding="utf-8",
        )

        result = check_integration(src_dir)
        unreg = result["unregistered"]
        assert isinstance(unreg, list)
        assert "foo" in unreg
        assert result["all_registered"] is False

    def test_registered_tool_not_in_unregistered(self, tmp_path: Path) -> None:
        src_dir = tmp_path / "src" / "pkg"
        tools_dir = src_dir / "tools"
        tools_dir.mkdir(parents=True)
        (tmp_path / "tests").mkdir(parents=True)

        (tools_dir / "bar.py").write_text(
            "def register_bar_tools(server):\n    pass\n",
            encoding="utf-8",
        )
        # server.py imports and calls bar registration
        (src_dir / "server.py").write_text(
            "from pkg.tools.bar import register_bar_tools\nregister_bar_tools(server)\n",
            encoding="utf-8",
        )

        result = check_integration(src_dir)
        unreg = result["unregistered"]
        assert isinstance(unreg, list)
        assert "bar" not in unreg
        assert result["all_registered"] is True


class TestCheckIntegrationMissingTests:
    """Modules without corresponding test files appear in missing_tests."""

    def test_missing_test_file_detected(self, tmp_path: Path) -> None:
        # check_integration resolves tests_dir as source_dir.parent.parent / "tests"
        src_dir = tmp_path / "src" / "pkg"
        tools_dir = src_dir / "tools"
        tools_dir.mkdir(parents=True)
        (src_dir / "server.py").write_text("", encoding="utf-8")
        # tests dir exists but has no test file for baz
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir(parents=True)

        (tools_dir / "baz.py").write_text(
            "def register_baz_tools(server):\n    pass\n",
            encoding="utf-8",
        )

        result = check_integration(src_dir)
        missing = result["missing_tests"]
        assert isinstance(missing, list)
        assert "test_tools_baz.py" in missing

    def test_present_test_file_not_in_missing(self, tmp_path: Path) -> None:
        # check_integration resolves tests_dir as source_dir.parent.parent / "tests"
        # With src_dir = tmp_path / "src" / "pkg", tests_dir = tmp_path / "tests"
        src_dir = tmp_path / "src" / "pkg"
        tools_dir = src_dir / "tools"
        tools_dir.mkdir(parents=True)
        (src_dir / "server.py").write_text("", encoding="utf-8")
        # Correct path: source_dir.parent.parent / "tests"
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir(parents=True)

        (tools_dir / "qux.py").write_text(
            "def register_qux_tools(server):\n    pass\n",
            encoding="utf-8",
        )
        # Create the expected test file at the resolved location
        (tests_dir / "test_tools_qux.py").write_text(
            "# tests\n",
            encoding="utf-8",
        )

        result = check_integration(src_dir)
        missing = result["missing_tests"]
        assert isinstance(missing, list)
        assert "test_tools_qux.py" not in missing

    def test_conventions_key_present(self, tmp_path: Path) -> None:
        src_dir = tmp_path / "src" / "pkg"
        (src_dir / "tools").mkdir(parents=True)
        (src_dir / "server.py").write_text("", encoding="utf-8")
        result = check_integration(src_dir)
        assert "conventions" in result
        conventions = result["conventions"]
        assert isinstance(conventions, dict)
        assert "tool_pattern" in conventions


class TestCheckIntegrationAllRegistered:
    """When all tool modules are registered, all_registered is True."""

    def test_all_registered_true_when_no_tools(self, tmp_path: Path) -> None:
        src_dir = tmp_path / "src" / "pkg"
        (src_dir / "tools").mkdir(parents=True)
        (src_dir / "server.py").write_text("", encoding="utf-8")
        result = check_integration(src_dir)
        assert result["all_registered"] is True

    def test_private_modules_skipped(self, tmp_path: Path) -> None:
        """Private modules (starting with _) are not scanned."""
        src_dir = tmp_path / "src" / "pkg"
        tools_dir = src_dir / "tools"
        tools_dir.mkdir(parents=True)
        (src_dir / "server.py").write_text("", encoding="utf-8")
        # Private module — should be ignored
        (tools_dir / "_private.py").write_text(
            "def register_private_tools(server):\n    pass\n",
            encoding="utf-8",
        )
        result = check_integration(src_dir)
        unreg = result["unregistered"]
        assert isinstance(unreg, list)
        assert "_private" not in unreg
        assert result["tool_modules_scanned"] == 0


# ---------------------------------------------------------------------------
# check_orphan_modules
# ---------------------------------------------------------------------------


class TestCheckOrphanModulesNoOrphans:
    """Modules imported by at least one other file are not orphans."""

    def test_imported_module_not_orphan(self, tmp_path: Path) -> None:
        src_dir = tmp_path / "src" / "pkg"
        state_dir = src_dir / "state"
        state_dir.mkdir(parents=True)
        # __init__.py imports foo — so foo is reachable
        (src_dir / "__init__.py").write_text("", encoding="utf-8")
        (src_dir / "server.py").write_text(
            "from pkg.state.foo import helper\n",
            encoding="utf-8",
        )
        (state_dir / "__init__.py").write_text("", encoding="utf-8")
        (state_dir / "foo.py").write_text(
            "def helper(): pass\n",
            encoding="utf-8",
        )
        result = check_orphan_modules(src_dir)
        assert result["all_reachable"] is True
        assert result["orphans"] == []
        assert result["modules_scanned"] >= 1

    def test_relative_import_counts(self, tmp_path: Path) -> None:
        src_dir = tmp_path / "src" / "pkg"
        state_dir = src_dir / "state"
        state_dir.mkdir(parents=True)
        (src_dir / "__init__.py").write_text("", encoding="utf-8")
        (state_dir / "__init__.py").write_text(
            "from .bar import something\n",
            encoding="utf-8",
        )
        (state_dir / "bar.py").write_text(
            "something = 1\n",
            encoding="utf-8",
        )
        result = check_orphan_modules(src_dir)
        assert "state/bar.py" not in result["orphans"]


class TestCheckOrphanModulesDetectsOrphans:
    """Modules not imported by any other source file are orphans."""

    def test_orphan_detected(self, tmp_path: Path) -> None:
        src_dir = tmp_path / "src" / "pkg"
        state_dir = src_dir / "state"
        state_dir.mkdir(parents=True)
        (src_dir / "__init__.py").write_text("", encoding="utf-8")
        (src_dir / "server.py").write_text("# no imports\n", encoding="utf-8")
        (state_dir / "__init__.py").write_text("", encoding="utf-8")
        # This module is never imported by anything
        (state_dir / "dead_module.py").write_text(
            "def unreachable(): pass\n",
            encoding="utf-8",
        )
        result = check_orphan_modules(src_dir)
        orphans = result["orphans"]
        assert isinstance(orphans, list)
        assert "state/dead_module.py" in orphans
        assert result["all_reachable"] is False

    def test_multiple_orphans_detected(self, tmp_path: Path) -> None:
        src_dir = tmp_path / "src" / "pkg"
        state_dir = src_dir / "state"
        models_dir = src_dir / "models"
        state_dir.mkdir(parents=True)
        models_dir.mkdir(parents=True)
        (src_dir / "__init__.py").write_text("", encoding="utf-8")
        (src_dir / "server.py").write_text("# no imports\n", encoding="utf-8")
        (state_dir / "__init__.py").write_text("", encoding="utf-8")
        (state_dir / "orphan_a.py").write_text("x = 1\n", encoding="utf-8")
        (models_dir / "__init__.py").write_text("", encoding="utf-8")
        (models_dir / "orphan_b.py").write_text("y = 2\n", encoding="utf-8")
        result = check_orphan_modules(src_dir)
        orphans = result["orphans"]
        assert "state/orphan_a.py" in orphans
        assert "models/orphan_b.py" in orphans


class TestCheckOrphanModulesExclusions:
    """__init__.py and entry points are excluded from orphan scanning."""

    def test_init_py_excluded(self, tmp_path: Path) -> None:
        src_dir = tmp_path / "src" / "pkg"
        state_dir = src_dir / "state"
        state_dir.mkdir(parents=True)
        (src_dir / "__init__.py").write_text("", encoding="utf-8")
        (src_dir / "server.py").write_text("", encoding="utf-8")
        (state_dir / "__init__.py").write_text("", encoding="utf-8")
        # Only __init__.py in state — no modules to scan
        result = check_orphan_modules(src_dir)
        assert result["modules_scanned"] == 0

    def test_entry_points_excluded(self, tmp_path: Path) -> None:
        src_dir = tmp_path / "src" / "pkg"
        src_dir.mkdir(parents=True)
        (src_dir / "__init__.py").write_text("", encoding="utf-8")
        # server.py is an entry point — not scanned for orphan status
        (src_dir / "server.py").write_text("", encoding="utf-8")
        (src_dir / "__main__.py").write_text("", encoding="utf-8")
        result = check_orphan_modules(src_dir)
        # Neither should appear in orphans
        orphans = result.get("orphans", [])
        assert isinstance(orphans, list)
        assert "server.py" not in orphans
        assert "__main__.py" not in orphans

    def test_package_import_from_dot_counts(self, tmp_path: Path) -> None:
        """'from . import module_name' style imports count as wired."""
        src_dir = tmp_path / "src" / "pkg"
        state_dir = src_dir / "state"
        state_dir.mkdir(parents=True)
        (src_dir / "__init__.py").write_text("", encoding="utf-8")
        (state_dir / "__init__.py").write_text(
            "from . import baz\n",
            encoding="utf-8",
        )
        (state_dir / "baz.py").write_text("z = 1\n", encoding="utf-8")
        result = check_orphan_modules(src_dir)
        assert "state/baz.py" not in result["orphans"]


# ---------------------------------------------------------------------------
# _coerce_v1_failures
# ---------------------------------------------------------------------------


class TestCoerceV1Failures:
    """_coerce_v1_failures converts raw input to ValidationFailure list."""

    def test_not_a_list_returns_empty(self) -> None:
        assert _coerce_v1_failures(None) == []
        assert _coerce_v1_failures("string") == []
        assert _coerce_v1_failures(42) == []
        assert _coerce_v1_failures({}) == []

    def test_list_of_validation_failures_passthrough(self) -> None:
        vf = ValidationFailure(
            field="test",
            rule="test_rule",
            message="msg",
            severity="warning",
        )
        result = _coerce_v1_failures([vf])
        assert len(result) == 1
        assert result[0] is vf

    def test_list_of_dicts_converted(self) -> None:
        raw: list[object] = [
            {
                "field": "some_field",
                "rule": "some_rule",
                "message": "a message",
                "severity": "error",
            }
        ]
        result = _coerce_v1_failures(raw)
        assert len(result) == 1
        assert result[0].field == "some_field"
        assert result[0].rule == "some_rule"
        assert result[0].message == "a message"
        assert result[0].severity == "error"

    def test_mixed_list_handles_both_types(self) -> None:
        vf = ValidationFailure(
            field="f1",
            rule="r1",
            message="m1",
            severity="warning",
        )
        raw_dict: dict[str, object] = {
            "field": "f2",
            "rule": "r2",
            "message": "m2",
            "severity": "info",
        }
        result = _coerce_v1_failures([vf, raw_dict])
        assert len(result) == 2
        assert result[0] is vf
        assert result[1].field == "f2"

    def test_dict_with_missing_keys_uses_defaults(self) -> None:
        raw: list[object] = [{}]
        result = _coerce_v1_failures(raw)
        assert len(result) == 1
        assert result[0].field == ""
        assert result[0].rule == ""
        assert result[0].severity == "warning"

    def test_empty_list_returns_empty(self) -> None:
        assert _coerce_v1_failures([]) == []


# ---------------------------------------------------------------------------
# derive_risk_level
# ---------------------------------------------------------------------------


class TestDeriveRiskLevel:
    """derive_risk_level returns explicit_risk when it overrides priority."""

    def test_explicit_risk_critical_overrides_priority(self) -> None:
        result = derive_risk_level("P3", explicit_risk="critical")
        assert result == "critical"

    def test_explicit_risk_low_overrides_p0(self) -> None:
        result = derive_risk_level("P0", explicit_risk="low")
        assert result == "low"

    def test_invalid_explicit_risk_falls_back_to_priority(self) -> None:
        # "unknown_risk" is not in RISK_PROFILES → falls back to priority
        result = derive_risk_level("P0", explicit_risk="unknown_risk")
        assert result == "critical"

    def test_none_explicit_risk_uses_priority(self) -> None:
        assert derive_risk_level("P0") == "critical"
        assert derive_risk_level("P1") == "high"
        assert derive_risk_level("P2") == "medium"
        assert derive_risk_level("P3") == "low"

    def test_unknown_priority_defaults_to_medium(self) -> None:
        result = derive_risk_level("P99")
        assert result == "medium"


# ---------------------------------------------------------------------------
# get_risk_scaled_config
# ---------------------------------------------------------------------------


class TestGetRiskScaledConfig:
    """get_risk_scaled_config returns original config for invalid risk levels."""

    def test_invalid_risk_level_returns_original_config(self) -> None:
        config = TRWConfig()
        result = get_risk_scaled_config(config, "invalid_level")
        assert result is config

    def test_medium_risk_returns_original_config(self) -> None:
        config = TRWConfig()
        result = get_risk_scaled_config(config, "medium")
        assert result is config

    def test_risk_scaling_disabled_returns_original(self) -> None:
        config = TRWConfig(risk_scaling_enabled=False)
        result = get_risk_scaled_config(config, "critical")
        assert result is config

    def test_critical_risk_scales_thresholds(self) -> None:
        config = TRWConfig(risk_scaling_enabled=True)
        result = get_risk_scaled_config(config, "critical")
        # Result should be a different object with adjusted thresholds
        assert result is not config
        assert result.validation_review_threshold == 92.0

    def test_high_risk_scales_thresholds(self) -> None:
        config = TRWConfig(risk_scaling_enabled=True)
        result = get_risk_scaled_config(config, "high")
        assert result is not config
        assert result.validation_review_threshold == 88.0

    def test_low_risk_scales_thresholds(self) -> None:
        config = TRWConfig(risk_scaling_enabled=True)
        result = get_risk_scaled_config(config, "low")
        assert result is not config
        assert result.validation_review_threshold == 75.0


# ---------------------------------------------------------------------------
# _check_prd_enforcement
# ---------------------------------------------------------------------------


class TestCheckPrdEnforcementOff:
    """_check_prd_enforcement returns empty list when enforcement is off."""

    def test_enforcement_off_returns_empty(
        self,
        tmp_path: Path,
        writer: FileStateWriter,
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        config = TRWConfig(phase_gate_enforcement="off")
        result = _check_prd_enforcement(
            run_dir,
            config,
            PRDStatus.APPROVED,
            "implement",
        )
        assert result == []


class TestCheckPrdEnforcementResearchRunType:
    """Research run types skip PRD enforcement."""

    def test_research_run_type_returns_empty(
        self,
        tmp_path: Path,
        writer: FileStateWriter,
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        # Overwrite run.yaml with run_type=research
        writer.write_yaml(
            run_dir / "meta" / "run.yaml",
            {
                "run_id": "20260101T000000Z-test1234",
                "task": "coverage-test",
                "framework": "v24.0_TRW",
                "status": "active",
                "phase": "research",
                "confidence": "medium",
                "run_type": "research",
            },
        )
        config = TRWConfig(phase_gate_enforcement="strict")
        result = _check_prd_enforcement(
            run_dir,
            config,
            PRDStatus.APPROVED,
            "implement",
        )
        assert result == []


class TestCheckPrdEnforcementNoPrds:
    """_check_prd_enforcement returns advisory warning when no PRDs found."""

    def test_no_prds_returns_advisory_warning(
        self,
        tmp_path: Path,
        writer: FileStateWriter,
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        # No prd_scope in run.yaml, no plan.md → empty discovery
        config = TRWConfig(phase_gate_enforcement="lenient")
        result = _check_prd_enforcement(
            run_dir,
            config,
            PRDStatus.APPROVED,
            "implement",
        )
        assert len(result) == 1
        assert result[0].rule == "prd_discovery"
        assert result[0].severity == "warning"


class TestCheckPrdEnforcementPrdFileNotFound:
    """_check_prd_enforcement fails when PRD file is missing."""

    def test_prd_file_not_found_returns_error(
        self,
        tmp_path: Path,
        writer: FileStateWriter,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        # Add prd_scope referencing a non-existent file
        writer.write_yaml(
            run_dir / "meta" / "run.yaml",
            {
                "run_id": "20260101T000000Z-test1234",
                "task": "coverage-test",
                "status": "active",
                "phase": "research",
                "prd_scope": ["PRD-FAKE-001"],
            },
        )
        # Point the project root to tmp_path so the prds dir resolves there
        monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
        # Create the prds dir but not the file
        prds_dir = tmp_path / "docs" / "requirements-aare-f" / "prds"
        prds_dir.mkdir(parents=True)

        config = TRWConfig(phase_gate_enforcement="strict")
        result = _check_prd_enforcement(
            run_dir,
            config,
            PRDStatus.APPROVED,
            "implement",
        )
        rules = [f.rule for f in result]
        assert "prd_exists" in rules


class TestCheckPrdEnforcementPrdStatusTooLow:
    """_check_prd_enforcement fails when PRD status is below required."""

    def test_draft_prd_fails_approved_requirement(
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
                "status": "active",
                "phase": "implement",
                "prd_scope": ["PRD-TEST-001"],
            },
        )
        monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
        prds_dir = tmp_path / "docs" / "requirements-aare-f" / "prds"
        prds_dir.mkdir(parents=True)
        # Write a draft PRD
        prd_content = """\
---
prd:
  id: PRD-TEST-001
  title: Test PRD
  version: "1.0"
  status: draft
  priority: P1
---

# PRD-TEST-001
"""
        (prds_dir / "PRD-TEST-001.md").write_text(prd_content, encoding="utf-8")

        config = TRWConfig(phase_gate_enforcement="strict")
        result = _check_prd_enforcement(
            run_dir,
            config,
            PRDStatus.APPROVED,
            "implement",
        )
        rules = [f.rule for f in result]
        assert "prd_status" in rules

    def test_approved_prd_passes_approved_requirement(
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
                "status": "active",
                "phase": "implement",
                "prd_scope": ["PRD-TEST-002"],
            },
        )
        monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
        prds_dir = tmp_path / "docs" / "requirements-aare-f" / "prds"
        prds_dir.mkdir(parents=True)
        prd_content = """\
---
prd:
  id: PRD-TEST-002
  title: Approved PRD
  version: "1.0"
  status: approved
  priority: P1
---

# PRD-TEST-002
"""
        (prds_dir / "PRD-TEST-002.md").write_text(prd_content, encoding="utf-8")

        config = TRWConfig(phase_gate_enforcement="strict")
        result = _check_prd_enforcement(
            run_dir,
            config,
            PRDStatus.APPROVED,
            "implement",
        )
        rules = [f.rule for f in result]
        assert "prd_status" not in rules
        assert "prd_exists" not in rules


class TestCheckPhaseInputWithPrdScope:
    """check_phase_input with prd_scope wires through _check_prd_enforcement."""

    def test_implement_with_prd_scope_no_prds_dir(
        self,
        tmp_path: Path,
        writer: FileStateWriter,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Implement phase with prd_scope but missing PRD file gets prd_exists failure."""
        run_dir = _make_run_dir(tmp_path, writer)
        # Write plan and manifest so those checks pass
        (run_dir / "reports" / "plan.md").write_text("# Plan\n", encoding="utf-8")
        writer.write_yaml(run_dir / "shards" / "manifest.yaml", {"waves": []})
        # Set prd_scope
        writer.write_yaml(
            run_dir / "meta" / "run.yaml",
            {
                "run_id": "20260101T000000Z-test1234",
                "task": "coverage-test",
                "status": "active",
                "phase": "implement",
                "prd_scope": ["PRD-MISSING-001"],
            },
        )
        monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
        prds_dir = tmp_path / "docs" / "requirements-aare-f" / "prds"
        prds_dir.mkdir(parents=True)
        # Do not create the PRD file

        config = TRWConfig(phase_gate_enforcement="lenient")
        result = check_phase_input(Phase.IMPLEMENT, run_dir, config)
        rules = [f.rule for f in result.failures]
        assert "prd_exists" in rules


# ---------------------------------------------------------------------------
# check_phase_exit — EXIT criteria checks (phase_gates.py)
# ---------------------------------------------------------------------------

from trw_mcp.state.validation import (
    _build_phase_result,
    check_phase_exit,
)


class TestCheckPhaseExitResearch:
    """Research exit criteria: research synthesis must exist."""

    def test_research_exit_warns_when_no_synthesis(
        self,
        tmp_path: Path,
        writer: FileStateWriter,
    ) -> None:
        """Missing synthesis at both locations produces a warning (not error)."""
        run_dir = _make_run_dir(tmp_path, writer)
        config = TRWConfig()
        result = check_phase_exit(Phase.RESEARCH, run_dir, config)
        rules = [f.rule for f in result.failures]
        assert "synthesis_exists" in rules
        synth_f = [f for f in result.failures if f.rule == "synthesis_exists"]
        assert synth_f[0].severity == "warning"
        # Warnings alone do not make the result invalid
        assert result.valid is True

    def test_research_exit_passes_with_primary_synthesis(
        self,
        tmp_path: Path,
        writer: FileStateWriter,
    ) -> None:
        """Synthesis in scratch/_orchestrator/ satisfies exit criteria."""
        run_dir = _make_run_dir(tmp_path, writer)
        synthesis = run_dir / "scratch" / "_orchestrator" / "research_synthesis.md"
        synthesis.parent.mkdir(parents=True, exist_ok=True)
        synthesis.write_text("# Synthesis\nDone.", encoding="utf-8")
        config = TRWConfig()
        result = check_phase_exit(Phase.RESEARCH, run_dir, config)
        rules = [f.rule for f in result.failures]
        assert "synthesis_exists" not in rules

    def test_research_exit_passes_with_alt_synthesis(
        self,
        tmp_path: Path,
        writer: FileStateWriter,
    ) -> None:
        """Synthesis in reports/ also satisfies exit criteria."""
        run_dir = _make_run_dir(tmp_path, writer)
        alt = run_dir / "reports" / "research_synthesis.md"
        alt.parent.mkdir(parents=True, exist_ok=True)
        alt.write_text("# Alt Synthesis", encoding="utf-8")
        config = TRWConfig()
        result = check_phase_exit(Phase.RESEARCH, run_dir, config)
        rules = [f.rule for f in result.failures]
        assert "synthesis_exists" not in rules


class TestCheckPhaseExitPlan:
    """Plan exit criteria: plan.md must exist, PRD enforcement checked."""

    def test_plan_exit_fails_without_plan_md(
        self,
        tmp_path: Path,
        writer: FileStateWriter,
    ) -> None:
        """Missing plan.md is an error-severity failure."""
        run_dir = _make_run_dir(tmp_path, writer)
        config = TRWConfig(phase_gate_enforcement="off")
        result = check_phase_exit(Phase.PLAN, run_dir, config)
        plan_failures = [f for f in result.failures if f.rule == "plan_exists"]
        assert len(plan_failures) == 1
        assert plan_failures[0].severity == "error"
        # Error-severity failure makes result invalid
        assert result.valid is False

    def test_plan_exit_passes_with_plan_md(
        self,
        tmp_path: Path,
        writer: FileStateWriter,
    ) -> None:
        """plan.md present satisfies the plan existence check."""
        run_dir = _make_run_dir(tmp_path, writer)
        plan = run_dir / "reports" / "plan.md"
        plan.parent.mkdir(parents=True, exist_ok=True)
        plan.write_text("# Plan\n", encoding="utf-8")
        config = TRWConfig(phase_gate_enforcement="off")
        result = check_phase_exit(Phase.PLAN, run_dir, config)
        rules = [f.rule for f in result.failures]
        assert "plan_exists" not in rules


class TestCheckPhaseExitImplement:
    """Implement exit criteria: manifest presence, PRD enforcement, build check."""

    def test_implement_exit_warns_when_shards_exist_without_manifest(
        self,
        tmp_path: Path,
        writer: FileStateWriter,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Shards dir without manifest.yaml produces a warning."""
        run_dir = _make_run_dir(tmp_path, writer)
        shards = run_dir / "shards"
        shards.mkdir(parents=True, exist_ok=True)
        # No manifest.yaml inside shards
        # Disable build and PRD checks to isolate this behavior
        config = TRWConfig(
            phase_gate_enforcement="off",
            build_check_enabled=False,
        )
        monkeypatch.setattr(
            "trw_mcp.state.validation.phase_gates._best_effort_build_check",
            lambda *a, **kw: None,
        )
        result = check_phase_exit(Phase.IMPLEMENT, run_dir, config)
        manifest_f = [f for f in result.failures if f.rule == "manifest_exists"]
        assert len(manifest_f) == 1
        assert manifest_f[0].severity == "warning"

    def test_implement_exit_no_manifest_warning_when_no_shards_dir(
        self,
        tmp_path: Path,
        writer: FileStateWriter,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When shards dir doesn't exist, no manifest_exists warning is emitted."""
        run_dir = _make_run_dir(tmp_path, writer)
        # Ensure shards dir does not exist
        shards = run_dir / "shards"
        if shards.exists():
            shards.rmdir()
        config = TRWConfig(
            phase_gate_enforcement="off",
            build_check_enabled=False,
        )
        monkeypatch.setattr(
            "trw_mcp.state.validation.phase_gates._best_effort_build_check",
            lambda *a, **kw: None,
        )
        result = check_phase_exit(Phase.IMPLEMENT, run_dir, config)
        rules = [f.rule for f in result.failures]
        assert "manifest_exists" not in rules

    def test_implement_exit_invalid_prd_status_config_falls_back_to_approved(
        self,
        tmp_path: Path,
        writer: FileStateWriter,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Invalid prd_required_status_for_implement falls back to APPROVED."""
        run_dir = _make_run_dir(tmp_path, writer)
        # Set up a PRD at 'review' status -- below 'approved'
        writer.write_yaml(
            run_dir / "meta" / "run.yaml",
            {
                "run_id": "20260101T000000Z-test1234",
                "task": "coverage-test",
                "status": "active",
                "phase": "implement",
                "prd_scope": ["PRD-TEST-099"],
            },
        )
        monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
        prds_dir = tmp_path / "docs" / "requirements-aare-f" / "prds"
        prds_dir.mkdir(parents=True)
        (prds_dir / "PRD-TEST-099.md").write_text(
            "---\nprd:\n  id: PRD-TEST-099\n  title: Test\n  version: '1.0'\n"
            "  status: review\n  priority: P1\n---\n# PRD-TEST-099\n",
            encoding="utf-8",
        )
        config = TRWConfig(
            phase_gate_enforcement="strict",
            prd_required_status_for_implement="INVALID_STATUS",
            build_check_enabled=False,
        )
        monkeypatch.setattr(
            "trw_mcp.state.validation.phase_gates._best_effort_build_check",
            lambda *a, **kw: None,
        )
        result = check_phase_exit(Phase.IMPLEMENT, run_dir, config)
        # Fallback to APPROVED means 'review' < 'approved' => prd_status failure
        rules = [f.rule for f in result.failures]
        assert "prd_status" in rules


class TestCheckPhaseExitValidate:
    """Validate exit criteria: advisory test info, integration/orphan/build checks."""

    def test_validate_exit_always_includes_test_advisory(
        self,
        tmp_path: Path,
        writer: FileStateWriter,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Validate exit always includes the phase_test_advisory info message."""
        run_dir = _make_run_dir(tmp_path, writer)
        # Stub out all best-effort checks to isolate
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
        monkeypatch.setattr(
            "trw_mcp.state.validation.phase_gates._best_effort_dry_check",
            lambda *a, **kw: None,
        )
        monkeypatch.setattr(
            "trw_mcp.state.validation.phase_gates._best_effort_migration_check",
            lambda *a, **kw: None,
        )
        monkeypatch.setattr(
            "trw_mcp.state.validation.phase_gates._best_effort_semantic_check",
            lambda *a, **kw: None,
        )
        config = TRWConfig()
        result = check_phase_exit(Phase.VALIDATE, run_dir, config)
        advisory = [f for f in result.failures if f.rule == "phase_test_advisory"]
        assert len(advisory) == 1
        assert advisory[0].severity == "info"
        # Info-only failures do not make result invalid
        assert result.valid is True


class TestCheckPhaseExitReview:
    """Review exit criteria: final report, reflection event, quality checks."""

    def test_review_exit_warns_when_no_final_report(
        self,
        tmp_path: Path,
        writer: FileStateWriter,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Missing final.md produces a warning."""
        run_dir = _make_run_dir(tmp_path, writer)
        # Write events with reflection so that branch doesn't confound
        meta = run_dir / "meta"
        writer.append_jsonl(
            meta / "events.jsonl",
            {
                "event": "reflection_complete",
                "ts": "2026-01-01T12:00:00Z",
            },
        )
        # Stub out the lazy imports used inside _check_review_exit
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
        """No events.jsonl at all produces reflection_required with 'unknown' message."""
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
        """Events exist but no reflection event produces reflection_required warning."""
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
        """Reflection event present means no reflection_required failure."""
        run_dir = _make_run_dir(tmp_path, writer)
        meta = run_dir / "meta"
        writer.append_jsonl(
            meta / "events.jsonl",
            {
                "event": "reflection_complete",
                "ts": "2026-01-01T12:00:00Z",
            },
        )
        # Stub the lazy imports used inside _check_review_exit
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
        """Run status != 'complete' produces a warning."""
        run_dir = _make_run_dir(tmp_path, writer)
        # run.yaml has status="active" by default from _make_run_dir
        # Stub best-effort checks
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
        """Run status == 'complete' passes the status check."""
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
        """Deliver exit always includes the phase_test_advisory info message."""
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
        """Events exist but no claude_md_sync event produces sync_required warning."""
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
        assert "trw_claude_md_sync()" in sync_f[0].message

    def test_deliver_exit_no_sync_warning_with_sync_event(
        self,
        tmp_path: Path,
        writer: FileStateWriter,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """claude_md_sync event satisfies the sync check."""
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
        """When events.jsonl doesn't exist, sync_required is NOT emitted.

        The code only checks sync when events list is truthy.
        """
        run_dir = _make_run_dir(tmp_path, writer)
        # No events.jsonl
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


# ---------------------------------------------------------------------------
# _build_phase_result — boundary conditions
# ---------------------------------------------------------------------------


class TestBuildPhaseResult:
    """Boundary conditions for _build_phase_result."""

    def test_no_failures_yields_valid_and_completeness_one(self) -> None:
        """Zero failures means valid=True and completeness_score=1.0."""
        result = _build_phase_result(
            failures=[],
            criteria=["crit1", "crit2", "crit3"],
            phase_name="test",
            log_event="test_event",
        )
        assert result.valid is True
        assert result.completeness_score == 1.0
        assert result.failures == []

    def test_warnings_only_still_valid(self) -> None:
        """Warnings do not set valid=False; only errors do."""
        warning = ValidationFailure(
            field="f",
            rule="r",
            message="m",
            severity="warning",
        )
        result = _build_phase_result(
            failures=[warning],
            criteria=["crit1", "crit2"],
            phase_name="test",
            log_event="test_event",
        )
        assert result.valid is True
        assert result.completeness_score == 0.5

    def test_error_makes_result_invalid(self) -> None:
        """A single error-severity failure makes valid=False."""
        error = ValidationFailure(
            field="f",
            rule="r",
            message="m",
            severity="error",
        )
        result = _build_phase_result(
            failures=[error],
            criteria=["crit1"],
            phase_name="test",
            log_event="test_event",
        )
        assert result.valid is False

    def test_more_failures_than_criteria_clamps_score_at_zero(self) -> None:
        """Completeness score never goes below 0.0."""
        failures = [ValidationFailure(field="f", rule="r", message="m", severity="warning") for _ in range(5)]
        result = _build_phase_result(
            failures=failures,
            criteria=["crit1"],
            phase_name="test",
            log_event="test_event",
        )
        assert result.completeness_score == 0.0

    def test_empty_criteria_does_not_divide_by_zero(self) -> None:
        """Empty criteria list uses max(len, 1) to avoid division by zero."""
        warning = ValidationFailure(
            field="f",
            rule="r",
            message="m",
            severity="warning",
        )
        result = _build_phase_result(
            failures=[warning],
            criteria=[],
            phase_name="test",
            log_event="test_event",
        )
        # 1 - (1 / max(0, 1)) = 1 - 1 = 0.0
        assert result.completeness_score == 0.0

    def test_info_severity_does_not_make_invalid(self) -> None:
        """Info-severity failures do not set valid=False."""
        info = ValidationFailure(
            field="f",
            rule="r",
            message="m",
            severity="info",
        )
        result = _build_phase_result(
            failures=[info],
            criteria=["crit1", "crit2"],
            phase_name="test",
            log_event="test_event",
        )
        assert result.valid is True


# ---------------------------------------------------------------------------
# check_phase_exit/input — unknown phase (no registered checker)
# ---------------------------------------------------------------------------


class TestUnknownPhaseHandling:
    """Phases without a registered checker return empty results gracefully."""

    def test_exit_for_research_with_no_checker_side_effects(
        self,
        tmp_path: Path,
        writer: FileStateWriter,
    ) -> None:
        """All phases have exit checkers, so test research which has the
        simplest logic and confirm the dispatch actually fires."""
        run_dir = _make_run_dir(tmp_path, writer)
        config = TRWConfig()
        # Calling check_phase_exit should not raise for any valid Phase
        result = check_phase_exit(Phase.RESEARCH, run_dir, config)
        assert isinstance(result.valid, bool)
        assert isinstance(result.completeness_score, float)


# ---------------------------------------------------------------------------
# check_phase_input — strict vs lenient severity propagation
# ---------------------------------------------------------------------------


class TestPhaseInputStrictSeverity:
    """strict_input_criteria=True escalates failures to error severity."""

    def test_plan_input_strict_makes_failures_errors(
        self,
        tmp_path: Path,
        writer: FileStateWriter,
    ) -> None:
        """Plan input with strict=True and missing synthesis uses error severity."""
        run_dir = _make_run_dir(tmp_path, writer)
        config = TRWConfig(strict_input_criteria=True)
        result = check_phase_input(Phase.PLAN, run_dir, config)
        synthesis_f = [f for f in result.failures if f.rule == "research_complete"]
        assert len(synthesis_f) == 1
        assert synthesis_f[0].severity == "error"
        assert result.valid is False

    def test_plan_input_lenient_makes_failures_warnings(
        self,
        tmp_path: Path,
        writer: FileStateWriter,
    ) -> None:
        """Plan input with strict=False and missing synthesis uses warning severity."""
        run_dir = _make_run_dir(tmp_path, writer)
        config = TRWConfig(strict_input_criteria=False)
        result = check_phase_input(Phase.PLAN, run_dir, config)
        synthesis_f = [f for f in result.failures if f.rule == "research_complete"]
        assert len(synthesis_f) == 1
        assert synthesis_f[0].severity == "warning"
        # Warnings only => still valid
        assert result.valid is True

    def test_implement_input_strict_plan_missing_is_error(
        self,
        tmp_path: Path,
        writer: FileStateWriter,
    ) -> None:
        """In strict mode, missing plan.md for implement input is an error."""
        run_dir = _make_run_dir(tmp_path, writer)
        config = TRWConfig(
            strict_input_criteria=True,
            phase_gate_enforcement="off",
        )
        result = check_phase_input(Phase.IMPLEMENT, run_dir, config)
        plan_f = [f for f in result.failures if f.rule == "plan_exists"]
        assert len(plan_f) == 1
        assert plan_f[0].severity == "error"

    def test_deliver_input_strict_no_events_is_error(
        self,
        tmp_path: Path,
        writer: FileStateWriter,
    ) -> None:
        """In strict mode, missing events.jsonl for deliver input is an error."""
        run_dir = _make_run_dir(tmp_path, writer)
        config = TRWConfig(strict_input_criteria=True)
        result = check_phase_input(Phase.DELIVER, run_dir, config)
        events_f = [f for f in result.failures if f.rule == "events_exist"]
        assert len(events_f) == 1
        assert events_f[0].severity == "error"
        assert result.valid is False


# ---------------------------------------------------------------------------
# check_phase_input — validate input with OSError on iterdir
# ---------------------------------------------------------------------------


class TestValidateInputOSError:
    """_check_validate_input handles OSError from shards iterdir gracefully."""

    def test_validate_input_oserror_treated_as_empty(
        self,
        tmp_path: Path,
        writer: FileStateWriter,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """OSError during shards.iterdir() is treated as empty shards."""
        run_dir = _make_run_dir(tmp_path, writer)
        shards = run_dir / "shards"
        shards.mkdir(parents=True, exist_ok=True)

        original_iterdir = Path.iterdir

        def _raise_oserror(self: Path) -> None:
            if "shards" in str(self):
                raise OSError("permission denied")
            return original_iterdir(self)

        monkeypatch.setattr(Path, "iterdir", _raise_oserror)
        config = TRWConfig(strict_input_criteria=True)
        result = check_phase_input(Phase.VALIDATE, run_dir, config)
        rules = [f.rule for f in result.failures]
        assert "implementation_complete" in rules


# ---------------------------------------------------------------------------
# check_phase_input — completeness score varies with failure count
# ---------------------------------------------------------------------------


class TestPhaseInputCompletenessScore:
    """Completeness score reflects the ratio of failures to criteria."""

    def test_research_input_with_run_yaml_has_full_completeness(
        self,
        tmp_path: Path,
        writer: FileStateWriter,
    ) -> None:
        """Research phase with run.yaml and no additional prereqs scores high."""
        run_dir = _make_run_dir(tmp_path, writer)
        config = TRWConfig()
        result = check_phase_input(Phase.RESEARCH, run_dir, config)
        # Research has 1 criterion, 0 failures -> 1.0
        assert result.completeness_score == 1.0

    def test_implement_input_all_missing_has_low_completeness(
        self,
        tmp_path: Path,
        writer: FileStateWriter,
    ) -> None:
        """Implement with plan, manifest, and PRDs all missing has low score."""
        run_dir = _make_run_dir(tmp_path, writer)
        config = TRWConfig(
            strict_input_criteria=False,
            phase_gate_enforcement="off",
        )
        result = check_phase_input(Phase.IMPLEMENT, run_dir, config)
        # At least plan_exists and manifest_exists failures
        assert result.completeness_score < 1.0
        assert len(result.failures) >= 2


# ---------------------------------------------------------------------------
# PHASE_INPUT_CRITERIA / PHASE_EXIT_CRITERIA — coverage for all phases
# ---------------------------------------------------------------------------


class TestPhaseCriteriaDictCoverage:
    """Ensure all Phase enum values have entries in criteria dicts."""

    def test_all_phases_have_exit_criteria(self) -> None:
        """Every Phase enum value has an entry in PHASE_EXIT_CRITERIA."""
        from trw_mcp.state.validation import PHASE_EXIT_CRITERIA

        for phase in Phase:
            assert phase.value in PHASE_EXIT_CRITERIA, f"Phase '{phase.value}' missing from PHASE_EXIT_CRITERIA"

    def test_all_phases_have_input_criteria(self) -> None:
        """Every Phase enum value has an entry in PHASE_INPUT_CRITERIA."""
        from trw_mcp.state.validation import PHASE_INPUT_CRITERIA

        for phase in Phase:
            assert phase.value in PHASE_INPUT_CRITERIA, f"Phase '{phase.value}' missing from PHASE_INPUT_CRITERIA"

    def test_exit_criteria_values_are_nonempty_lists(self) -> None:
        """Each exit criteria list has at least one item."""
        from trw_mcp.state.validation import PHASE_EXIT_CRITERIA

        for phase_name, criteria in PHASE_EXIT_CRITERIA.items():
            assert isinstance(criteria, list)
            assert len(criteria) > 0, f"{phase_name} has empty exit criteria"

    def test_input_criteria_values_are_nonempty_lists(self) -> None:
        """Each input criteria list has at least one item."""
        from trw_mcp.state.validation import PHASE_INPUT_CRITERIA

        for phase_name, criteria in PHASE_INPUT_CRITERIA.items():
            assert isinstance(criteria, list)
            assert len(criteria) > 0, f"{phase_name} has empty input criteria"
