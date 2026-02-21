"""Tests for trw_mcp.audit — cross-project audit CLI module."""

from __future__ import annotations

import json
from pathlib import Path

from trw_mcp.audit import (
    _audit_duplicates,
    _audit_index_consistency,
    _audit_learnings,
    _audit_recall_effectiveness,
    _retire_telemetry_bloat,
    format_markdown,
    run_audit,
)
from trw_mcp.models.config import TRWConfig
from trw_mcp.state.persistence import FileStateReader, FileStateWriter

_writer = FileStateWriter()
_reader = FileStateReader()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_entry(
    entries_dir: Path,
    *,
    summary: str = "Test learning",
    impact: float = 0.8,
    status: str = "active",
    tags: list[str] | None = None,
    source_type: str = "agent",
) -> None:
    """Write a YAML learning entry file."""
    import uuid

    entry_id = f"L-{uuid.uuid4().hex[:8]}"
    slug = summary.lower().replace(" ", "-")[:40]
    filename = f"2026-02-21-{slug}-{uuid.uuid4().hex[:6]}.yaml"
    _writer.write_yaml(entries_dir / filename, {
        "id": entry_id,
        "summary": summary,
        "detail": f"Detail for: {summary}",
        "impact": impact,
        "status": status,
        "tags": tags or ["test"],
        "source_type": source_type,
        "created": "2026-02-21T00:00:00Z",
    })


def _setup_project(tmp_path: Path) -> Path:
    """Create minimal .trw structure for audit tests."""
    trw_dir = tmp_path / ".trw"
    trw_dir.mkdir(exist_ok=True)
    (trw_dir / "learnings" / "entries").mkdir(parents=True, exist_ok=True)
    (trw_dir / "context").mkdir(exist_ok=True)
    return tmp_path


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestAuditLearnings:
    """Tests for _audit_learnings section function."""

    def test_empty_entries(self) -> None:
        result = _audit_learnings([])
        assert result["total"] == 0
        bloat = result["telemetry_bloat"]
        assert isinstance(bloat, dict)
        assert bloat["count"] == 0
        assert bloat["verdict"] == "PASS"

    def test_counts_by_status(self) -> None:
        entries: list[dict[str, object]] = [
            {"status": "active", "impact": 0.8, "summary": "A"},
            {"status": "active", "impact": 0.5, "summary": "B"},
            {"status": "resolved", "impact": 0.9, "summary": "C"},
            {"status": "obsolete", "impact": 0.3, "summary": "D"},
        ]
        result = _audit_learnings(entries)
        assert result["total"] == 4
        by_status = result["by_status"]
        assert isinstance(by_status, dict)
        assert by_status["active"] == 2
        assert by_status["resolved"] == 1
        assert by_status["obsolete"] == 1

    def test_impact_buckets(self) -> None:
        entries: list[dict[str, object]] = [
            {"impact": 0.9, "summary": "High"},
            {"impact": 0.7, "summary": "High2"},
            {"impact": 0.5, "summary": "Medium"},
            {"impact": 0.2, "summary": "Low"},
        ]
        result = _audit_learnings(entries)
        by_impact = result["by_impact"]
        assert isinstance(by_impact, dict)
        assert by_impact["high"] == 2
        assert by_impact["medium"] == 1
        assert by_impact["low"] == 1

    def test_detects_telemetry_bloat(self) -> None:
        entries: list[dict[str, object]] = [
            {"summary": "Repeated operation: checkpoint 5x", "impact": 0.3},
            {"summary": "Success: build passed", "impact": 0.2},
            {"summary": "Normal learning", "impact": 0.8},
        ]
        result = _audit_learnings(entries)
        bloat = result["telemetry_bloat"]
        assert isinstance(bloat, dict)
        assert bloat["count"] == 2
        # 2/3 = 0.667 > 0.20 threshold
        assert bloat["verdict"] == "WARN"


class TestAuditDuplicates:
    """Tests for _audit_duplicates section function."""

    def test_no_duplicates(self, tmp_path: Path) -> None:
        project = _setup_project(tmp_path)
        entries_dir = project / ".trw" / "learnings" / "entries"
        _make_entry(entries_dir, summary="Pydantic v2 enum values gotcha")
        _make_entry(entries_dir, summary="Structlog reserved event keyword")
        result = _audit_duplicates(entries_dir)
        assert result["count"] == 0
        assert result["verdict"] == "PASS"

    def test_finds_duplicates(self, tmp_path: Path) -> None:
        project = _setup_project(tmp_path)
        entries_dir = project / ".trw" / "learnings" / "entries"
        _make_entry(entries_dir, summary="Pydantic v2 use enum values changes comparison semantics in models")
        _make_entry(entries_dir, summary="Pydantic v2 use enum values changes comparison semantics in models today")
        result = _audit_duplicates(entries_dir)
        assert result["count"] >= 1
        assert result["verdict"] == "WARN"


