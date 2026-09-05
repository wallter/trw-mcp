"""Record types for the configuration-admission table (PRD-CORE-218-FR05).

Split out of ``_field_admission_registry.py`` so that per-domain admission
tables (e.g. ``_field_admission_instruction_writes.py``) can import the record
type without importing the registry that aggregates them — the shape that would
otherwise be an import cycle.

Imports nothing from the rest of ``trw_mcp.models.config``.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

BudgetDecision = Literal["admitted", "legacy-admitted", "rejected", "deferred"]


class ConfigAdmission(BaseModel):
    """Full admission record for one public configuration field."""

    model_config = ConfigDict(frozen=True)

    field_name: str
    owner: str
    consumer: str
    default_rationale: str
    interaction_analysis: str
    deprecation_plan: str
    docs_pointer: str
    test_pointer: str
    budget_decision: BudgetDecision


__all__ = ["BudgetDecision", "ConfigAdmission"]
