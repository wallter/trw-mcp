"""Extra coverage tests for trw_mcp/state/validation.py."""

from __future__ import annotations

from pathlib import Path

import pytest

from trw_mcp.models.run import OutputContract, ShardCard, ShardStatus, WaveEntry
from trw_mcp.state.validation import (
    FileContractValidator,
    _is_validate_pass,
    validate_wave_contracts,
)


class TestIsValidatePass:
    """Unit tests for _is_validate_pass predicate."""

    def test_returns_true_for_phase_check_validate_valid(self) -> None:
        event = {"event": "phase_check", "data": {"phase": "validate", "valid": True}}
        assert _is_validate_pass(event) is True

    def test_returns_false_for_wrong_event_name(self) -> None:
        event = {"event": "run_init", "data": {"phase": "validate", "valid": True}}
        assert _is_validate_pass(event) is False

    def test_returns_false_when_data_not_dict(self) -> None:
        event = {"event": "phase_check", "data": "not_a_dict"}
        assert _is_validate_pass(event) is False

    def test_returns_false_when_data_is_none(self) -> None:
        event: dict[str, object] = {"event": "phase_check", "data": None}
        assert _is_validate_pass(event) is False

    def test_returns_false_for_different_phase(self) -> None:
        event = {"event": "phase_check", "data": {"phase": "implement", "valid": True}}
        assert _is_validate_pass(event) is False

    def test_returns_false_when_valid_is_false(self) -> None:
        event = {"event": "phase_check", "data": {"phase": "validate", "valid": False}}
        assert _is_validate_pass(event) is False

    def test_returns_false_when_no_data_key(self) -> None:
        event: dict[str, object] = {"event": "phase_check"}
        assert _is_validate_pass(event) is False


class TestFileContractValidator:
    """Tests for FileContractValidator.validate_contract."""

    def test_required_file_missing_returns_failure(self, tmp_path: Path) -> None:
        contract = OutputContract(
            file="missing_file.yaml",
            required=True,
            schema_keys=[],
        )
        validator = FileContractValidator()
        failures = validator.validate_contract(contract, tmp_path)
        assert len(failures) == 1
        assert failures[0].rule == "file_exists"
        assert failures[0].severity == "error"

    def test_optional_file_missing_returns_no_failure(self, tmp_path: Path) -> None:
        contract = OutputContract(
            file="optional_file.yaml",
            required=False,
            schema_keys=[],
        )
        validator = FileContractValidator()
        failures = validator.validate_contract(contract, tmp_path)
        assert failures == []

    def test_file_exists_no_schema_keys_passes(self, tmp_path: Path) -> None:
        output_file = tmp_path / "output.yaml"
        output_file.write_text("key: value\n", encoding="utf-8")
        contract = OutputContract(file="output.yaml", required=True, schema_keys=[])
        validator = FileContractValidator()
        failures = validator.validate_contract(contract, tmp_path)
        assert failures == []

    def test_file_exists_with_required_keys_present(self, tmp_path: Path) -> None:
        output_file = tmp_path / "output.yaml"
        output_file.write_text("status: done\nresult: ok\n", encoding="utf-8")
        contract = OutputContract(
            file="output.yaml",
            required=True,
            schema_keys=["status", "result"],
        )
        validator = FileContractValidator()
        failures = validator.validate_contract(contract, tmp_path)
        assert failures == []

    def test_file_exists_with_missing_schema_key_fails(self, tmp_path: Path) -> None:
        output_file = tmp_path / "output.yaml"
        output_file.write_text("status: done\n", encoding="utf-8")
        contract = OutputContract(
            file="output.yaml",
            required=True,
            schema_keys=["status", "missing_key"],
        )
        validator = FileContractValidator()
        failures = validator.validate_contract(contract, tmp_path)
        assert any(f.rule == "required_key" for f in failures)

    def test_unparseable_yaml_returns_parseable_failure(self, tmp_path: Path) -> None:
        output_file = tmp_path / "broken.yaml"
        output_file.write_text("{ invalid yaml: [unclosed", encoding="utf-8")
        contract = OutputContract(
            file="broken.yaml",
            required=True,
            schema_keys=["key"],
        )
        validator = FileContractValidator()
        failures = validator.validate_contract(contract, tmp_path)
        assert any(f.rule == "parseable" for f in failures)


