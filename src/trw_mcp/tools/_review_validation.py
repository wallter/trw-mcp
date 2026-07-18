"""Shared validation for findings that may satisfy substantive REVIEW readiness."""

from __future__ import annotations

_VALID_REVIEW_SEVERITIES = frozenset(
    {
        "critical",
        "error",
        "high",
        "warning",
        "medium",
        "info",
        "low",
    }
)


def normalize_review_finding(
    finding: object,
    *,
    default_confidence: float | None = None,
) -> dict[str, object] | None:
    """Return a canonical schema-valid finding, or ``None``.

    A finding can contribute substantive REVIEW evidence only when its category
    and description are non-blank strings and its severity is a recognized
    canonical or external alias. This rejects placeholder mappings such as
    ``{}`` instead of silently converting them into ``info`` findings. Surviving
    input is validated and serialized by the canonical ``ReviewFinding`` model;
    percentage confidence values are normalized to that model's 0..1 scale.
    """
    if not isinstance(finding, dict):
        return None

    category = finding.get("category")
    description = finding.get("description")
    severity = finding.get("severity")
    if not isinstance(category, str) or not category.strip():
        return None
    if not isinstance(description, str) or not description.strip():
        return None
    if not isinstance(severity, str) or severity.strip().lower() not in _VALID_REVIEW_SEVERITIES:
        return None

    from pydantic import ValidationError

    from trw_mcp.models.run import ReviewFinding
    from trw_mcp.tools._review_helpers import _normalize_severity

    normalized: dict[str, object] = {
        **finding,
        "category": category.strip(),
        "description": description.strip(),
        "severity": _normalize_severity(severity),
    }
    if "confidence" not in normalized and default_confidence is not None:
        normalized["confidence"] = default_confidence
    raw_confidence = normalized.get("confidence")
    if isinstance(raw_confidence, bool):
        return None
    if isinstance(raw_confidence, (int, float)) and 1 < raw_confidence <= 100:
        normalized["confidence"] = raw_confidence / 100

    try:
        return ReviewFinding.model_validate(normalized).model_dump()
    except (TypeError, ValidationError):
        return None
