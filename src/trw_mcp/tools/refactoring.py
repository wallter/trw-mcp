"""TRW refactoring tools — classify, debt register, debt gate.

PRD-CORE-016: Proactive Refactoring Workflow.
Three tools for managing technical debt and refactoring decisions.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import structlog
from fastmcp import FastMCP

from trw_mcp.models.config import TRWConfig
from trw_mcp.models.debt import (
    CLASSIFICATION_ACTIONS,
    DebtEntry,
    DebtPriority,
    DebtRegistry,
    DebtStatus,
    RefactorClassification,
    compute_refactoring_budget,
)
from trw_mcp.state._paths import resolve_project_root
from trw_mcp.state.persistence import (
    FileStateReader,
    FileStateWriter,
    model_to_dict,
)

logger = structlog.get_logger()

_config = TRWConfig()
_reader = FileStateReader()
_writer = FileStateWriter()


def _load_debt_registry(trw_dir: Path) -> DebtRegistry:
    """Load debt registry from .trw/{config.debt_registry_filename}.

    Args:
        trw_dir: Path to the .trw directory.

    Returns:
        DebtRegistry model (empty if file doesn't exist).
    """
    registry_path = trw_dir / _config.debt_registry_filename
    if not _reader.exists(registry_path):
        return DebtRegistry()
    data = _reader.read_yaml(registry_path)
    return DebtRegistry.model_validate(data)


def _save_debt_registry(trw_dir: Path, registry: DebtRegistry) -> None:
    """Save debt registry to .trw/{config.debt_registry_filename}.

    Args:
        trw_dir: Path to the .trw directory.
        registry: Registry model to persist.
    """
    registry_path = trw_dir / _config.debt_registry_filename
    _writer.write_yaml(registry_path, model_to_dict(registry))


def register_refactoring_tools(server: FastMCP) -> None:
    """Register 3 refactoring tools on the MCP server.

    Args:
        server: FastMCP server instance to register tools on.
    """

    @server.tool()
    def trw_refactor_classify(
        description: str,
        blocks_output_contract: bool,
        changes_interface: bool,
    ) -> dict[str, object]:
        """Classify a refactoring decision using the 2x2 matrix.

        Applies the PRD-CORE-016-REQ-001 classification heuristic to determine
        whether a discovered refactoring need is blocking/deferrable and
        local/architectural, then returns the prescribed action.

        Args:
            description: Description of the structural impediment discovered.
            blocks_output_contract: False if the current shard CANNOT complete
                without this refactor (blocking). True if it CAN (deferrable).
            changes_interface: True if the refactor changes an interface that
                other modules depend on (architectural). False otherwise (local).
        """
        classification = RefactorClassification.classify(
            blocks_output_contract=blocks_output_contract,
            changes_interface=changes_interface,
        )
        fallback_action: dict[str, str] = {"action": "Unknown", "tracking": "Unknown"}
        action_info = CLASSIFICATION_ACTIONS.get(
            classification.value,
            fallback_action,
        )

        logger.info(
            "refactor_classified",
            classification=classification.value,
            description=description[:100],
        )

        return {
            "classification": classification.value,
            "action": action_info["action"],
            "tracking": action_info["tracking"],
            "description": description,
        }

    @server.tool()
    def trw_debt_register(
        title: str,
        description: str = "",
        classification: str = "deferrable-local",
        priority: str = "medium",
        category: str = "code_quality",
        affected_files: list[str] | None = None,
        discovered_in: str = "",
        discovered_by: str = "",
        estimated_effort: str = "",
        estimated_impact: str = "",
        resolve_id: str | None = None,
        resolve_prd: str | None = None,
    ) -> dict[str, object]:
        """Add, update, or resolve items in the technical debt registry.

        Creates a new debt entry in .trw/debt-registry.yaml, or resolves
        an existing entry if resolve_id is provided.

        Args:
            title: Short title for the debt item.
            description: Detailed description of the structural issue.
            classification: Refactor classification from the 2x2 matrix.
            priority: Debt priority (low, medium, high, critical).
            category: Debt category from the taxonomy.
            affected_files: List of file paths affected by this debt.
            discovered_in: Run ID where the debt was discovered.
            discovered_by: Shard ID that discovered the debt.
            estimated_effort: Effort estimate string (e.g., "2-3 hours").
            estimated_impact: Impact description.
            resolve_id: If set, resolve the debt entry with this ID instead of creating.
            resolve_prd: PRD ID that resolved this debt (used with resolve_id).
        """
        project_root = resolve_project_root()
        trw_dir = project_root / _config.trw_dir
        registry = _load_debt_registry(trw_dir)

        now = datetime.now(timezone.utc).isoformat()

        if resolve_id:
            # Resolve existing entry
            for entry in registry.entries:
                if entry.id == resolve_id:
                    entry.status = DebtStatus.RESOLVED.value
                    entry.resolved_at = now
                    if resolve_prd:
                        entry.resolved_by_prd = resolve_prd
                    _save_debt_registry(trw_dir, registry)
                    logger.info("debt_resolved", debt_id=resolve_id)
                    return {
                        "status": "resolved",
                        "debt_id": resolve_id,
                        "resolved_by_prd": resolve_prd or "",
                    }
            return {
                "status": "not_found",
                "debt_id": resolve_id,
                "error": f"Debt entry {resolve_id} not found",
            }

        # Create new entry
        debt_id = registry.next_id(prefix=_config.debt_id_prefix)
        entry = DebtEntry(
            id=debt_id,
            title=title,
            description=description,
            classification=classification,
            priority=priority,
            category=category,
            discovered_at=now,
            discovered_in=discovered_in,
            discovered_by=discovered_by,
            affected_files=affected_files or [],
            decay_score=_config.debt_initial_decay_score,
            last_assessed_at=now,
            assessment_count=1,
            estimated_effort=estimated_effort,
            estimated_impact=estimated_impact,
            status=DebtStatus.DISCOVERED.value,
        )

        # Auto-promote if decay is already critical
        if entry.should_auto_promote(threshold=_config.debt_auto_promote_threshold):
            entry.priority = DebtPriority.CRITICAL.value

        registry.entries.append(entry)
        _save_debt_registry(trw_dir, registry)

        logger.info(
            "debt_registered",
            debt_id=debt_id,
            classification=classification,
            priority=priority,
        )

        return {
            "status": "created",
            "debt_id": debt_id,
            "title": title,
            "classification": classification,
            "priority": priority,
            "decay_score": entry.decay_score,
        }

    @server.tool()
    def trw_debt_gate(
        phase: str = "plan",
    ) -> dict[str, object]:
        """Evaluate debt status for phase gate decisions.

        Reads the debt registry and produces an assessment summary
        suitable for inclusion in phase gate checks.

        For PLAN exit: reports critical/high debt items affecting planned files.
        For VALIDATE exit: indicates number of potential new debt items.

        Args:
            phase: Phase to evaluate for ("plan" or "validate").
        """
        project_root = resolve_project_root()
        trw_dir = project_root / _config.trw_dir
        registry = _load_debt_registry(trw_dir)

        active_entries = [
            e for e in registry.entries
            if e.status != DebtStatus.RESOLVED.value
        ]

        # Count entries by priority in a single pass
        priority_counts: dict[str, int] = {
            DebtPriority.CRITICAL.value: 0,
            DebtPriority.HIGH.value: 0,
            DebtPriority.MEDIUM.value: 0,
            DebtPriority.LOW.value: 0,
        }
        for entry in active_entries:
            if entry.priority in priority_counts:
                priority_counts[entry.priority] += 1

        critical_count = priority_counts[DebtPriority.CRITICAL.value]
        high_count = priority_counts[DebtPriority.HIGH.value]

        # Budget recommendation
        total_active = len(active_entries)
        budget = compute_refactoring_budget(
            total_shards=_config.debt_default_wave_size,
            has_critical_debt=critical_count > 0,
            has_high_debt=high_count > 0,
            critical_ratio=_config.debt_budget_critical_ratio,
            high_ratio=_config.debt_budget_high_ratio,
        )

        result: dict[str, object] = {
            "phase": phase,
            "total_active_debt": total_active,
            "debt_assessment": {
                "critical": critical_count,
                "high": high_count,
                "medium": priority_counts[DebtPriority.MEDIUM.value],
                "low": priority_counts[DebtPriority.LOW.value],
            },
            "budget_recommendation": budget,
            "actionable_items": [
                {"id": e.id, "title": e.title, "priority": e.priority, "decay_score": e.decay_score}
                for e in registry.get_actionable(
                    decay_threshold=_config.debt_actionable_threshold,
                )
            ],
        }

        if phase == "plan" and critical_count > 0:
            result["gate_warning"] = (
                f"{critical_count} critical debt item(s) should be addressed "
                f"before proceeding to IMPLEMENT"
            )

        logger.info(
            "debt_gate_evaluated",
            phase=phase,
            total_active=total_active,
            critical=critical_count,
        )

        return result