class TestValidateWaveContracts:
    """Tests for validate_wave_contracts function."""

    def test_no_shards_raises_validation_error(self, tmp_path: Path) -> None:
        from trw_mcp.exceptions import ValidationError

        wave = WaveEntry(wave=1, description="Wave 1")
        with pytest.raises(ValidationError):
            validate_wave_contracts(wave, [], tmp_path)

    def test_incomplete_shard_produces_failure(self, tmp_path: Path) -> None:
        wave = WaveEntry(wave=1, description="Wave 1")
        shard = ShardCard(
            id="shard-01",
            wave=1,
            title="Test Shard",
            status=ShardStatus.PENDING,
        )
        failures = validate_wave_contracts(wave, [shard], tmp_path)
        assert any(f.rule == "shard_complete" for f in failures)

    def test_failed_shard_has_error_severity(self, tmp_path: Path) -> None:
        wave = WaveEntry(wave=1, description="Wave 1")
        shard = ShardCard(
            id="shard-01",
            wave=1,
            title="Test Shard",
            status=ShardStatus.FAILED,
        )
        failures = validate_wave_contracts(wave, [shard], tmp_path)
        failed = [f for f in failures if f.rule == "shard_complete"]
        assert len(failed) == 1
        assert failed[0].severity == "error"

    def test_pending_shard_has_warning_severity(self, tmp_path: Path) -> None:
        wave = WaveEntry(wave=1, description="Wave 1")
        shard = ShardCard(
            id="shard-01",
            wave=1,
            title="Test Shard",
            status=ShardStatus.PENDING,
        )
        failures = validate_wave_contracts(wave, [shard], tmp_path)
        pending = [f for f in failures if f.rule == "shard_complete"]
        assert len(pending) == 1
        assert pending[0].severity == "warning"

    def test_complete_shard_no_contract_passes(self, tmp_path: Path) -> None:
        wave = WaveEntry(wave=1, description="Wave 1")
        shard = ShardCard(
            id="shard-01",
            wave=1,
            title="Test Shard",
            status=ShardStatus.COMPLETE,
        )
        failures = validate_wave_contracts(wave, [shard], tmp_path)
        assert failures == []

    def test_shard_from_different_wave_is_skipped(self, tmp_path: Path) -> None:
        wave = WaveEntry(wave=1, description="Wave 1")
        shard_wave1 = ShardCard(id="shard-01", wave=1, title="Shard 1", status=ShardStatus.COMPLETE)
        shard_wave2 = ShardCard(id="shard-02", wave=2, title="Shard 2", status=ShardStatus.PENDING)
        failures = validate_wave_contracts(wave, [shard_wave1, shard_wave2], tmp_path)
        assert not any("shard-02" in f.field for f in failures)

    def test_complete_shard_with_integration_checklist_false_flag(self, tmp_path: Path) -> None:
        wave = WaveEntry(wave=1, description="Wave 1")
        shard = ShardCard(
            id="shard-01",
            wave=1,
            title="Test Shard",
            status=ShardStatus.COMPLETE,
            registered_in_server=False,
        )
        failures = validate_wave_contracts(wave, [shard], tmp_path)
        assert any(f.rule == "integration_checklist" for f in failures)

    def test_complete_shard_with_output_contract_file_missing(self, tmp_path: Path) -> None:
        wave = WaveEntry(wave=1, description="Wave 1")
        contract = OutputContract(file="missing.yaml", required=True, schema_keys=[])
        shard = ShardCard(
            id="shard-01",
            wave=1,
            title="Test Shard",
            status=ShardStatus.COMPLETE,
            output_contract=contract,
        )
        failures = validate_wave_contracts(wave, [shard], tmp_path)
        assert any(f.rule == "file_exists" for f in failures)
