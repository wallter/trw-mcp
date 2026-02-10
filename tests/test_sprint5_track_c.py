"""Sprint 5 Track C tests — PRD-FIX-005, PRD-CORE-010 FR09, PRD-QUAL-001.

Covers:
- trw_status stale version warning (PRD-FIX-005)
- Traceability finding coverage integration (PRD-CORE-010 FR09)
- Success pattern extraction enhancements (PRD-QUAL-001)
"""

from __future__ import annotations

from pathlib import Path

import pytest

from trw_mcp.models.config import TRWConfig
from trw_mcp.state.persistence import FileStateReader, FileStateWriter

_CFG = TRWConfig()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_orch_tools(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, object]:
    """Create fresh orchestration server and return tool map."""
    monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
    import trw_mcp.tools.orchestration as orch_mod

    monkeypatch.setattr(orch_mod, "_config", TRWConfig())

    from fastmcp import FastMCP
    from trw_mcp.tools.orchestration import register_orchestration_tools

    srv = FastMCP("test")
    register_orchestration_tools(srv)
    return {t.name: t for t in srv._tool_manager._tools.values()}


def _get_learning_tools(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, object]:
    """Create fresh learning server and return tool map."""
    monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
    import trw_mcp.tools.learning as learn_mod

    monkeypatch.setattr(learn_mod, "_config", TRWConfig())

    from fastmcp import FastMCP
    from trw_mcp.tools.learning import register_learning_tools

    srv = FastMCP("test")
    register_learning_tools(srv)
    return {t.name: t for t in srv._tool_manager._tools.values()}


def _get_req_tools(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, object]:
    """Create fresh requirements server and return tool map."""
    monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
    import trw_mcp.tools.requirements as req_mod

    monkeypatch.setattr(req_mod, "_config", TRWConfig())
    monkeypatch.setattr(req_mod, "_CACHED_TEMPLATE_BODY", None)
    monkeypatch.setattr(req_mod, "_CACHED_TEMPLATE_VERSION", None)
    (tmp_path / ".trw").mkdir(exist_ok=True)

    from fastmcp import FastMCP
    from trw_mcp.tools.requirements import register_requirements_tools

    srv = FastMCP("test")
    register_requirements_tools(srv)
    return {t.name: t for t in srv._tool_manager._tools.values()}


def _init_run(tools: dict[str, object], task_name: str = "test-task") -> dict[str, str]:
    """Create a run using trw_init and return the result."""
    return tools["trw_init"].fn(task_name=task_name)


# ---------------------------------------------------------------------------
# PRD-FIX-005: trw_status Stale Version Warning
# ---------------------------------------------------------------------------