class TestAuditIndexConsistency:
    """Tests for _audit_index_consistency."""

    def test_match(self, tmp_path: Path) -> None:
        project = _setup_project(tmp_path)
        config = TRWConfig()
        _writer.write_yaml(
            project / ".trw" / "context" / "analytics.yaml",
            {"total_learnings": 5},
        )
        result = _audit_index_consistency(project / ".trw", config, 5)
        assert result["verdict"] == "PASS"
        assert result["match"] is True

    def test_mismatch(self, tmp_path: Path) -> None:
        project = _setup_project(tmp_path)
        config = TRWConfig()
        _writer.write_yaml(
            project / ".trw" / "context" / "analytics.yaml",
            {"total_learnings": 131},
        )
        result = _audit_index_consistency(project / ".trw", config, 124)
        assert result["verdict"] == "WARN"
        assert result["match"] is False
        assert result["analytics_total"] == 131
        assert result["actual_count"] == 124

    def test_missing_analytics(self, tmp_path: Path) -> None:
        project = _setup_project(tmp_path)
        config = TRWConfig()
        result = _audit_index_consistency(project / ".trw", config, 10)
        assert result["verdict"] == "SKIP"


class TestAuditRecallEffectiveness:
    """Tests for _audit_recall_effectiveness."""

    def test_parses_recall_log(self, tmp_path: Path) -> None:
        project = _setup_project(tmp_path)
        config = TRWConfig()
        receipts_dir = project / ".trw" / "learnings" / "receipts"
        receipts_dir.mkdir(parents=True, exist_ok=True)
        log_path = receipts_dir / "recall_log.jsonl"

        lines = [
            json.dumps({"query": "*", "matched_ids": ["L-1", "L-2"]}),
            json.dumps({"query": "pydantic", "matched_ids": ["L-1"]}),
            json.dumps({"query": "nonexistent concept", "matched_ids": []}),
            json.dumps({"query": "another miss", "matched_ids": []}),
        ]
        log_path.write_text("\n".join(lines), encoding="utf-8")

        result = _audit_recall_effectiveness(project / ".trw", config)
        assert result["total_queries"] == 4
        assert result["wildcard_queries"] == 1
        assert result["named_queries"] == 3
        assert result["zero_match"] == 2
        # miss_rate = 2/3 = 0.667
        assert result["verdict"] == "WARN"

    def test_no_log_returns_skip(self, tmp_path: Path) -> None:
        project = _setup_project(tmp_path)
        config = TRWConfig()
        result = _audit_recall_effectiveness(project / ".trw", config)
        assert result["verdict"] == "SKIP"


class TestRunAudit:
    """Tests for the main run_audit orchestrator."""

    def test_no_trw_dir(self, tmp_path: Path) -> None:
        result = run_audit(tmp_path)
        assert result["status"] == "failed"
        assert "No .trw directory" in str(result.get("error", ""))

    def test_empty_project(self, tmp_path: Path) -> None:
        project = _setup_project(tmp_path)
        result = run_audit(project)
        assert result["status"] == "ok"
        learnings = result.get("learnings")
        assert isinstance(learnings, dict)
        assert learnings["total"] == 0

    def test_json_output_complete(self, tmp_path: Path) -> None:
        project = _setup_project(tmp_path)
        entries_dir = project / ".trw" / "learnings" / "entries"
        _make_entry(entries_dir, summary="Test entry")
        result = run_audit(project)

        assert result["status"] == "ok"
        assert "learnings" in result
        assert "duplicates" in result
        assert "index_consistency" in result
        assert "recall_effectiveness" in result
        assert "ceremony_compliance" in result
        assert "reflection_quality" in result
        assert "hook_versions" in result


