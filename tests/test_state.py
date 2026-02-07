"""Tests for state persistence and validation modules."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from trw_mcp.exceptions import StateError
from trw_mcp.models.config import TRWConfig
from trw_mcp.models.requirements import PRDQualityGates, ValidationFailure
from trw_mcp.models.run import (
    OutputContract,
    Phase,
    ShardCard,
    ShardStatus,
    WaveEntry,
    WaveStatus,
)
from trw_mcp.state.persistence import (
    FileEventLogger,
    FileStateReader,
    FileStateWriter,
    model_to_dict,
)
from trw_mcp.state.validation import (
    FileContractValidator,
    check_phase_exit,
    validate_prd_quality,
    validate_wave_contracts,
)


class TestFileStateReader:
    """Tests for FileStateReader."""

    def test_read_yaml_valid(self, tmp_path: Path, writer: FileStateWriter, reader: FileStateReader) -> None:
        path = tmp_path / "test.yaml"
        writer.write_yaml(path, {"key": "value", "num": 42})
        data = reader.read_yaml(path)
        assert data["key"] == "value"
        assert data["num"] == 42

    def test_read_yaml_not_found(self, tmp_path: Path, reader: FileStateReader) -> None:
        with pytest.raises(StateError, match="not found"):
            reader.read_yaml(tmp_path / "nonexistent.yaml")

    def test_read_yaml_empty(self, tmp_path: Path, reader: FileStateReader) -> None:
        path = tmp_path / "empty.yaml"
        path.write_text("", encoding="utf-8")
        data = reader.read_yaml(path)
        assert data == {}

    def test_read_jsonl_valid(self, tmp_path: Path, writer: FileStateWriter, reader: FileStateReader) -> None:
        path = tmp_path / "events.jsonl"
        writer.append_jsonl(path, {"event": "test1", "ts": "2026-01-01"})
        writer.append_jsonl(path, {"event": "test2", "ts": "2026-01-02"})
        records = reader.read_jsonl(path)
        assert len(records) == 2
        assert records[0]["event"] == "test1"

    def test_read_jsonl_not_found(self, tmp_path: Path, reader: FileStateReader) -> None:
        records = reader.read_jsonl(tmp_path / "nonexistent.jsonl")
        assert records == []

    def test_read_jsonl_empty_lines(self, tmp_path: Path, reader: FileStateReader) -> None:
        path = tmp_path / "sparse.jsonl"
        path.write_text('{"a": 1}\n\n{"b": 2}\n\n', encoding="utf-8")
        records = reader.read_jsonl(path)
        assert len(records) == 2

    def test_read_jsonl_invalid(self, tmp_path: Path, reader: FileStateReader) -> None:
        path = tmp_path / "bad.jsonl"
        path.write_text("not json\n", encoding="utf-8")
        with pytest.raises(StateError, match="Failed to parse"):
            reader.read_jsonl(path)

    def test_exists(self, tmp_path: Path, reader: FileStateReader) -> None:
        path = tmp_path / "exists.txt"
        assert reader.exists(path) is False
        path.write_text("hi", encoding="utf-8")
        assert reader.exists(path) is True


class TestFileStateWriter:
    """Tests for FileStateWriter."""

    def test_write_yaml_creates_parents(self, tmp_path: Path, writer: FileStateWriter, reader: FileStateReader) -> None:
        path = tmp_path / "deep" / "nested" / "file.yaml"
        writer.write_yaml(path, {"created": True})
        data = reader.read_yaml(path)
        assert data["created"] is True

    def test_write_yaml_atomic(self, tmp_path: Path, writer: FileStateWriter) -> None:
        path = tmp_path / "atomic.yaml"
        writer.write_yaml(path, {"version": 1})
        writer.write_yaml(path, {"version": 2})
        # Should have the latest value
        reader = FileStateReader()
        data = reader.read_yaml(path)
        assert data["version"] == 2

    def test_append_jsonl(self, tmp_path: Path, writer: FileStateWriter) -> None:
        path = tmp_path / "log.jsonl"
        writer.append_jsonl(path, {"event": "first"})
        writer.append_jsonl(path, {"event": "second"})
        lines = path.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 2
        assert json.loads(lines[0])["event"] == "first"

    def test_ensure_dir(self, tmp_path: Path, writer: FileStateWriter) -> None:
        path = tmp_path / "a" / "b" / "c"
        writer.ensure_dir(path)
        assert path.exists()
        assert path.is_dir()


class TestFileEventLogger:
    """Tests for FileEventLogger."""

    def test_log_event(self, tmp_path: Path, event_logger: FileEventLogger, reader: FileStateReader) -> None:
        events_path = tmp_path / "events.jsonl"
        event_logger.log_event(events_path, "test_event", {"key": "value"})
        records = reader.read_jsonl(events_path)
        assert len(records) == 1
        assert records[0]["event"] == "test_event"
        assert records[0]["key"] == "value"
        assert "ts" in records[0]


class TestModelToDict:
    """Tests for model_to_dict helper."""

    def test_run_state(self) -> None:
        from trw_mcp.models.run import RunState
        state = RunState(run_id="test", task="test-task")
        d = model_to_dict(state)
        assert d["run_id"] == "test"
        assert d["status"] == "active"
        assert d["phase"] == "research"
        assert isinstance(d, dict)


class TestFileContractValidator:
    """Tests for FileContractValidator."""

    def test_valid_contract(self, tmp_path: Path, writer: FileStateWriter) -> None:
        # Create output file with required keys
        output_file = tmp_path / "scratch" / "shard-001" / "result.yaml"
        writer.write_yaml(output_file, {"summary": "test", "findings": []})

        contract = OutputContract(
            file="scratch/shard-001/result.yaml",
            schema_keys=["summary", "findings"],
            required=True,
        )
        validator = FileContractValidator()
        failures = validator.validate_contract(contract, tmp_path)
        assert failures == []

    def test_missing_file(self, tmp_path: Path) -> None:
        contract = OutputContract(
            file="missing.yaml",
            schema_keys=["key"],
            required=True,
        )
        validator = FileContractValidator()
        failures = validator.validate_contract(contract, tmp_path)
        assert len(failures) == 1
        assert failures[0].rule == "file_exists"

    def test_missing_key(self, tmp_path: Path, writer: FileStateWriter) -> None:
        output_file = tmp_path / "result.yaml"
        writer.write_yaml(output_file, {"summary": "test"})  # Missing 'findings'

        contract = OutputContract(
            file="result.yaml",
            schema_keys=["summary", "findings"],
            required=True,
        )
        validator = FileContractValidator()
        failures = validator.validate_contract(contract, tmp_path)
        assert len(failures) == 1
        assert "findings" in failures[0].message

    def test_optional_missing_file(self, tmp_path: Path) -> None:
        contract = OutputContract(
            file="optional.yaml",
            schema_keys=[],
            required=False,
        )
        validator = FileContractValidator()
        failures = validator.validate_contract(contract, tmp_path)
        assert failures == []


class TestValidateWaveContracts:
    """Tests for validate_wave_contracts."""

    def test_all_complete(self, tmp_path: Path, writer: FileStateWriter) -> None:
        writer.write_yaml(
            tmp_path / "out.yaml",
            {"summary": "done", "findings": []},
        )
        wave = WaveEntry(wave=1, shards=["shard-001"], status=WaveStatus.ACTIVE)
        shards = [
            ShardCard(
                id="shard-001",
                title="Test",
                wave=1,
                status=ShardStatus.COMPLETE,
                output_contract=OutputContract(
                    file="out.yaml",
                    schema_keys=["summary", "findings"],
                ),
            ),
        ]
        failures = validate_wave_contracts(wave, shards, tmp_path)
        assert failures == []

    def test_failed_shard(self, tmp_path: Path) -> None:
        wave = WaveEntry(wave=1, shards=["shard-001"], status=WaveStatus.ACTIVE)
        shards = [
            ShardCard(
                id="shard-001",
                title="Failed",
                wave=1,
                status=ShardStatus.FAILED,
            ),
        ]
        failures = validate_wave_contracts(wave, shards, tmp_path)
        assert len(failures) >= 1
        # With use_enum_values, status is a string — validation checks for enum membership
        assert any("not complete" in f.message or "shard-001" in f.message for f in failures)


class TestCheckPhaseExit:
    """Tests for check_phase_exit."""

    def test_research_no_synthesis(self, sample_run_dir: Path) -> None:
        config = TRWConfig()
        result = check_phase_exit(Phase.RESEARCH, sample_run_dir, config)
        # Should warn about missing synthesis
        assert any("synthesis" in f.message.lower() for f in result.failures)

    def test_plan_no_plan_doc(self, sample_run_dir: Path) -> None:
        config = TRWConfig()
        result = check_phase_exit(Phase.PLAN, sample_run_dir, config)
        assert any("plan" in f.message.lower() for f in result.failures)

    def test_plan_with_plan_doc(self, sample_run_dir: Path) -> None:
        config = TRWConfig()
        plan_path = sample_run_dir / "reports" / "plan.md"
        plan_path.write_text("# Plan\n\nContent", encoding="utf-8")
        result = check_phase_exit(Phase.PLAN, sample_run_dir, config)
        # Should pass without plan-related errors
        plan_failures = [f for f in result.failures if "plan" in f.message.lower()]
        assert plan_failures == []


class TestValidatePrdQuality:
    """Tests for validate_prd_quality."""

    def test_valid_prd(self) -> None:
        frontmatter: dict[str, object] = {
            "id": "PRD-CORE-001",
            "title": "Test PRD",
            "version": "1.0",
            "status": "draft",
            "priority": "P1",
            "confidence": {
                "implementation_feasibility": 0.8,
                "requirement_clarity": 0.8,
                "estimate_confidence": 0.7,
            },
            "traceability": {
                "implements": ["KE-FRAME-001"],
            },
        }
        sections = [
            "Problem Statement",
            "Goals & Non-Goals",
            "User Stories",
            "Functional Requirements",
            "Non-Functional Requirements",
            "Technical Approach",
            "Test Strategy",
            "Rollout Plan",
            "Success Metrics",
            "Dependencies & Risks",
            "Open Questions",
            "Traceability Matrix",
        ]
        result = validate_prd_quality(frontmatter, sections)
        assert result.valid is True

    def test_missing_fields(self) -> None:
        frontmatter: dict[str, object] = {"id": "PRD-CORE-001"}
        sections: list[str] = []
        result = validate_prd_quality(frontmatter, sections)
        assert result.valid is False
        assert len(result.failures) > 0

    def test_insufficient_sections(self) -> None:
        frontmatter: dict[str, object] = {
            "id": "PRD-CORE-001",
            "title": "Test",
            "version": "1.0",
            "status": "draft",
            "priority": "P1",
            "traceability": {"implements": ["KE-001"]},
        }
        sections = ["Problem Statement", "Goals"]
        result = validate_prd_quality(frontmatter, sections)
        assert any("sections" in f.message.lower() for f in result.failures)
