"""Output contract validation for shard and wave artifacts.

Provides a protocol-based contract validator and a file-system implementation
that checks declared output files exist and contain required schema keys.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

import structlog

from trw_mcp.exceptions import ValidationError
from trw_mcp.models.requirements import ValidationFailure
from trw_mcp.models.run import (
    OutputContract,
    ShardCard,
    ShardStatus,
    WaveEntry,
)

logger = structlog.get_logger()

# Integration checklist fields checked per shard (PRD-QUAL-011)
_INTEGRATION_CHECKLIST: dict[str, str] = {
    "registered_in_server": "Tool not registered in server.py",
    "documented_in_framework": "Not documented in FRAMEWORK.md",
    "configured_in_pyproject": "Not configured in pyproject.toml",
    "updated_in_claude_md": "Not updated in CLAUDE.md",
}


class ContractValidator(Protocol):
    """Validate output contracts for shards."""

    def validate_contract(
        self,
        contract: OutputContract,
        base_path: Path,
    ) -> list[ValidationFailure]: ...


class FileContractValidator:
    """File-based output contract validator.

    Checks that declared output files exist and contain required keys.
    """

    def validate_contract(
        self,
        contract: OutputContract,
        base_path: Path,
    ) -> list[ValidationFailure]:
        """Validate a single output contract against the filesystem.

        Args:
            contract: Output contract to validate.
            base_path: Base directory to resolve relative file paths.

        Returns:
            List of validation failures (empty if valid).
        """
        failures: list[ValidationFailure] = []
        file_path = base_path / contract.file

        if not file_path.exists():
            if contract.required:
                failures.append(
                    ValidationFailure(
                        field=contract.file,
                        rule="file_exists",
                        message=f"Required output file missing: {contract.file}",
                        severity="error",
                    )
                )
            return failures

        # Check schema keys if specified
        if contract.schema_keys:
            from trw_mcp.state.persistence import FileStateReader

            reader = FileStateReader()
            try:
                data = reader.read_yaml(file_path)
                for key in contract.schema_keys:
                    if key not in data:
                        failures.append(
                            ValidationFailure(
                                field=f"{contract.file}:{key}",
                                rule="required_key",
                                message=f"Required key missing in {contract.file}: {key}",
                                severity="error",
                            )
                        )
            except Exception as exc:
                failures.append(
                    ValidationFailure(
                        field=contract.file,
                        rule="parseable",
                        message=f"Failed to parse {contract.file}: {exc}",
                        severity="error",
                    )
                )

        logger.debug(
            "contract_validated",
            file=contract.file,
            failures=len(failures),
        )
        return failures


def validate_wave_contracts(
    wave: WaveEntry,
    shards: list[ShardCard],
    base_path: Path,
    validator: ContractValidator | None = None,
) -> list[ValidationFailure]:
    """Validate all output contracts for shards in a wave.

    Args:
        wave: Wave entry to validate.
        shards: Shard cards belonging to this wave.
        base_path: Base directory to resolve file paths.
        validator: Contract validator to use. Defaults to FileContractValidator.

    Returns:
        List of all validation failures across all shards.

    Raises:
        ValidationError: If wave has no shards to validate.
    """
    if not shards:
        raise ValidationError(
            "No shards to validate for wave",
            wave=wave.wave,
        )

    _validator = validator or FileContractValidator()
    all_failures: list[ValidationFailure] = []

    for shard in shards:
        if shard.wave != wave.wave:
            continue
        status_val = shard.status if isinstance(shard.status, str) else shard.status.value
        if status_val not in (ShardStatus.COMPLETE.value, ShardStatus.PARTIAL.value):
            all_failures.append(
                ValidationFailure(
                    field=shard.id,
                    rule="shard_complete",
                    message=f"Shard {shard.id} not complete (status: {status_val})",
                    severity="error" if status_val == ShardStatus.FAILED.value else "warning",
                )
            )
            continue
        if shard.output_contract is not None:
            failures = _validator.validate_contract(shard.output_contract, base_path)
            all_failures.extend(failures)

        # Check integration checklist fields
        for field_name, warning_msg in _INTEGRATION_CHECKLIST.items():
            value = getattr(shard, field_name, None)
            if value is False:
                all_failures.append(
                    ValidationFailure(
                        field=f"{shard.id}:{field_name}",
                        rule="integration_checklist",
                        message=f"Shard {shard.id}: {warning_msg}",
                        severity="warning",
                    )
                )

    logger.info(
        "wave_validated",
        wave=wave.wave,
        shards_checked=len(shards),
        failures=len(all_failures),
    )
    return all_failures
