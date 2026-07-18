"""Trust state TypedDicts (state/trust.py)."""

from __future__ import annotations

from typing import Literal

from typing_extensions import TypedDict


class TrustLevelResult(TypedDict):
    """Return shape of ``trust_level_calculate()``."""

    tier: str
    session_count: int
    review_mode: str
    review_sample_rate: float | None
    locked: bool
    lock_reason: str | None


class HumanReviewResult(TypedDict):
    """Return shape of ``requires_human_review()``."""

    required: bool
    reason: str
    override_tier: bool


class ApprovalControlPrimitive(TypedDict):
    """Operator-facing approval primitive description."""

    purpose: str
    code_path: str


class ApprovalControlMapResult(TypedDict):
    """Return shape of ``approval_control_map()``."""

    compliance_claim: Literal["none"]
    non_compliance_boundary: str
    operator_diagnostics: tuple[str, ...]
    controls: dict[str, ApprovalControlPrimitive]
