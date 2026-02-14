"""Compliance enforcement models — PRD-QUAL-003.

Pydantic v2 models for the automated compliance check tool.
"""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict

ComplianceMode = Literal["advisory", "gate"]


class ComplianceDimension(str, Enum):
    """Dimensions checked by the compliance tool."""

    RECALL = "recall"
    EVENTS = "events"
    REFLECTION = "reflection"
    CHECKPOINT = "checkpoint"
    CHANGELOG = "changelog"
    CLAUDE_MD_SYNC = "claude_md_sync"
    FRAMEWORK_DOCS = "framework_docs"


class ComplianceStatus(str, Enum):
    """Status values for compliance checks."""

    PASS = "pass"
    FAIL = "fail"
    WARNING = "warning"
    PENDING = "pending"
    EXEMPT = "exempt"
    ERROR = "error"


class DimensionResult(BaseModel):
    """Result for a single compliance dimension check."""

    model_config = ConfigDict(use_enum_values=True)

    dimension: ComplianceDimension
    status: ComplianceStatus
    message: str
    remediation: str = ""


class ComplianceReport(BaseModel):
    """Full compliance report across all dimensions."""

    model_config = ConfigDict(use_enum_values=True)

    overall_status: ComplianceStatus
    compliance_score: float
    dimensions: list[DimensionResult]
    mode: ComplianceMode
    timestamp: str
    run_id: str = ""
    applicable_count: int
    passing_count: int