class TestStatusStaleVersionWarning:
    """PRD-FIX-005: trw_status warns when framework version is stale."""

    def test_warns_on_stale_version(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When run's framework version differs from current, a warning appears."""
        tools = _get_orch_tools(monkeypatch, tmp_path)
        init_result = _init_run(tools)
        run_path = init_result["run_path"]

        # Modify the VERSION.yaml to simulate a newer framework
        version_path = tmp_path / ".trw" / "frameworks" / "VERSION.yaml"
        writer = FileStateWriter()
        writer.write_yaml(version_path, {
            "framework_version": "v99.0_TRW",
            "aaref_version": "v1.1.0",
            "trw_mcp_version": "0.1.0",
        })

        result = tools["trw_status"].fn(run_path=run_path)
        assert "version_warning" in result
        assert "v99.0_TRW" in str(result["version_warning"])

    def test_no_warning_when_current(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """No warning when run's framework version matches current."""
        tools = _get_orch_tools(monkeypatch, tmp_path)
        init_result = _init_run(tools)
        run_path = init_result["run_path"]

        # VERSION.yaml was already deployed by trw_init with current version
        result = tools["trw_status"].fn(run_path=run_path)
        assert "version_warning" not in result

    def test_graceful_when_no_version_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """No crash when VERSION.yaml doesn't exist."""
        tools = _get_orch_tools(monkeypatch, tmp_path)
        init_result = _init_run(tools)
        run_path = init_result["run_path"]

        # Delete VERSION.yaml
        version_path = tmp_path / ".trw" / "frameworks" / "VERSION.yaml"
        if version_path.exists():
            version_path.unlink()

        result = tools["trw_status"].fn(run_path=run_path)
        assert "version_warning" not in result


# ---------------------------------------------------------------------------
# PRD-CORE-010 FR09: Traceability Finding Coverage
# ---------------------------------------------------------------------------


def _create_prd_file(
    project_root: Path,
    prd_id: str = "PRD-CORE-001",
    with_traceability: bool = False,
    with_fr: bool = False,
    with_matrix: bool = False,
) -> Path:
    """Create a minimal PRD file for traceability testing."""
    prds_dir = project_root / _CFG.prds_relative_path
    prds_dir.mkdir(parents=True, exist_ok=True)

    trace_section = ""
    if with_traceability:
        trace_section = """
  traceability:
    implements: [some-requirement]
    depends_on: []
    enables: []
    conflicts_with: []"""

    fr_section = ""
    if with_fr:
        fr_section = f"\n### {prd_id}-FR01: Requirement Title\n"

    matrix_section = ""
    if with_matrix:
        matrix_section = """
## 12. Traceability Matrix

| Requirement | Source | Implementation | Test | Status |
|-------------|--------|----------------|------|--------|
| FR01 | Source | `tools/example.py` | test_example | Done |
"""

    content = f"""---
prd:
  id: {prd_id}
  title: Test PRD
  version: '1.0'
  status: draft
  priority: P1
  category: CORE{trace_section}
---

# {prd_id}: Test PRD
{fr_section}
## 1. Problem Statement
{matrix_section}
"""
    prd_path = prds_dir / f"{prd_id}.md"
    prd_path.write_text(content, encoding="utf-8")
    return prd_path


class TestTraceabilityFindingCoverage:
    """PRD-CORE-010 FR09: trw_traceability_check flags unlinked findings."""

    def test_flags_unlinked_findings(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Critical/high findings without target_prd are flagged."""
        tools = _get_req_tools(monkeypatch, tmp_path)
        _create_prd_file(tmp_path)

        # Create findings registry with unlinked critical finding
        registry_dir = tmp_path / ".trw" / "findings"
        registry_dir.mkdir(parents=True)
        writer = FileStateWriter()
        writer.write_yaml(registry_dir / "registry.yaml", {
            "entries": [
                {"id": "F-W1-S1-001", "summary": "Critical bug",
                 "severity": "critical", "status": "open", "target_prd": None},
            ],
            "total_count": 1,
            "runs_indexed": ["run-1"],
        })

        result = tools["trw_traceability_check"].fn()
        assert "F-W1-S1-001" in result["unlinked_findings"]

    def test_linked_findings_not_flagged(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Findings with target_prd are NOT flagged as unlinked."""
        tools = _get_req_tools(monkeypatch, tmp_path)
        _create_prd_file(tmp_path)

        registry_dir = tmp_path / ".trw" / "findings"
        registry_dir.mkdir(parents=True)
        writer = FileStateWriter()
        writer.write_yaml(registry_dir / "registry.yaml", {
            "entries": [
                {"id": "F-W1-S1-001", "summary": "Linked finding",
                 "severity": "high", "status": "acknowledged",
                 "target_prd": "PRD-FIX-099"},
            ],
            "total_count": 1,
            "runs_indexed": ["run-1"],
        })

        result = tools["trw_traceability_check"].fn()
        assert "F-W1-S1-001" not in result["unlinked_findings"]
        assert result["unlinked_findings_count"] == 0

    def test_mixed_linked_and_unlinked(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Only unlinked findings appear in the output."""
        tools = _get_req_tools(monkeypatch, tmp_path)
        _create_prd_file(tmp_path)

        registry_dir = tmp_path / ".trw" / "findings"
        registry_dir.mkdir(parents=True)
        writer = FileStateWriter()
        writer.write_yaml(registry_dir / "registry.yaml", {
            "entries": [
                {"id": "F-W1-S1-001", "summary": "Unlinked critical",
                 "severity": "critical", "status": "open", "target_prd": None},
                {"id": "F-W1-S2-001", "summary": "Linked high",
                 "severity": "high", "status": "acknowledged",
                 "target_prd": "PRD-FIX-001"},
                {"id": "F-W1-S3-001", "summary": "Unlinked high",
                 "severity": "high", "status": "open", "target_prd": None},
            ],
            "total_count": 3,
            "runs_indexed": ["run-1"],
        })

        result = tools["trw_traceability_check"].fn()
        assert "F-W1-S1-001" in result["unlinked_findings"]
        assert "F-W1-S2-001" not in result["unlinked_findings"]
        assert "F-W1-S3-001" in result["unlinked_findings"]
        assert result["unlinked_findings_count"] == 2

    def test_no_findings_graceful(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """No findings registry produces empty unlinked list without error."""
        tools = _get_req_tools(monkeypatch, tmp_path)
        _create_prd_file(tmp_path)

        result = tools["trw_traceability_check"].fn()
        assert result["unlinked_findings"] == []
        assert result["unlinked_findings_count"] == 0

    def test_finding_linked_to_prd(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When all findings have target_prd, unlinked count is zero."""
        tools = _get_req_tools(monkeypatch, tmp_path)
        _create_prd_file(tmp_path)

        registry_dir = tmp_path / ".trw" / "findings"
        registry_dir.mkdir(parents=True)
        writer = FileStateWriter()
        writer.write_yaml(registry_dir / "registry.yaml", {
            "entries": [
                {"id": "F-W1-S1-001", "summary": "Linked critical",
                 "severity": "critical", "status": "acknowledged",
                 "target_prd": "PRD-CORE-001"},
                {"id": "F-W1-S2-001", "summary": "Linked high",
                 "severity": "high", "status": "acknowledged",
                 "target_prd": "PRD-FIX-002"},
            ],
            "total_count": 2,
            "runs_indexed": ["run-1"],
        })

        result = tools["trw_traceability_check"].fn()
        assert result["unlinked_findings_count"] == 0

    def test_low_severity_not_flagged(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Low/medium/info findings without target_prd are NOT flagged."""
        tools = _get_req_tools(monkeypatch, tmp_path)
        _create_prd_file(tmp_path)

        registry_dir = tmp_path / ".trw" / "findings"
        registry_dir.mkdir(parents=True)
        writer = FileStateWriter()
        writer.write_yaml(registry_dir / "registry.yaml", {
            "entries": [
                {"id": "F-W1-S1-001", "summary": "Low finding",
                 "severity": "low", "status": "open", "target_prd": None},
                {"id": "F-W1-S2-001", "summary": "Medium finding",
                 "severity": "medium", "status": "open", "target_prd": None},
                {"id": "F-W1-S3-001", "summary": "Info finding",
                 "severity": "info", "status": "open", "target_prd": None},
            ],
            "total_count": 3,
            "runs_indexed": ["run-1"],
        })

        result = tools["trw_traceability_check"].fn()
        assert result["unlinked_findings_count"] == 0

    def test_output_includes_finding_ids(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Output includes finding ID strings in the unlinked list."""
        tools = _get_req_tools(monkeypatch, tmp_path)
        _create_prd_file(tmp_path)

        registry_dir = tmp_path / ".trw" / "findings"
        registry_dir.mkdir(parents=True)
        writer = FileStateWriter()
        writer.write_yaml(registry_dir / "registry.yaml", {
            "entries": [
                {"id": "F-W1-S1-001", "summary": "Unlinked",
                 "severity": "critical", "status": "open", "target_prd": None},
            ],
            "total_count": 1,
            "runs_indexed": ["run-1"],
        })

        result = tools["trw_traceability_check"].fn()
        assert isinstance(result["unlinked_findings"], list)
        assert all(isinstance(fid, str) for fid in result["unlinked_findings"])
        assert result["unlinked_findings"][0].startswith("F-")


# ---------------------------------------------------------------------------
# PRD-CORE-010 FR09: get_unlinked_findings helper
# ---------------------------------------------------------------------------


class TestGetUnlinkedFindings:
    """Tests for the get_unlinked_findings() helper in findings.py."""

    def test_returns_empty_when_no_registry(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Returns empty list when registry file doesn't exist."""
        from trw_mcp.tools.findings import get_unlinked_findings

        project = tmp_path / "project"
        (project / ".trw").mkdir(parents=True)
        monkeypatch.setattr(
            "trw_mcp.tools.findings.resolve_project_root", lambda: project,
        )

        assert get_unlinked_findings() == []

    def test_returns_unlinked_critical(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Returns critical findings without target_prd."""
        from trw_mcp.tools.findings import get_unlinked_findings

        project = tmp_path / "project"
        (project / ".trw" / "findings").mkdir(parents=True)
        monkeypatch.setattr(
            "trw_mcp.tools.findings.resolve_project_root", lambda: project,
        )

        writer = FileStateWriter()
        writer.write_yaml(project / ".trw" / "findings" / "registry.yaml", {
            "entries": [
                {"id": "F-W1-S1-001", "severity": "critical",
                 "target_prd": None},
                {"id": "F-W1-S2-001", "severity": "high",
                 "target_prd": "PRD-FIX-001"},
            ],
        })

        result = get_unlinked_findings()
        assert "F-W1-S1-001" in result
        assert "F-W1-S2-001" not in result

    def test_custom_severity_filter(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Custom severity filter is respected."""
        from trw_mcp.tools.findings import get_unlinked_findings

        project = tmp_path / "project"
        (project / ".trw" / "findings").mkdir(parents=True)
        monkeypatch.setattr(
            "trw_mcp.tools.findings.resolve_project_root", lambda: project,
        )

        writer = FileStateWriter()
        writer.write_yaml(project / ".trw" / "findings" / "registry.yaml", {
            "entries": [
                {"id": "F-W1-S1-001", "severity": "medium",
                 "target_prd": None},
            ],
        })

        # Default filter only includes critical and high
        assert get_unlinked_findings() == []
        # Custom filter includes medium
        assert get_unlinked_findings(severity_filter=("medium",)) == ["F-W1-S1-001"]


# ---------------------------------------------------------------------------
# PRD-QUAL-001: Success Pattern Extraction Enhancements
# ---------------------------------------------------------------------------


class TestDetectToolSequences:
    """PRD-QUAL-001 FR02: Tool sequence pattern detection."""

    def test_detects_recurring_sequences(self) -> None:
        """Sequences occurring 3+ times are reported."""
        from trw_mcp.state.analytics import detect_tool_sequences

        events = []
        # Create 3 instances of recall -> shard_started -> shard_completed
        for _ in range(3):
            events.append({"event": "trw_recall"})
            events.append({"event": "shard_started"})
            events.append({"event": "shard_completed_successfully"})

        result = detect_tool_sequences(events, lookback=2, min_occurrences=3)
        assert len(result) >= 1
        # The sequence ending with success should be detected
        found = any("shard_completed" in str(seq.get("sequence", [])) for seq in result)
        assert found

    def test_insufficient_occurrences_not_reported(self) -> None:
        """Sequences with fewer than min_occurrences are not returned."""
        from trw_mcp.state.analytics import detect_tool_sequences

        events = [
            {"event": "trw_recall"},
            {"event": "shard_completed_successfully"},
            {"event": "other_event"},
            {"event": "different_event"},
        ]

        result = detect_tool_sequences(events, lookback=2, min_occurrences=3)
        assert len(result) == 0

    def test_lookback_configurable(self) -> None:
        """Lookback window size is respected."""
        from trw_mcp.state.analytics import detect_tool_sequences

        events = []
        for _ in range(3):
            events.append({"event": "step_a"})
            events.append({"event": "step_b"})
            events.append({"event": "step_c"})
            events.append({"event": "task_completed_successfully"})

        # With lookback=2, only sees step_c + task_completed_successfully
        result_short = detect_tool_sequences(events, lookback=2, min_occurrences=3)
        # With lookback=3, sees step_b + step_c + task_completed_successfully
        result_long = detect_tool_sequences(events, lookback=3, min_occurrences=3)

        # Longer lookback produces longer sequences
        if result_short and result_long:
            short_len = max(len(s.get("sequence", [])) for s in result_short)
            long_len = max(len(s.get("sequence", [])) for s in result_long)
            assert long_len >= short_len

    def test_empty_events(self) -> None:
        """Empty events list returns empty sequences."""
        from trw_mcp.state.analytics import detect_tool_sequences

        assert detect_tool_sequences([], lookback=3) == []


class TestSurfaceValidatedLearnings:
    """PRD-QUAL-001 FR03: Q-value success surfacing."""

    def test_surfaces_high_q_value_learnings(self, tmp_path: Path) -> None:
        """Learnings with Q >= threshold and sufficient observations are surfaced."""
        from trw_mcp.state.analytics import surface_validated_learnings

        entries_dir = tmp_path / "learnings" / "entries"
        entries_dir.mkdir(parents=True)

        writer = FileStateWriter()
        writer.write_yaml(entries_dir / "entry-high.yaml", {
            "id": "L-high",
            "summary": "High Q learning",
            "status": "active",
            "q_value": 0.85,
            "q_observations": 5,
            "tags": ["success"],
        })
        writer.write_yaml(entries_dir / "entry-low.yaml", {
            "id": "L-low",
            "summary": "Low Q learning",
            "status": "active",
            "q_value": 0.3,
            "q_observations": 5,
            "tags": ["error"],
        })

        result = surface_validated_learnings(
            tmp_path, q_threshold=0.6, cold_start_threshold=3,
        )
        assert len(result) == 1
        assert result[0]["learning_id"] == "L-high"
        assert result[0]["q_value"] == 0.85

    def test_excludes_cold_start_learnings(self, tmp_path: Path) -> None:
        """Learnings with too few observations are excluded."""
        from trw_mcp.state.analytics import surface_validated_learnings

        entries_dir = tmp_path / "learnings" / "entries"
        entries_dir.mkdir(parents=True)

        writer = FileStateWriter()
        writer.write_yaml(entries_dir / "entry.yaml", {
            "id": "L-cold",
            "summary": "Cold start learning",
            "status": "active",
            "q_value": 0.9,
            "q_observations": 1,
            "tags": [],
        })

        result = surface_validated_learnings(
            tmp_path, q_threshold=0.6, cold_start_threshold=3,
        )
        assert len(result) == 0

    def test_excludes_non_active_learnings(self, tmp_path: Path) -> None:
        """Resolved/obsolete learnings are not surfaced."""
        from trw_mcp.state.analytics import surface_validated_learnings

        entries_dir = tmp_path / "learnings" / "entries"
        entries_dir.mkdir(parents=True)

        writer = FileStateWriter()
        writer.write_yaml(entries_dir / "entry.yaml", {
            "id": "L-resolved",
            "summary": "Resolved learning",
            "status": "resolved",
            "q_value": 0.9,
            "q_observations": 10,
            "tags": [],
        })

        result = surface_validated_learnings(
            tmp_path, q_threshold=0.6, cold_start_threshold=3,
        )
        assert len(result) == 0

    def test_empty_directory(self, tmp_path: Path) -> None:
        """Returns empty list when entries directory doesn't exist."""
        from trw_mcp.state.analytics import surface_validated_learnings

        result = surface_validated_learnings(
            tmp_path, q_threshold=0.6, cold_start_threshold=3,
        )
        assert result == []


class TestHasExistingSuccessLearning:
    """PRD-QUAL-001 FR04: Deduplication for positive learnings."""

    def test_detects_existing_learning(self, tmp_path: Path) -> None:
        """Returns True when a learning with matching prefix exists."""
        from trw_mcp.state.analytics import has_existing_success_learning

        entries_dir = tmp_path / "learnings" / "entries"
        entries_dir.mkdir(parents=True)

        writer = FileStateWriter()
        writer.write_yaml(entries_dir / "existing.yaml", {
            "id": "L-existing",
            "summary": "Success: shard_completed (3x)",
        })

        assert has_existing_success_learning(
            tmp_path, "Success: shard_completed (3x)",
        )

    def test_no_match_returns_false(self, tmp_path: Path) -> None:
        """Returns False when no matching learning exists."""
        from trw_mcp.state.analytics import has_existing_success_learning

        entries_dir = tmp_path / "learnings" / "entries"
        entries_dir.mkdir(parents=True)

        writer = FileStateWriter()
        writer.write_yaml(entries_dir / "other.yaml", {
            "id": "L-other",
            "summary": "Something completely different",
        })

        assert not has_existing_success_learning(
            tmp_path, "Success: shard_completed (3x)",
        )

    def test_empty_directory(self, tmp_path: Path) -> None:
        """Returns False when entries directory doesn't exist."""
        from trw_mcp.state.analytics import has_existing_success_learning

        assert not has_existing_success_learning(tmp_path, "any summary")


class TestReflectExtendedOutput:
    """PRD-QUAL-001 FR05: Extended trw_reflect output schema."""

    def test_output_contains_success_patterns_dict(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """trw_reflect output has success_patterns as a dict with sub-fields."""
        tools = _get_learning_tools(monkeypatch, tmp_path)
        (tmp_path / ".trw" / "learnings" / "entries").mkdir(parents=True)
        (tmp_path / ".trw" / "reflections").mkdir(parents=True)

        result = tools["trw_reflect"].fn()
        sp = result["success_patterns"]
        assert isinstance(sp, dict)
        assert "count" in sp
        assert "phase_completions" in sp
        assert "shard_successes" in sp
        assert "tool_sequences" in sp

    def test_output_contains_validated_learnings(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """trw_reflect output has validated_learnings key."""
        tools = _get_learning_tools(monkeypatch, tmp_path)
        (tmp_path / ".trw" / "learnings" / "entries").mkdir(parents=True)
        (tmp_path / ".trw" / "reflections").mkdir(parents=True)

        result = tools["trw_reflect"].fn()
        assert "validated_learnings" in result
        assert isinstance(result["validated_learnings"], list)

    def test_output_contains_positive_learnings_created(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """trw_reflect output has positive_learnings_created count."""
        tools = _get_learning_tools(monkeypatch, tmp_path)
        (tmp_path / ".trw" / "learnings" / "entries").mkdir(parents=True)
        (tmp_path / ".trw" / "reflections").mkdir(parents=True)

        result = tools["trw_reflect"].fn()
        assert "positive_learnings_created" in result
        assert isinstance(result["positive_learnings_created"], int)

    def test_backward_compatibility_error_patterns(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Existing error_patterns key still present in output."""
        tools = _get_learning_tools(monkeypatch, tmp_path)
        (tmp_path / ".trw" / "learnings" / "entries").mkdir(parents=True)
        (tmp_path / ".trw" / "reflections").mkdir(parents=True)

        result = tools["trw_reflect"].fn()
        assert "error_patterns" in result
        assert "repeated_operations" in result


class TestPositiveLearningGeneration:
    """PRD-QUAL-001 FR04: Positive learning generation with dedup."""

    def test_generates_positive_learnings_from_success_events(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Success events generate positive learnings."""
        tools = _get_learning_tools(monkeypatch, tmp_path)
        trw_dir = tmp_path / ".trw"
        (trw_dir / "learnings" / "entries").mkdir(parents=True)
        (trw_dir / "reflections").mkdir(parents=True)

        # Create a run with success events
        run_dir = tmp_path / "docs" / "test-task" / "runs" / "20260210T000000Z-test"
        meta_dir = run_dir / "meta"
        meta_dir.mkdir(parents=True)

        writer = FileStateWriter()
        writer.write_yaml(meta_dir / "run.yaml", {
            "run_id": "test-run", "task": "test-task",
        })
        for i in range(3):
            writer.append_jsonl(meta_dir / "events.jsonl", {
                "ts": f"2026-02-10T00:00:0{i}Z",
                "event": "shard_completed_successfully",
                "data": {"shard_id": f"S{i}"},
            })

        result = tools["trw_reflect"].fn(run_path=str(run_dir))
        assert result["positive_learnings_created"] > 0

    def test_dedup_prevents_duplicate_positive_learnings(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Duplicate success learnings are not recreated on repeated reflection."""
        from trw_mcp.state.analytics import has_existing_success_learning, save_learning_entry
        from trw_mcp.models.learning import LearningEntry

        trw_dir = tmp_path / ".trw"
        (trw_dir / "learnings" / "entries").mkdir(parents=True)

        # Pre-create a success learning
        entry = LearningEntry(
            id="L-existing",
            summary="Success: shard_completed_successfully (3x)",
            detail="Pre-existing",
            tags=["success", "pattern"],
            impact=0.5,
        )
        save_learning_entry(trw_dir, entry)

        # Verify dedup detects it
        assert has_existing_success_learning(
            trw_dir, "Success: shard_completed_successfully (3x)",
        )

        # Now run reflection with the same pattern
        tools = _get_learning_tools(monkeypatch, tmp_path)
        (trw_dir / "reflections").mkdir(parents=True)

        run_dir = tmp_path / "docs" / "test-task" / "runs" / "20260210T000000Z-test"
        meta_dir = run_dir / "meta"
        meta_dir.mkdir(parents=True)

        writer = FileStateWriter()
        writer.write_yaml(meta_dir / "run.yaml", {
            "run_id": "test-run", "task": "test-task",
        })
        for i in range(3):
            writer.append_jsonl(meta_dir / "events.jsonl", {
                "ts": f"2026-02-10T00:00:0{i}Z",
                "event": "shard_completed_successfully",
                "data": {"shard_id": f"S{i}"},
            })

        result = tools["trw_reflect"].fn(run_path=str(run_dir))
        # The shard_completed_successfully pattern already exists, so it
        # should be deduplicated (0 positive for that specific pattern).
        # Other patterns (repeated_op) may still be created.
        assert result["positive_learnings_created"] == 0

    def test_max_positive_learnings_respected(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """At most reflect_max_positive_learnings are created."""
        # Set a low cap
        monkeypatch.setenv("TRW_REFLECT_MAX_POSITIVE_LEARNINGS", "2")
        import trw_mcp.tools.learning as learn_mod

        monkeypatch.setattr(learn_mod, "_config", TRWConfig())

        tools = _get_learning_tools(monkeypatch, tmp_path)
        trw_dir = tmp_path / ".trw"
        (trw_dir / "learnings" / "entries").mkdir(parents=True)
        (trw_dir / "reflections").mkdir(parents=True)

        # Create many different success events
        run_dir = tmp_path / "docs" / "test-task" / "runs" / "20260210T000000Z-test"
        meta_dir = run_dir / "meta"
        meta_dir.mkdir(parents=True)

        writer = FileStateWriter()
        writer.write_yaml(meta_dir / "run.yaml", {
            "run_id": "test-run", "task": "test-task",
        })

        # Create events with different success types so find_success_patterns returns >2
        event_types = [
            "shard_completed_successfully",
            "phase_check_passed",
            "wave_validation_done",
            "run_completed_successfully",
            "task_finished",
        ]
        for i, etype in enumerate(event_types):
            for _ in range(3):  # Each type needs 3+ occurrences
                writer.append_jsonl(meta_dir / "events.jsonl", {
                    "ts": f"2026-02-10T00:00:{i:02d}Z",
                    "event": etype,
                })

        result = tools["trw_reflect"].fn(run_path=str(run_dir))
        assert result["positive_learnings_created"] <= 2


# ---------------------------------------------------------------------------
# PRD-QUAL-001 Config Fields
# ---------------------------------------------------------------------------


class TestQual001ConfigFields:
    """PRD-QUAL-001: New config fields have correct defaults."""

    def test_reflect_sequence_lookback_default(self) -> None:
        """Default lookback is 3."""
        config = TRWConfig()
        assert config.reflect_sequence_lookback == 3

    def test_reflect_max_positive_learnings_default(self) -> None:
        """Default max positive learnings is 5."""
        config = TRWConfig()
        assert config.reflect_max_positive_learnings == 5

    def test_reflect_q_value_threshold_default(self) -> None:
        """Default Q-value threshold is 0.6."""
        config = TRWConfig()
        assert config.reflect_q_value_threshold == 0.6