class TestFormatMarkdown:
    """Tests for markdown output formatting."""

    def test_has_expected_sections(self, tmp_path: Path) -> None:
        project = _setup_project(tmp_path)
        entries_dir = project / ".trw" / "learnings" / "entries"
        _make_entry(entries_dir, summary="Test learning")
        result = run_audit(project)
        md = format_markdown(result)

        assert "# TRW Audit Report" in md
        assert "## Learnings" in md
        assert "## Duplicates" in md
        assert "## Index Consistency" in md

    def test_fix_actions_section(self) -> None:
        audit: dict[str, object] = {
            "project": "test",
            "generated_at": "2026-02-21T00:00:00Z",
            "fix_actions": {
                "prune": {"actions_taken": 3},
                "index_resynced": True,
            },
        }
        md = format_markdown(audit)
        assert "## Fix Actions Applied" in md
        assert "3" in md
        assert "Index resynced" in md

    def test_fix_actions_telemetry_bloat_retired(self) -> None:
        audit: dict[str, object] = {
            "project": "test",
            "generated_at": "2026-02-21T00:00:00Z",
            "fix_actions": {
                "telemetry_bloat_retired": 5,
                "prune": {"actions_taken": 0},
                "index_resynced": True,
            },
        }
        md = format_markdown(audit)
        assert "Telemetry bloat retired: 5" in md


class TestRetireTelemetryBloat:
    """Tests for _retire_telemetry_bloat helper."""

    def test_retires_repeated_operation_entries(self, tmp_path: Path) -> None:
        project = _setup_project(tmp_path)
        entries_dir = project / ".trw" / "learnings" / "entries"
        _make_entry(entries_dir, summary="Repeated operation: checkpoint (22x)", status="active")
        _make_entry(entries_dir, summary="Repeated operation: file_modified (5x)", status="active")
        _make_entry(entries_dir, summary="Normal learning — keep me", status="active")

        trw_dir = project / ".trw"
        entries_list = [_reader.read_yaml(f) for f in sorted(entries_dir.glob("*.yaml"))]
        retired = _retire_telemetry_bloat(entries_list, trw_dir)
        assert retired == 2

        # Verify the files were updated to obsolete
        updated = [_reader.read_yaml(f) for f in sorted(entries_dir.glob("*.yaml"))]
        statuses = {str(e.get("summary", "")): str(e.get("status", "")) for e in updated}
        assert statuses["Repeated operation: checkpoint (22x)"] == "obsolete"
        assert statuses["Repeated operation: file_modified (5x)"] == "obsolete"
        assert statuses["Normal learning — keep me"] == "active"

    def test_retires_success_entries(self, tmp_path: Path) -> None:
        project = _setup_project(tmp_path)
        entries_dir = project / ".trw" / "learnings" / "entries"
        _make_entry(entries_dir, summary="Success: reflection_complete (6x)", status="active")
        _make_entry(entries_dir, summary="Normal learning", status="active")

        trw_dir = project / ".trw"
        entries_list = [_reader.read_yaml(f) for f in sorted(entries_dir.glob("*.yaml"))]
        retired = _retire_telemetry_bloat(entries_list, trw_dir)
        assert retired == 1

    def test_skips_already_obsolete_entries(self, tmp_path: Path) -> None:
        project = _setup_project(tmp_path)
        entries_dir = project / ".trw" / "learnings" / "entries"
        _make_entry(entries_dir, summary="Repeated operation: old (3x)", status="obsolete")

        trw_dir = project / ".trw"
        entries_list = [_reader.read_yaml(f) for f in sorted(entries_dir.glob("*.yaml"))]
        retired = _retire_telemetry_bloat(entries_list, trw_dir)
        assert retired == 0

    def test_returns_zero_for_empty_entries(self, tmp_path: Path) -> None:
        project = _setup_project(tmp_path)
        trw_dir = project / ".trw"
        retired = _retire_telemetry_bloat([], trw_dir)
        assert retired == 0

    def test_run_audit_fix_retires_bloat(self, tmp_path: Path) -> None:
        project = _setup_project(tmp_path)
        entries_dir = project / ".trw" / "learnings" / "entries"
        _make_entry(entries_dir, summary="Repeated operation: checkpoint (10x)", status="active")
        _make_entry(entries_dir, summary="Success: build_check (3x)", status="active")
        _make_entry(entries_dir, summary="Real insight about Pydantic", status="active")

        result = run_audit(project, fix=True)
        fix_actions = result.get("fix_actions")
        assert isinstance(fix_actions, dict)
        assert fix_actions.get("telemetry_bloat_retired") == 2
